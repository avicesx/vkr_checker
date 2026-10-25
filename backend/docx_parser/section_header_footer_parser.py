"""Секции, колонтитулы, сноски и оценка вертикальной геометрии.

Работает в фазах 2–4: по w:sectPr определяет границы секций и их свойства (размер
листа, поля, ссылки на колонтитулы), а HybridPageTracker оценивает высоту абзацев
и строк таблиц, чтобы до появления PDF была правдоподобная пагинация.

Колонтитулы разбираются с изоляцией связей: у каждой части headerN.xml/footerN.xml
свой .rels, поэтому rId из document.xml.rels здесь не действуют.
"""
from __future__ import annotations

import math
import posixpath
import re
import zipfile
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple
from lxml import etree

from .paragraph_parser import NS_EXT, ParagraphParser
from .schema import (
    BlockUnion,
    BoxBorders,
    FootnoteItem,
    HeaderFooterContent,
    PageMarginsMm,
    ParagraphAlignment,
    ParagraphBlock,
    RunFontPropertiesEffective,
    SectionProperties,
    TableBlock,
)
from .style_resolver import (
    NS,
    StyleCascadeResolver,
    TableCascadeContext,
    dxa_to_mm,
    parse_oxml_bool,
    safe_fromstring,
    w_attr,
)
from .table_parser import TableParser
from .visual_and_math_parser import VisualAndMathParser

RE_PAGE_FIELD_INSTR = re.compile(r"^\s*PAGE\b", re.IGNORECASE)
RE_HARDCODED_PAGE_NUM = re.compile(
    r"^\s*(?:стр\.?|страница|page|p\.?)?\s*[\-\u2012\u2013\u2014]?\s*\d{1,4}\s*[\-\u2012\u2013\u2014]?\s*$",
    re.IGNORECASE,
)


@dataclass
class SectionSpanBound:
    """Границы секции в списке дочерних узлов w:body."""

    section_idx: int
    start_body_child_idx: int
    end_body_child_idx: int
    sect_pr_elem: etree._Element


