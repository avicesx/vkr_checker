"""Семейство таблиц: линии и ширина, перенос на другую страницу, заголовки граф.

Проверки работают по уже разобранным данным ``TableBlock`` и ``TableCell``:
линии берутся из ``TableCell.effective_borders``, ширина — из
``TableBlock.total_width_mm`` и ``SectionProperties.text_area_width_mm``,
перенос — из ``TableBlock.continuation_of_table_id``. Если разбор не дал нужных
полей (например, ни одна строка не помечена ``is_tbl_header``), правило честно
не оценивается, а не выдает ложное «все хорошо».
"""
from __future__ import annotations

import re
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from ..findings import RuleAssessment, Violation, ViolationLocation
from ..rules import (
    TableContinuationRule,
    TableHeaderFormatRule,
    TableLayoutRule,
)
from .builders import (
    _build_assessment,
    _build_violation,
    _collapse_text,
    _counted_assessment,
)
from .common import _safe_page_est
from .constants import ALIGNMENT_LABELS
from parser.core.schema import (
    BorderEdge,
    NumberedCaptionEntity,
    ParagraphBlock,
    ParsedDocxDocument,
    TableBlock,
    TableCell,
    TableRow,
)

RE_TABLE_CONTINUATION = re.compile(r"^\s*(?P<word>[А-ЯЁа-яё]+)\s+таблиц", re.IGNORECASE)
"""Метка продолжения таблицы: «Продолжение таблицы 3», «Окончание таблицы 3»."""

_HIDDEN_BORDER_STYLES = frozenset({"", "none", "nil"})
"""Стили линии, которые не печатаются: пусто, none и nil."""


def _table_blocks(document: ParsedDocxDocument) -> List[TableBlock]:
    """Возвращает таблицы документа в порядке следования."""
    return [block for block in document.blocks if isinstance(block, TableBlock)]


def _iter_cells(table: TableBlock) -> Iterator[TableCell]:
    """Перебирает ячейки таблицы, пропуская тени объединенных ячеек."""
    for row in table.rows:
        for cell in row.cells:
            if cell.is_merged_shadow:
                continue
            yield cell


def _border_visible(edge: Optional[BorderEdge]) -> bool:
    """Проверяет, что линия видна: задана и не является none/nil."""
    if edge is None:
        return False
    return (edge.style or "").strip().lower() not in _HIDDEN_BORDER_STYLES


def _missing_border_edges(cell: TableCell) -> List[str]:
    """Возвращает названия отсутствующих сторон рамки ячейки."""
    borders = cell.effective_borders
    sides = (
        ("top", borders.top),
        ("bottom", borders.bottom),
        ("left", borders.left),
        ("right", borders.right),
    )
    return [name for name, edge in sides if not _border_visible(edge)]


def _caption_by_id(
    document: ParsedDocxDocument,
) -> Dict[str, NumberedCaptionEntity]:
    """Возвращает подписи по идентификатору абзаца подписи."""
    index = document.cross_ref_index
    if index is None:
        return {}
    captions: List[NumberedCaptionEntity] = [
        *index.figures,
        *index.tables,
        *index.Apps,
    ]
    return {caption.caption_block_id: caption for caption in captions}


def _table_snippet(
    table: TableBlock,
    captions: Dict[str, NumberedCaptionEntity],
) -> str:
    """Собирает короткий фрагмент для отчета: подпись таблицы или первая ячейка."""
    if table.caption_block_id:
        caption = captions.get(table.caption_block_id)
        if caption is not None:
            prefix = caption.prefix_word or "Таблица"
            number = caption.number_str or ""
            title = caption.title_text or ""
            return _collapse_text(f"{prefix} {number} {title}".strip())
    for cell in _iter_cells(table):
        text = (cell.text_combined or "").strip()
        if text:
            return _collapse_text(text)
    return "таблица"


def _table_location(table: TableBlock, snippet: str) -> ViolationLocation:
    """Создает локацию таблицы: идентификатор блока и оценка страницы."""
    return ViolationLocation(
        block_id=table.block_id,
        section_idx=table.section_idx,
        page_start_est=_safe_page_est(table.page_start_est),
        page_end_est=_safe_page_est(table.page_end_est),
        container_type="body",
        snippet=snippet,
    )


