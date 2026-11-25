"""Разрыв страницы в конце абзаца переносится на следующий блок.

Явный ``w:br w:type="page"`` в конце абзаца, в том числе пустого, означает,
что следующий блок начинается с новой страницы. Признак нужен правилу
heading_page_break: без него документ с такими разрывами считался
неразмеченным, и положение глав не проверялось.
"""
from __future__ import annotations

from parser.core.schema import ParagraphBlock, ParsedDocument
from rule_engine import RuleEngine
from rule_engine.rules import load_profile_ruleset
from services.document_parsing import parse_document
from tests.conftest import DocxPackageBuilder

CHAPTER_STYLE = "Heading1"


def _paragraphs(document: ParsedDocument) -> list[ParagraphBlock]:
    """Возвращает абзацы документа в порядке следования."""
    return [block for block in document.blocks if isinstance(block, ParagraphBlock)]


def _text_paragraph(text: str, style: str | None = None) -> str:
    """Собирает абзац с одним текстовым прогоном."""
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{props}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def _break_paragraph(text: str = "") -> str:
    """Собирает абзац, который заканчивается разрывом страницы."""
    body = f'<w:t xml:space="preserve">{text}</w:t>' if text else ""
    return f'<w:p><w:r>{body}<w:br w:type="page"/></w:r></w:p>'


def _inner_break_paragraph(before: str, after: str) -> str:
    """Собирает абзац с разрывом в середине: после него ещё есть текст."""
    return (
        f'<w:p><w:r><w:t xml:space="preserve">{before}</w:t>'
        f'<w:br w:type="page"/></w:r>'
        f'<w:r><w:t xml:space="preserve">{after}</w:t></w:r></w:p>'
    )


def _table(cell_text: str = "Ячейка") -> str:
    """Собирает таблицу из одной ячейки и одного абзаца."""
    return (
        '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="2000"/></w:tblGrid>'
        '<w:tr><w:tc><w:tcPr><w:tcW w:w="2000" w:type="dxa"/></w:tcPr>'
        f"<w:p><w:r><w:t>{cell_text}</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )


def _parse(builder: DocxPackageBuilder, body: str, filename: str = "breaks.docx") -> ParsedDocument:
    """Собирает пакет с заданным телом и разбирает его."""
    return parse_document(builder.set_body(body).build(filename))


def _flags(document: ParsedDocument) -> list[bool]:
    """Возвращает признаки разрыва перед абзацем для всех абзацев."""
    return [block.effective_props.page_break_before for block in _paragraphs(document)]


def _drop_style_page_break(builder: DocxPackageBuilder) -> None:
    """Убирает разрыв страницы из стиля заголовка, чтобы проверить разметку абзаца."""
    styles = builder.files["word/styles.xml"].decode("utf-8")
    builder.set_xml("word/styles.xml", styles.replace("<w:pageBreakBefore/>", ""))


def test_empty_break_paragraph_marks_only_next_paragraph(docx_builder: DocxPackageBuilder) -> None:
    """Пустой абзац с разрывом переносит на новую страницу только следующий абзац."""
    document = _parse(
        docx_builder,
        _break_paragraph() + _text_paragraph("Первый абзац") + _text_paragraph("Второй абзац"),
    )
    paragraphs = _paragraphs(document)

    assert _flags(document) == [False, True, False]
    assert paragraphs[1].page_start_est == paragraphs[0].page_start_est + 1


def test_trailing_break_in_text_paragraph_marks_next_paragraph(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Разрыв в конце абзаца с текстом тоже переносит следующий абзац."""
    document = _parse(
        docx_builder,
        _break_paragraph("Текст перед разрывом") + _text_paragraph("Следующий абзац"),
    )

    assert _flags(document) == [False, True]


def test_inner_break_does_not_mark_next_paragraph(docx_builder: DocxPackageBuilder) -> None:
    """Разрыв в середине абзаца не делает следующий блок началом страницы."""
    document = _parse(
        docx_builder,
        _inner_break_paragraph("До разрыва", "После разрыва") + _text_paragraph("Следующий абзац"),
    )

    assert _flags(document) == [False, False]


def test_document_without_breaks_keeps_flags_empty(docx_builder: DocxPackageBuilder) -> None:
    """Без разрывов признаки не появляются ни у одного абзаца."""
    document = _parse(
        docx_builder,
        _text_paragraph("Первый абзац") + _text_paragraph("Второй абзац"),
    )

    assert _flags(document) == [False, False]


def test_table_after_break_keeps_next_paragraph_on_table_page(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Таблица забирает разрыв на себя: абзац после неё новую страницу не начинает."""
    document = _parse(
        docx_builder,
        _break_paragraph() + _table() + _text_paragraph("После таблицы"),
    )
    paragraphs = _paragraphs(document)

    assert _flags(document) == [False, False]
    assert paragraphs[-1].page_start_est == paragraphs[0].page_start_est + 1


def test_two_breaks_in_one_paragraph_shift_by_two_pages(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Два разрыва подряд сдвигают следующий абзац на две страницы."""
    document = _parse(
        docx_builder,
        '<w:p><w:r><w:br w:type="page"/><w:br w:type="page"/></w:r></w:p>'
        + _text_paragraph("После двух разрывов"),
    )
    paragraphs = _paragraphs(document)

    assert _flags(document) == [False, True]
    assert paragraphs[1].page_start_est == paragraphs[0].page_start_est + 2


def test_rule_assesses_chapter_after_empty_break_paragraph(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Правило проверяет главу, если разрыв перед ней стоит в пустом абзаце."""
    _drop_style_page_break(docx_builder)
    body = (
        _text_paragraph("ВВЕДЕНИЕ", CHAPTER_STYLE)
        + _text_paragraph("Вводный текст.")
        + _break_paragraph()
        + _text_paragraph("1 ГЛАВА ПЕРВАЯ", CHAPTER_STYLE)
        + _text_paragraph("Текст главы.")
    )
    document = _parse(docx_builder, body)

    report = RuleEngine(load_profile_ruleset("urgpu")).check(document)
    assessment = next(item for item in report.assessments if item.rule_id == "heading_page_break")

    assert assessment.status == "pass"
    assert [violation for violation in report.violations if violation.rule_id == "heading_page_break"] == []


def test_rule_stays_unassessed_without_explicit_breaks(docx_builder: DocxPackageBuilder) -> None:
    """Без разрывов в документе положение главы по-прежнему не определено."""
    _drop_style_page_break(docx_builder)
    body = (
        _text_paragraph("ВВЕДЕНИЕ", CHAPTER_STYLE)
        + _text_paragraph("Вводный текст.")
        + _text_paragraph("1 ГЛАВА ПЕРВАЯ", CHAPTER_STYLE)
        + _text_paragraph("Текст главы.")
    )
    document = _parse(docx_builder, body)

    report = RuleEngine(load_profile_ruleset("urgpu")).check(document)
    assessment = next(item for item in report.assessments if item.rule_id == "heading_page_break")

    assert assessment.status == "not_assessed"