class HybridPageTracker:
    """
    Отслеживает номер страницы при проходе по документу (используется как первичный трекер
    до запуска PdfPageAligner или как резервный механизм, если PDF не предоставлен).
    """

    FONT_WIDTH_FACTOR = {
        "aptos": 0.44,
        "calibri": 0.44,
        "arial": 0.48,
        "times new roman": 0.46,
        "georgia": 0.49,
        "courier new": 0.60,
        "consolas": 0.55,
    }
    PT_TO_MM = 0.352778

    def __init__(self, has_rendered_page_breaks_in_doc: bool) -> None:
        """Задает стартовую страницу и наличие w:lastRenderedPageBreak."""
        self.use_geometric_fallback = not has_rendered_page_breaks_in_doc
        self.current_page: int = 1
        self.accumulated_height_mm: float = 0.0
        self._current_printable_height_mm: float = 257.0
        self._current_text_area_width_mm: float = 165.0
        self._prev_para_style_id: Optional[str] = None
        self._prev_space_after_mm: float = 0.0

    def on_section_enter(
        self,
        section: SectionProperties,
        is_first_section: bool,
    ) -> int:
        """Переключает свойства секции и стартовую страницу."""
        self._current_printable_height_mm = max(
            50.0,
            section.page_height_mm - section.margins.top_mm - section.margins.bottom_mm,
        )
        self._current_text_area_width_mm = max(40.0, section.text_area_width_mm)

        if not is_first_section:
            if section.start_type == "nextPage":
                self.current_page += 1
                self.accumulated_height_mm = 0.0
            elif section.start_type == "evenPage":
                self.current_page += 1
                if self.current_page % 2 != 0:
                    self.current_page += 1
                self.accumulated_height_mm = 0.0
            elif section.start_type == "oddPage":
                self.current_page += 1
                if self.current_page % 2 == 0:
                    self.current_page += 1
                self.accumulated_height_mm = 0.0

        if section.page_num_start_override is not None and section.page_num_start_override > 0:
            self.current_page = section.page_num_start_override
            self.accumulated_height_mm = 0.0

        section.page_start_est = self.current_page
        self._prev_para_style_id = None
        self._prev_space_after_mm = 0.0
        return self.current_page

    def advance_after_paragraph(
        self,
        para: ParagraphBlock,
        page_after_explicit_breaks: int,
        visual_heights_mm: Optional[List[float]] = None,
    ) -> int:
        """Сдвигает текущую страницу после абзаца."""
        if page_after_explicit_breaks > self.current_page:
            self.current_page = page_after_explicit_breaks
            self.accumulated_height_mm = self._estimate_paragraph_height_mm(
                para=para,
                visual_heights_mm=visual_heights_mm,
                text_area_width_mm=self._current_text_area_width_mm,
                prev_style_id=None,
                prev_space_after_mm=0.0,
            )
            self._prev_para_style_id = para.style_id
            self._prev_space_after_mm = para.effective_props.space_after_pt * self.PT_TO_MM
            return self.current_page

        if not self.use_geometric_fallback:
            return self.current_page

        if (
            para.heading_info is not None
            and para.heading_info.is_heading
            and para.heading_info.level == 1
            and self.accumulated_height_mm > 35.0
        ):
            self.current_page += 1
            para.page_start_est = self.current_page
            para.page_end_est = self.current_page
            self.accumulated_height_mm = 0.0
            self._prev_para_style_id = None
            self._prev_space_after_mm = 0.0

        p_height = self._estimate_paragraph_height_mm(
            para=para,
            visual_heights_mm=visual_heights_mm,
            text_area_width_mm=self._current_text_area_width_mm,
            prev_style_id=self._prev_para_style_id,
            prev_space_after_mm=self._prev_space_after_mm,
        )

        if (
            self.accumulated_height_mm + p_height > self._current_printable_height_mm
            and self.accumulated_height_mm > 15.0
        ):
            overflow_mm = (self.accumulated_height_mm + p_height) - self._current_printable_height_mm
            self.current_page += 1

            if (
                para.effective_props.keep_lines
                or p_height < 25.0
                or (self._current_printable_height_mm - self.accumulated_height_mm) < 12.0
            ):
                para.page_start_est = self.current_page
                para.page_end_est = self.current_page
                self.accumulated_height_mm = p_height
            else:
                para.page_end_est = self.current_page
                self.accumulated_height_mm = overflow_mm
        else:
            self.accumulated_height_mm += p_height

        self._prev_para_style_id = para.style_id
        self._prev_space_after_mm = para.effective_props.space_after_pt * self.PT_TO_MM
        return self.current_page

    def advance_after_table(
        self,
        table: TableBlock,
        page_after_explicit_breaks: int,
    ) -> int:
        """Сдвигает текущую страницу после таблицы."""
        if page_after_explicit_breaks > self.current_page:
            self.current_page = page_after_explicit_breaks
            self.accumulated_height_mm = 20.0
            self._prev_para_style_id = None
            self._prev_space_after_mm = 0.0
            return self.current_page

        if self.use_geometric_fallback:
            table.page_start_est = self.current_page
            for row in table.rows:
                row_h = self._estimate_table_row_height_mm(row)
                if (
                    self.accumulated_height_mm + row_h > self._current_printable_height_mm
                    and self.accumulated_height_mm > 15.0
                ):
                    self.current_page += 1
                    row.has_page_break_inside_or_before = True
                    self.accumulated_height_mm = row_h
                else:
                    self.accumulated_height_mm += row_h
                row.page_start_est = self.current_page
                row.page_end_est = self.current_page

            table.page_end_est = self.current_page
            table.spans_multiple_pages = table.page_end_est > table.page_start_est
            self._prev_para_style_id = None
            self._prev_space_after_mm = 0.0

        return self.current_page

    @classmethod
    def _estimate_paragraph_height_mm(
        cls,
        para: ParagraphBlock,
        visual_heights_mm: Optional[List[float]] = None,
        text_area_width_mm: float = 165.0,
        prev_style_id: Optional[str] = None,
        prev_space_after_mm: float = 0.0,
    ) -> float:
        """Оценивает высоту абзаца по строкам и интерлиньяжу."""
        eff = para.effective_props

        if para.runs:
            total_chars = sum(max(1, len(r.visible_text)) for r in para.runs)
            font_pt = sum(
                r.effective_props.size_pt * max(1, len(r.visible_text)) for r in para.runs
            ) / total_chars
            main_run = max(para.runs, key=lambda r: len(r.visible_text))
            font_family = (main_run.effective_props.font_hansi or main_run.effective_props.font_ascii).lower()
            is_bold = main_run.effective_props.bold
        elif para.para_mark_font_effective is not None:
            font_pt = para.para_mark_font_effective.size_pt
            font_family = (para.para_mark_font_effective.font_hansi or "times new roman").lower()
            is_bold = para.para_mark_font_effective.bold
        else:
            font_pt = 14.0
            font_family = "times new roman"
            is_bold = False

        if eff.line_spacing_rule == "auto":
            single_line_mm = font_pt * cls.PT_TO_MM * 1.07 * eff.line_spacing_value
        elif eff.line_spacing_rule == "exact":
            single_line_mm = eff.line_spacing_value * cls.PT_TO_MM
        else:
            min_mm = eff.line_spacing_value * cls.PT_TO_MM
            auto_mm = font_pt * cls.PT_TO_MM * 1.07
            single_line_mm = max(min_mm, auto_mm)

        space_before_mm = eff.space_before_pt * cls.PT_TO_MM
        space_after_mm = eff.space_after_pt * cls.PT_TO_MM

        if eff.contextual_spacing and prev_style_id is not None and para.style_id == prev_style_id:
            space_before_mm = 0.0
            space_after_mm = 0.0
        elif space_before_mm > 0.0 and prev_space_after_mm > 0.0:
            space_before_mm = max(0.0, space_before_mm - min(space_before_mm, prev_space_after_mm * 0.35))

        vis_h = sum(visual_heights_mm) if visual_heights_mm else 0.0
        if para.is_empty_or_whitespace:
            return space_before_mm + single_line_mm + space_after_mm

        if para.contains_only_visuals_or_math and vis_h > 0:
            return space_before_mm + vis_h + space_after_mm

        avail_line_width_mm = max(
            35.0,
            text_area_width_mm - max(0.0, eff.indent_left_mm) - max(0.0, eff.indent_right_mm),
        )

        width_factor = cls.FONT_WIDTH_FACTOR.get(font_family, 0.45)
        if is_bold:
            width_factor *= 1.06
        if para.text_visible.isupper() and len(para.text_visible) > 5:
            width_factor *= 1.22

        avg_char_width_mm = max(1.2, font_pt * cls.PT_TO_MM * width_factor)
        chars_per_full_line = max(15.0, (avail_line_width_mm / avg_char_width_mm) * 0.94)

        first_line_indent_chars = max(0.0, eff.indent_first_line_mm) / avg_char_width_mm
        segments = para.text_visible.split("\n")
        total_lines = 0

        for seg_idx, seg in enumerate(segments):
            seg_len = len(seg.rstrip())
            if seg_len == 0:
                total_lines += 1
                continue
            effective_chars = seg_len + (first_line_indent_chars if seg_idx == 0 else 0.0)
            total_lines += max(1, math.ceil(effective_chars / chars_per_full_line))

        return space_before_mm + (total_lines * single_line_mm) + space_after_mm + vis_h

    @staticmethod
    def _estimate_table_row_height_mm(row) -> float:
        """Оценивает высоту строки таблицы по содержимому."""
        if row.height_mm is not None and row.height_mm > 0:
            return row.height_mm
        max_lines = 1
        for cell in row.cells:
            if cell.is_merged_shadow:
                continue
            col_w = cell.width_mm if (cell.width_mm and cell.width_mm > 10.0) else 40.0
            chars_per_cell_line = max(8.0, (col_w - 3.8) / 2.1)
            c_lines = max(1, math.ceil(len(cell.text_combined) / chars_per_cell_line))
            if c_lines > max_lines:
                max_lines = c_lines
        return max_lines * 4.8 + 2.0