def _check_table_layout_rule(
    rule: TableLayoutRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет линии со всех сторон и ширину таблицы относительно текста.

    Ширина сравнивается с шириной текстового поля секции таблицы: по методичке
    таблица должна точно соответствовать размерам полей. Если секция неизвестна,
    проверка ширины для этой таблицы пропускается, а не сравнивается с догадкой.
    """
    expected = rule.params
    tables = _table_blocks(document)
    expected_text = "таблица ограничена линиями и соответствует ширине полей"
    if not tables:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text="в документе нет таблиц: проверять оформление нечего",
                confidence="low",
                details={"tables": 0},
            ),
        )

    captions = _caption_by_id(document)
    sections = {section.section_idx: section for section in document.sections}
    violations: List[Violation] = []
    cells_checked = 0

    for table in tables:
        snippet = _table_snippet(table, captions)
        location = _table_location(table, snippet)

        if expected.require_all_sides_borders:
            cells = list(_iter_cells(table))
            cells_checked += len(cells)
            broken = [cell for cell in cells if _missing_border_edges(cell)]
            if broken:
                first = broken[0]
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{table.block_id}:borders",
                        location=location,
                        expected={"borders": "all_sides"},
                        found={
                            "cells_without_borders": len(broken),
                            "cells_total": len(cells),
                            "first_cell_id": first.cell_id,
                            "missing_edges": _missing_border_edges(first),
                        },
                        expected_text="линии со всех сторон таблицы и между ячейками",
                        found_text=(
                            f"без полной рамки {len(broken)} из {len(cells)} ячеек, "
                            f"первая — {first.cell_id}"
                        ),
                        occurrences=len(broken),
                    )
                )

        if expected.require_full_text_width:
            section = sections.get(table.section_idx)
            width = table.total_width_mm
            if section is None or width is None:
                continue
            text_width = section.text_area_width_mm
            delta = abs(width - text_width)
            if delta > expected.width_tolerance_mm + 1e-9:
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{table.block_id}:width",
                        location=location,
                        expected={"width_mm": round(text_width, 2)},
                        found={
                            "width_mm": round(width, 2),
                            "delta_mm": round(delta, 2),
                            "section_idx": table.section_idx,
                        },
                        expected_text=(
                            f"ширина таблицы равна ширине текстового поля "
                            f"({round(text_width, 2)} мм)"
                        ),
                        found_text=(
                            f"ширина таблицы {round(width, 2)} мм, "
                            f"расхождение {round(delta, 2)} мм"
                        ),
                    )
                )

    assessment = _counted_assessment(
        rule,
        violations,
        len(tables),
        expected_text=expected_text,
        confidence="high",
        empty_observed="в документе нет таблиц",
        details={
            "tables": len(tables),
            "cells_checked": cells_checked,
            "width_checked": expected.require_full_text_width,
        },
    )
    return violations, len(tables), assessment


def _find_continuation_label(
    document: ParsedDocxDocument,
    table: TableBlock,
    label_words: Sequence[str],
) -> Optional[ParagraphBlock]:
    """Ищет абзац-метку продолжения перед таблицей-продолжением.

    Метка стоит непосредственно перед таблицей, поэтому просматриваются три
    предыдущих блока: между меткой и таблицей иногда оказывается пустой абзац.
    """
    blocks = document.blocks
    try:
        position = next(
            index for index, block in enumerate(blocks) if block is table
        )
    except StopIteration:
        return None
    words = {word.casefold() for word in label_words if word}
    for index in range(position - 1, max(-1, position - 4), -1):
        block = blocks[index]
        if isinstance(block, TableBlock):
            break
        if not isinstance(block, ParagraphBlock):
            continue
        text = (block.text_visible or "").strip()
        if not text:
            continue
        match = RE_TABLE_CONTINUATION.match(text)
        if match and match.group("word").casefold() in words:
            return block
    return None


def _check_table_continuation_rule(
    rule: TableContinuationRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет оформление таблиц, перенесенных на следующую страницу.

    Оцениваются только таблицы с ``continuation_of_table_id``: для них
    проверяются метка «Продолжение/Окончание таблицы N» и ее выравнивание,
    повтор шапки и отсутствие повторного названия. Если в документе нет таблиц
    с признаком продолжения, проверка не оценивается: разбор не нашел переносов.
    """
    expected = rule.params
    tables = _table_blocks(document)
    continuations = [
        table for table in tables if table.continuation_of_table_id
    ]
    expected_text = "перенос таблицы оформлен меткой продолжения и повтором шапки"
    if not continuations:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    f"в документе {len(tables)} таблиц, но ни одна не размечена как "
                    "продолжение (continuation_of_table_id): проверять перенос нечего"
                ),
                confidence="low",
                details={"tables": len(tables), "continuations": 0},
            ),
        )

    violations: List[Violation] = []
    labels_found = 0
    tables_by_id = {table.block_id: table for table in tables}
    break_rows_checked = 0
    for table in continuations:
        label = _find_continuation_label(document, table, expected.label_words)
        if label is not None:
            labels_found += 1
        if expected.require_label and label is None:
            violations.append(
                _build_violation(
                    rule,
                    address=f"{table.block_id}:label",
                    location=_table_location(table, "продолжение таблицы"),
                    expected={"label": " / ".join(expected.label_words) + " таблицы N"},
                    found={"label": None, "continuation_of": table.continuation_of_table_id},
                    expected_text="перед продолжением таблицы стоит метка «Продолжение таблицы N»",
                    found_text="метка продолжения таблицы не найдена",
                )
            )
        if (
            label is not None
            and expected.label_alignment is not None
            and label.effective_props.alignment
            and label.effective_props.alignment != expected.label_alignment
        ):
            violations.append(
                _build_violation(
                    rule,
                    address=f"{label.block_id}:label_alignment",
                    location=ViolationLocation(
                        block_id=label.block_id,
                        section_idx=label.section_idx,
                        page_start_est=_safe_page_est(label.page_start_est),
                        page_end_est=_safe_page_est(label.page_end_est),
                        container_type="body",
                        snippet=_collapse_text(label.text_visible),
                    ),
                    expected={"alignment": expected.label_alignment},
                    found={"alignment": label.effective_props.alignment},
                    expected_text=(
                        "метка продолжения таблицы "
                        f"{ALIGNMENT_LABELS.get(expected.label_alignment, expected.label_alignment)}"
                    ),
                    found_text=(
                        "выравнивание метки продолжения: "
                        f"{ALIGNMENT_LABELS.get(label.effective_props.alignment, 'не задано')}"
                    ),
                )
            )

        if expected.repeat_header:
            header_rows = [row for row in table.rows if row.is_tbl_header]
            if not header_rows:
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{table.block_id}:header",
                        location=_table_location(table, "продолжение таблицы"),
                        expected={"repeat_header": True},
                        found={"repeat_header": False},
                        expected_text="шапка таблицы повторена на новой странице",
                        found_text="у таблицы-продолжения нет строки-шапки",
                    )
                )

        if expected.forbid_bottom_border_on_break:
            original = tables_by_id.get(table.continuation_of_table_id or "")
            if original is not None:
                break_rows = [
                    row
                    for row in original.rows
                    if row.has_page_break_inside_or_before
                ]
                if break_rows:
                    break_rows_checked += 1
                    break_row = break_rows[-1]
                    bordered = [
                        cell
                        for cell in break_row.cells
                        if not cell.is_merged_shadow
                        and _border_visible(cell.effective_borders.bottom)
                    ]
                    if bordered:
                        violations.append(
                            _build_violation(
                                rule,
                                address=f"{original.block_id}:bottom_border",
                                location=_table_location(original, "таблица с переносом"),
                                expected={"bottom_border_on_break": False},
                                found={
                                    "bottom_border_on_break": True,
                                    "row_idx": break_row.row_idx,
                                    "cells_with_bottom_border": len(bordered),
                                },
                                expected_text="у строки, разорванной переносом, нижней линии нет",
                                found_text=(
                                    f"нижняя линия есть у {len(bordered)} ячеек строки "
                                    f"{break_row.row_idx} таблицы {original.block_id}"
                                ),
                                occurrences=len(bordered),
                            )
                        )

        if expected.forbid_repeated_caption and table.caption_block_id:
            caption = _caption_by_id(document).get(table.caption_block_id)
            text = (
                f"{caption.prefix_word or 'Таблица'} {caption.number_str or ''}".strip()
                if caption is not None
                else "название таблицы"
            )
            violations.append(
                _build_violation(
                    rule,
                    address=f"{table.block_id}:caption",
                    location=_table_location(table, text),
                    expected={"repeat_caption": False},
                    found={"repeat_caption": True, "caption_block_id": table.caption_block_id},
                    expected_text="название таблицы не повторяется при переносе",
                    found_text=f"таблица-продолжение имеет название: {text}",
                )
            )

    assessment = _counted_assessment(
        rule,
        violations,
        len(continuations),
        expected_text=expected_text,
        confidence="medium",
        empty_observed="таблиц с признаком продолжения не найдено",
        details={
            "tables": len(tables),
            "continuations": len(continuations),
            "labels_found": labels_found,
            "break_rows_checked": break_rows_checked,
        },
    )
    return violations, len(continuations), assessment


