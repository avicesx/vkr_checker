"""Новые коды проверок из разбора методичек: размещение, сноски, источники.

Каждый код проверяется в двух состояниях: документ либо нарушает требование и
получает находку с адресом (block_id и страница), либо проходит проверку. Если
данных в документе нет, правило обязано вернуть `not_assessed` с объяснением.

Документы собираются синтетически из схемы разбора, поэтому тесты не зависят от
корпуса ВКР: сноски, координаты иллюстраций и формулы в корпусе есть не всегда.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import pytest
from pydantic import ValidationError

from parser.core.schema import (
    EquationObject,
    FootnoteItem,
    MicroTypographyReport,
    ParagraphBlock,
    PageMarginsMm,
    ParagraphPropertiesEffective,
    SectionProperties,
    SpanLocation,
    TextCrossReferenceMention,
    VisualObject,
)
from test_rules_urgpu import (
    _assessment,
    _caption,
    _doc,
    _engine,
    _ids,
    _para,
    _rule,
    _table,
)
from rule_engine.rules import (
    CitationRequiredExpectation,
    FigureSpacingExpectation,
    FormulaPlacementExpectation,
    ParagraphStyleExpectation,
)


def _footnote(footnote_id: str, text: str, *, page: int = 2, referenced: str = "p1") -> FootnoteItem:
    """Сноска с текстом и номером в начале."""
    return FootnoteItem(
        footnote_id=footnote_id,
        kind="footnote",
        referenced_in_block_id=referenced,
        page_est=page,
        blocks=[_para(f"{footnote_id}_p", text, container_type="footnote")],
    )


def _footnote_with_spaces(footnote_id: str, text: str, *, page: int = 2) -> FootnoteItem:
    """Сноска, в которой разбор отметил лишние пробелы."""
    paragraph = _para(f"{footnote_id}_p", text, container_type="footnote").model_copy(
        update={
            "typography": MicroTypographyReport(
                multiple_spaces=[
                    SpanLocation(
                        start_char=text.index("  "),
                        end_char=text.index("  ") + 2,
                        snippet="  ",
                        issue_subtype="multiple_spaces",
                    )
                ]
            )
        }
    )
    return FootnoteItem(
        footnote_id=footnote_id,
        kind="footnote",
        referenced_in_block_id="p1",
        page_est=page,
        blocks=[paragraph],
    )


def _visual(
    visual_id: str,
    *,
    parent: str = "p1",
    page: int = 2,
    top: Optional[float] = 100.0,
    bottom: Optional[float] = 150.0,
) -> VisualObject:
    """Иллюстрация с измеренными координатами на странице."""
    return VisualObject(
        visual_id=visual_id,
        parent_block_id=parent,
        section_idx=0,
        visual_kind="raster_image",
        placement="inline",
        width_mm=100.0,
        height_mm=50.0,
        page_est=page,
        pdf_y_top_mm=top,
        pdf_y_bottom_mm=bottom,
    )


def _mention(
    mention_id: str,
    block_id: str,
    entity_id: str,
    *,
    ref_type: str = "figure",
    page: int = 2,
) -> TextCrossReferenceMention:
    """Упоминание объекта в тексте с разрешённой ссылкой."""
    return TextCrossReferenceMention(
        mention_id=mention_id,
        block_id=block_id,
        page_est=page,
        start_char=0,
        end_char=10,
        raw_snippet="рисунок 1",
        ref_type=ref_type,  # type: ignore[arg-type]
        resolved_entity_ids=[entity_id],
    )


def _equation(
    equation_id: str,
    text: str,
    *,
    standalone: bool = True,
    alignment: str = "center",
) -> EquationObject:
    """Формула с выравниванием и текстом."""
    return EquationObject(
        equation_id=equation_id,
        parent_block_id="p1",
        section_idx=0,
        representation_kind="omml_para",
        is_standalone_line=standalone,
        text_linearized=text,
        alignment=alignment,  # type: ignore[arg-type]
    )


def _styled(
    block_id: str,
    text: str,
    style_id: str,
    *,
    direct: Optional[ParagraphPropertiesEffective] = None,
) -> ParagraphBlock:
    """Абзац с заданным стилем и прямыми свойствами."""
    paragraph = _para(block_id, text)
    return paragraph.model_copy(update={"style_id": style_id, "direct_props": direct})


# ---------------------------------------------------------------------
# Размещение объектов и формул
# ---------------------------------------------------------------------


def test_figure_after_reference_detects_late_reference() -> None:
    """Ссылка позже рисунка — нарушение, ссылка до объекта — норма."""
    engine = _engine(_rule("figref", "figure_after_reference", params={}, scope="captions"))
    caption = _caption("fig1", "figure", "1", caption_block_id="cap1")
    later = _doc(
        blocks=[_para("p0", "Текст"), _para("cap1", "Рисунок 1 — Схема"), _para("p2", "Текст")],
        figures=[caption],
        mentions=[_mention("m1", "p2", "fig1")],
    )
    report = engine.check(later)
    assert len(_ids(report, "figref")) == 1
    violation = report.violations[0]
    assert violation.location.block_id == "cap1"
    assert violation.location.page_start_est == 2

    earlier = _doc(
        blocks=[_para("p0", "Ссылка"), _para("cap1", "Рисунок 1 — Схема")],
        figures=[caption],
        mentions=[_mention("m1", "p0", "fig1")],
    )
    assert _ids(engine.check(earlier), "figref") == []


def test_table_after_reference_detects_late_reference() -> None:
    """Ссылка позже таблицы — нарушение, ссылка до объекта — норма."""
    engine = _engine(_rule("tabref", "table_after_reference", params={}, scope="captions"))
    caption = _caption("tab1", "table", "1", caption_block_id="cap1", prefix="Таблица")
    later = _doc(
        blocks=[_para("cap1", "Таблица 1 — Данные"), _para("p2", "Текст")],
        tables=[caption],
        mentions=[_mention("m1", "p2", "tab1", ref_type="table")],
    )
    assert len(_ids(engine.check(later), "tabref")) == 1
    earlier = _doc(
        blocks=[_para("p0", "Ссылка"), _para("cap1", "Таблица 1 — Данные")],
        tables=[caption],
        mentions=[_mention("m1", "p0", "tab1", ref_type="table")],
    )
    assert _ids(engine.check(earlier), "tabref") == []


def test_formula_placement_requires_standalone_and_center() -> None:
    """Формула в тексте или с другим выравниванием — нарушение."""
    engine = _engine(
        _rule("place", "formula_placement", params={"alignment": "center"}, scope="document")
    )
    inline = _doc(blocks=[_para("p1", "Формула")], )
    inline = inline.model_copy(
        update={"cross_ref_index": inline.cross_ref_index.model_copy(update={"equations": [_equation("eq1", "x = 1", standalone=False)]})}
    )
    ids = _ids(engine.check(inline), "place")
    assert any("standalone" in item for item in ids)

    left = _doc(blocks=[_para("p1", "Формула")])
    left = left.model_copy(
        update={"cross_ref_index": left.cross_ref_index.model_copy(update={"equations": [_equation("eq1", "x = 1", alignment="left")]})}
    )
    assert any("alignment" in item for item in _ids(engine.check(left), "place"))

    center = _doc(blocks=[_para("p1", "Формула")])
    center = center.model_copy(
        update={"cross_ref_index": center.cross_ref_index.model_copy(update={"equations": [_equation("eq1", "x = 1")]})}
    )
    assert _ids(engine.check(center), "place") == []

    empty = _doc(blocks=[_para("p1", "Текст")])
    assert _assessment(engine.check(empty), "place").status == "not_assessed"


def test_formula_line_break_repeats_operator() -> None:
    """Перенос без знака в начале строки — нарушение."""
    engine = _engine(_rule("line", "formula_line_break", params={}, scope="document"))
    broken = _doc(blocks=[_para("p1", "Формула")])
    broken = broken.model_copy(
        update={"cross_ref_index": broken.cross_ref_index.model_copy(update={"equations": [_equation("eq1", "x = a +\nb")]})}
    )
    assert any(":line2" in item for item in _ids(engine.check(broken), "line"))

    correct = _doc(blocks=[_para("p1", "Формула")])
    correct = correct.model_copy(
        update={"cross_ref_index": correct.cross_ref_index.model_copy(update={"equations": [_equation("eq1", "x = a +\n+ b")]})}
    )
    assert _ids(engine.check(correct), "line") == []


# ---------------------------------------------------------------------
# Сноски
# ---------------------------------------------------------------------


def test_footnote_numbering_continuous_and_per_page() -> None:
    """Сквозная нумерация и нумерация с единицы на странице."""
    engine = _engine(
        _rule("fn", "footnote_numbering", params={"scheme": "continuous"}, scope="footnotes")
    )
    good = _doc(blocks=[_para("p1", "Текст")], )
    good = good.model_copy(
        update={"footnotes": [_footnote("f1", "1 Первая"), _footnote("f2", "2 Вторая")]}
    )
    assert _ids(engine.check(good), "fn") == []

    bad = _doc(blocks=[_para("p1", "Текст")])
    bad = bad.model_copy(
        update={"footnotes": [_footnote("f1", "1 Первая"), _footnote("f2", "3 Третья")]}
    )
    assert len(_ids(engine.check(bad), "fn")) == 1

    per_page = _engine(
        _rule("fn2", "footnote_numbering", params={"scheme": "per_page"}, scope="footnotes")
    )
    pages = _doc(blocks=[_para("p1", "Текст")])
    pages = pages.model_copy(
        update={
            "footnotes": [
                _footnote("f1", "1 Первая", page=2),
                _footnote("f2", "1 Вторая", page=3),
            ]
        }
    )
    assert _ids(per_page.check(pages), "fn2") == []


def test_footnote_numbering_without_numbers_is_not_assessed() -> None:
    """Без печатных номеров сносок проверка честно не выполняется."""
    engine = _engine(
        _rule("fn", "footnote_numbering", params={"scheme": "continuous"}, scope="footnotes")
    )
    document = _doc(blocks=[_para("p1", "Текст")])
    document = document.model_copy(update={"footnotes": [_footnote("f1", "Без номера")]})
    assert _assessment(engine.check(document), "fn").status == "not_assessed"


# ---------------------------------------------------------------------
# Источники и подписи
# ---------------------------------------------------------------------


def test_source_note_under_figure_and_table() -> None:
    """Ссылка на источник под объектом: есть — норма, нет — нарушение."""
    engine = _engine(
        _rule("snf", "source_note_figure", params={"source_words": ["Источник"]}, scope="captions")
    )
    caption = _caption("fig1", "figure", "1", caption_block_id="cap1")
    good = _doc(
        blocks=[_para("cap1", "Рисунок 1 — Схема"), _para("p2", "Источник: данные автора")],
        figures=[caption],
    )
    assert _ids(engine.check(good), "snf") == []
    bad = _doc(
        blocks=[_para("cap1", "Рисунок 1 — Схема"), _para("p2", "Обычный текст")],
        figures=[caption],
    )
    assert len(_ids(engine.check(bad), "snf")) == 1

    tables = _engine(
        _rule("snt", "source_note_table", params={"source_words": ["Источник"]}, scope="captions")
    )
    table = _table("tbl1", texts=("Показатель",))
    table_caption = _caption("tab1", "table", "1", caption_block_id="cap1", prefix="Таблица")
    bad_table = _doc(
        blocks=[_para("cap1", "Таблица 1 — Данные"), table],
        tables=[table_caption],
    )
    assert len(_ids(tables.check(bad_table), "snt")) == 1


def test_table_units_require_units_in_header() -> None:
    """Единицы измерения в шапке таблицы: есть — норма, нет — нарушение."""
    engine = _engine(_rule("units", "table_units", params={"require_units": True}, scope="table_cells"))
    with_units = _doc(blocks=[_table("tbl1", texts=("Показатель, тыс. руб.", "10"), header=True)])
    assert _ids(engine.check(with_units), "units") == []
    without = _doc(blocks=[_table("tbl1", texts=("Показатель", "10"), header=True)])
    ids = _ids(engine.check(without), "units")
    assert len(ids) == 1
    assert ids[0].endswith(":units")


def test_source_label_content_and_table_requirement() -> None:
    """Подпись «Источник» без описания — нарушение; под таблицей она обязательна."""
    engine = _engine(
        _rule(
            "label",
            "source_label",
            params={"require_content": True, "require_under_tables": True},
            scope="body_paragraphs",
        )
    )
    empty = _doc(blocks=[_para("p1", "Источник:")])
    assert "label:p1:label_content" in _ids(engine.check(empty), "label")

    filled = _doc(blocks=[_para("p1", "Источник: данные Росстата")])
    assert _ids(engine.check(filled), "label") == []

    table = _table("tbl1", texts=("Показатель",))
    missing = _doc(blocks=[table])
    assert any(":label_missing" in item for item in _ids(engine.check(missing), "label"))


def test_citation_required_reference() -> None:
    """Цитата в кавычках без ссылки — нарушение; со ссылкой — норма."""
    engine = _engine(_rule("cite", "citation_required", params={}, scope="body_paragraphs"))
    quote = "«Достаточно длинная цитата, в которой заведомо больше сорока символов»"
    without = _doc(blocks=[_para("p1", quote)])
    assert len(_ids(engine.check(without), "cite")) == 1
    with_reference = _doc(blocks=[_para("p1", quote + " [3]")])
    assert _ids(engine.check(with_reference), "cite") == []
    short = _doc(blocks=[_para("p1", "«Короткий термин»")])
    assert _assessment(engine.check(short), "cite").status == "not_assessed"


# ---------------------------------------------------------------------
# Иллюстрации, числа и стиль
# ---------------------------------------------------------------------


def test_figure_spacing_between_and_page_bottom() -> None:
    """Тесная отбивка между иллюстрациями и до нижнего края — нарушение."""
    section = SectionProperties(section_idx=0, margins=PageMarginsMm(bottom_mm=20.0))
    engine = _engine(
        _rule(
            "gap",
            "figure_spacing",
            params={"min_gap_between_mm": 10.0, "min_gap_to_page_bottom_mm": 10.0},
            scope="document",
        )
    )
    tight = _doc(
        blocks=[_para("p1", "Текст"), _para("p2", "Текст")],
        sections=[section],
        visuals=[
            _visual("v1", parent="p1", bottom=150.0),
            _visual("v2", parent="p2", top=153.0),
        ],
    )
    assert any(":gap" in item for item in _ids(engine.check(tight), "gap"))

    bottom = _doc(
        blocks=[_para("p1", "Текст")],
        sections=[section],
        visuals=[_visual("v1", bottom=272.0)],
    )
    assert any(":page_bottom" in item for item in _ids(engine.check(bottom), "gap"))

    normal = _doc(
        blocks=[_para("p1", "Текст"), _para("p2", "Текст")],
        sections=[section],
        visuals=[
            _visual("v1", parent="p1", bottom=150.0),
            _visual("v2", parent="p2", top=180.0),
        ],
    )
    assert _ids(engine.check(normal), "gap") == []


def test_number_format_decimal_separator() -> None:
    """Десятичная точка — нарушение, запятая — норма."""
    engine = _engine(_rule("num", "number_format", params={}, scope="body_paragraphs"))
    assert len(_ids(engine.check(_doc(blocks=[_para("p1", "Рост составил 2.5 раза")])), "num")) == 1
    assert _ids(engine.check(_doc(blocks=[_para("p1", "Рост составил 2,5 раза")])), "num") == []


def test_paragraph_style_and_direct_formatting() -> None:
    """Прямое форматирование и чужой стиль — нарушение."""
    engine = _engine(_rule("style", "paragraph_style", params={}, scope="body_paragraphs"))
    custom = _doc(blocks=[_styled("p1", "Текст", "Custom")])
    assert "style:p1:style" in _ids(engine.check(custom), "style")

    plain = _doc(blocks=[_styled("p1", "Текст", "Normal")])
    assert _ids(engine.check(plain), "style") == []

    direct = _doc(
        blocks=[
            _styled(
                "p1",
                "Текст",
                "Normal",
                direct=ParagraphPropertiesEffective(alignment="left"),
            )
        ]
    )
    assert "style:p1:direct" in _ids(engine.check(direct), "style")


def test_microtypography_in_footnotes() -> None:
    """Микротипографика работает в области сносок."""
    engine = _engine(
        _rule("micro", "microtypography", params={"checks": ["multiple_spaces"]}, scope="footnotes")
    )
    bad = _doc(blocks=[_para("p1", "Текст")])
    bad = bad.model_copy(update={"footnotes": [_footnote_with_spaces("f1", "Сноска  с пробелом")]})
    assert len(_ids(engine.check(bad), "micro")) == 1
    good = _doc(blocks=[_para("p1", "Текст")])
    good = good.model_copy(update={"footnotes": [_footnote("f1", "Сноска с одним пробелом")]})
    assert _ids(engine.check(good), "micro") == []

    trailing = _doc(blocks=[_para("p1", "Текст")])
    trailing = trailing.model_copy(
        update={"footnotes": [_footnote_with_spaces("f1", "Сноска с пробелом    ")]}
    )
    assert _ids(engine.check(trailing), "micro") == []


def test_new_expectation_validators() -> None:
    """Взаимоисключающие и пустые наборы отклоняются при загрузке."""
    with pytest.raises(ValidationError):
        FormulaPlacementExpectation(require_standalone_line=False, alignment=None)
    with pytest.raises(ValidationError):
        FigureSpacingExpectation(min_gap_between_mm=0.0, min_gap_to_page_bottom_mm=0.0)
    with pytest.raises(ValidationError):
        ParagraphStyleExpectation(allowed_styles=[], forbid_direct_formatting=False)
    with pytest.raises(ValidationError):
        CitationRequiredExpectation(quote_chars=[], require_reference=True)
