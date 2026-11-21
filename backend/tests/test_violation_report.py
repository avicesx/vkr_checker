from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Sequence

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
from rule_engine.findings import (
    DocumentRef,
    RuleAssessment,
    RuleCheckReport,
    Violation as RuleViolation,
    ViolationLocation as RuleViolationLocation,
    build_summary,
)
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


def _assessment(
    rule_id: str,
    code: str,
    status: str,
    *,
    expected_text: str = "требование методички",
    observed_text: str = "фактическое оформление",
    confidence: str = "medium",
) -> RuleAssessment:
    """создаёт оценку правила с заданным статусом"""
    return RuleAssessment(
        rule_id=rule_id,
        code=code,
        status=status,
        expected_text=expected_text,
        observed_text=observed_text,
        confidence=confidence,
    )


def _rule_violation(
    rule_id: str,
    code: str,
    *,
    address: str = "p-12",
    block_id: str | None = "p-12",
    section_idx: int | None = None,
    page_start_est: int | None = 4,
    page_end_est: int | None = None,
    snippet: str = "Проверочный фрагмент.",
    message: str = "Требование оформления не выполнено",
    expected_text: str = "требование методички",
    found_text: str = "фактическое оформление",
    found: dict[str, int] | None = None,
    occurrences: int = 1,
    severity: str = "error",
) -> RuleViolation:
    """создаёт нарушение движка с адресом для проверки отчёта"""
    return RuleViolation(
        violation_id=f"{rule_id}:{address}",
        rule_id=rule_id,
        code=code,
        severity=severity,
        message=message,
        location=RuleViolationLocation(
            block_id=block_id,
            section_idx=section_idx,
            page_start_est=page_start_est,
            page_end_est=page_end_est,
            snippet=snippet,
        ),
        expected={},
        found=found or {},
        expected_text=expected_text,
        found_text=found_text,
        occurrences=occurrences,
    )


def _rule_report(
    violations: Sequence[RuleViolation] = (),
    assessments: Iterable[RuleAssessment] = (),
) -> RuleCheckReport:
    """собирает отчёт движка для передачи в адаптер отчёта"""
    violation_list = list(violations)
    return RuleCheckReport(
        ruleset_id="test-ruleset",
        ruleset_version="1.0.0",
        parser_schema_version="3.0.0",
        document=DocumentRef(
            filename="sample-thesis.docx",
            file_sha256="test-hash",
        ),
        summary=build_summary(violation_list, rules_applied=0, objects_checked=0),
        violations=violation_list,
        assessments=list(assessments),
        passed=not any(item.severity == "error" for item in violation_list),
    )


def _violations_by_id(report: ViolationReport) -> dict[str, Violation]:
    """индексирует нарушения отчёта по их идентификаторам"""
    return {
        violation.id: violation
        for group in report.groups
        for violation in group.violations
    }


def _rule_engine_violations(report: ViolationReport) -> list[Violation]:
    """возвращает записи отчёта, созданные по нарушениям движка"""
    return [
        violation
        for group in report.groups
        for violation in group.violations
        if violation.source == "rule_engine"
    ]


