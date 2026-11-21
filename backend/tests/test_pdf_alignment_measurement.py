"""Выравнивание основного текста: измеренное против оцененного по PDF.

В PDF выключка по ширине видна только по полным строкам. У абзаца из одной-двух
строк последняя строка заведомо короче, и оценка парсера не отличает выключку
от левого края. Такое выравнивание — отсутствие измерения, а не нарушение.
Тесты фиксируют границу: до трёх строк — not_assessed, от трёх — проверка.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from parser.core.schema import (
    CrossReferenceIndex,
    DocumentMeta,
    GlobalSettings,
    PageCountInfo,
    ParagraphBlock,
    ParagraphPropertiesEffective,
    ParsedDocxDocument,
    TableBlock,
    ThemeData,
)
from rule_engine import RuleEngine, RuleSet
from rule_engine.checks.text import _pdf_alignment_unmeasurable
from rule_engine.context import classify_document_contexts


def _paragraph(
    block_id: str,
    text: str,
    *,
    alignment: str = "left",
    alignment_source: str = "pdf_estimate",
    lines: Optional[int] = None,
    page: int = 2,
) -> ParagraphBlock:
    """Создает абзац с оцененным выравниванием и счетчиком строк PDF."""
    return ParagraphBlock(
        block_id=block_id,
        section_idx=0,
        container_type="body",
        page_start_est=page,
        page_end_est=page,
        text_raw=text,
        text_visible=text,
        effective_props=ParagraphPropertiesEffective(
            alignment=alignment,  # type: ignore[arg-type]
            alignment_source=alignment_source,  # type: ignore[arg-type]
        ),
        pdf_lines_count_on_start_page=lines,
        pdf_total_lines_count=lines,
    )


def _document(*blocks: Any) -> ParsedDocxDocument:
    """Собирает минимальный документ из блоков."""
    return ParsedDocxDocument(
        meta=DocumentMeta(filename="vkr.pdf", file_sha256="0" * 64),
        settings=GlobalSettings(),
        theme=ThemeData(),
        page_count=PageCountInfo(
            total_pages=3, method="pdf_exact", confidence="high"
        ),
        blocks=list(blocks),
        cross_ref_index=CrossReferenceIndex(),
    )


def _engine(*rules: Dict[str, Any]) -> RuleEngine:
    """Собирает движок из описаний правил."""
    payload = {
        "ruleset_id": "test-pdf-alignment",
        "ruleset_version": "1.0.0",
        "rules": list(rules),
    }
    return RuleEngine(RuleSet.model_validate(payload))


def _body_alignment_rule() -> Dict[str, Any]:
    """Правило выравнивания основного текста по ширине."""
    return {
        "rule_id": "alignment_body_text",
        "code": "alignment",
        "message": "Основной текст выравнивается по ширине",
        "target": {"scope": "body_text"},
        "params": {"alignment": "justify"},
    }


def test_single_line_pdf_estimate_is_unmeasurable() -> None:
    """Однострочный абзац PDF: оценка «слева» не становится нарушением."""
    paragraph = _paragraph("p_1", "Короткая строка.", alignment="left", lines=1)

    assert _pdf_alignment_unmeasurable(paragraph) is True

    report = _engine(_body_alignment_rule()).check(_document(paragraph))

    assert report.violations == []
    assert report.summary.not_assessed_total == 1
    assessment = report.not_assessed[0]
    assert assessment.status == "not_assessed"
    assert assessment.details["unmeasurable_pdf_short_lines"] == 1
    assert "не измерено" in assessment.observed_text


def test_two_line_pdf_estimate_is_unmeasurable() -> None:
    """Две строки: вторая — последняя, её край рваный; выключка неотличима."""
    paragraph = _paragraph("p_2", "Первая строка.\nВторая строка.", lines=2)

    report = _engine(_body_alignment_rule()).check(_document(paragraph))

    assert report.violations == []
    assert report.summary.not_assessed_total == 1


def test_three_line_pdf_estimate_is_measured() -> None:
    """Три строки: есть полные строки, оценка «слева» — уже нарушение."""
    paragraph = _paragraph(
        "p_3", "Первая строка.\nВторая строка.\nТретья строка.", lines=3
    )

    report = _engine(_body_alignment_rule()).check(_document(paragraph))

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.location.block_id == "p_3"
    assert violation.found["alignment"] == "left"


def test_direct_alignment_is_never_filtered() -> None:
    """Прямое выравнивание DOCX проверяется независимо от числа строк."""
    paragraph = _paragraph(
        "p_4",
        "Абзац с прямым выравниванием.",
        alignment="center",
        alignment_source="direct",
        lines=1,
    )

    assert _pdf_alignment_unmeasurable(paragraph) is False

    report = _engine(_body_alignment_rule()).check(_document(paragraph))

    assert report.summary.violations_total == 1
    assert report.violations[0].found["alignment_source"] == "direct"


def test_pdf_line_counts_are_not_mixed_up() -> None:
    """Счетчик строк берется из pdf-полей, а не из геометрии страницы."""
    paragraph = _paragraph("p_5", "Текст.", lines=4)

    assert _pdf_alignment_unmeasurable(paragraph) is False


def test_service_contexts_are_classified() -> None:
    """Структурные зачины, метки приложений, формулы и заголовки таблиц."""
    blocks: List[Any] = [
        _paragraph("structural", "Содержание:", alignment="center", alignment_source="direct"),
        _paragraph("appendix", "Продолжение ПРИЛОЖЕНИЯ А", alignment="center", alignment_source="direct"),
        _paragraph("formula", "Д = К * И,", alignment="center", alignment_source="direct"),
        _paragraph("table_title", "Актив агрегированного баланса", alignment="center", alignment_source="direct"),
        TableBlock(block_id="tbl_1", section_idx=0),
        _paragraph("caption", "Рис.4 Структура статей", alignment="center", alignment_source="direct"),
        _paragraph("body", "Обычный текст работы.", alignment="both", alignment_source="direct"),
    ]

    contexts = classify_document_contexts(blocks)

    assert contexts["structural"] == "structural_title"
    assert contexts["appendix"] == "appendix_label"
    assert contexts["formula"] == "text_formula"
    assert contexts["table_title"] == "table_title"
    assert contexts["caption"] == "caption_like"
    assert contexts["body"] == "body"


def test_body_text_skips_service_contexts_by_default() -> None:
    """Область body_text не проверяет служебные контексты, но видит обычный текст."""
    blocks = [
        _paragraph("structural", "ЗАКЛЮЧЕНИЕ", alignment="center", alignment_source="direct"),
        _paragraph("body", "Обычный текст работы.", alignment="both", alignment_source="direct"),
    ]

    report = _engine(_body_alignment_rule()).check(_document(*blocks))

    assert report.violations == []
    assert report.summary.objects_checked == 1
