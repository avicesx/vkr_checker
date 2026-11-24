"""Правила и параметры УрГПУ: новые коды и расширения существующих проверок.

Документы собираются синтетически из схемы разбора: абзацы, таблицы, подписи,
дерево разделов, список литературы и страничный аудит PDF. Тесты фиксируют
контракты требований методички УрГПУ (docs/URGPU_SPEC.md): внутритекстовые
ссылки, рамки и ширина таблиц, продолжение и шапка таблицы, положение подписи,
перечисления, переносы в заголовках, порядок приложений, титульный лист, а
также новые параметры уже существующих кодов.

Проверки, которым нужны данные, отсутствующие в документе (строки-шапки,
приложения, PDF-аудит), обязаны честно возвращать `not_assessed`, а не
«нарушений нет»: это отдельно зафиксировано в тестах.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import pytest
from pydantic import ValidationError

from parser.core.schema import (
    BibliographyEntry,
    BorderEdge,
    BoxBorders,
    CrossReferenceIndex,
    DocumentMeta,
    DocumentTreeNode,
    GlobalSettings,
    HeadingMetadata,
    ListItemMetadata,
    NumberedCaptionEntity,
    PageCountInfo,
    PageMarginsMm,
    ParagraphBlock,
    ParagraphPropertiesEffective,
    ParsedDocxDocument,
    PdfPageAudit,
    RunFontPropertiesEffective,
    SectionProperties,
    TableBlock,
    TableCell,
    TableRow,
    TextCrossReferenceMention,
    TextRun,
    ThemeData,
    TocContainerBlock,
    TocEntryItem,
    VisualObject,
)
from rule_engine import RuleEngine, RuleSet
from rule_engine.rules import (
    CaptionNumberingExpectation,
    FontExpectation,
    InlineReferenceExpectation,
    TableLayoutExpectation,
)


# ---------------------------------------------------------------------
# Сборка документов
# ---------------------------------------------------------------------


def _run(
    run_id: str,
    text: str,
    *,
    size_pt: float = 14.0,
    bold: bool = False,
    underline: str = "none",
    start: int = 0,
) -> TextRun:
    """Создает текстовый ран с заданным шрифтом и начертанием."""
    return TextRun(
        run_id=run_id,
        text=text,
        visible_text=text,
        start_char=start,
        end_char=start + len(text),
        effective_props=RunFontPropertiesEffective(
            font_ascii="Times New Roman",
            font_hansi="Times New Roman",
            font_cs="Times New Roman",
            size_pt=size_pt,
            bold=bold,
            underline=underline,  # type: ignore[arg-type]
        ),
    )


def _para(
    block_id: str,
    text: str,
    *,
    alignment: str = "left",
    page: int = 2,
    runs: Optional[Sequence[TextRun]] = None,
    heading: Optional[HeadingMetadata] = None,
    list_info: Optional[ListItemMetadata] = None,
    container_type: str = "body",
    page_break_before: bool = False,
    space_after_pt: Optional[float] = None,
    space_before_pt: Optional[float] = None,
    hyphenated_break: bool = False,
) -> ParagraphBlock:
    """Создает абзац с заданными свойствами и, при необходимости, заголовком."""
    return ParagraphBlock(
        block_id=block_id,
        section_idx=0,
        container_type=container_type,  # type: ignore[arg-type]
        page_start_est=page,
        page_end_est=page,
        pdf_has_hyphenated_break=hyphenated_break,
        text_raw=text,
        text_visible=text,
        is_empty_or_whitespace=not text.strip(),
        runs=list(runs) if runs is not None else [],
        list_info=list_info,
        heading_info=heading,
        effective_props=ParagraphPropertiesEffective(
            alignment=alignment,  # type: ignore[arg-type]
            alignment_source="direct",
            page_break_before=page_break_before,
            space_after_pt=space_after_pt if space_after_pt is not None else 0.0,
            space_before_pt=space_before_pt if space_before_pt is not None else 0.0,
        ),
    )


def _heading(
    block_id: str,
    text: str,
    *,
    level: int = 1,
    role: str = "main_chapter",
    page: int = 2,
    title: Optional[str] = None,
) -> ParagraphBlock:
    """Создает абзац-заголовок уровня ``level``."""
    return _para(
        block_id,
        text,
        page=page,
        heading=HeadingMetadata(
            detection_confidence="structural_keyword_match",
            heading_kind="structural_unnumbered",
            level=level,
            clean_title_text=title if title is not None else text,
        ),
    )


def _node(
    node_id: str,
    title: str,
    *,
    role: str = "unknown",
    level: int = 1,
    page_start: int = 1,
    page_end: int = 1,
    heading_block_id: Optional[str] = None,
    children: Sequence[DocumentTreeNode] = (),
) -> DocumentTreeNode:
    """Создает узел дерева документа с ролью и страницами."""
    return DocumentTreeNode(
        node_id=node_id,
        title=title,
        level=level,
        semantic_role=role,  # type: ignore[arg-type]
        page_start_est=page_start,
        page_end_est=page_end,
        heading_block_id=heading_block_id,
        direct_block_ids=[heading_block_id] if heading_block_id else [],
        children=list(children),
    )


def _borders(style: str = "single") -> BoxBorders:
    """Возвращает рамки со всех четырех сторон."""
    edge = BorderEdge(style=style)  # type: ignore[arg-type]
    return BoxBorders(top=edge, bottom=edge, left=edge, right=edge)


def _table(
    block_id: str,
    *,
    texts: Sequence[str] = ("Ячейка",),
    borders: Optional[BoxBorders] = None,
    page: int = 2,
    total_width_mm: float = 160.0,
    header: bool = False,
    continuation_of: Optional[str] = None,
    break_row: bool = False,
) -> TableBlock:
    """Собирает таблицу с одной или двумя строками для проверок таблиц."""
    rows: List[TableRow] = []
    for row_idx, row_text in enumerate(texts):
        paragraph = _para(f"{block_id}_p{row_idx}", row_text, container_type="table_cell")
        cell = TableCell(
            cell_id=f"{block_id}_c{row_idx}",
            row_idx=row_idx,
            col_idx=0,
            blocks=[paragraph],
            text_combined=row_text,
            effective_borders=borders if borders is not None else _borders(),
        )
        rows.append(
            TableRow(
                row_idx=row_idx,
                cells=[cell],
                is_tbl_header=header and row_idx == 0,
                has_page_break_inside_or_before=break_row and row_idx == 0,
            )
        )
    return TableBlock(
        block_id=block_id,
        section_idx=0,
        page_start_est=page,
        page_end_est=page,
        total_width_mm=total_width_mm,
        table_borders=_borders(),
        rows=rows,
        continuation_of_table_id=continuation_of,
    )


def _caption(
    entity_id: str,
    entity_type: str,
    number: Optional[str],
    *,
    caption_block_id: str,
    title: str = "Схема",
    prefix: str = "Рисунок",
    scheme: str = "continuous_arabic",
    mentioned: bool = False,
    target_object_id: Optional[str] = None,
) -> NumberedCaptionEntity:
    """Создает подпись объекта с номером и схемой нумерации."""
    return NumberedCaptionEntity(
        entity_id=entity_id,
        entity_type=entity_type,  # type: ignore[arg-type]
        caption_block_id=caption_block_id,
        target_object_id=target_object_id,
        page_est=2,
        prefix_word=prefix,
        number_str=number,
        numbering_scheme=scheme,  # type: ignore[arg-type]
        separator=".",
        title_text=title,
        is_mentioned_in_text=mentioned,
    )


def _doc(
    *,
    blocks: Sequence[Any] = (),
    tree: Sequence[DocumentTreeNode] = (),
    figures: Sequence[NumberedCaptionEntity] = (),
    tables: Sequence[NumberedCaptionEntity] = (),
    apps: Sequence[NumberedCaptionEntity] = (),
    sections: Sequence[SectionProperties] = (),
    pdf_pages: Sequence[PdfPageAudit] = (),
    bibliography: Sequence[BibliographyEntry] = (),
    mentions: Sequence[TextCrossReferenceMention] = (),
    visuals: Sequence[VisualObject] = (),
) -> ParsedDocxDocument:
    """Собирает минимальный документ с нужными для проверки данными."""
    return ParsedDocxDocument(
        meta=DocumentMeta(filename="vkr.docx", file_sha256="0" * 64),
        settings=GlobalSettings(),
        theme=ThemeData(),
        page_count=PageCountInfo(
            total_pages=3, method="docx_native_breaks", confidence="high"
        ),
        sections=list(sections),
        pdf_pages_audit=list(pdf_pages),
        blocks=list(blocks),
        visuals={visual.visual_id: visual for visual in visuals},
        document_tree=list(tree),
        cross_ref_index=CrossReferenceIndex(
            figures=list(figures),
            tables=list(tables),
            Apps=list(apps),
            bibliography=list(bibliography),
            mentions=list(mentions),
        ),
    )


def _rule(
    rule_id: str,
    code: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    scope: Optional[str] = None,
    message: str = "Требование выполнено",
) -> Dict[str, Any]:
    """Описание правила для набора."""
    rule: Dict[str, Any] = {
        "rule_id": rule_id,
        "code": code,
        "message": message,
    }
    if params is not None:
        rule["params"] = params
    if scope is not None:
        rule["target"] = {"scope": scope}
    return rule


def _engine(*rules: Dict[str, Any]) -> RuleEngine:
    """Собирает движок из описаний правил, валидируя набор схемой."""
    return RuleEngine(
        RuleSet.model_validate(
            {
                "ruleset_id": "test-urgpu",
                "ruleset_version": "1.0.0",
                "rules": list(rules),
            }
        )
    )


def _ids(report, rule_id: str) -> List[str]:
    """Адреса находок правила."""
    return [
        violation.violation_id
        for violation in report.violations
        if violation.rule_id == rule_id
    ]


def _assessment(report, rule_id: str):
    """Оценка правила по идентификатору."""
    return next(
        assessment
        for assessment in report.assessments
        if assessment.rule_id == rule_id
    )


# ---------------------------------------------------------------------
# Новые коды: внутритекстовые ссылки
# ---------------------------------------------------------------------


def test_inline_reference_requires_page_and_number() -> None:
    """Ссылка ``[3]`` без страницы и без номера источника даёт нарушение."""
    document = _doc(
        blocks=[
            _para("p1", "В работе [3] описан подход."),
            _para("p2", "В работе [4, с. 15] описан подход."),
        ]
    )
    engine = _engine(
        _rule(
            "refs",
            "inline_reference_format",
            params={"ref_type": "bibliography", "style": "square_brackets", "require_page": True},
        )
    )
    report = engine.check(document)

    addresses = _ids(report, "refs")
    assert len(addresses) == 1
    assert addresses[0].startswith("refs:p1:ref")
    violation = report.violations[0]
    assert violation.location.block_id == "p1"
    assert violation.location.page_start_est == 2
    assert violation.location.snippet == "[3]"


def test_inline_reference_style_quoted_from() -> None:
    """Правило про «Цит. по» не считает обычную ссылку цитатой из чужой книги."""
    document = _doc(
        blocks=[
            _para("p1", "Как отмечено в исследовании [Цит. по кн.: 5. С. 12], вывод верен."),
            _para("p2", "В работе [4, с. 15] описан подход."),
        ]
    )
    engine = _engine(
        _rule(
            "quoted",
            "inline_reference_format",
            params={"ref_type": "bibliography", "style": "quoted_from"},
        )
    )
    report = engine.check(document)

    assessment = _assessment(report, "quoted")
    assert _ids(report, "quoted") == []
    assert assessment.status == "pass"
    assert assessment.details["references"] == 1
    assert assessment.details["skipped_other_styles"] == 1


def test_inline_reference_pattern_and_abbrev() -> None:
    """Свой шаблон ссылки и обязательное сокращение проверяются вместе."""
    document = _doc(
        blocks=[
            _para("p1", "Смотрите (прил. 2) и [7, с. 3]."),
        ]
    )
    engine = _engine(
        _rule(
            "abbrev",
            "inline_reference_format",
            params={
                "ref_type": "bibliography",
                "patterns": [r"^\[\d+,\s*с\.\s*\d+\]$"],
                "require_abbrev": "прил.",
            },
        )
    )
    report = engine.check(document)

    # Ссылка [7, с. 3] подходит под шаблон, но не содержит сокращения «прил.».
    addresses = _ids(report, "abbrev")
    assert len(addresses) == 1
    assert addresses[0].startswith("abbrev:p1:ref")


def test_inline_reference_absent_is_not_assessed() -> None:
    """Если ссылок нужного вида в тексте нет, правило не оценивается."""
    document = _doc(blocks=[_para("p1", "Текст без ссылок.")])
    engine = _engine(
        _rule(
            "refs",
            "inline_reference_format",
            params={"ref_type": "appendix", "require_abbrev": "прил."},
        )
    )
    report = engine.check(document)

    assert report.passed is True
    assert _assessment(report, "refs").status == "not_assessed"


# ---------------------------------------------------------------------
# Новые коды: таблицы
# ---------------------------------------------------------------------


def test_table_layout_requires_borders_and_width() -> None:
    """Таблица без рамок и шире текста даёт два нарушения с адресами."""
    no_borders = BorderEdge(style="none")
    document = _doc(
        blocks=[
            _table(
                "tbl1",
                borders=BoxBorders(top=no_borders, bottom=no_borders, left=no_borders, right=no_borders),
                total_width_mm=200.0,
            )
        ],
        sections=[
            SectionProperties(
                section_idx=0,
                margins=PageMarginsMm(),
                text_area_width_mm=165.0,
            )
        ],
    )
    engine = _engine(
        _rule(
            "layout",
            "table_layout",
            params={"require_all_sides_borders": True, "require_full_text_width": True},
            scope="table_cells",
        )
    )
    report = engine.check(document)

    assert set(_ids(report, "layout")) == {"layout:tbl1:borders", "layout:tbl1:width"}
    border_finding = next(v for v in report.violations if v.violation_id.endswith(":borders"))
    assert border_finding.location.block_id == "tbl1"
    assert border_finding.found["first_cell_id"] == "tbl1_c0"


def test_table_layout_passes_with_borders_and_width() -> None:
    """Таблица с рамками и шириной текста проходит проверку."""
    document = _doc(
        blocks=[_table("tbl1", total_width_mm=165.0)],
        sections=[
            SectionProperties(
                section_idx=0,
                margins=PageMarginsMm(),
                text_area_width_mm=165.0,
            )
        ],
    )
    engine = _engine(
        _rule(
            "layout",
            "table_layout",
            params={"require_all_sides_borders": True, "require_full_text_width": True},
            scope="table_cells",
        )
    )

    assert engine.check(document).passed is True


def test_table_continuation_requires_label() -> None:
    """Продолжение таблицы без подписи «Продолжение таблицы N» ловится."""
    document = _doc(
        blocks=[
            _table("tbl1", texts=("Шапка",)),
            _table("tbl2", texts=("Данные",), continuation_of="tbl1"),
        ]
    )
    engine = _engine(
        _rule("cont", "table_continuation", params={"require_label": True}, scope="table_cells")
    )
    report = engine.check(document)

    assert "cont:tbl2:label" in _ids(report, "cont")


def test_table_continuation_label_and_header() -> None:
    """Подпись продолжения слева и повтор шапки закрывают требования."""
    document = _doc(
        blocks=[
            _table("tbl1", texts=("Шапка",), header=True),
            _para("p_label", "Продолжение таблицы 1"),
            _table("tbl2", texts=("Шапка", "Данные"), continuation_of="tbl1", header=True),
        ]
    )
    engine = _engine(
        _rule(
            "cont",
            "table_continuation",
            params={
                "require_label": True,
                "label_alignment": "left",
                "repeat_header": True,
                "forbid_repeated_caption": True,
            },
            scope="table_cells",
        )
    )

    assert engine.check(document).passed is True


def test_table_continuation_without_marks_is_not_assessed() -> None:
    """Без разметки продолжений правило честно не оценивается."""
    document = _doc(blocks=[_table("tbl1")])
    engine = _engine(_rule("cont", "table_continuation", params={"require_label": True}, scope="table_cells"))
    report = engine.check(document)

    assert report.passed is True
    assert _assessment(report, "cont").status == "not_assessed"


def test_table_header_format_checks_case_and_dot() -> None:
    """Шапка со строчной буквы и точкой в конце даёт два нарушения."""
    document = _doc(
        blocks=[_table("tbl1", texts=("наименование.",), header=True)]
    )
    engine = _engine(
        _rule(
            "header",
            "table_header_format",
            params={"first_letter": "capitalize", "forbid_trailing_dot": True},
            scope="table_cells",
        )
    )
    report = engine.check(document)

    assert set(_ids(report, "header")) == {
        "header:tbl1_c0:first_letter",
        "header:tbl1_c0:trailing_dot",
    }


def test_table_header_absent_is_not_assessed() -> None:
    """Без строк-шапок правило не подменяет отсутствие данных оценкой «пройдено»."""
    document = _doc(blocks=[_table("tbl1", texts=("Данные",))])
    engine = _engine(
        _rule("header", "table_header_format", params={"first_letter": "capitalize"}, scope="table_cells")
    )
    report = engine.check(document)

    assert report.passed is True
    assessment = _assessment(report, "header")
    assert assessment.status == "not_assessed"
    assert "шапки" in assessment.observed_text


# ---------------------------------------------------------------------
# Новые коды: подпись, перечисление, заголовки, приложения, титул
# ---------------------------------------------------------------------


def test_caption_placement_table_above_right_without_quotes() -> None:
    """Подпись таблицы над таблицей справа и без кавычек проходит."""
    caption = _para("cap", "Таблица 1. Состав системы", alignment="right", page=2)
    table = _table("tbl1", page=3)
    document = _doc(
        blocks=[caption, table],
        tables=[_caption("t1", "table", "1", caption_block_id="cap", prefix="Таблица", title="Состав системы", target_object_id="tbl1")],
    )
    engine = _engine(
        _rule(
            "place",
            "caption_placement",
            params={
                "entity_type": "table",
                "position": "above_table",
                "alignment": "right",
                "forbid_quotes": True,
                "prefix_word": "Таблица",
                "require_number": True,
            },
        )
    )
    report = engine.check(document)

    assert report.passed is True


def test_caption_placement_quotes_and_position() -> None:
    """Подпись под таблицей и в кавычках нарушает оба требования."""
    caption = _para("cap", "Таблица 1. «Состав системы»", alignment="left", page=4)
    table = _table("tbl1", page=3)
    document = _doc(
        blocks=[table, caption],
        tables=[_caption("t1", "table", "1", caption_block_id="cap", prefix="Таблица", title="«Состав системы»", target_object_id="tbl1")],
    )
    engine = _engine(
        _rule(
            "place",
            "caption_placement",
            params={
                "entity_type": "table",
                "position": "above_table",
                "alignment": "right",
                "forbid_quotes": True,
            },
        )
    )
    report = engine.check(document)

    assert _ids(report, "place") == ["place:t1"]
    assert report.violations[0].found["position"] == "below_object"


def test_list_format_markers_by_level() -> None:
    """Маркер «а)» на нулевом уровне не соответствует требованию «дефис»."""
    document = _doc(
        blocks=[
            _para(
                "p1",
                "а) первый пункт",
                list_info=ListItemMetadata(
                    mechanism="manual_text_prefix",
                    level=0,
                    rendered_marker="а)",
                    body_text_without_marker="первый пункт",
                    first_letter_case="lowercase",
                    ending_punctuation="none",
                ),
            ),
            _para(
                "p2",
                "- второй пункт",
                list_info=ListItemMetadata(
                    mechanism="manual_text_prefix",
                    level=0,
                    rendered_marker="-",
                    body_text_without_marker="второй пункт",
                    first_letter_case="lowercase",
                    ending_punctuation="none",
                ),
            ),
        ]
    )
    engine = _engine(
        _rule(
            "list",
            "list_format",
            params={
                "level_markers": [{"level": 0, "allowed": ["hyphen"]}],
                "forbid_other_markers": True,
            },
        )
    )
    report = engine.check(document)

    assert _ids(report, "list") == ["list:p1:marker"]
    assert report.violations[0].found["marker"] == "а)"


def test_heading_hyphenation_forbidden() -> None:
    """Мягкий перенос в заголовке даёт нарушение с адресом абзаца."""
    document = _doc(
        blocks=[
            _para("h1", "Вве\u00adдение", heading=HeadingMetadata(
                detection_confidence="structural_keyword_match", heading_kind="structural_unnumbered", level=1, clean_title_text="Введение"
            ))
        ],
        tree=[_node("n1", "Введение", role="introduction", heading_block_id="h1")],
    )
    engine = _engine(_rule("hyph", "heading_hyphenation", params={"forbid_hyphenation": True}))
    report = engine.check(document)

    assert _ids(report, "hyph") == ["hyph:h1"]


def test_appendix_order_by_first_mention_and_new_page() -> None:
    """Приложения идут в порядке упоминания и начинаются с нового листа."""
    document = _doc(
        blocks=[_para("p1", "См. приложение Б, затем приложение А.")],
        tree=[
            _node("a", "Приложение А", role="appendix", page_start=10, page_end=11, heading_block_id="pa"),
            _node("b", "Приложение Б", role="appendix", page_start=11, page_end=12, heading_block_id="pb"),
        ],
        mentions=[
            TextCrossReferenceMention(
                mention_id="m1",
                block_id="p1",
                page_est=3,
                start_char=3,
                end_char=18,
                raw_snippet="приложение Б",
                ref_type="appendix",
                resolved_structure_node_ids=["b"],
            ),
            TextCrossReferenceMention(
                mention_id="m2",
                block_id="p1",
                page_est=3,
                start_char=20,
                end_char=35,
                raw_snippet="приложение А",
                ref_type="appendix",
                resolved_structure_node_ids=["a"],
            ),
        ],
    )
    engine = _engine(_rule("order", "appendix_order", params={"order": "by_first_mention", "new_page_each": True}))
    report = engine.check(document)

    found_ids = _ids(report, "order")
    assert "order:pb:order" in found_ids
    assert "order:pb:new_page" in found_ids


def test_appendix_order_without_appendices_is_not_assessed() -> None:
    """В документе без приложений правило не оценивается."""
    document = _doc(blocks=[_para("p1", "Текст.")])
    engine = _engine(_rule("order", "appendix_order", params={"order": "by_first_mention"}))
    report = engine.check(document)

    assert report.passed is True
    assert _assessment(report, "order").status == "not_assessed"


def test_title_page_content_fields() -> None:
    """Титульный лист без кафедры и города получает адресные находки."""
    title_paragraph = _para(
        "tp1",
        "МИНИСТЕРСТВО НАУКИ И ВЫСШЕГО ОБРАЗОВАНИЯ РОССИЙСКОЙ ФЕДЕРАЦИИ\n"
        "Уральский государственный педагогический университет\n"
        "Тема: Нормоконтроль ВКР\n"
        "Выполнил: студент Иванов И.И.",
    )
    document = _doc(
        blocks=[title_paragraph],
        tree=[
            _node(
                "tp",
                "Титульный лист",
                role="title_page",
                page_start=1,
                page_end=1,
                heading_block_id="tp1",
            )
        ],
    )
    engine = _engine(
        _rule(
            "title",
            "title_page_content",
            params={"required_fields": ["министерство", "вуз", "кафедра", "тема", "автор", "город"]},
        )
    )
    report = engine.check(document)

    assert set(_ids(report, "title")) == {"title:tp1:кафедра", "title:tp1:город"}


def test_title_page_without_extracted_text_is_not_assessed() -> None:
    """Если текст титула не извлечён, правило не выдумывает пропажу реквизитов."""
    document = _doc(
        blocks=[_para("tp1", "")],
        tree=[_node("tp", "Титульный лист", role="title_page", heading_block_id="tp1")],
    )
    engine = _engine(_rule("title", "title_page_content", params={"required_fields": ["тема"]}))
    report = engine.check(document)

    assert report.passed is True
    assert _assessment(report, "title").status == "not_assessed"


# ---------------------------------------------------------------------
# Новые параметры существующих кодов
# ---------------------------------------------------------------------


def test_font_min_size_pt() -> None:
    """Нижняя граница кегля ловит мелкий шрифт в ячейках таблицы."""
    document = _doc(
        blocks=[
            _para(
                "c1",
                "Мелкий текст",
                container_type="table_cell",
                runs=[_run("r1", "Мелкий текст", size_pt=9.0)],
            )
        ]
    )
    engine = _engine(
        _rule("font_min", "font", params={"min_size_pt": 10.0}, scope="table_cells")
    )
    report = engine.check(document)

    assert _ids(report, "font_min") == ["font_min:c1"]
    assert report.violations[0].found["size_pt"] == pytest.approx(9.0)


def test_font_size_and_min_size_are_mutually_exclusive() -> None:
    """Точный кегль и нижняя граница одновременно запрещены схемой."""
    with pytest.raises(ValidationError, match="взаимоисключающи"):
        FontExpectation(size_pt=14.0, min_size_pt=10.0)


def test_heading_format_underline_and_spacing() -> None:
    """Подчёркнутый заголовок и интервал после него проверяются параметрами."""
    document = _doc(
        blocks=[
            _para(
                "h1",
                "ВВЕДЕНИЕ",
                alignment="center",
                heading=HeadingMetadata(
                    detection_confidence="structural_keyword_match",
                    heading_kind="structural_unnumbered",
                    level=1,
                    clean_title_text="ВВЕДЕНИЕ",
                ),
                runs=[_run("r1", "ВВЕДЕНИЕ", underline="single")],
                space_after_pt=0.0,
            )
        ],
        tree=[_node("n1", "ВВЕДЕНИЕ", role="introduction", heading_block_id="h1")],
    )
    engine = _engine(
        _rule(
            "hfmt",
            "heading_format",
            params={"underline": "forbid", "space_after_lines": 1.0, "tolerance_pt": 3.0},
        )
    )
    report = engine.check(document)

    assert set(_ids(report, "hfmt")) == {"hfmt:h1"}
    found = report.violations[0].found
    assert found["underline"] is True
    assert found["space_after_pt"] == pytest.approx(0.0)


def test_heading_format_spacing_passes() -> None:
    """Интервал 21 пт после заголовка соответствует одному интервалу кегля 14."""
    document = _doc(
        blocks=[
            _para(
                "h1",
                "ВВЕДЕНИЕ",
                alignment="center",
                heading=HeadingMetadata(
                    detection_confidence="structural_keyword_match",
                    heading_kind="structural_unnumbered",
                    level=1,
                    clean_title_text="ВВЕДЕНИЕ",
                ),
                space_after_pt=21.0,
            )
        ],
        tree=[_node("n1", "ВВЕДЕНИЕ", role="introduction", heading_block_id="h1")],
    )
    engine = _engine(
        _rule("hfmt", "heading_format", params={"space_after_lines": 1.0, "tolerance_pt": 3.0})
    )

    assert engine.check(document).passed is True


def test_page_number_position_pdf_params() -> None:
    """Арабские цифры без точки и сквозная нумерация проверяются по PDF-аудиту."""
    document = _doc(
        pdf_pages=[
            PdfPageAudit(
                page_num=1,
                width_mm=210.0,
                height_mm=297.0,
                has_rendered_page_number=True,
                page_number_location="footer",
                page_number_alignment="center",
                rendered_page_number_raw="I",
                rendered_page_number_int=1,
                matches_physical_page_num=True,
            ),
            PdfPageAudit(
                page_num=2,
                width_mm=210.0,
                height_mm=297.0,
                has_rendered_page_number=True,
                page_number_location="footer",
                page_number_alignment="center",
                rendered_page_number_raw="2.",
                rendered_page_number_int=2,
                matches_physical_page_num=False,
            ),
        ]
    )
    engine = _engine(
        _rule(
            "pagenum",
            "page_number_position",
            params={
                "zone": "footer",
                "alignment": "center",
                "skip_first_page": True,
                "require_arabic_digits": True,
                "forbid_trailing_dot": True,
                "require_continuous": True,
            },
        )
    )
    report = engine.check(document)

    found_ids = set(_ids(report, "pagenum"))
    assert found_ids == {
        "pagenum:page:1:skip_first_page",
        "pagenum:page:1:require_arabic_digits",
        "pagenum:page:2:forbid_trailing_dot",
        "pagenum:page:2:require_continuous",
    }
    page_two = next(
        v for v in report.violations if v.violation_id == "pagenum:page:2:forbid_trailing_dot"
    )
    assert page_two.location.page_start_est == 2
    assert page_two.found.get("rendered_number") == "2."


def test_page_number_position_docx_has_no_pdf_details() -> None:
    """В DOCX требования к отрисованному номеру уходят в not_assessed-заметку."""
    document = _doc(
        sections=[
            SectionProperties(
                section_idx=0,
                margins=PageMarginsMm(),
                page_num_fmt="decimal",
            )
        ]
    )
    engine = _engine(
        _rule(
            "pagenum",
            "page_number_position",
            params={"zone": "footer", "alignment": "center", "forbid_trailing_dot": True},
        )
    )
    report = engine.check(document)

    assessment = _assessment(report, "pagenum")
    assert assessment.status == "not_assessed"
    assert "PDF" in str(assessment.details) or "PDF" in assessment.observed_text


def test_toc_new_params() -> None:
    """Отточие, выравнивание, полужирное начертание и регистр проверяются."""
    entry_paragraph = _para("e1", "1 Введение.......4", alignment="center")
    document = _doc(
        blocks=[entry_paragraph],
        tree=[_node("n1", "Введение", role="introduction", heading_block_id="h1")],
    )
    document.blocks.append(_para("h1", "Введение"))
    entries = [
        TocEntryItem(
            entry_index=0,
            block_id="e1",
            raw_text="1 Введение.......4",
            extracted_number="1",
            extracted_title="введение",
            extracted_page_str="4",
            has_dot_leader=True,
            matched_heading_block_id="h1",
        )
    ]
    document.blocks.append(
        TocContainerBlock(
            block_id="toc",
            section_idx=0,
            page_start_est=2,
            page_end_est=2,
            is_automated_field_or_sdt=False,
            entries=entries,
            raw_paragraphs=[entry_paragraph],
        )
    )
    engine = _engine(
        _rule(
            "tocfmt",
            "toc",
            params={
                "require_toc": True,
                "forbid_dot_leader": True,
                "entry_alignment": "left",
                "entry_bold": False,
                "capitalize_entry": True,
            },
        )
    )
    report = engine.check(document)

    expected = {
        "tocfmt:toc:toc:entry0",
    }
    assert set(_ids(report, "tocfmt")) == expected
    found = report.violations[0].found
    assert found["dot_leader"] is True
    assert found["entry_alignment"] == "center"
    assert found["entry_case"] == "lowercase_start"


def test_toc_entry_requires_number() -> None:
    """Обязательный раздел без номера в оглавлении ловится параметром require_number."""
    entry_paragraph = _para("e1", "Введение.......4", alignment="left")
    document = _doc(
        blocks=[
            entry_paragraph,
            _heading("h1", "Введение", level=1, role="introduction"),
            TocContainerBlock(
                block_id="toc",
                section_idx=0,
                page_start_est=2,
                page_end_est=2,
                is_automated_field_or_sdt=False,
                entries=[
                    TocEntryItem(
                        entry_index=0,
                        block_id="e1",
                        raw_text="Введение.......4",
                        extracted_title="Введение",
                        extracted_page_str="4",
                        has_dot_leader=False,
                        matched_heading_block_id="h1",
                    )
                ],
                raw_paragraphs=[entry_paragraph],
            ),
        ],
        tree=[_node("n1", "Введение", role="introduction", heading_block_id="h1")],
    )
    engine = _engine(
        _rule(
            "tocnum",
            "toc",
            params={
                "require_toc": True,
                "require_dot_leader": False,
                "required_entries": [
                    {"label": "введение", "role": "introduction", "require_number": True}
                ],
            },
        )
    )
    report = engine.check(document)

    assert "tocnum:toc:toc:entry0" in _ids(report, "tocnum")
    assert report.violations[0].found["entry_number"] is False


def test_required_sections_chapter_bounds() -> None:
    """Границы числа основных глав проверяются в обе стороны."""
    one_chapter = _doc(
        blocks=[_heading("h1", "Глава 1")],
        tree=[_node("n1", "Глава 1", role="main_chapter", heading_block_id="h1")],
    )
    engine = _engine(
        _rule(
            "sections",
            "required_sections",
            params={"min_main_chapters": 2, "max_main_chapters": 3},
        )
    )
    report = engine.check(one_chapter)

    assert _ids(report, "sections") == ["sections:main_chapters"]


def test_required_sections_too_many_chapters() -> None:
    """Четыре основные главы при максимуме три — нарушение у четвёртой."""
    document = _doc(
        blocks=[_heading(f"h{i}", f"Глава {i}") for i in range(1, 5)],
        tree=[
            _node(f"n{i}", f"Глава {i}", role="main_chapter", heading_block_id=f"h{i}")
            for i in range(1, 5)
        ],
    )
    engine = _engine(
        _rule(
            "sections",
            "required_sections",
            params={"min_main_chapters": 2, "max_main_chapters": 3},
        )
    )
    report = engine.check(document)

    assert _ids(report, "sections") == ["sections:h4"]


def test_heading_page_break_forbidden_role() -> None:
    """Подраздел, начатый с нового листа, ловится запрещающим параметром."""
    document = _doc(
        blocks=[
            _para("prev", "Текст предыдущего раздела", page=2),
            _heading("h1", "1.1 Подраздел", level=2, role="sub_section", page=3),
        ],
        tree=[_node("n1", "1.1 Подраздел", role="sub_section", level=2, heading_block_id="h1", page_start=3, page_end=3)],
    )
    engine = _engine(
        _rule(
            "breaks",
            "heading_page_break",
            params={"roles": ["main_chapter"], "forbid_new_page_roles": ["sub_section"]},
        )
    )
    report = engine.check(document)

    assert _ids(report, "breaks") == ["breaks:h1"]
    assert report.violations[0].found["start_page"] == 3


def test_caption_numbering_schemes_and_appendix() -> None:
    """Список допустимых схем и самостоятельная нумерация приложений."""
    document = _doc(
        tables=[
            _caption("t1", "table", "1.2", caption_block_id="cap_t1", prefix="Таблица", scheme="section_based"),
        ],
        apps=[
            _caption("a1", "appendix", "1", caption_block_id="cap_a1", prefix="Приложение", scheme="continuous_arabic"),
        ],
    )
    engine = _engine(
        _rule(
            "num",
            "caption_numbering",
            params={
                "entity_type": "any",
                "scheme": "any_of",
                "schemes": ["continuous_arabic"],
                "appendix": "own",
            },
        )
    )
    report = engine.check(document)

    found_ids = set(_ids(report, "num"))
    assert "num:t1:scheme" in found_ids
    assert "num:a1:appendix_numbering" in found_ids


def test_caption_numbering_single_object_without_number() -> None:
    """У единственного объекта номер не ставят: параметр находит номер."""
    document = _doc(figures=[_caption("f1", "figure", "1", caption_block_id="cap_f1")])
    engine = _engine(
        _rule(
            "num",
            "caption_numbering",
            params={"entity_type": "figure", "skip_number_when_single": True},
        )
    )
    report = engine.check(document)

    assert _ids(report, "num") == ["num:f1:single_number"]


def test_caption_format_capitalize_first_letter() -> None:
    """Название подписи со строчной буквы ловится новым параметром."""
    document = _doc(
        figures=[
            _caption("f1", "figure", "1", caption_block_id="cap_f1", title="схема процесса")
        ]
    )
    engine = _engine(
        _rule(
            "capfmt",
            "caption_format",
            params={"entity_type": "figure", "capitalize_first_letter": True},
        )
    )
    report = engine.check(document)

    assert _ids(report, "capfmt") == ["capfmt:f1"]
    assert report.violations[0].found["capitalize_first_letter"] is False


def test_caption_reference_appendix() -> None:
    """Ссылка на каждое приложение проверяется новым значением entity_type."""
    document = _doc(
        apps=[
            _caption("a1", "appendix", "1", caption_block_id="cap_a1", prefix="Приложение", mentioned=False),
        ]
    )
    engine = _engine(
        _rule("refs", "caption_reference", params={"entity_type": "appendix", "required": True})
    )
    report = engine.check(document)

    assert _ids(report, "refs") == ["refs:a1"]


def test_bibliography_format_forbid_dot_dash() -> None:
    """Тире между областями описания запрещено параметром."""
    document = _doc(
        bibliography=[
            BibliographyEntry(
                bib_id="b1",
                block_id="bp1",
                order_index=1,
                raw_text="Иванов И.И. Нормоконтроль. М.: Наука, 2020. — 150 с.",
                detected_category="book_monograph",
                dot_dash_count=1,
                year_extracted=2020,
                pages_extracted="150",
            )
        ]
    )
    engine = _engine(
        _rule(
            "bibfmt",
            "bibliography_format",
            params={"require_year": True, "require_pages": True, "forbid_dot_dash": True},
        )
    )
    report = engine.check(document)

    assert _ids(report, "bibfmt") == ["bibfmt:b1:dot_dash_count"]


def test_bibliography_order_groups_and_alphabetical_sort() -> None:
    """Заголовок группы запрещён, а порядок проверяется по алфавиту."""
    document = _doc(
        bibliography=[
            BibliographyEntry(
                bib_id="b1",
                block_id="bp1",
                order_index=1,
                raw_text="Нормативные акты:",
                detected_category="unknown",
            ),
            BibliographyEntry(
                bib_id="b2",
                block_id="bp2",
                order_index=2,
                raw_text="Яковлев П.П. Право. М.: Наука, 2019.",
                detected_category="book_monograph",
                year_extracted=2019,
            ),
            BibliographyEntry(
                bib_id="b3",
                block_id="bp3",
                order_index=3,
                raw_text="Алексеев С.С. Теория. М.: Наука, 2018.",
                detected_category="book_monograph",
                year_extracted=2018,
            ),
        ]
    )
    engine = _engine(
        _rule(
            "biborder",
            "bibliography_order",
            params={"start_at": 1, "allow_groups": False, "sort": "alphabetical"},
        )
    )
    report = engine.check(document)

    found_ids = set(_ids(report, "biborder"))
    assert "biborder:b1:group" in found_ids
    assert "biborder:b3:sort" in found_ids


def test_table_layout_expectation_defaults() -> None:
    """Схема нового кода таблиц принимает пустые параметры и запрещает лишние."""
    assert TableLayoutExpectation().require_all_sides_borders is True
    with pytest.raises(ValidationError):
        TableLayoutExpectation(unknown_param=True)  # type: ignore[call-arg]


def test_inline_reference_expectation_rejects_patterns_with_style() -> None:
    """Свой шаблон и стиль одновременно задавать нельзя."""
    with pytest.raises(ValidationError, match="patterns"):
        InlineReferenceExpectation(style="square_brackets", patterns=[r"^\d+$"])


def test_caption_numbering_scheme_any_of_requires_schemes() -> None:
    """Режим any_of без списка допустимых схем отклоняется схемой."""
    with pytest.raises(ValidationError, match="schemes"):
        CaptionNumberingExpectation(scheme="any_of")