def _header_rows(table: TableBlock) -> List[TableRow]:
    """Возвращает строки-шапки таблицы в порядке следования."""
    return [row for row in table.rows if row.is_tbl_header]


def _cell_text(cell: TableCell) -> str:
    """Возвращает видимый текст ячейки без лишних пробелов."""
    return " ".join((cell.text_combined or "").split())


def _first_alpha(text: str) -> str:
    """Возвращает первую букву текста или пустую строку."""
    for char in text:
        if char.isalpha():
            return char
    return ""


def _check_table_header_format_rule(
    rule: TableHeaderFormatRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет регистр и завершающую точку в заголовках граф и строк.

    Строки шапки определяются флагом ``TableRow.is_tbl_header``. Если разбор не
    пометил ни одной строки (в DOCX шапка часто не размечена), правило не
    оценивается: считать заголовками первую строку каждой таблицы значило бы
    выдавать находки по обычным данным.
    """
    expected = rule.params
    tables = _table_blocks(document)
    headers: List[Tuple[TableBlock, TableRow, int]] = []
    for table in tables:
        for order, row in enumerate(_header_rows(table)):
            headers.append((table, row, order))
    expected_text = "заголовки граф и строк таблицы с заглавной буквы, без точки в конце"
    if not headers:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    f"в документе {len(tables)} таблиц, но строки-шапки не размечены "
                    "(TableRow.is_tbl_header пуст): проверять заголовки нечего"
                ),
                confidence="low",
                details={"tables": len(tables), "header_rows": 0},
            ),
        )

    violations: List[Violation] = []
    cells_checked = 0
    for table, row, order in headers:
        for cell in row.cells:
            if cell.is_merged_shadow:
                continue
            text = _cell_text(cell)
            if not text:
                continue
            cells_checked += 1
            page_start = row.page_start_est or table.page_start_est
            location = ViolationLocation(
                block_id=table.block_id,
                section_idx=table.section_idx,
                page_start_est=_safe_page_est(page_start),
                page_end_est=_safe_page_est(row.page_end_est or table.page_end_est),
                container_type="table_cell",
                snippet=_collapse_text(text),
            )
            first = _first_alpha(text)
            if expected.first_letter == "capitalize" and first and first.islower():
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{cell.cell_id}:first_letter",
                        location=location,
                        expected={"first_letter": "capitalize"},
                        found={"first_letter": "lowercase", "cell_id": cell.cell_id},
                        expected_text="заголовок графы или строки с заглавной буквы",
                        found_text=f"заголовок начинается со строчной буквы: «{_collapse_text(text)}»",
                    )
                )
            if expected.forbid_trailing_dot and text.endswith("."):
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{cell.cell_id}:trailing_dot",
                        location=location,
                        expected={"trailing_dot": False},
                        found={"trailing_dot": True, "cell_id": cell.cell_id},
                        expected_text="без точки в конце заголовка графы или строки",
                        found_text=f"заголовок заканчивается точкой: «{_collapse_text(text)}»",
                    )
                )
            if (
                expected.subheader_case in ("lowercase", "lowercase_if_continuation")
                and order > 0
            ):
                # Подзаголовок-продолжение пишется со строчной буквы, когда
                # составляет одно предложение с заголовком верхней строки.
                if first and first.isupper() and not text.endswith("."):
                    violations.append(
                        _build_violation(
                            rule,
                            address=f"{cell.cell_id}:subheader_case",
                            location=location,
                            expected={"subheader_case": "lowercase"},
                            found={"subheader_case": "uppercase", "cell_id": cell.cell_id},
                            expected_text="подзаголовок-продолжение со строчной буквы",
                            found_text=f"подзаголовок начинается с заглавной: «{_collapse_text(text)}»",
                        )
                    )

    assessment = _counted_assessment(
        rule,
        violations,
        len(headers),
        expected_text=expected_text,
        confidence="high",
        empty_observed="строк-шапок в таблицах не найдено",
        details={
            "tables": len(tables),
            "header_rows": len(headers),
            "cells_checked": cells_checked,
        },
    )
    return violations, len(headers), assessment
