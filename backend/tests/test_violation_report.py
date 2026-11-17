from __future__ import annotations

from datetime import datetime, timezone

from parser.core.schema import (
    CommentArtifact,
    DocumentAnomalies,
    DocumentMeta,
    GlobalSettings,
    ParagraphBlock,
    ParsedDocxDocument,
    ThemeData,
    TrackChangeArtifact,
)
from rule_engine.findings import RuleAssessment
from violation_reports import ReportCategory, ReportSeverity, build_violation_report


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
