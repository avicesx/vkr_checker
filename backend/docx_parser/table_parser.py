"""Разбор таблиц: сетка, рамки, объединения, подписи и продолжения.

Основной вызов — фаза 3 для w:tbl; те же функции разбирают вложенные таблицы
и таблицы в колонтитулах.

Учитывает каскад стилей (w:tblStyle + w:cnfStyle), прямые w:tblPr/w:tcPr,
исключения строки w:tblPrEx, объединения gridSpan/vMerge, вертикальное
выравнивание и повтор заголовка. Отдельные функции связывают подписи
с таблицами и помечают таблицы-продолжения.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, Iterator, List, Optional, Tuple
from lxml import etree

from .schema import (
    BlockUnion,
    BorderEdge,
    BoxBorders,
    CellMarginsMm,
    ColorValue,
    ParagraphBlock,
    TableBlock,
    TableCell,
    TableRow,
)
from .style_resolver import (
    NS,
    StyleCascadeResolver,
    TableCascadeContext,
    dxa_to_mm,
    eighths_to_pt,
    parse_oxml_bool,
    safe_fromstring,
    safe_int,
    w_attr,
)

# Тип колбэка для парсинга вложенных блоков внутри ячейки (абзацев и вложенных таблиц)
# Принимает: (xml_element, block_id_prefix, section_idx, current_page, tbl_context)
# Возвращает: (BlockUnion, updated_page)
CellChildParserCallback = Callable[
    [etree._Element, str, int, int, TableCascadeContext],
    Tuple[BlockUnion, int],
]

# Регулярные выражения для эвристик содержимого ячеек и подписей таблиц
RE_DASH_ONLY = re.compile(r"^[\-\u2012\u2013\u2014\u2015\s]+$|^(нет\s+данных|н/д)$", re.IGNORECASE)
RE_NUMERIC_VALUE = re.compile(
    r"^[+\-\u2212]?\s*\d+([.,]\d+)?(\s*([%°]|тыс\.|млн|млрд|руб\.|кг|мм|см|м|с|Гц|Вт))?$"
)
RE_TABLE_CAPTION = re.compile(
    r"^\s*(?:таблица|табл\.)\s+([А-ЯЁA-Z]?\s*\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)
RE_TABLE_CONTINUATION = re.compile(
    r"^\s*(?:продолжение|окончание)\s+таблицы\s+([А-ЯЁA-Z]?\s*\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)


def iter_unwrapped_elements(
    parent: etree._Element, allowed_local_names: Tuple[str, ...]
) -> Iterator[etree._Element]:
    """
    Итерирует потомков, прозрачно раскрывая контейнеры-обёртки вокруг строк, ячеек и абзацев:
    w:sdt/w:sdtContent, w:customXml, w:smartTag, w:ins, w:moveTo и mc:AlternateContent.
    Word и LibreOffice активно оборачивают w:tr/w:tc/w:p в w:sdt, из-за чего прямой
    findall("w:tr") терял целые строки и ячейки таблицы.
    """
    for child in parent:
        if not isinstance(child.tag, str):
            continue
        local = etree.QName(child).localname
        if local in allowed_local_names:
            yield child
        elif local == "sdt":
            sdt_content = child.find("w:sdtContent", namespaces=NS)
            if sdt_content is not None:
                yield from iter_unwrapped_elements(sdt_content, allowed_local_names)
        elif local in ("customXml", "smartTag", "ins", "moveTo"):
            yield from iter_unwrapped_elements(child, allowed_local_names)
        elif local == "AlternateContent":
            for branch in child:
                if (
                    isinstance(branch.tag, str)
                    and etree.QName(branch).localname in ("Choice", "Fallback")
                ):
                    yield from iter_unwrapped_elements(branch, allowed_local_names)
                    break


class TableParser:
    """
    Парсер узлов <w:tbl> в структуру TableBlock.
    Разрешает сетку колонок, объединения ячеек (gridSpan, vMerge), каскад границ,
    внутренние поля, перенос шапки и пагинацию.
    """

    def __init__(
        self,
        style_resolver: StyleCascadeResolver,
        styles_xml_bytes: Optional[bytes] = None,
    ) -> None:
        """Принимает резолвер стилей и байты styles.xml для каскада таблиц."""
        self.style_resolver = style_resolver
        # Кэш табличных свойств из styles.xml: style_id -> (BoxBorders, CellMarginsMm, indent_left_mm)
        self._table_style_props: Dict[str, Tuple[BoxBorders, CellMarginsMm, float]] = {}
        if styles_xml_bytes:
            self._preload_table_styles(styles_xml_bytes)

    def _preload_table_styles(self, xml_bytes: bytes) -> None:
        """Извлекает дефолтные рамки и поля ячеек из табличных стилей в styles.xml."""
        root = safe_fromstring(xml_bytes)
        raw_map: Dict[str, etree._Element] = {}
        based_on_map: Dict[str, Optional[str]] = {}

        for style_el in root.findall("w:style[@w:type='table']", namespaces=NS):
            s_id = w_attr(style_el, "styleId")
            if not s_id:
                continue
            raw_map[s_id] = style_el
            based_el = style_el.find("w:basedOn", namespaces=NS)
            based_on_map[s_id] = w_attr(based_el, "val") if based_el is not None else None

        for s_id in raw_map:
            chain: List[str] = []
            curr: Optional[str] = s_id
            visited = set()
            while curr and curr in raw_map and curr not in visited:
                visited.add(curr)
                chain.append(curr)
                curr = based_on_map.get(curr)
            chain.reverse()

            borders = BoxBorders()
            margins = CellMarginsMm()
            indent_mm = 0.0

            for cid in chain:
                tbl_pr = raw_map[cid].find("w:tblPr", namespaces=NS)
                if tbl_pr is None:
                    continue
                bdr_el = tbl_pr.find("w:tblBorders", namespaces=NS)
                if bdr_el is not None:
                    borders = self._merge_box_borders(
                        borders, self._parse_borders_element(bdr_el, "table_style")
                    )
                mar_el = tbl_pr.find("w:tblCellMar", namespaces=NS)
                if mar_el is not None:
                    margins = self._parse_cell_margins(mar_el, margins)
                ind_el = tbl_pr.find("w:tblInd", namespaces=NS)
                if ind_el is not None and w_attr(ind_el, "w") is not None:
                    indent_mm = dxa_to_mm(w_attr(ind_el, "w") or 0)

            self._table_style_props[s_id] = (borders, margins, indent_mm)

    # -----------------------------------------------------------------
    # Главный метод парсинга таблицы <w:tbl>
    # -----------------------------------------------------------------

    def parse_table(
        self,
        tbl_elem: etree._Element,
        block_id: str,
        section_idx: int,
        start_page: int,
        page_text_area_width_mm: float,
        child_parser: CellChildParserCallback,
    ) -> Tuple[TableBlock, int]:
        """
        Парсит <w:tbl> и возвращает (TableBlock, end_page).
        """
        tbl_pr = tbl_elem.find("w:tblPr", namespaces=NS)

        # 1. Стиль таблицы и базовые свойства из стиля
        style_id: Optional[str] = None
        if tbl_pr is not None:
            style_el = tbl_pr.find("w:tblStyle", namespaces=NS)
            if style_el is not None:
                style_id = w_attr(style_el, "val")
        if not style_id:
            style_id = self.style_resolver.default_style_ids.get("table")

        base_borders, default_margins, indent_left_mm = self._table_style_props.get(
            style_id or "", (BoxBorders(), CellMarginsMm(), 0.0)
        )
        table_borders = base_borders.model_copy(deep=True)
        default_margins = default_margins.model_copy(deep=True)

        # 2. Геометрия, выравнивание и прямое форматирование таблицы (w:tblPr)
        alignment = "left"
        is_floating = False
        width_type = "auto"
        declared_width_mm: Optional[float] = None
        tbl_look_flags: Dict[str, bool] = {}

        if tbl_pr is not None:
            # Выравнивание самой таблицы
            jc_el = tbl_pr.find("w:jc", namespaces=NS)
            if jc_el is not None:
                jc_val = w_attr(jc_el, "val")
                if jc_val in ("center", "right"):
                    alignment = jc_val
                elif jc_val in ("left", "start"):
                    alignment = "left"

            # Плавающая таблица (w:tblpPr)
            if tbl_pr.find("w:tblpPr", namespaces=NS) is not None:
                is_floating = True

            # Отступ слева (w:tblInd)
            ind_el = tbl_pr.find("w:tblInd", namespaces=NS)
            if ind_el is not None and w_attr(ind_el, "w") is not None:
                ind_type = w_attr(ind_el, "type") or "dxa"
                if ind_type == "dxa":
                    indent_left_mm = dxa_to_mm(w_attr(ind_el, "w") or 0)
                elif ind_type in ("nil", "none"):
                    indent_left_mm = 0.0

            # Ширина таблицы (w:tblW)
            tbl_w_el = tbl_pr.find("w:tblW", namespaces=NS)
            if tbl_w_el is not None:
                w_type_raw = w_attr(tbl_w_el, "type") or "auto"
                w_val_raw = w_attr(tbl_w_el, "w")
                if w_type_raw in ("dxa", "pct", "auto", "nil"):
                    width_type = w_type_raw
                if w_val_raw is not None:
                    if width_type == "dxa":
                        declared_width_mm = dxa_to_mm(w_val_raw)
                    elif width_type == "pct":
                        # В OpenXML проценты ширины таблицы хранятся в 1/50 процента (5000 = 100%)
                        pct_val = float(w_val_raw.rstrip("%"))
                        ratio = (pct_val / 100.0) if "%" in w_val_raw else (pct_val / 5000.0)
                        declared_width_mm = round(page_text_area_width_mm * ratio, 2)

            # Прямые границы таблицы (w:tblBorders)
            direct_bdr_el = tbl_pr.find("w:tblBorders", namespaces=NS)
            if direct_bdr_el is not None:
                table_borders = self._merge_box_borders(
                    table_borders, self._parse_borders_element(direct_bdr_el, "direct")
                )

            # Прямые внутренние поля ячеек по умолчанию (w:tblCellMar)
            direct_mar_el = tbl_pr.find("w:tblCellMar", namespaces=NS)
            if direct_mar_el is not None:
                default_margins = self._parse_cell_margins(direct_mar_el, default_margins)

            # Флаги условного отображения стиля (w:tblLook)
            tbl_look_el = tbl_pr.find("w:tblLook", namespaces=NS)
            if tbl_look_el is not None:
                tbl_look_flags = self._parse_tbl_look(tbl_look_el)

        # 3. Сетка колонок (w:tblGrid / w:gridCol)
        grid_widths_mm: List[float] = []
        tbl_grid = tbl_elem.find("w:tblGrid", namespaces=NS)
        if tbl_grid is not None:
            for g_col in tbl_grid.findall("w:gridCol", namespaces=NS):
                col_w = w_attr(g_col, "w")
                if col_w is not None:
                    grid_widths_mm.append(dxa_to_mm(col_w))

        grid_sum_mm = round(sum(grid_widths_mm), 2) if grid_widths_mm else None
        total_width_mm = grid_sum_mm or declared_width_mm

        # Допуск 1.5 мм на погрешность округления twips в Word
        exceeds_width = False
        if total_width_mm is not None and page_text_area_width_mm > 0:
            effective_right_edge = total_width_mm + max(0.0, indent_left_mm)
            exceeds_width = effective_right_edge > (page_text_area_width_mm + 1.5)

        # 4. Проход по строкам и построение 2D-матрицы ячеек
        tr_elements = list(iter_unwrapped_elements(tbl_elem, ("tr",)))
        num_rows = len(tr_elements)
        num_cols_est = max(len(grid_widths_mm), self._estimate_max_cols(tr_elements))

        rows_parsed: List[TableRow] = []
        # Карта активных вертикальных объединений: col_idx -> стартовая ячейка (TableCell)
        active_vmerge_origins: Dict[int, TableCell] = {}
        # Сырые нижние границы теневых ячеек для переноса на родительскую ячейку: (origin_cell_id) -> BorderEdge
        shadow_bottom_borders: Dict[str, BorderEdge] = {}
        # Сохраняем прямые границы каждой ячейки и строки для финальной сборки рамок после расчета row_span
        cell_direct_borders: Dict[str, Tuple[Optional[BoxBorders], Optional[BoxBorders]]] = {}

        current_page = start_page

        for r_idx, tr_el in enumerate(tr_elements):
            tr_pr = tr_el.find("w:trPr", namespaces=NS)
            row_height_mm: Optional[float] = None
            row_height_rule = "auto"
            is_header = False
            cant_split = False
            grid_before = 0
            row_cnf_bits: Optional[str] = None
            row_bdr_override: Optional[BoxBorders] = None

            if tr_pr is not None:
                tr_h = tr_pr.find("w:trHeight", namespaces=NS)
                if tr_h is not None:
                    val_h = w_attr(tr_h, "val")
                    rule_h = w_attr(tr_h, "hRule") or "auto"
                    if val_h is not None:
                        row_height_mm = dxa_to_mm(val_h)
                    if rule_h in ("auto", "exact", "atLeast"):
                        row_height_rule = rule_h

                is_header = parse_oxml_bool(tr_pr.find("w:tblHeader", namespaces=NS)) is True
                cant_split = parse_oxml_bool(tr_pr.find("w:cantSplit", namespaces=NS)) is True

                gb_el = tr_pr.find("w:gridBefore", namespaces=NS)
                if gb_el is not None and w_attr(gb_el, "val") is not None:
                    grid_before = max(0, safe_int(w_attr(gb_el, "val"), 0) or 0)

                cnf_el = tr_pr.find("w:cnfStyle", namespaces=NS)
                if cnf_el is not None:
                    row_cnf_bits = w_attr(cnf_el, "val")

            # Переопределения границ на уровне строки.
            # По ECMA-376 они лежат в w:tblPrEx/w:tblBorders (соседний с w:trPr элемент),
            # устаревшие генераторы пишут их прямо в w:trPr/w:tblBorders — поддерживаем оба места.
            # Блок вынесен из `if tr_pr is not None`: строка без w:trPr сохраняет рамки.
            tbl_pr_ex = tr_el.find("w:tblPrEx", namespaces=NS)
            tr_bdr_el = (
                tbl_pr_ex.find("w:tblBorders", namespaces=NS)
                if tbl_pr_ex is not None
                else None
            )
            if tr_bdr_el is None and tr_pr is not None:
                tr_bdr_el = tr_pr.find("w:tblBorders", namespaces=NS)
            if tr_bdr_el is not None:
                row_bdr_override = self._parse_borders_element(tr_bdr_el, "direct")

            row_start_page = current_page
            row_max_end_page = current_page
            row_has_break = False

            col_cursor = grid_before
            cells_parsed: List[TableCell] = []
            tc_elements = list(iter_unwrapped_elements(tr_el, ("tc",)))

            for tc_idx, tc_el in enumerate(tc_elements):
                tc_pr = tc_el.find("w:tcPr", namespaces=NS)
                col_span = 1
                v_merge_state = "none"
                cell_width_mm: Optional[float] = None
                v_align = "top"
                text_dir = "lrTb"
                no_wrap = False
                shading_fill: Optional[ColorValue] = None
                cell_margins = default_margins.model_copy(deep=True)
                tc_direct_bdr: Optional[BoxBorders] = None
                cell_cnf_bits: Optional[str] = None

                if tc_pr is not None:
                    # Горизонтальное объединение (w:gridSpan)
                    gs_el = tc_pr.find("w:gridSpan", namespaces=NS)
                    if gs_el is not None and w_attr(gs_el, "val") is not None:
                        col_span = max(1, safe_int(w_attr(gs_el, "val"), 1) or 1)

                    # Вертикальное объединение (w:vMerge)
                    vm_el = tc_pr.find("w:vMerge", namespaces=NS)
                    if vm_el is not None:
                        vm_val = w_attr(vm_el, "val")
                        if vm_val == "restart":
                            v_merge_state = "restart"
                        else:
                            # Отсутствие атрибута val (<w:vMerge/>) или val="continue" означает продолжение
                            v_merge_state = "continue"

                    # Ширина ячейки (w:tcW)
                    tc_w_el = tc_pr.find("w:tcW", namespaces=NS)
                    if tc_w_el is not None and w_attr(tc_w_el, "w") is not None:
                        tc_w_type = w_attr(tc_w_el, "type") or "dxa"
                        if tc_w_type == "dxa":
                            cell_width_mm = dxa_to_mm(w_attr(tc_w_el, "w") or 0)

                    # Если ширина не задана явно, берем сумму ширин покрываемых колонок из tblGrid
                    if cell_width_mm is None and grid_widths_mm and col_cursor < len(grid_widths_mm):
                        covered_Slice = grid_widths_mm[col_cursor : col_cursor + col_span]
                        if covered_Slice:
                            cell_width_mm = round(sum(covered_Slice), 2)

                    # Вертикальное выравнивание внутри ячейки (w:vAlign)
                    va_el = tc_pr.find("w:vAlign", namespaces=NS)
                    if va_el is not None:
                        va_val = w_attr(va_el, "val")
                        if va_val in ("top", "center", "bottom"):
                            v_align = va_val
                        elif va_val == "both":
                            v_align = "center"

                    # Направление текста (w:textDirection)
                    td_el = tc_pr.find("w:textDirection", namespaces=NS)
                    if td_el is not None:
                        td_val = w_attr(td_el, "val") or "lrTb"
                        text_dir = td_val if td_val in ("lrTb", "tbRl", "btLr") else "other"

                    no_wrap = parse_oxml_bool(tc_pr.find("w:noWrap", namespaces=NS)) is True

                    # Заливка ячейки (w:shd)
                    shd_el = tc_pr.find("w:shd", namespaces=NS)
                    if shd_el is not None:
                        shading_fill = self.style_resolver.theme_resolver.resolve_color_element(
                            shd_el, "direct", attr_name="fill"
                        )

                    # Индивидуальные поля ячейки (w:tcMar)
                    tc_mar_el = tc_pr.find("w:tcMar", namespaces=NS)
                    if tc_mar_el is not None:
                        cell_margins = self._parse_cell_margins(tc_mar_el, cell_margins)

                    # Прямые границы ячейки (w:tcBorders)
                    tc_bdr_el = tc_pr.find("w:tcBorders", namespaces=NS)
                    if tc_bdr_el is not None:
                        tc_direct_bdr = self._parse_borders_element(tc_bdr_el, "direct")

                    cnf_el = tc_pr.find("w:cnfStyle", namespaces=NS)
                    if cnf_el is not None:
                        cell_cnf_bits = w_attr(cnf_el, "val")

                cell_id = f"{block_id}_r{r_idx}_c{col_cursor}"

                # Слияние или синтез 12-битной маски условного форматирования cnfStyle
                effective_cnf_bits = self._resolve_cnf_bits(
                    row_cnf_bits=row_cnf_bits,
                    cell_cnf_bits=cell_cnf_bits,
                    tbl_look=tbl_look_flags,
                    r_idx=r_idx,
                    c_idx=col_cursor,
                    col_span=col_span,
                    num_rows=num_rows,
                    num_cols=num_cols_est,
                )
                tbl_ctx = TableCascadeContext(
                    table_style_id=style_id,
                    cnf_style_bits=effective_cnf_bits,
                )

                # Парсинг внутренних блоков ячейки (w:p и вложенных w:tbl)
                # Все ячейки одной строки стартуют с row_start_page, а страница строки = максимум по ячейкам
                cell_page_cursor = row_start_page
                inner_blocks: List[BlockUnion] = []
                text_parts: List[str] = []

                child_idx = 0
                for child_el in iter_unwrapped_elements(tc_el, ("p", "tbl")):
                    local_tag = etree.QName(child_el).localname
                    child_block_id = f"{cell_id}_{local_tag}{child_idx}"
                    child_idx += 1

                    parsed_block, new_page = child_parser(
                        child_el, child_block_id, section_idx, cell_page_cursor, tbl_ctx
                    )
                    if isinstance(parsed_block, ParagraphBlock):
                        if parsed_block.effective_props.page_break_before and child_idx == 1 and r_idx > 0:
                            # Разрыв страницы в первом абзаце ячейки переносит всю строку на новую страницу
                            row_has_break = True
                        if parsed_block.rendered_page_breaks_offsets or new_page > cell_page_cursor:
                            row_has_break = True
                        if parsed_block.text_visible:
                            text_parts.append(parsed_block.text_visible.strip())

                    cell_page_cursor = new_page
                    inner_blocks.append(parsed_block)

                if cell_page_cursor > row_max_end_page:
                    row_max_end_page = cell_page_cursor

                combined_text = "\n".join(t for t in text_parts if t).strip()
                is_merged_shadow = False

                # Обновление вертикальных объединений (vMerge)
                if v_merge_state == "continue":
                    origin_cell = active_vmerge_origins.get(col_cursor)
                    if origin_cell is not None:
                        origin_cell.row_span += 1
                        is_merged_shadow = True
                        # Если у поглощенной ячейки задана нижняя рамка, запоминаем ее для родительской
                        if tc_direct_bdr is not None and tc_direct_bdr.bottom.style != "none":
                            shadow_bottom_borders[origin_cell.cell_id] = tc_direct_bdr.bottom
                    else:
                        # Сиротский vMerge="continue" в первой строке без родителя — трактуем как restart
                        v_merge_state = "restart"
                        for c_span in range(col_cursor, col_cursor + col_span):
                            active_vmerge_origins[c_span] = None  # заполним ниже

                cell_obj = TableCell(
                    cell_id=cell_id,
                    row_idx=r_idx,
                    col_idx=col_cursor,
                    col_span=col_span,
                    row_span=1,
                    v_merge_state=v_merge_state,  # type: ignore[arg-type]
                    is_merged_shadow=is_merged_shadow,
                    width_mm=cell_width_mm,
                    v_align=v_align,  # type: ignore[arg-type]
                    text_direction=text_dir,  # type: ignore[arg-type]
                    no_wrap=no_wrap,
                    shading_fill=shading_fill,
                    margins_mm=cell_margins,
                    text_combined=combined_text,
                    is_visually_empty=(len(combined_text) == 0),
                    content_type_heuristic=self._classify_cell_content(combined_text),  # type: ignore[arg-type]
                    blocks=inner_blocks,
                )

                if v_merge_state == "restart":
                    for c_span in range(col_cursor, col_cursor + col_span):
                        active_vmerge_origins[c_span] = cell_obj
                elif v_merge_state == "none":
                    for c_span in range(col_cursor, col_cursor + col_span):
                        active_vmerge_origins.pop(c_span, None)

                cell_direct_borders[cell_id] = (row_bdr_override, tc_direct_bdr)
                cells_parsed.append(cell_obj)
                col_cursor += col_span

            if col_cursor > num_cols_est:
                num_cols_est = col_cursor

            current_page = row_max_end_page
            rows_parsed.append(
                TableRow(
                    row_idx=r_idx,
                    height_mm=row_height_mm,
                    height_rule=row_height_rule,  # type: ignore[arg-type]
                    is_tbl_header=is_header,
                    cant_split=cant_split,
                    page_start_est=row_start_page,
                    page_end_est=row_max_end_page,
                    has_page_break_inside_or_before=row_has_break,
                    cells=cells_parsed,
                )
            )

        # 5. Финальное разрешение эффективных границ ячеек (когда известны все row_span и col_span)
        for row in rows_parsed:
            for cell in row.cells:
                row_bdr_ovr, tc_bdr_ovr = cell_direct_borders.get(cell.cell_id, (None, None))
                eff_bdr = self._resolve_cell_effective_borders(
                    cell=cell,
                    num_rows=num_rows,
                    num_cols=num_cols_est,
                    table_borders=table_borders,
                    row_borders=row_bdr_ovr,
                    cell_borders=tc_bdr_ovr,
                )
                # Если ячейка растянута вниз (vMerge), подтягиваем нижнюю рамку с последней теневой ячейки
                if cell.cell_id in shadow_bottom_borders:
                    eff_bdr.bottom = shadow_bottom_borders[cell.cell_id]
                elif cell.row_span > 1 and (cell.row_idx + cell.row_span == num_rows):
                    # Если объединенная ячейка доходит до самого дна таблицы и не имела явного переопределения
                    if tc_bdr_ovr is None or tc_bdr_ovr.bottom.style == "none":
                        eff_bdr.bottom = table_borders.bottom

                cell.effective_borders = eff_bdr

        has_col_nums_row = self._detect_column_numbers_row(rows_parsed, num_cols_est)

        table_block = TableBlock(
            block_id=block_id,
            section_idx=section_idx,
            page_start_est=start_page,
            page_end_est=current_page,
            spans_multiple_pages=(current_page > start_page),
            style_id=style_id,
            alignment=alignment,  # type: ignore[arg-type]
            is_floating_table=is_floating,
            indent_left_mm=indent_left_mm,
            total_width_mm=total_width_mm,
            width_type=width_type,  # type: ignore[arg-type]
            grid_column_widths_mm=grid_widths_mm,
            exceeds_page_text_width=exceeds_width,
            table_borders=table_borders,
            default_cell_margins_mm=default_margins,
            rows=rows_parsed,
            has_column_numbers_row=has_col_nums_row,
        )

        return table_block, current_page

    # -----------------------------------------------------------------
    # Вспомогательные методы расчета границ, полей и эвристик
    # -----------------------------------------------------------------

    def _resolve_cell_effective_borders(
        self,
        cell: TableCell,
        num_rows: int,
        num_cols: int,
        table_borders: BoxBorders,
        row_borders: Optional[BoxBorders],
        cell_borders: Optional[BoxBorders],
    ) -> BoxBorders:
        """
        Вычисляет видимые рамки ячейки с учетом ее положения на периметре или внутри сетки таблицы.
        """
        is_top_edge = cell.row_idx == 0
        is_bottom_edge = (cell.row_idx + cell.row_span) >= num_rows
        is_left_edge = cell.col_idx == 0
        is_right_edge = (cell.col_idx + cell.col_span) >= num_cols

        eff = BoxBorders(
            top=(
                table_borders.top
                if is_top_edge
                else (table_borders.inside_h or BorderEdge())
            ),
            bottom=(
                table_borders.bottom
                if is_bottom_edge
                else (table_borders.inside_h or BorderEdge())
            ),
            left=(
                table_borders.left
                if is_left_edge
                else (table_borders.inside_v or BorderEdge())
            ),
            right=(
                table_borders.right
                if is_right_edge
                else (table_borders.inside_v or BorderEdge())
            ),
        )

        # Переопределения уровня строки (w:tblPrEx/w:tblBorders, реже w:trPr/w:tblBorders).
        # Верхняя/нижняя рамка строки применяется ко ВСЕМ её ячейкам, а не только к ячейкам
        # на периметре таблицы: w:top описывает верхнюю границу строки (частая практика
        # для строки-разделителя «Итого»), поэтому проверка is_top_edge здесь не нужна.
        if row_borders is not None:
            if row_borders.top.style != "none":
                eff.top = row_borders.top
            elif row_borders.inside_h and row_borders.inside_h.style != "none":
                eff.top = row_borders.inside_h

            if row_borders.bottom.style != "none":
                eff.bottom = row_borders.bottom
            elif row_borders.inside_h and row_borders.inside_h.style != "none":
                eff.bottom = row_borders.inside_h

            if row_borders.left.style != "none":
                eff.left = row_borders.left
            if row_borders.right.style != "none":
                eff.right = row_borders.right

        # Прямые границы ячейки (w:tcPr/w:tcBorders) имеют наивысший приоритет
        # В OpenXML w:val="nil" на ячейке стирает унаследованную границу таблицы.
        if cell_borders is not None:
            for side in ("top", "bottom", "left", "right", "tl2br", "tr2bl"):
                c_edge: Optional[BorderEdge] = getattr(cell_borders, side)
                if c_edge is not None and (c_edge.style != "none" or c_edge.width_pt > 0):
                    setattr(eff, side, c_edge)

        return eff

    def _parse_borders_element(
        self, bdr_el: etree._Element, source: str
    ) -> BoxBorders:
        """Парсит <w:tblBorders> или <w:tcBorders> с различением не-заданного 'none' и явного сброса 'nil'."""
        box = BoxBorders()
        for xml_name, attr_name in (
            ("top", "top"),
            ("bottom", "bottom"),
            ("left", "left"),
            ("start", "left"),
            ("right", "right"),
            ("end", "right"),
            ("insideH", "inside_h"),
            ("insideV", "inside_v"),
            ("tl2br", "tl2br"),
            ("tr2bl", "tr2bl"),
        ):
            edge_el = bdr_el.find(f"w:{xml_name}", namespaces=NS)
            if edge_el is None:
                continue
            val = w_attr(edge_el, "val") or "none"
            sz = w_attr(edge_el, "sz")
            space = w_attr(edge_el, "space")
            color = self.style_resolver.theme_resolver.resolve_color_element(
                edge_el, source, attr_name="color"  # type: ignore[arg-type]
            )
            style_norm = (
                val
                if val in ("none", "nil", "single", "double", "dotted", "dashed", "thick")
                else "other"
            )
            setattr(
                box,
                attr_name,
                BorderEdge(
                    style=style_norm,  # type: ignore[arg-type]
                    width_pt=eighths_to_pt(sz) if (sz is not None and style_norm not in ("none", "nil")) else 0.0,
                    space_pt=float(space) if space is not None else 0.0,
                    color=color,
                ),
            )
        return box

    @staticmethod
    def _merge_box_borders(base: BoxBorders, override: BoxBorders) -> BoxBorders:
        """Накладывает верхний слой рамок на нижний по каждой стороне."""
        merged = base.model_copy(deep=True)
        for field_name in ("top", "bottom", "left", "right", "inside_h", "inside_v", "tl2br", "tr2bl"):
            ovr_edge: Optional[BorderEdge] = getattr(override, field_name)
            if ovr_edge is not None and ovr_edge.style != "none":
                setattr(merged, field_name, ovr_edge)
        return merged

    @staticmethod
    def _parse_cell_margins(mar_el: etree._Element, fallback: CellMarginsMm) -> CellMarginsMm:
        """Читает w:tcMar в миллиметрах с наследованием от таблицы."""
        res = fallback.model_copy(deep=True)
        for xml_sides, field_name in (
            (("top",), "top_mm"),
            (("bottom",), "bottom_mm"),
            (("left", "start"), "left_mm"),
            (("right", "end"), "right_mm"),
        ):
            for tag in xml_sides:
                el = mar_el.find(f"w:{tag}", namespaces=NS)
                if el is not None and w_attr(el, "w") is not None:
                    w_type = w_attr(el, "type") or "dxa"
                    if w_type == "dxa":
                        setattr(res, field_name, dxa_to_mm(w_attr(el, "w") or 0))
                    elif w_type in ("nil", "none"):
                        setattr(res, field_name, 0.0)
        return res

    @staticmethod
    def _parse_tbl_look(tbl_look_el: etree._Element) -> Dict[str, bool]:
        """Разбирает флаги <w:tblLook> (как именованные атрибуты, так и старую битовую маску w:val)."""
        flags = {
            "firstRow": False,
            "lastRow": False,
            "firstColumn": False,
            "lastColumn": False,
            "noHBand": False,
            "noVBand": False,
        }
        hex_val = w_attr(tbl_look_el, "val")
        if hex_val:
            try:
                mask = int(hex_val, 16)
                flags["firstRow"] = bool(mask & 0x0020)
                flags["lastRow"] = bool(mask & 0x0040)
                flags["firstColumn"] = bool(mask & 0x0080)
                flags["lastColumn"] = bool(mask & 0x0100)
                flags["noHBand"] = bool(mask & 0x0200)
                flags["noVBand"] = bool(mask & 0x0400)
            except ValueError:
                pass

        for k in list(flags.keys()):
            attr_v = w_attr(tbl_look_el, k)
            if attr_v is not None:
                flags[k] = attr_v.lower() not in ("0", "false", "off")
        return flags

    @staticmethod
    def _resolve_cnf_bits(
        row_cnf_bits: Optional[str],
        cell_cnf_bits: Optional[str],
        tbl_look: Dict[str, bool],
        r_idx: int,
        c_idx: int,
        col_span: int,
        num_rows: int,
        num_cols: int,
    ) -> str:
        """
        Объединяет явные биты w:cnfStyle строки и ячейки или синтезирует их из w:tblLook.
        Порядок 12 бит: firstRow, lastRow, firstCol, lastCol, band1Vert, band2Vert,
        band1Horz, band2Horz, neCell, nwCell, seCell, swCell.
        """
        if row_cnf_bits and len(row_cnf_bits) == 12 and cell_cnf_bits and len(cell_cnf_bits) == 12:
            return "".join(
                "1" if (row_cnf_bits[i] == "1" or cell_cnf_bits[i] == "1") else "0"
                for i in range(12)
            )
        if cell_cnf_bits and len(cell_cnf_bits) == 12:
            return cell_cnf_bits
        if row_cnf_bits and len(row_cnf_bits) == 12:
            return row_cnf_bits

        if not tbl_look:
            return "000000000000"

        is_first_r = r_idx == 0 and tbl_look.get("firstRow", False)
        is_last_r = (r_idx == num_rows - 1) and tbl_look.get("lastRow", False)
        is_first_c = c_idx == 0 and tbl_look.get("firstColumn", False)
        is_last_c = (c_idx + col_span >= num_cols) and tbl_look.get("lastColumn", False)

        bits = [
            "1" if is_first_r else "0",
            "1" if is_last_r else "0",
            "1" if is_first_c else "0",
            "1" if is_last_c else "0",
            "0",
            "0",
            "0",
            "0",
            "1" if (is_first_r and is_last_c) else "0",
            "1" if (is_first_r and is_first_c) else "0",
            "1" if (is_last_r and is_last_c) else "0",
            "1" if (is_last_r and is_first_c) else "0",
        ]
        return "".join(bits)

    @staticmethod
    def _estimate_max_cols(tr_elements: List[etree._Element]) -> int:
        """Оценивает число колонок по максимальной сумме gridSpan в строках."""
        max_c = 0
        for tr in tr_elements:
            c_count = 0
            tr_pr = tr.find("w:trPr", namespaces=NS)
            if tr_pr is not None:
                gb = tr_pr.find("w:gridBefore", namespaces=NS)
                if gb is not None and w_attr(gb, "val") is not None:
                    c_count += max(0, safe_int(w_attr(gb, "val"), 0) or 0)
            for tc in iter_unwrapped_elements(tr, ("tc",)):
                tc_pr = tc.find("w:tcPr", namespaces=NS)
                span = 1
                if tc_pr is not None:
                    gs = tc_pr.find("w:gridSpan", namespaces=NS)
                    if gs is not None and w_attr(gs, "val") is not None:
                        span = max(1, safe_int(w_attr(gs, "val"), 1) or 1)
                c_count += span
            if c_count > max_c:
                max_c = c_count
        return max(1, max_c)

    @staticmethod
    def _classify_cell_content(text: str) -> str:
        """Определяет тип данных в ячейке для проверки правил выравнивания (числа — по центру/справа, текст — слева)."""
        clean = text.strip()
        if not clean:
            return "empty"
        if RE_DASH_ONLY.match(clean):
            return "dash_placeholder"
        if RE_NUMERIC_VALUE.match(clean):
            return "numeric"
        has_digits = any(ch.isdigit() for ch in clean)
        has_letters = any(ch.isalpha() for ch in clean)
        if has_digits and has_letters and len(clean) <= 15:
            return "mixed"
        return "text"

    @staticmethod
    def _detect_column_numbers_row(rows: List[TableRow], num_cols: int) -> bool:
        """
        Проверяет наличие строки нумерации граф ('1 | 2 | 3 | ... | N').
        По ГОСТ 7.32 при переносе таблицы на следующую страницу допускается не повторять шапку,
        а повторять только строку с номерами граф.
        """
        if num_cols < 2 or not rows:
            return False

        # Строка нумерации граф может быть первой (в таблице-продолжении) или сразу под шапкой (строки 0..3)
        for row in rows[:4]:
            if len(row.cells) != num_cols:
                continue
            is_seq = True
            for idx, cell in enumerate(row.cells, start=1):
                val = cell.text_combined.strip().rstrip(".")
                if val != str(idx):
                    is_seq = False
                    break
            if is_seq:
                return True
        return False


# =====================================================================
# ПОСТ-ПРОЦЕССОР СВЯЗЫВАНИЯ ТАБЛИЦ С НАДПИСЯМИ И ПЕРЕНОСАМИ В ПОТОКЕ БЛОКОВ
# =====================================================================

def link_table_captions_and_continuations(blocks: List[BlockUnion]) -> None:
    """
    Проходит по линейному потоку блоков документа (w:body) и связывает:
    1. Абзац 'Таблица X — Название' над таблицей -> TableBlock.caption_block_id
    2. Абзацы 'Продолжение таблицы X' / 'Окончание таблицы X' и разорванные части таблицы ->
       TableBlock.continuation_block_ids.
    """
    # Карта: номер таблицы ('1.1', '2') -> главная (первая) TableBlock
    primary_tables_by_num: Dict[str, TableBlock] = {}

    for idx, block in enumerate(blocks):
        if not isinstance(block, TableBlock):
            continue

        # Ищем ближайший непустой абзац строго над таблицей (пропуская случайные пустые строки-распорки)
        prev_para: Optional[ParagraphBlock] = None
        for back_idx in range(idx - 1, max(-1, idx - 4), -1):
            candidate = blocks[back_idx]
            if isinstance(candidate, ParagraphBlock):
                if candidate.is_empty_or_whitespace:
                    continue
                prev_para = candidate
                break
            else:
                break

        if prev_para is None:
            continue

        text = prev_para.text_visible.strip()

        # Случай 1: Над таблицей стоит 'Продолжение таблицы X' или 'Окончание таблицы X'
        cont_match = RE_TABLE_CONTINUATION.match(text)
        if cont_match:
            tbl_num = re.sub(r"\s+", "", cont_match.group(1)).upper()
            block.caption_block_id = prev_para.block_id
            origin_tbl = primary_tables_by_num.get(tbl_num)
            if origin_tbl is not None:
                origin_tbl.continuation_block_ids.append(prev_para.block_id)
                origin_tbl.spans_multiple_pages = True
                # Обратная ссылка: по ней потребитель JSON понимает, что это не новая таблица,
                # а продолжение таблицы N (и склеивает данные при необходимости)
                block.continuation_of_table_id = origin_tbl.block_id
                if block.page_end_est > origin_tbl.page_end_est:
                    origin_tbl.page_end_est = block.page_end_est
            continue

        # Случай 2: Над таблицей стоит основная подпись 'Таблица X — ...'
        cap_match = RE_TABLE_CAPTION.match(text)
        if cap_match:
            tbl_num = re.sub(r"\s+", "", cap_match.group(1)).upper()
            block.caption_block_id = prev_para.block_id
            primary_tables_by_num[tbl_num] = block