def test_report_groups_test_findings_and_counts_severity() -> None:
    """группирует примеры нарушений по категории и критичности"""
    rule_report = _rule_report(
        [
            _rule_violation(
                "font_body_text",
                "font",
                expected_text="Times New Roman, 14 pt",
                found_text="Arial, 12 pt",
            )
        ],
        [
            _assessment("font_body_text", "font", "fail"),
            _assessment(
                "line_spacing",
                "line_spacing",
                "not_assessed",
                expected_text="1.5",
                observed_text="not enough lines",
                confidence="low",
            ),
            _assessment(
                "page_size_a4",
                "page_format",
                "pass",
                expected_text="A4",
                observed_text="A4",
                confidence="high",
            ),
        ],
    )
    report = build_violation_report(
        _sample_parser_result(),
        rule_report,
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

    engine_violation = _violations_by_id(report)["font_body_text:p-12"]
    assert engine_violation.source == "rule_engine"
    assert engine_violation.category == ReportCategory.FORMATTING
    assert engine_violation.location.block_id == "p-12"
    assert engine_violation.location.page_start == 4
    assert "Ожидалось: Times New Roman, 14 pt" in engine_violation.description
    assert "Обнаружено: Arial, 12 pt" in engine_violation.description

    assert report.coverage.rules_assessed == 3
    assert report.coverage.passed == 1
    assert report.coverage.failed == 1
    assert report.coverage.not_assessed == 1
    assert [item.rule_id for item in report.coverage.not_assessed_rules] == [
        "line_spacing"
    ]


def test_report_serializes_to_json_and_renders_markdown() -> None:
    """проверяет JSON-представление отчёта и его вывод в Markdown"""
    report = build_violation_report(
        _sample_parser_result(),
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    json_report = report.model_dump(mode="json")
    markdown_report = report.to_markdown()

    assert json_report["summary"]["total_violations"] == 3
    assert json_report["coverage"]["rules_assessed"] == 0
    assert json_report["groups"][0]["violations"][0]["location"]["page_start"] == 4
    assert "# Отчёт о нарушениях: sample-thesis.docx" in markdown_report
    assert "## Артефакты рецензирования" in markdown_report
    assert "- Место: стр. 4, блок `p-12`" in markdown_report
    assert "- Рекомендация:" in markdown_report
    assert "- Покрытие правил: проверено — 0" in markdown_report


def test_empty_result_has_zero_summary_and_markdown_notice() -> None:
    """проверяет отчёт без нарушений, не создавая фиктивных групп"""
    document = _sample_parser_result()
    document.anomalies = DocumentAnomalies()

    report = build_violation_report(
        document,
        _rule_report(),
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    assert report.summary.total_violations == 0
    assert report.groups == []
    assert report.coverage.rules_assessed == 0
    assert "Нарушений не найдено." in report.to_markdown()


def test_new_rule_families_fall_into_own_categories() -> None:
    """новые семейства правил попадают в свои категории отчёта"""
    document = _sample_parser_result()
    document.anomalies = DocumentAnomalies()

    rule_report = _rule_report(
        [
            _rule_violation("bibliography_link", "bibliography_link", address="b1"),
            _rule_violation("bibliography_cited", "bibliography_cited", address="b2"),
            _rule_violation(
                "bibliography_format", "bibliography_format", address="b3"
            ),
            _rule_violation("caption_numbering", "caption_numbering", address="c1"),
            _rule_violation("caption_format", "caption_format", address="c2"),
            _rule_violation("caption_reference", "caption_reference", address="c3"),
            _rule_violation("alignment", "alignment", address="a1"),
        ]
    )
    report = build_violation_report(
        document,
        rule_report,
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    violations = _violations_by_id(report)
    assert violations["bibliography_link:b1"].category == ReportCategory.BIBLIOGRAPHY
    assert violations["bibliography_cited:b2"].category == ReportCategory.BIBLIOGRAPHY
    assert violations["bibliography_format:b3"].category == ReportCategory.BIBLIOGRAPHY
    assert violations["caption_numbering:c1"].category == ReportCategory.CAPTIONS
    assert violations["caption_format:c2"].category == ReportCategory.CAPTIONS
    assert violations["caption_reference:c3"].category == ReportCategory.CAPTIONS
    assert violations["alignment:a1"].category == ReportCategory.FORMATTING

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


def test_new_rule_ids_inherit_category_from_kind() -> None:
    """rule_id без отдельного маппинга получает категорию по code правила"""
    document = _sample_parser_result()
    document.anomalies = DocumentAnomalies()

    rule_report = _rule_report(
        [
            _rule_violation(
                "bibliography_page_number_outdated",
                "bibliography_format",
                address="b1",
            ),
            _rule_violation(
                "caption_alignment_left", "caption_format", address="c1"
            ),
            _rule_violation("table_cell_font_size", "font", address="t1"),
            _rule_violation("font_table_cells", "font", address="t2"),
        ]
    )
    report = build_violation_report(
        document,
        rule_report,
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    violations = _violations_by_id(report)
    bibliography = violations["bibliography_page_number_outdated:b1"]
    caption = violations["caption_alignment_left:c1"]
    table = violations["table_cell_font_size:t1"]
    font = violations["font_table_cells:t2"]

    assert bibliography.category == ReportCategory.BIBLIOGRAPHY
    assert caption.category == ReportCategory.CAPTIONS
    assert table.category == ReportCategory.FORMATTING
    assert font.category == ReportCategory.FORMATTING
    for violation in (bibliography, caption, table, font):
        assert "Не выполнено требование оформления" not in violation.description
        assert violation.recommendation


def test_every_engine_violation_becomes_one_addressed_entry() -> None:
    """ZERO LOSS: каждое нарушение движка — отдельная запись отчёта с адресом"""
    document = _sample_parser_result()
    engine_violations = [
        _rule_violation("font_body_text", "font", address="p-12"),
        _rule_violation(
            "indent_first_line",
            "indent",
            address="p-13",
            block_id="p-13",
            page_start_est=5,
            snippet="Второй абзац.",
        ),
        _rule_violation(
            "page_margins",
            "margin",
            address="section:0",
            block_id=None,
            section_idx=0,
            page_start_est=None,
            snippet="",
        ),
        _rule_violation(
            "heading_numbering",
            "heading_numbering",
            address="section:7",
            block_id=None,
            page_start_est=7,
            page_end_est=8,
            snippet="",
        ),
        _rule_violation(
            "bibliography_cited",
            "bibliography_cited",
            address="entry:3",
            block_id=None,
            page_start_est=11,
            snippet="Иванов И. И. ...",
        ),
    ]
    rule_report = _rule_report(engine_violations)
    report = build_violation_report(
        document,
        rule_report,
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    entries = _rule_engine_violations(report)
    assert len(entries) == len(engine_violations) == 5
    assert report.summary.total_violations == 3 + 5

    by_id = _violations_by_id(report)
    for source in engine_violations:
        entry = by_id[source.violation_id]
        assert entry.source == "rule_engine"
        assert entry.description
        assert entry.recommendation

    first = by_id["font_body_text:p-12"]
    assert first.location.block_id == "p-12"
    assert first.location.page_start == 4
    assert first.location.scope == "block"

    indent = by_id["indent_first_line:p-13"]
    assert indent.location.block_id == "p-13"
    assert indent.location.page_start == 5
    assert indent.location.text_excerpt == "Второй абзац."

    margins = by_id["page_margins:section:0"]
    assert margins.location.scope == "section"
    assert margins.location.section_idx == 0
    assert margins.location.page_start is None

    heading = by_id["heading_numbering:section:7"]
    assert heading.location.scope == "page"
    assert heading.location.page_start == 7
    assert heading.location.page_end == 8

    bibliography = by_id["bibliography_cited:entry:3"]
    assert bibliography.location.page_start == 11
    assert bibliography.location.text_excerpt == "Иванов И. И. ..."

    markdown_report = report.to_markdown()
    assert "font_body_text:p-12" in markdown_report
    assert "стр. 5, блок `p-13`" in markdown_report
    assert "секция 0" in markdown_report
    assert "- Покрытие правил: проверено — 0" in markdown_report


def test_repeated_rule_violations_are_grouped_without_losing_addresses() -> None:
    """ZERO LOSS: группа хранит все адреса, а summary считает исходные находки"""
    document = _sample_parser_result()
    document.anomalies = DocumentAnomalies()
    engine_violations = [
        _rule_violation(
            "alignment_table_cells",
            "alignment",
            address=f"tbl_{table:04d}_r0_c{cell}_p0",
            block_id=f"tbl_{table:04d}_r0_c{cell}_p0",
            page_start_est=5 + table,
            snippet=f"Ячейка {table}-{cell}",
        )
        for table in (1, 2)
        for cell in range(6)
    ]
    report = build_violation_report(
        document,
        _rule_report(engine_violations),
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    entries = _rule_engine_violations(report)
    assert len(entries) == 1
    grouped = entries[0]
    assert grouped.id == "group:alignment_table_cells"
    assert grouped.rule_id == "alignment_table_cells"
    assert grouped.count == 12
    assert len(grouped.locations) == 12
    assert grouped.location.block_id == "tbl_0001_r0_c0_p0"
    assert {item.block_id for item in grouped.locations} == {
        violation.location.block_id for violation in engine_violations
    }
    assert report.summary.total_violations == 12

    payload = report.model_dump(mode="json")
    grouped_payload = next(
        item
        for group in payload["groups"]
        for item in group["violations"]
        if item["id"] == "group:alignment_table_cells"
    )
    assert len(grouped_payload["locations"]) == 12
    assert grouped_payload["locations"][0]["page_start"] == 6
    assert grouped_payload["count"] == 12

    markdown_report = report.to_markdown()
    assert markdown_report.count("### [MEDIUM] alignment_table_cells") == 1
    assert "alignment_table_cells — 12 нарушений (таблицы 1–2)" in markdown_report
    assert "tbl_0001_r0_c0_p0 (стр. 6): «Ячейка 1-0»" in markdown_report
    assert "и ещё 2" in markdown_report


def test_grouped_microtypography_keeps_subtype_totals_and_span_examples() -> None:
    """группа микротипографики хранит суммы подтипов и примеры из span-фрагментов"""
    document = _sample_parser_result()
    document.anomalies = DocumentAnomalies()
    engine_violations = [
        _rule_violation(
            "microtypography_prose",
            "microtypography",
            address="p-1",
            block_id="p-1",
            page_start_est=2,
            snippet="Начало абзаца один",
            found_text="неверные кавычки: 2 (фрагмент кавычек)",
            found={"wrong_quotes": 2},
            occurrences=2,
        ),
        _rule_violation(
            "microtypography_prose",
            "microtypography",
            address="p-2",
            block_id="p-2",
            page_start_est=2,
            snippet="Начало абзаца два",
            found_text="дефис вместо тире: 3 (фрагмент тире)",
            found={"wrong_dashes": 3},
            occurrences=3,
        ),
        _rule_violation(
            "microtypography_prose",
            "microtypography",
            address="p-3",
            block_id="p-3",
            page_start_est=3,
            snippet="Начало абзаца три",
            found_text="лишние пробелы: 1 (фрагмент пробелов)",
            found={"multiple_spaces": 1},
            occurrences=1,
        ),
        _rule_violation(
            "microtypography_prose",
            "microtypography",
            address="p-4",
            block_id="p-4",
            page_start_est=4,
            snippet="Начало абзаца четыре",
            found_text="дефис вместо тире: 1 (ещё тире)",
            found={"wrong_dashes": 1},
            occurrences=1,
        ),
    ]
    report = build_violation_report(
        document,
        _rule_report(engine_violations),
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    entries = _rule_engine_violations(report)
    assert len(entries) == 1
    grouped = entries[0]
    assert grouped.id == "group:microtypography_prose"
    assert grouped.count == 4
    assert grouped.occurrences == 7
    assert grouped.found == {
        "wrong_quotes": 2,
        "wrong_dashes": 4,
        "multiple_spaces": 1,
    }
    assert len(grouped.locations) == 4
    assert grouped.location.text_excerpt == "фрагмент кавычек"
    assert {item.text_excerpt for item in grouped.locations} == {
        "фрагмент кавычек",
        "фрагмент тире",
        "фрагмент пробелов",
        "ещё тире",
    }
    assert "кавычки 2, тире 4, лишние пробелы 1" in grouped.description
    assert "Всего однотипных нарушений: 4." in grouped.description

    payload = report.model_dump(mode="json")
    grouped_payload = next(
        item
        for group in payload["groups"]
        for item in group["violations"]
        if item["id"] == "group:microtypography_prose"
    )
    assert grouped_payload["found"] == {
        "wrong_quotes": 2,
        "wrong_dashes": 4,
        "multiple_spaces": 1,
    }
    assert grouped_payload["occurrences"] == 7

    markdown_report = report.to_markdown()
    assert (
        "microtypography_prose — 4 нарушений "
        "(кавычки 2, тире 4, лишние пробелы 1)"
    ) in markdown_report
    assert "p-1 (стр. 2): «фрагмент кавычек»" in markdown_report
    assert "p-2 (стр. 2): «фрагмент тире»" in markdown_report
    assert "p-3 (стр. 3): «фрагмент пробелов»" in markdown_report
    assert "… и ещё 1" in markdown_report
    assert "Начало абзаца" not in markdown_report
    assert report.summary.total_violations == 4


def test_small_groups_stay_as_separate_entries() -> None:
    """правила с числом находок меньше четырёх печатаются по одной записи"""
    document = _sample_parser_result()
    document.anomalies = DocumentAnomalies()
    engine_violations = [
        _rule_violation(
            "indent_first_line",
            "indent",
            address=f"p-{index}",
            block_id=f"p-{index}",
            page_start_est=index + 1,
            snippet=f"Абзац {index}",
        )
        for index in range(3)
    ]
    report = build_violation_report(
        document,
        _rule_report(engine_violations),
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    entries = _rule_engine_violations(report)
    assert len(entries) == 3
    assert all(entry.count == 1 for entry in entries)
    assert all(entry.locations == [] for entry in entries)
    assert {entry.id for entry in entries} == {
        violation.violation_id for violation in engine_violations
    }
    assert report.summary.total_violations == 3
    assert report.to_markdown().count("### [LOW] indent_first_line:") == 3


def test_grouping_keeps_summary_equal_to_engine_and_parser_totals() -> None:
    """группировка не меняет summary: engine-находки и аномалии парсера сходятся"""
    document = _sample_parser_result()
    engine_violations = [
        _rule_violation(
            "font_body_text",
            "font",
            address=f"p-{index}",
            block_id=f"p-{index}",
            page_start_est=index + 1,
            snippet=f"Абзац {index}",
        )
        for index in range(6)
    ] + [
        _rule_violation(
            "bibliography_cited",
            "bibliography_cited",
            address="entry:3",
            block_id=None,
            page_start_est=11,
            snippet="Иванов И. И. ...",
        )
    ]
    report = build_violation_report(
        document,
        _rule_report(engine_violations),
        generated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    engine_entries = _rule_engine_violations(report)
    assert len(engine_entries) == 2
    assert sum(entry.count for entry in engine_entries) == len(engine_violations)
    assert report.summary.total_violations == 3 + len(engine_violations)

    font_group = next(
        entry for entry in engine_entries if entry.rule_id == "font_body_text"
    )
    assert font_group.count == 6
    assert len(font_group.locations) == 6
    assert "font_body_text — 6 нарушений" in report.to_markdown()