class SectionAndHeaderFooterParser:
    """Секции, колонтитулы и сноски: свойства, содержимое и привязка к страницам."""

    def __init__(
        self,
        zf: zipfile.ZipFile,
        doc_rels_map: Dict[str, str],
        style_resolver: StyleCascadeResolver,
        paragraph_parser: ParagraphParser,
        table_parser: TableParser,
        visual_math_parser: Optional[VisualAndMathParser] = None,
        rels_loader: Optional[Callable[[str], Dict[str, str]]] = None,
    ) -> None:
        """Принимает пакет, карту связей документа и парсеры блоков."""
        self.zf = zf
        self.namelist = set(zf.namelist())
        self.doc_rels_map = doc_rels_map
        self.style_resolver = style_resolver
        self.paragraph_parser = paragraph_parser
        self.table_parser = table_parser
        # Нужен для подмены карты связей при разборе header/footer: у каждой такой части
        # свой файл word/_rels/headerN.xml.rels, и r:embed нельзя разрешать по document.xml.rels
        self.visual_math_parser = visual_math_parser
        self.rels_loader = rels_loader
        self._header_footer_cache: Dict[Tuple[str, str], HeaderFooterContent] = {}

    def discover_sections(
        self, body_elem: etree._Element
    ) -> Tuple[List[SectionProperties], List[SectionSpanBound]]:
        """Возвращает свойства секций и их границы в w:body."""
        children = list(body_elem)
        bounds: List[SectionSpanBound] = []
        start_idx = 0

        for idx, child in enumerate(children):
            if not isinstance(child.tag, str):
                continue
            local_name = etree.QName(child).localname

            if local_name == "p":
                pPr = child.find("w:pPr", namespaces=NS)
                if pPr is not None:
                    sect_pr = pPr.find("w:sectPr", namespaces=NS)
                    if sect_pr is not None:
                        bounds.append(
                            SectionSpanBound(
                                section_idx=len(bounds),
                                start_body_child_idx=start_idx,
                                end_body_child_idx=idx,
                                sect_pr_elem=sect_pr,
                            )
                        )
                        start_idx = idx + 1
            elif local_name == "sectPr":
                bounds.append(
                    SectionSpanBound(
                        section_idx=len(bounds),
                        start_body_child_idx=start_idx,
                        end_body_child_idx=idx,
                        sect_pr_elem=child,
                    )
                )
                start_idx = idx + 1

        if not bounds:
            dummy_sect = etree.Element(f"{{{NS['w']}}}sectPr")
            bounds.append(
                SectionSpanBound(
                    section_idx=0,
                    start_body_child_idx=0,
                    end_body_child_idx=max(0, len(children) - 1),
                    sect_pr_elem=dummy_sect,
                )
            )

        sections: List[SectionProperties] = []
        for b in bounds:
            prev_sec = sections[-1] if sections else None
            sec_props = self._parse_single_sect_pr(b.sect_pr_elem, b.section_idx, prev_sec)
            sections.append(sec_props)

        return sections, bounds

    def _parse_single_sect_pr(
        self,
        sect_pr: etree._Element,
        section_idx: int,
        prev_section: Optional[SectionProperties],
    ) -> SectionProperties:
        """Читает один w:sectPr: лист, поля, колонтитулы и колонки."""
        type_el = sect_pr.find("w:type", namespaces=NS)
        start_type = "nextPage"
        if type_el is not None:
            t_val = w_attr(type_el, "val")
            if t_val in ("nextPage", "continuous", "evenPage", "oddPage", "nextColumn"):
                start_type = t_val

        pg_sz = sect_pr.find("w:pgSz", namespaces=NS)
        width_mm = 210.0
        height_mm = 297.0
        orientation = "portrait"
        if pg_sz is not None:
            w_val = w_attr(pg_sz, "w")
            h_val = w_attr(pg_sz, "h")
            orient_val = w_attr(pg_sz, "orient")
            if w_val is not None:
                width_mm = round(dxa_to_mm(w_val), 1)
            if h_val is not None:
                height_mm = round(dxa_to_mm(h_val), 1)
            if orient_val in ("portrait", "landscape"):
                orientation = orient_val
            elif width_mm > height_mm:
                orientation = "landscape"

        short_side = min(width_mm, height_mm)
        long_side = max(width_mm, height_mm)
        is_a4 = (abs(short_side - 210.0) <= 2.0) and (abs(long_side - 297.0) <= 2.0)

        pg_mar = sect_pr.find("w:pgMar", namespaces=NS)
        margins = PageMarginsMm()
        if pg_mar is not None:
            for attr_key, field_name in (
                ("top", "top_mm"),
                ("bottom", "bottom_mm"),
                ("left", "left_mm"),
                ("right", "right_mm"),
                ("gutter", "gutter_mm"),
                ("header", "header_dist_mm"),
                ("footer", "footer_dist_mm"),
            ):
                raw_v = w_attr(pg_mar, attr_key)
                if raw_v is not None:
                    setattr(margins, field_name, round(abs(dxa_to_mm(raw_v)), 2))

        text_area_width_mm = round(
            max(20.0, width_mm - margins.left_mm - margins.right_mm - margins.gutter_mm), 2
        )

        v_align = "top"
        va_el = sect_pr.find("w:vAlign", namespaces=NS)
        if va_el is not None:
            va_val = w_attr(va_el, "val")
            if va_val in ("top", "center", "both", "bottom"):
                v_align = va_val

        different_first_page = parse_oxml_bool(sect_pr.find("w:titlePg", namespaces=NS)) is True

        pg_num_fmt = "decimal"
        pg_num_start: Optional[int] = None
        pg_num_el = sect_pr.find("w:pgNumType", namespaces=NS)
        if pg_num_el is not None:
            fmt_v = w_attr(pg_num_el, "fmt")
            start_v = w_attr(pg_num_el, "start")
            if fmt_v:
                pg_num_fmt = fmt_v
            if start_v is not None:
                try:
                    pg_num_start = int(start_v)
                except ValueError:
                    pass

        pg_bdr_el = sect_pr.find("w:pgBorders", namespaces=NS)
        page_borders = (
            self.style_resolver._parse_box_borders(pg_bdr_el, "direct")
            if pg_bdr_el is not None
            else BoxBorders()
        )

        headers = self._resolve_header_footer_group(
            sect_pr=sect_pr,
            ref_tag_name="headerReference",
            prev_group=prev_section.headers if prev_section else None,
            section_idx=section_idx,
            text_area_width_mm=text_area_width_mm,
            container_label="header",
        )
        footers = self._resolve_header_footer_group(
            sect_pr=sect_pr,
            ref_tag_name="footerReference",
            prev_group=prev_section.footers if prev_section else None,
            section_idx=section_idx,
            text_area_width_mm=text_area_width_mm,
            container_label="footer",
        )

        return SectionProperties(
            section_idx=section_idx,
            start_type=start_type,  # type: ignore[arg-type]
            page_width_mm=width_mm,
            page_height_mm=height_mm,
            orientation=orientation,  # type: ignore[arg-type]
            is_a4_standard=is_a4,
            margins=margins,
            text_area_width_mm=text_area_width_mm,
            v_align=v_align,  # type: ignore[arg-type]
            different_first_page=different_first_page,
            page_num_fmt=pg_num_fmt,
            page_num_start_override=pg_num_start,
            page_borders=page_borders,
            headers=headers,  # type: ignore[arg-type]
            footers=footers,  # type: ignore[arg-type]
        )

    def _resolve_header_footer_group(
        self,
        sect_pr: etree._Element,
        ref_tag_name: str,
        prev_group: Optional[Dict[str, HeaderFooterContent]],
        section_idx: int,
        text_area_width_mm: float,
        container_label: str,
    ) -> Dict[str, HeaderFooterContent]:
        """Определяет ссылки на колонтитулы с наследованием от предыдущих секций."""
        explicit_refs: Dict[str, str] = {}
        for ref_el in sect_pr.findall(f"w:{ref_tag_name}", namespaces=NS):
            hf_type = w_attr(ref_el, "type") or "default"
            r_id = ref_el.get(f"{{{NS['r']}}}id")
            if hf_type in ("default", "first", "even") and r_id:
                explicit_refs[hf_type] = r_id

        result: Dict[str, HeaderFooterContent] = {}

        for slot in ("default", "first", "even"):
            if slot in explicit_refs:
                r_id = explicit_refs[slot]
                target_rel = self.doc_rels_map.get(r_id, "")
                zip_path = self._resolve_word_zip_path(target_rel)
                parsed_hf = self._parse_header_footer_xml_file(
                    zip_path=zip_path,
                    hf_type=slot,  # type: ignore[arg-type]
                    section_idx=section_idx,
                    text_area_width_mm=text_area_width_mm,
                    container_label=container_label,
                )
                result[slot] = parsed_hf
            elif prev_group is not None and slot in prev_group:
                inherited = prev_group[slot].model_copy(deep=True)
                inherited.is_linked_to_previous = True
                result[slot] = inherited
            else:
                result[slot] = HeaderFooterContent(
                    hf_type=slot,  # type: ignore[arg-type]
                    is_linked_to_previous=(section_idx > 0),
                    is_empty=True,
                )

        return result

    def _parse_header_footer_xml_file(
        self,
        zip_path: str,
        hf_type: str,
        section_idx: int,
        text_area_width_mm: float,
        container_label: str,
    ) -> HeaderFooterContent:
        """Разбирает headerN.xml/footerN.xml по его собственному .rels."""
        cache_key = (zip_path, hf_type)
        if cache_key in self._header_footer_cache:
            cached = self._header_footer_cache[cache_key].model_copy(deep=True)
            cached.is_linked_to_previous = False
            return cached

        if not zip_path or zip_path not in self.namelist:
            empty_hf = HeaderFooterContent(
                hf_type=hf_type,  # type: ignore[arg-type]
                is_linked_to_previous=False,
                is_empty=True,
            )
            self._header_footer_cache[cache_key] = empty_hf
            return empty_hf

        root = safe_fromstring(self.zf.read(zip_path))
        prefix_id = f"sec{section_idx}_{container_label}_{hf_type}"

        # Изоляция связей части: headerN.xml.rels / footerN.xml.rels лежат рядом с самой частью
        # и содержат СВОИ r:id (обычно начинающиеся с rId1), которые конфликтуют с document.xml.rels.
        prev_rels_map: Optional[Dict[str, str]] = None
        part_rels_map: Optional[Dict[str, str]] = None
        if self.rels_loader is not None and self.visual_math_parser is not None:
            rels_path = posixpath.join(
                posixpath.dirname(zip_path),
                "_rels",
                posixpath.basename(zip_path) + ".rels",
            )
            part_rels_map = self.rels_loader(rels_path)
            if part_rels_map:
                prev_rels_map = self.visual_math_parser.doc_rels_map
                self.visual_math_parser.set_rels_map(part_rels_map)

        try:
            blocks = self._parse_container_elements(
                container_el=root,
                id_prefix=prefix_id,
                section_idx=section_idx,
                start_page=1,
                container_type=container_label,
                text_area_width_mm=text_area_width_mm,
            )
        finally:
            if prev_rels_map is not None:
                self.visual_math_parser.set_rels_map(prev_rels_map)

        all_paragraphs = self._collect_all_paragraphs_recursive(blocks)
        has_non_empty_text = any(not p.is_empty_or_whitespace for p in all_paragraphs)
        has_visuals = any(bool(p.embedded_visual_ids) for p in all_paragraphs)
        has_tables = any(isinstance(b, TableBlock) for b in blocks)
        is_empty = not (has_non_empty_text or has_visuals or has_tables)

        has_eskd_stamp = has_visuals or any(
            isinstance(b, TableBlock) and len(b.rows) >= 3 for b in blocks
        )

        has_page_field = False
        has_hardcoded_number = False
        page_num_alignment: Optional[ParagraphAlignment] = None
        page_num_font: Optional[RunFontPropertiesEffective] = None
        page_num_affixes: Optional[str] = None

        for para in all_paragraphs:
            page_runs = [
                r
                for r in para.runs
                if r.in_field and r.field_instr and RE_PAGE_FIELD_INSTR.match(r.field_instr)
            ]
            if page_runs:
                has_page_field = True
                target_run = page_runs[0]
                page_num_font = target_run.effective_props
                page_num_alignment = para.effective_props.alignment

                non_page_text = "".join(
                    r.visible_text for r in para.runs if r not in page_runs
                ).strip()
                if non_page_text:
                    page_num_affixes = non_page_text
                break

        if not has_page_field:
            for para in all_paragraphs:
                txt = para.text_visible.strip()
                if txt and RE_HARDCODED_PAGE_NUM.match(txt):
                    has_hardcoded_number = True
                    page_num_alignment = para.effective_props.alignment
                    if para.runs:
                        page_num_font = para.runs[0].effective_props
                    break

        hf_content = HeaderFooterContent(
            hf_type=hf_type,  # type: ignore[arg-type]
            is_linked_to_previous=False,
            is_empty=is_empty,
            has_page_field=has_page_field,
            has_hardcoded_number=has_hardcoded_number,
            page_number_alignment=page_num_alignment,
            page_number_font_effective=page_num_font,
            page_number_affixes=page_num_affixes,
            has_shapes_or_stamps=has_eskd_stamp,
            blocks=blocks,
        )

        self._header_footer_cache[cache_key] = hf_content
        return hf_content

    def parse_footnotes_and_endnotes(
        self,
        body_blocks: List[BlockUnion],
        default_text_width_mm: float = 165.0,
    ) -> List[FootnoteItem]:
        """Разбирает сноски и концевые сноски и связывает их с абзацами."""
        ref_lookup: Dict[str, Tuple[str, int]] = {}
        for p in self._collect_all_paragraphs_recursive(body_blocks):
            for fn_id in p.footnote_ids:
                if fn_id not in ref_lookup:
                    ref_lookup[fn_id] = (p.block_id, p.page_start_est)

        items: List[FootnoteItem] = []

        for xml_path, tag_name, kind_label in (
            ("word/footnotes.xml", "footnote", "footnote"),
            ("word/endnotes.xml", "endnote", "endnote"),
        ):
            if xml_path not in self.namelist:
                continue

            root = safe_fromstring(self.zf.read(xml_path))
            for note_el in root.findall(f"w:{tag_name}", namespaces=NS):
                note_id = w_attr(note_el, "id")
                note_type = w_attr(note_el, "type")
                if note_type in ("separator", "continuationSeparator", "continuationNotice"):
                    continue
                if note_id is None:
                    continue
                try:
                    if int(note_id) <= 0:
                        continue
                except ValueError:
                    pass

                ref_block_id, ref_page = ref_lookup.get(note_id, (None, None))
                prefix = f"{kind_label}_{note_id}"

                note_blocks = self._parse_container_elements(
                    container_el=note_el,
                    id_prefix=prefix,
                    section_idx=0,
                    start_page=ref_page or 1,
                    container_type=kind_label,
                    text_area_width_mm=default_text_width_mm,
                )

                items.append(
                    FootnoteItem(
                        footnote_id=note_id,
                        kind=kind_label,  # type: ignore[arg-type]
                        referenced_in_block_id=ref_block_id,
                        page_est=ref_page,
                        blocks=note_blocks,
                    )
                )

        return items

    def _parse_container_elements(
        self,
        container_el: etree._Element,
        id_prefix: str,
        section_idx: int,
        start_page: int,
        container_type: str,
        text_area_width_mm: float,
    ) -> List[BlockUnion]:
        """Разбирает блоки контейнера (абзацы и таблицы) с локальными id."""
        blocks: List[BlockUnion] = []
        seq = 0
        cur_page = start_page

        def _child_cb(
            el: etree._Element,
            b_id: str,
            sec_i: int,
            pg: int,
            t_ctx: TableCascadeContext,
        ) -> Tuple[BlockUnion, int]:
            tag = etree.QName(el).localname
            if tag == "tbl":
                return self.table_parser.parse_table(
                    el, b_id, sec_i, pg, text_area_width_mm, _child_cb
                )
            return self.paragraph_parser.parse_paragraph(
                el, b_id, sec_i, pg, container_type="table_cell", tbl_ctx=t_ctx
            )

        for child in self._iter_unwrapped_block_nodes(container_el):
            local_name = etree.QName(child).localname
            if local_name == "p":
                b_id = f"{id_prefix}_p{seq}"
                seq += 1
                p_block, cur_page = self.paragraph_parser.parse_paragraph(
                    child, b_id, section_idx, cur_page, container_type=container_type
                )
                blocks.append(p_block)
            elif local_name == "tbl":
                b_id = f"{id_prefix}_tbl{seq}"
                seq += 1
                t_block, cur_page = self.table_parser.parse_table(
                    child, b_id, section_idx, cur_page, text_area_width_mm, _child_cb
                )
                blocks.append(t_block)

        return blocks

    @classmethod
    def _iter_unwrapped_block_nodes(cls, parent_el: etree._Element):
        """Разворачивает обертки w:sdt и w:customXml до блоков."""
        for child in parent_el:
            if not isinstance(child.tag, str):
                continue
            local_name = etree.QName(child).localname
            if local_name in ("p", "tbl"):
                yield child
            elif local_name == "sdt":
                sdt_content = child.find("w:sdtContent", namespaces=NS)
                if sdt_content is not None:
                    yield from cls._iter_unwrapped_block_nodes(sdt_content)
            elif local_name == "AlternateContent":
                choice = child.find("mc:Choice", namespaces=NS_EXT)
                branch = choice if choice is not None else child.find("mc:Fallback", namespaces=NS_EXT)
                if branch is not None:
                    yield from cls._iter_unwrapped_block_nodes(branch)

    @classmethod
    def _collect_all_paragraphs_recursive(
        cls, blocks: List[BlockUnion]
    ) -> List[ParagraphBlock]:
        """Собирает абзацы рекурсивно, включая вложенные таблицы."""
        result: List[ParagraphBlock] = []
        for b in blocks:
            if isinstance(b, ParagraphBlock):
                result.append(b)
            elif isinstance(b, TableBlock):
                for row in b.rows:
                    for cell in row.cells:
                        if not cell.is_merged_shadow:
                            result.extend(cls._collect_all_paragraphs_recursive(cell.blocks))
        return result

    @staticmethod
    def _resolve_word_zip_path(target_rel: str) -> str:
        """Приводит Target связи к пути внутри пакета."""
        if not target_rel:
            return ""
        clean = target_rel.lstrip("/")
        if clean.startswith("word/"):
            return posixpath.normpath(clean)
        return posixpath.normpath(posixpath.join("word", clean))