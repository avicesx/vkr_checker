from __future__ import annotations

from datetime import datetime, timezone

from parser.core.schema import (
    CommentArtifact,
    DocumentAnomalies,
    DocumentMeta,
    FigureNumberingIssue,
    GlobalSettings,
    NumberedCaptionEntity,
    ParagraphBlock,
    ParsedDocxDocument,
    ThemeData,
    TrackChangeArtifact,
)
from rule_engine.findings import RuleAssessment
from violation_reports import (
    ReportCategory,
    ReportSeverity,
    Violation,
    ViolationReport,
    build_violation_report,
)


def _sample_parser_result() -> ParsedDocxDocument:
    """создаёт тестовые данные парсера с примерами нарушений"""
    paragraph = ParagraphBlock(
        block_id="p-12",
        section_idx=0,
        page_start_est=4,
        page_end_est=4,
        text_raw="Проверочный фрагмент.",
        text_visible="Проверочный фрагмент.",
    )
    return ParsedDocxDocument(
        meta=DocumentMeta(filename="sample-thesis.docx", file_sha256="test-hash"),
        settings=GlobalSettings(),
        theme=ThemeData(),
        blocks=[paragraph],
        anomalies=DocumentAnomalies(
            unaccepted_track_changes=[
                TrackChangeArtifact(
                    artifact_type="insertion",
                    author="Test author",
                    block_id="p-12",
                    page_est=4,
                    text_snippet="новая фраза",
                )
            ],
            leftover_comments=[
                CommentArtifact(
                    comment_id="comment-1",
                    block_id="p-12",
                    page_est=4,
                    commented_text="Проверочный фрагмент.",
                    comment_body="Проверить формулировку",
                )
            ],
            hidden_text_block_ids=["p-12"],
        ),
    )


def _rule_failure(rule_id: str) -> RuleAssessment:
    """создаёт неуспешный результат проверки правила с заданным rule_id"""
    return RuleAssessment(
        rule_id=rule_id,
        kind="font",
        status="fail",
        expected_text="требование методички",
        observed_text="фактическое оформление",
        confidence="medium",
    )


def _violations_by_id(report: ViolationReport) -> dict[str, Violation]:
    """индексирует нарушения отчёта по их идентификаторам"""
    return {
        violation.id: violation
        for group in report.groups
        for violation in group.violations
    }


