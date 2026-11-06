from __future__ import annotations

from datetime import datetime, timezone

from docx_parser.pdf_norm_control_parser import PdfRuleFinding
from docx_parser.schema import (
    CommentArtifact,
    DocumentAnomalies,
    DocumentMeta,
    GlobalSettings,
    ParagraphBlock,
    ParsedDocxDocument,
    ThemeData,
    TrackChangeArtifact,
)
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
            PdfRuleFinding(
                rule_id="body_font_size",
                status="fail",
                expected="14 pt",
                observed="12 pt",
                confidence="medium",
            ),
            PdfRuleFinding(
                rule_id="line_spacing",
                status="not_assessed",
                expected="1.5",
                observed="not enough lines",
                confidence="low",
            ),
            PdfRuleFinding(
                rule_id="page_size_a4",
                status="pass",
                expected="A4",
                observed="A4",
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