def test_report_groups_test_findings_and_counts_severity() -> None:
    """группирует примеры нарушений по категории и критичности"""
    report = build_violation_report(
        _sample_parser_result(),
        [
            RuleAssessment(
                rule_id="body_font_size",
                kind="font",
                status="fail",
                expected_text="14 pt",
                observed_text="12 pt",
                confidence="medium",
            ),
            RuleAssessment(
                rule_id="line_spacing",
                kind="line_spacing",
                status="not_assessed",
                expected_text="1.5",
                observed_text="not enough lines",
                confidence="low",
            ),
            RuleAssessment(
                rule_id="page_size_a4",
                kind="page_format",
                status="pass",
                expected_text="A4",
                observed_text="A4",
                confidence="high",
            ),
        ],
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    assert report.document_name == "sample-thesis.docx"
    assert report.summary.total_violations == 4
    assert report.summary.high == 2
    assert report.summary.medium == 2
    assert report.summary.low == 0
    assert report.summary.critical == 0
    assert {group.category for group in report.groups} == {
        ReportCategory.REVIEW_ARTIFACTS,
        ReportCategory.DOCUMENT_INTEGRITY,
        ReportCategory.FORMATTING,
    }

    hidden_text = next(
        violation
        for group in report.groups
        for violation in group.violations
        if violation.id == "hidden-text:p-12"
    )
    assert hidden_text.severity == ReportSeverity.HIGH
    assert hidden_text.location.block_id == "p-12"
    assert hidden_text.location.page_start == 4
    assert hidden_text.location.text_excerpt == "Проверочный фрагмент."


def test_report_serializes_to_json_and_renders_markdown() -> None:
    """проверяет JSON-представление отчёта и его вывод в Markdown"""
    report = build_violation_report(
        _sample_parser_result(),
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    json_report = report.model_dump(mode="json")
    markdown_report = report.to_markdown()

    assert json_report["summary"]["total_violations"] == 3
    assert json_report["groups"][0]["violations"][0]["location"]["page_start"] == 4
    assert "# Отчёт о нарушениях: sample-thesis.docx" in markdown_report
    assert "## Артефакты рецензирования" in markdown_report
    assert "- Место: стр. 4, блок `p-12`" in markdown_report
    assert "- Рекомендация:" in markdown_report


def test_empty_result_has_zero_summary_and_markdown_notice() -> None:
    """проверяет отчёт без нарушений, не создавая фиктивных групп"""
    document = _sample_parser_result()
    document.anomalies = DocumentAnomalies()

    report = build_violation_report(
        document,
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    assert report.summary.total_violations == 0
    assert report.groups == []
    assert "Нарушений не найдено." in report.to_markdown()


def test_new_rule_families_fall_into_own_categories() -> None:
    """новые семейства правил попадают в свои категории отчёта"""
    document = _sample_parser_result()
    document.anomalies = DocumentAnomalies()

    report = build_violation_report(
        document,
        [
            _rule_failure("bibliography_link"),
            _rule_failure("bibliography_cited"),
            _rule_failure("bibliography_format"),
            _rule_failure("caption_numbering"),
            _rule_failure("caption_format"),
            _rule_failure("caption_reference"),
            _rule_failure("alignment"),
        ],
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    violations = _violations_by_id(report)
    assert violations["pdf:bibliography_link"].category == ReportCategory.BIBLIOGRAPHY
    assert violations["pdf:bibliography_cited"].category == ReportCategory.BIBLIOGRAPHY
    assert violations["pdf:bibliography_format"].category == ReportCategory.BIBLIOGRAPHY
    assert violations["pdf:caption_numbering"].category == ReportCategory.CAPTIONS
    assert violations["pdf:caption_format"].category == ReportCategory.CAPTIONS
    assert violations["pdf:caption_reference"].category == ReportCategory.CAPTIONS
    assert violations["pdf:alignment"].category == ReportCategory.FORMATTING

    titles = {group.category: group.title for group in report.groups}
    assert titles[ReportCategory.BIBLIOGRAPHY] == "Список литературы и ссылки на источники"
    assert titles[ReportCategory.CAPTIONS] == "Подписи рисунков и таблиц"


def test_table_without_reference_adds_violation() -> None:
    """таблица без ссылки в основном тексте даёт нарушение отчёта"""
    document = _sample_parser_result()
    document.anomalies = DocumentAnomalies(unreferenced_table_ids=["tbl-2"])
    document.cross_ref_index.tables = [
        NumberedCaptionEntity(
            entity_id="tbl-2",
            entity_type="table",
            caption_block_id="p-12",
            page_est=7,
            prefix_word="Таблица",
            number_str="2",
            numbering_scheme="continuous_arabic",
            title_text="Результаты измерений",
        )
    ]

    report = build_violation_report(
        document,
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    violation = _violations_by_id(report)["unreferenced-table:tbl-2"]
    assert violation.category == ReportCategory.STRUCTURE
    assert violation.severity == ReportSeverity.LOW
    assert violation.location.block_id == "p-12"
    assert violation.location.page_start == 7
    assert "таблицу" in violation.description


def test_table_numbering_duplicate_adds_violation() -> None:
    """дубль номера подписи таблицы даёт нарушение категории подписей"""
    document = _sample_parser_result()
    document.anomalies = DocumentAnomalies(
        figure_numbering_issues=[
            FigureNumberingIssue(
                issue_kind="duplicate",
                number_str="3",
                caption_block_id="p-12",
                previous_number_str="3",
                entity_type="table",
            ),
            FigureNumberingIssue(
                issue_kind="duplicate",
                number_str="5",
                previous_number_str="5",
            ),
        ]
    )

    report = build_violation_report(
        document,
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    violations = _violations_by_id(report)
    table_violation = violations["table-numbering:duplicate:p-12"]
    assert table_violation.category == ReportCategory.CAPTIONS
    assert table_violation.severity == ReportSeverity.MEDIUM
    assert "подписи таблицы" in table_violation.description
    assert "подписей таблиц" in table_violation.recommendation

    figure_violation = violations["figure-numbering:duplicate:5"]
    assert figure_violation.category == ReportCategory.FIGURES
    assert "подписи рисунка" in figure_violation.description


def test_unknown_rule_id_falls_back_by_family_prefix() -> None:
    """rule_id без точного маппинга получает описание по префиксу семейства"""
    document = _sample_parser_result()
    document.anomalies = DocumentAnomalies()

    report = build_violation_report(
        document,
        [
            _rule_failure("bibliography_page_number_outdated"),
            _rule_failure("caption_alignment_left"),
            _rule_failure("table_cell_font_size"),
            _rule_failure("font_table_cells"),
        ],
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    violations = _violations_by_id(report)
    bibliography = violations["pdf:bibliography_page_number_outdated"]
    caption = violations["pdf:caption_alignment_left"]
    table = violations["pdf:table_cell_font_size"]
    font = violations["pdf:font_table_cells"]

    assert bibliography.category == ReportCategory.BIBLIOGRAPHY
    assert caption.category == ReportCategory.CAPTIONS
    assert table.category == ReportCategory.FORMATTING
    assert font.category == ReportCategory.FORMATTING
    for violation in (bibliography, caption, table, font):
        assert "Не выполнено требование оформления" not in violation.description
        assert violation.recommendation
