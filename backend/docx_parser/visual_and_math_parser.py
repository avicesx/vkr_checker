"""Визуальные объекты и формулы: картинки, фигуры, диаграммы, OLE, OMML.

Вызывается из ParagraphParser при обходе ранов и регистрирует найденное
в реестрах визуальных объектов и формул.

Разбирает DrawingML (pic, wps/wpg, диаграммы), устаревший VML и OLE-объекты,
текстовые рамки внутри фигур, а также OMML-формулы: структуру, шрифты
и типографские нарушения в математических токенах.
"""
from __future__ import annotations

import posixpath
import re
import struct
import zipfile
from typing import Dict, List, Optional, Set, Tuple
from lxml import etree

from .paragraph_parser import NS_EXT, ParagraphParser
from .schema import (
    BorderEdge,
    ChartDetails,
    ColorValue,
    EquationObject,
    MathTokenStyleIssue,
    ParagraphBlock,
    RasterImageDetails,
    ShapeTextboxElement,
    VisualObject,
)
from .style_resolver import NS, eighths_to_pt, safe_fromstring, safe_int, w_attr

NS_VM = {
    **NS_EXT,
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "wps": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingShape",
    "wpg": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingGroup",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "v": "urn:schemas-microsoft-com:vml",
}

RE_CYRILLIC_TOKEN = re.compile(r"[А-Яа-яЁё]+")
RE_MATH_STD_FUNCS = re.compile(
    r"\b(?:sin|cos|tg|ctg|tan|cot|arcsin|arccos|arctg|sh|ch|th|ln|lg|log|exp|lim|max|min|sup|inf|det|mod|const)\b"
)


class VisualAndMathParser:
    """
    Парсер графики (w:drawing, w:pict, c:chart, wps:wsp)
    и математических формул (m:oMathPara, m:oMath, OLE MathType).
    """

    def __init__(
        self,
        zf: zipfile.ZipFile,
        doc_rels_map: Dict[str, str],
        paragraph_parser: ParagraphParser,
    ) -> None:
        """Принимает пакет, парсер абзацев и ширину текстовой области."""
        self.zf = zf
        self.namelist = set(zf.namelist())
        self.doc_rels_map = doc_rels_map
        self.paragraph_parser = paragraph_parser

        self.visuals_registry: Dict[str, VisualObject] = {}
        self.equations_registry: List[EquationObject] = []

        self._visual_counter = 0
        self._equation_counter = 0
        self.active_text_area_width_mm: float = 165.0
        self._ole_prog_id_cache: Dict[str, Optional[str]] = {}

    def set_rels_map(self, rels_map: Dict[str, str]) -> None:
        """
        Подменяет карту связей текущей XML-части.

        У headerN.xml/footerN.xml/footnotes.xml свои *.xml.rels, и одинаковые rId в них
        указывают на разные файлы. Перед разбором такой части карта переключается,
        после — восстанавливается исходная карта document.xml.
        """
        self.doc_rels_map = rels_map

    # -----------------------------------------------------------------
    # Колбэки для ParagraphParser и пост-обогащение из родительского абзаца
    # -----------------------------------------------------------------

    def register_visual_from_callback(
        self,
        elem: etree._Element,
        parent_block_id: str,
        section_idx: int,
        page_est: int,
    ) -> Optional[str]:
        """Регистрирует визуальный объект, найденный при обходе рана."""
        vis = self.parse_visual_drawing(
            drawing_elem=elem,
            parent_block_id=parent_block_id,
            section_idx=section_idx,
            page_est=page_est,
            text_area_width_mm=self.active_text_area_width_mm,
        )
        if vis is not None:
            self.visuals_registry[vis.visual_id] = vis
            return vis.visual_id
        return None

    def resolve_ole_prog_id(self, ole_el: etree._Element) -> Optional[str]:
        """
        Восстанавливает ProgID OLE-объекта по его embedding-файлу.

        LibreOffice при конвертации .doc в .docx оставляет OLE-объекты без ProgID,
        поэтому формула MathType/Equation иначе классифицируется как картинка.
        """
        r_id = ole_el.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = self.doc_rels_map.get(r_id) if r_id else None
        if not target:
            return None
        if target.startswith("/"):
            target = target.lstrip("/")
        elif not target.startswith("word/"):
            target = f"word/{target}"
        if target in self._ole_prog_id_cache:
            return self._ole_prog_id_cache[target]
        prog_id = None
        if target in self.namelist:
            prog_id = self._sniff_equation_prog_id(self.zf.read(target))
        self._ole_prog_id_cache[target] = prog_id
        return prog_id

    @staticmethod
    def _sniff_equation_prog_id(raw: bytes) -> Optional[str]:
        """Ищет в бинарном OLE-контейнере маркеры Equation/MathType (ASCII и UTF-16)."""
        markers = (
            b"MathType", b"Microsoft Equation", b"Equation Native",
            "Equation".encode("utf-16-le"), "MathType".encode("utf-16-le"),
        )
        for marker in markers:
            if marker in raw:
                return "Equation.DSMT4"
        return None

    def register_equation_from_callback(
        self,
        elem: etree._Element,
        parent_block_id: str,
        section_idx: int,
        page_est: int,
    ) -> Optional[str]:
        """Регистрирует формулу, найденную при обходе рана."""
        eq = self.parse_equation_math(
            math_elem=elem,
            parent_block_id=parent_block_id,
            section_idx=section_idx,
            page_est=page_est,
        )
        self.equations_registry.append(eq)
        return eq.equation_id

    def attach_parent_paragraph_context(self, para: ParagraphBlock) -> None:
        """
        Вызывается сразу после сборки ParagraphBlock, чтобы записать в привязанные
        VisualObject и EquationObject точные вычисленные свойства родительского абзаца.
        """
        eff = para.effective_props
        for vis_id in para.embedded_visual_ids:
            vis = self.visuals_registry.get(vis_id)
            if vis is not None:
                vis.parent_para_alignment = eff.alignment
                vis.parent_para_first_line_indent_mm = eff.indent_first_line_mm
                vis.parent_para_line_spacing_rule = eff.line_spacing_rule
                vis.parent_para_keep_next = eff.keep_next
                vis.page_est = para.page_start_est

        if para.embedded_equation_ids:
            eq_map = {e.equation_id: e for e in self.equations_registry}
            for eq_id in para.embedded_equation_ids:
                eq = eq_map.get(eq_id)
                if eq is not None:
                    eq.parent_para_first_line_indent_mm = eff.indent_first_line_mm
                    eq.page_est = para.page_start_est
                    if eff.alignment in ("left", "center", "right"):
                        eq.alignment = eff.alignment  # type: ignore[assignment]

    # -----------------------------------------------------------------
    # 1. ПАРСИНГ ГРАФИЧЕСКИХ ОБЪЕКТОВ (w:drawing, w:pict, w:object)
    # -----------------------------------------------------------------

    def parse_visual_drawing(
        self,
        drawing_elem: etree._Element,
        parent_block_id: str,
        section_idx: int,
        page_est: int,
        text_area_width_mm: float = 165.0,
    ) -> Optional[VisualObject]:
        """Разбирает w:drawing или w:pict в VisualObject."""
        self._visual_counter += 1
        vis_id = f"vis_{self._visual_counter:04d}"

        unwrapped = self._unwrap_alternate_content(drawing_elem)
        local_tag = etree.QName(unwrapped).localname

        inline_el = unwrapped.find(".//wp:inline", namespaces=NS_VM)
        anchor_el = unwrapped.find(".//wp:anchor", namespaces=NS_VM)
        placement_node = inline_el if inline_el is not None else anchor_el

        if placement_node is None:
            if local_tag in ("pict", "object") or unwrapped.find(".//v:shape", namespaces=NS_VM) is not None:
                return self._parse_vml_or_ole_legacy(
                    unwrapped, vis_id, parent_block_id, section_idx, page_est, local_tag
                )
            return None

        placement = "inline" if inline_el is not None else "anchor_floating"
        wrap_type = "inline"
        horiz_rel: Optional[str] = None
        horiz_align: Optional[str] = None

        if anchor_el is not None:
            for w_tag_name, w_label in (
                ("wrapNone", "none"),
                ("wrapSquare", "square"),
                ("wrapTight", "tight"),
                ("wrapThrough", "through"),
                ("wrapTopAndBottom", "topAndBottom"),
            ):
                if anchor_el.find(f"wp:{w_tag_name}", namespaces=NS_VM) is not None:
                    wrap_type = w_label
                    break

            pos_h = anchor_el.find("wp:positionH", namespaces=NS_VM)
            if pos_h is not None:
                horiz_rel = pos_h.get("relativeFrom")
                align_el = pos_h.find("wp:align", namespaces=NS_VM)
                offset_el = pos_h.find("wp:posOffset", namespaces=NS_VM)
                if align_el is not None and align_el.text:
                    horiz_align = align_el.text.strip()
                elif offset_el is not None and offset_el.text:
                    offset_emu = safe_int(offset_el.text, 0) or 0
                    horiz_align = f"{round(offset_emu / 360000.0, 2)}mm"

        # Габариты на листе в EMU (1 мм = 36 000 EMU)
        extent_el = placement_node.find("wp:extent", namespaces=NS_VM)
        cx = safe_int(extent_el.get("cx"), 0) if extent_el is not None else 0
        cy = safe_int(extent_el.get("cy"), 0) if extent_el is not None else 0
        width_mm = round(cx / 36000.0, 2)
        height_mm = round(cy / 36000.0, 2)
        exceeds_margins = width_mm > (text_area_width_mm + 1.5)

        graphic_data = placement_node.find(".//a:graphicData", namespaces=NS_VM)
        search_root = graphic_data if graphic_data is not None else placement_node

        visual_kind = "vector_shape"
        raster_details: Optional[RasterImageDetails] = None
        chart_details: Optional[ChartDetails] = None
        shapes_list: List[ShapeTextboxElement] = []
        has_border_frame = False
        border_edge: Optional[BorderEdge] = None

        pic_el = search_root.find(".//pic:pic", namespaces=NS_VM)
        chart_el = search_root.find(".//c:chart", namespaces=NS_VM)
        dgm_el = search_root.find(".//dgm:relIds", namespaces=NS_VM)
        wgp_el = search_root.find(".//wpg:wgp", namespaces=NS_VM)
        wsp_el = search_root.find(".//wps:wsp", namespaces=NS_VM)

        if pic_el is not None:
            visual_kind = "raster_image"
            raster_details = self._parse_raster_image_details(pic_el, width_mm, height_mm)
            border_edge = self._extract_drawingml_outline(pic_el.find("pic:spPr", namespaces=NS_VM))
            has_border_frame = border_edge is not None and border_edge.style != "none"

        elif chart_el is not None:
            visual_kind = "chart"
            c_r_id = chart_el.get(f"{{{NS['r']}}}id")
            chart_details = self._parse_chart_details(c_r_id)

        elif dgm_el is not None:
            visual_kind = "smart_art"

        elif wgp_el is not None or wsp_el is not None:
            visual_kind = "shape_group_canvas" if wgp_el is not None else "vector_shape"
            shapes_list = self._parse_shapes_and_textboxes(search_root, vis_id, section_idx, page_est)

        return VisualObject(
            visual_id=vis_id,
            parent_block_id=parent_block_id,
            section_idx=section_idx,
            page_est=page_est,
            visual_kind=visual_kind,  # type: ignore[arg-type]
            placement=placement,  # type: ignore[arg-type]
            wrap_type=wrap_type,  # type: ignore[arg-type]
            horizontal_relative_from=horiz_rel,
            horizontal_align_anchor=horiz_align,
            width_mm=width_mm,
            height_mm=height_mm,
            exceeds_page_margins=exceeds_margins,
            has_visible_border_frame=has_border_frame,
            border_frame=border_edge,
            image_details=raster_details,
            chart_details=chart_details,
            shapes=shapes_list,
        )

    def _parse_raster_image_details(
        self, pic_el: etree._Element, display_w_mm: float, display_h_mm: float
    ) -> Optional[RasterImageDetails]:
        """Читает размеры, DPI, обрезку и файл растрового изображения."""
        blip = pic_el.find(".//a:blip", namespaces=NS_VM)
        if blip is None:
            return None
        # В OOXML у <a:blip> связь с медиафайлом хранится в r:embed (встроенная картинка)
        # или r:link (внешняя ссылка). r:id встречается только в самодельных генераторах,
        # поэтому оставлен как последний fallback.
        embed_r_id = (
            blip.get(f"{{{NS['r']}}}embed")
            or blip.get(f"{{{NS['r']}}}link")
            or blip.get(f"{{{NS['r']}}}id")
        )
        target_rel = self.doc_rels_map.get(embed_r_id or "", "")
        media_path = self._resolve_word_zip_path(target_rel)

        # Обрезка в тысячных долях процента (1000 = 1%)
        src_rect = pic_el.find(".//a:srcRect", namespaces=NS_VM)
        crop_t = round((safe_int(src_rect.get("t"), 0) or 0) / 1000.0, 2) if src_rect is not None else 0.0
        crop_b = round((safe_int(src_rect.get("b"), 0) or 0) / 1000.0, 2) if src_rect is not None else 0.0
        crop_l = round((safe_int(src_rect.get("l"), 0) or 0) / 1000.0, 2) if src_rect is not None else 0.0
        crop_r = round((safe_int(src_rect.get("r"), 0) or 0) / 1000.0, 2) if src_rect is not None else 0.0

        px_w, px_h = 0, 0
        content_type = self._guess_image_mime(media_path)
        if media_path and media_path in self.namelist:
            file_bytes = self.zf.read(media_path)
            px_w, px_h = self._get_image_pixel_dimensions(file_bytes, content_type)

        dpi_x: Optional[float] = None
        dpi_y: Optional[float] = None
        is_distorted = False

        if px_w > 0 and px_h > 0 and display_w_mm > 0 and display_h_mm > 0:
            net_px_w = px_w * max(0.05, 1.0 - (crop_l + crop_r) / 100.0)
            net_px_h = px_h * max(0.05, 1.0 - (crop_t + crop_b) / 100.0)
            dpi_x = round(net_px_w / (display_w_mm / 25.4), 1)
            dpi_y = round(net_px_h / (display_h_mm / 25.4), 1)

            src_ratio = net_px_w / net_px_h
            disp_ratio = display_w_mm / display_h_mm
            rel_diff = abs(src_ratio - disp_ratio) / max(src_ratio, 0.001)
            is_distorted = rel_diff > 0.035  # Искажение пропорций более чем на 3.5%

        return RasterImageDetails(
            media_target=media_path,
            content_type=content_type,
            pixel_width=px_w if px_w > 0 else None,
            pixel_height=px_h if px_h > 0 else None,
            effective_dpi_x=dpi_x,
            effective_dpi_y=dpi_y,
            is_aspect_ratio_distorted=is_distorted,
            crop_top_pct=crop_t,
            crop_bottom_pct=crop_b,
            crop_left_pct=crop_l,
            crop_right_pct=crop_r,
        )

    def _parse_chart_details(self, c_r_id: Optional[str]) -> Optional[ChartDetails]:
        """Читает тип диаграммы, серии и подписи."""
        if not c_r_id:
            return None
        target_rel = self.doc_rels_map.get(c_r_id, "")
        chart_path = self._resolve_word_zip_path(target_rel)
        if not chart_path or chart_path not in self.namelist:
            return ChartDetails(chart_rel_id=c_r_id)

        root = safe_fromstring(self.zf.read(chart_path))
        chart_types: List[str] = []
        plot_area = root.find(".//c:plotArea", namespaces=NS_VM)
        if plot_area is not None:
            for ch in plot_area:
                if isinstance(ch.tag, str) and etree.QName(ch).localname.endswith("Chart"):
                    chart_types.append(etree.QName(ch).localname)

        # Заголовок внутри самой диаграммы (c:title)
        title_el = root.find(".//c:chart/c:title", namespaces=NS_VM)
        del_title = root.find(".//c:chart/c:autoTitleDeleted", namespaces=NS_VM)
        is_deleted = del_title is not None and del_title.get("val") in ("1", "true")

        has_title = False
        title_text: Optional[str] = None
        if title_el is not None and not is_deleted:
            t_nodes = title_el.xpath(".//a:t/text()", namespaces=NS_VM)
            joined = "".join(t_nodes).strip()
            if joined:
                has_title = True
                title_text = joined

        # Извлечение шрифтов и размеров (в DrawingML @sz хранится в сотых долях пункта: 1000 = 10 pt)
        fonts: Set[str] = set()
        sizes_pt: Set[float] = set()
        for rpr in root.xpath(".//a:defRPr | .//a:rPr", namespaces=NS_VM):
            sz_val = rpr.get("sz")
            if sz_val and sz_val.isdigit():
                sizes_pt.add(round(int(sz_val) / 100.0, 1))
            for f_tag in ("a:latin", "a:cs", "a:ea"):
                f_el = rpr.find(f_tag, namespaces=NS_VM)
                if f_el is not None and f_el.get("typeface"):
                    tf = f_el.get("typeface", "")
                    if not tf.startswith("+"):
                        fonts.add(tf)

        return ChartDetails(
            chart_rel_id=c_r_id,
            chart_type=chart_types,
            has_internal_title=has_title,
            internal_title_text=title_text,
            detected_fonts=sorted(fonts),
            detected_font_sizes_pt=sorted(sizes_pt),
        )

    def _parse_shapes_and_textboxes(
        self, root_el: etree._Element, vis_id: str, section_idx: int, page_est: int
    ) -> List[ShapeTextboxElement]:
        """
        Извлекает все фигуры (wps:wsp) внутри полотна/группы, их размеры, цвета заливки/линий
        и парсит все абзацы внутри <w:txbxContent>.
        """
        shapes: List[ShapeTextboxElement] = []
        shape_seq = 0

        for wsp in root_el.xpath(".//wps:wsp", namespaces=NS_VM):
            shape_seq += 1
            cnv = wsp.find("wps:nvSpPr/wps:cNvPr", namespaces=NS_VM)
            shape_id = cnv.get("id", str(shape_seq)) if cnv is not None else str(shape_seq)

            sp_pr = wsp.find("wps:spPr", namespaces=NS_VM)
            geom_preset: Optional[str] = None
            w_mm: Optional[float] = None
            h_mm: Optional[float] = None
            fill_color: Optional[ColorValue] = None
            stroke_color: Optional[ColorValue] = None
            stroke_w_pt: Optional[float] = None

            if sp_pr is not None:
                prst = sp_pr.find("a:prstGeom", namespaces=NS_VM)
                if prst is not None:
                    geom_preset = prst.get("prst")

                ext = sp_pr.find("a:xfrm/a:ext", namespaces=NS_VM)
                if ext is not None:
                    if ext.get("cx"):
                        w_mm = round((safe_int(ext.get("cx"), 0) or 0) / 36000.0, 2)
                    if ext.get("cy"):
                        h_mm = round((safe_int(ext.get("cy"), 0) or 0) / 36000.0, 2)

                solid_fill = sp_pr.find("a:solidFill/a:srgbClr", namespaces=NS_VM)
                if solid_fill is not None and solid_fill.get("val"):
                    fill_color = ColorValue(
                        is_auto=False, rgb_hex=solid_fill.get("val", "").upper(), source="direct"
                    )

                edge = self._extract_drawingml_outline(sp_pr)
                if edge is not None and edge.style != "none":
                    stroke_color = edge.color
                    stroke_w_pt = edge.width_pt

            # Вертикальное центрирование текста внутри фигуры (wps:bodyPr/@anchor)
            body_pr = wsp.find("wps:bodyPr", namespaces=NS_VM)
            v_anchor = "unknown"
            if body_pr is not None:
                anc = body_pr.get("anchor")
                v_anchor = {"t": "top", "ctr": "center", "b": "bottom"}.get(anc or "", "unknown")

            # Текст фигуры хранится в w:txbxContent (пространство имён w:, не wps:)
            txbx = wsp.find(".//w:txbxContent", namespaces=NS_VM)
            inner_paras: List[ParagraphBlock] = []
            if txbx is not None:
                p_seq = 0
                for p_el in txbx.findall("w:p", namespaces=NS_VM):
                    p_seq += 1
                    p_id = f"{vis_id}_sh{shape_seq}_p{p_seq}"
                    p_block, _ = self.paragraph_parser.parse_paragraph(
                        p_el, p_id, section_idx, page_est, container_type="textbox"
                    )
                    inner_paras.append(p_block)

            shapes.append(
                ShapeTextboxElement(
                    shape_id=f"sh_{shape_id}",
                    geometry_preset=geom_preset,
                    width_mm=w_mm,
                    height_mm=h_mm,
                    fill_color=fill_color,
                    stroke_color=stroke_color,
                    stroke_width_pt=stroke_w_pt,
                    vertical_anchor=v_anchor,  # type: ignore[arg-type]
                    has_text=bool(inner_paras),
                    paragraphs=inner_paras,
                )
            )

        return shapes

    def _parse_vml_or_ole_legacy(
        self,
        elem: etree._Element,
        vis_id: str,
        parent_block_id: str,
        section_idx: int,
        page_est: int,
        local_tag: str,
    ) -> VisualObject:
        """Разбирает устаревшие VML-фигуры и OLE-объекты."""
        shape_el = elem.find(".//v:shape", namespaces=NS_VM)
        if shape_el is None:
            # В старых файлах вместо v:shape встречаются v:rect/v:roundrect/v:group
            shape_el = elem.find(".//v:rect", namespaces=NS_VM)
        style_str = shape_el.get("style", "") if shape_el is not None else ""
        w_mm, h_mm = 40.0, 40.0

        has_visible_frame = False
        if shape_el is not None:
            style_norm = style_str.replace(" ", "").lower()
            stroke_color_attr = shape_el.get("strokecolor")
            stroke_weight = shape_el.get("strokeweight")
            has_visible_frame = (
                "stroke:none" not in style_norm
                and (bool(stroke_color_attr) or bool(stroke_weight))
            )

        for prop, is_w in (("width", True), ("height", False)):
            m = re.search(rf"{prop}\s*:\s*([0-9.]+)\s*(pt|px|cm|mm|in)", style_str, re.I)
            if m:
                val, unit = float(m.group(1)), m.group(2).lower()
                mm_val = {
                    "pt": val * 0.3528,
                    "px": val * 0.2646,
                    "cm": val * 10.0,
                    "in": val * 25.4,
                    "mm": val,
                }.get(unit, val)
                if is_w:
                    w_mm = mm_val
                else:
                    h_mm = mm_val

        # Проверяем текстовые поля внутри старых VML-фигур (v:textbox/w:txbxContent)
        shapes: List[ShapeTextboxElement] = []
        txbx = elem.find(".//w:txbxContent", namespaces=NS_VM)
        if txbx is not None:
            paras: List[ParagraphBlock] = []
            for idx, p_el in enumerate(txbx.findall("w:p", namespaces=NS_VM), start=1):
                p_block, _ = self.paragraph_parser.parse_paragraph(
                    p_el, f"{vis_id}_vml_p{idx}", section_idx, page_est, container_type="textbox"
                )
                paras.append(p_block)
            shapes.append(
                ShapeTextboxElement(
                    shape_id=f"{vis_id}_vml_sh1",
                    geometry_preset="vml_rect",
                    width_mm=round(w_mm, 2),
                    height_mm=round(h_mm, 2),
                    has_text=bool(paras),
                    paragraphs=paras,
                )
            )

        return VisualObject(
            visual_id=vis_id,
            parent_block_id=parent_block_id,
            section_idx=section_idx,
            page_est=page_est,
            visual_kind="ole_object" if local_tag == "object" else "vml_legacy",
            placement="anchor_floating" if "position:absolute" in style_str.replace(" ", "") else "inline",
            width_mm=round(w_mm, 2),
            height_mm=round(h_mm, 2),
            exceeds_page_margins=round(w_mm, 2) > (self.active_text_area_width_mm + 1.5),
            has_visible_border_frame=has_visible_frame,
            shapes=shapes,
        )

    @staticmethod
    def _extract_drawingml_outline(sp_pr: Optional[etree._Element]) -> Optional[BorderEdge]:
        """Читает обводку фигуры DrawingML."""
        if sp_pr is None:
            return None
        ln = sp_pr.find("a:ln", namespaces=NS_VM)
        if ln is None or ln.find("a:noFill", namespaces=NS_VM) is not None:
            return None
        w_emu = safe_int(ln.get("w"), 9525) or 9525  # 1 pt = 12700 EMU
        w_pt = round(w_emu / 12700.0, 2)
        srgb = ln.find("a:solidFill/a:srgbClr", namespaces=NS_VM)
        color = (
            ColorValue(is_auto=False, rgb_hex=srgb.get("val", "").upper(), source="direct")
            if (srgb is not None and srgb.get("val"))
            else ColorValue(is_auto=True, source="direct")
        )
        return BorderEdge(style="single", width_pt=w_pt, color=color)

    # -----------------------------------------------------------------
    # 2. ПАРСИНГ И АУДИТ МАТЕМАТИЧЕСКИХ ФОРМУЛ (EquationObject)
    # -----------------------------------------------------------------

    def parse_equation_math(
        self,
        math_elem: etree._Element,
        parent_block_id: str,
        section_idx: int,
        page_est: int,
    ) -> EquationObject:
        """Разбирает m:oMath в EquationObject: структуру, текст и шрифты."""
        self._equation_counter += 1
        eq_id = f"eq_{self._equation_counter:04d}"

        local_tag = etree.QName(math_elem).localname
        if local_tag == "object":
            return EquationObject(
                equation_id=eq_id,
                parent_block_id=parent_block_id,
                section_idx=section_idx,
                page_est=page_est,
                representation_kind="ole_mathtype_eq3",
                is_standalone_line=True,
                text_linearized="[OLE MathType Equation]",
            )

        is_standalone = local_tag == "oMathPara"
        m_align = "center"
        if is_standalone:
            jc_el = math_elem.find("m:oMathParaPr/m:jc", namespaces=NS_VM)
            if jc_el is not None:
                m_val = jc_el.get(f"{{{NS_VM['m']}}}val") or jc_el.get("val")
                if m_val in ("left", "right", "center", "centerGroup"):
                    m_align = m_val

        fonts_set: Set[str] = set()
        sizes_list: List[float] = []
        style_issues: List[MathTokenStyleIssue] = []

        linear_text = self._serialize_omml_node(
            math_elem,
            fonts_set=fonts_set,
            sizes_list=sizes_list,
            style_issues=style_issues,
            in_function_name=False,
        ).strip()

        base_sz = sizes_list[0] if sizes_list else None

        return EquationObject(
            equation_id=eq_id,
            parent_block_id=parent_block_id,
            section_idx=section_idx,
            page_est=page_est,
            representation_kind="omml_para" if is_standalone else "omml_inline",
            is_standalone_line=is_standalone,
            text_linearized=linear_text,
            alignment=m_align,  # type: ignore[arg-type]
            fonts_detected=sorted(fonts_set),
            base_size_pt=base_sz,
            style_issues=style_issues,
        )

    def _serialize_omml_node(
        self,
        node: etree._Element,
        fonts_set: Set[str],
        sizes_list: List[float],
        style_issues: List[MathTokenStyleIssue],
        in_function_name: bool = False,
    ) -> str:
        """
        Рекурсивный структурный обход дерева OMML:
        - Строит корректную скобочную запись для дробей (a)/(b), степеней x^(y), индексов x_(i), корней sqrt(...);
        - Проверяет начертание каждого математического рана <m:r> на ошибки курсива кириллицы и функций.
        """
        if not isinstance(node.tag, str):
            return ""
        tag = etree.QName(node).localname

        if tag == "r":
            return self._process_omml_run(
                node, fonts_set, sizes_list, style_issues, in_function_name
            )

        if tag == "f":  # Дробь: (числитель)/(знаменатель)
            num_el = node.find("m:num", namespaces=NS_VM)
            den_el = node.find("m:den", namespaces=NS_VM)
            num_s = self._serialize_omml_node(num_el, fonts_set, sizes_list, style_issues) if num_el is not None else ""
            den_s = self._serialize_omml_node(den_el, fonts_set, sizes_list, style_issues) if den_el is not None else ""
            return f"({num_s})/({den_s})"

        if tag == "sSup":  # Верхний индекс / степень: base^(sup)
            e_el = node.find("m:e", namespaces=NS_VM)
            sup_el = node.find("m:sup", namespaces=NS_VM)
            b_s = self._serialize_omml_node(e_el, fonts_set, sizes_list, style_issues) if e_el is not None else ""
            s_s = self._serialize_omml_node(sup_el, fonts_set, sizes_list, style_issues) if sup_el is not None else ""
            return f"{b_s}^({s_s})"

        if tag == "sSub":  # Нижний индекс: base_(sub)
            e_el = node.find("m:e", namespaces=NS_VM)
            sub_el = node.find("m:sub", namespaces=NS_VM)
            b_s = self._serialize_omml_node(e_el, fonts_set, sizes_list, style_issues) if e_el is not None else ""
            sub_s = self._serialize_omml_node(sub_el, fonts_set, sizes_list, style_issues) if sub_el is not None else ""
            return f"{b_s}_({sub_s})"

        if tag == "sSubSup":  # Одновременно нижний и верхний индекс
            e_el = node.find("m:e", namespaces=NS_VM)
            sub_el = node.find("m:sub", namespaces=NS_VM)
            sup_el = node.find("m:sup", namespaces=NS_VM)
            b_s = self._serialize_omml_node(e_el, fonts_set, sizes_list, style_issues) if e_el is not None else ""
            sub_s = self._serialize_omml_node(sub_el, fonts_set, sizes_list, style_issues) if sub_el is not None else ""
            sup_s = self._serialize_omml_node(sup_el, fonts_set, sizes_list, style_issues) if sup_el is not None else ""
            return f"{b_s}_({sub_s})^({sup_s})"

        if tag == "rad":  # Корень: sqrt(e)
            e_el = node.find("m:e", namespaces=NS_VM)
            e_s = self._serialize_omml_node(e_el, fonts_set, sizes_list, style_issues) if e_el is not None else ""
            return f"sqrt({e_s})"

        if tag == "d":  # Скобки-разделители (m:d)
            e_nodes = node.findall("m:e", namespaces=NS_VM)
            inner = ", ".join(
                self._serialize_omml_node(e, fonts_set, sizes_list, style_issues) for e in e_nodes
            )
            return f"({inner})"

        is_fn = in_function_name or (tag == "fName")
        parts = [
            self._serialize_omml_node(ch, fonts_set, sizes_list, style_issues, in_function_name=is_fn)
            for ch in node
        ]
        return "".join(parts)

    @staticmethod
    def _process_omml_run(
        r_node: etree._Element,
        fonts_set: Set[str],
        sizes_list: List[float],
        style_issues: List[MathTokenStyleIssue],
        in_function_name: bool,
    ) -> str:
        """Обрабатывает один m:r: текст, шрифт и типографские нарушения."""
        t_el = r_node.find("m:t", namespaces=NS_VM)
        text = (t_el.text or "") if t_el is not None else ""
        if not text:
            return ""

        # Проверяем свойства прямого начертания в OMML: <m:rPr><m:nor/> или <m:sty m:val="p"/>
        m_rpr = r_node.find("m:rPr", namespaces=NS_VM)
        w_rpr = r_node.find("w:rPr", namespaces=NS_VM)

        is_upright = in_function_name
        if m_rpr is not None:
            if m_rpr.find("m:nor", namespaces=NS_VM) is not None:
                is_upright = True
            sty_el = m_rpr.find("m:sty", namespaces=NS_VM)
            if sty_el is not None:
                sty_val = sty_el.get(f"{{{NS_VM['m']}}}val") or sty_el.get("val")
                if sty_val in ("p", "b"):  # 'p' = plain (прямой), 'i' = italic (по умолчанию в OMML)
                    is_upright = True

        if w_rpr is not None:
            r_fonts = w_rpr.find("w:rFonts", namespaces=NS_VM)
            if r_fonts is not None:
                f_name = w_attr(r_fonts, "ascii") or w_attr(r_fonts, "hAnsi") or w_attr(r_fonts, "cs")
                if f_name:
                    fonts_set.add(f_name)
            sz_el = w_rpr.find("w:sz", namespaces=NS_VM)
            if sz_el is not None and w_attr(sz_el, "val") is not None:
                sizes_list.append(round(float(w_attr(sz_el, "val") or 28) / 2.0, 1))

        # Проверка 1: Русские буквы в формуле набраны курсивом (без m:nor / m:sty="p")
        if not is_upright and RE_CYRILLIC_TOKEN.search(text):
            style_issues.append(
                MathTokenStyleIssue(token_text=text.strip(), issue="cyrillic_in_italic")
            )

        # Проверка 2: Стандартные функции (sin, cos, ln, max) набраны курсивом как обычные переменные
        if not is_upright and RE_MATH_STD_FUNCS.search(text):
            style_issues.append(
                MathTokenStyleIssue(token_text=text.strip(), issue="function_in_italic")
            )

        return text

    # -----------------------------------------------------------------
    # Вспомогательные методы чтения медиафайлов
    # -----------------------------------------------------------------

    @staticmethod
    def _unwrap_alternate_content(node: etree._Element) -> etree._Element:
        """Выбирает поддерживаемую ветку mc:AlternateContent."""
        if etree.QName(node).localname == "AlternateContent":
            choice = node.find("mc:Choice", namespaces=NS_VM)
            branch = choice if choice is not None else node.find("mc:Fallback", namespaces=NS_VM)
            if branch is not None and len(branch) > 0:
                first = branch[0]
                # Для привычной схемы mc:Choice/w:drawing возвращаем сам объект,
                # иначе — контейнер ветки (внутри может лежать w:pict, wps:wsp, v:group и т.п.)
                if isinstance(first.tag, str) and etree.QName(first).localname in ("drawing", "pict", "object"):
                    return first
                return branch
        return node

    @staticmethod
    def _guess_image_mime(path: str) -> str:
        """Определяет MIME-тип изображения по расширению и сигнатуре."""
        p = path.lower()
        for ext, mime in (
            (".png", "image/png"),
            (".jpg", "image/jpeg"),
            (".jpeg", "image/jpeg"),
            (".emf", "image/x-emf"),
            (".wmf", "image/x-wmf"),
            (".svg", "image/svg+xml"),
            (".gif", "image/gif"),
        ):
            if p.endswith(ext):
                return mime
        return "application/octet-stream"

    @staticmethod
    def _get_image_pixel_dimensions(data: bytes, mime: str) -> Tuple[int, int]:
        """Читает ширину и высоту изображения из заголовка файла."""
        try:
            if mime == "image/png" and len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", data[16:24])
                return int(w), int(h)
            if mime == "image/jpeg" and len(data) > 4 and data[:2] == b"\xff\xd8":
                idx = 2
                size = len(data)
                while idx < size - 9:
                    while idx < size and data[idx] != 0xFF:
                        idx += 1
                    while idx < size and data[idx] == 0xFF:
                        idx += 1
                    if idx >= size:
                        break
                    marker = data[idx]
                    idx += 1
                    if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7):
                        continue
                    if idx + 2 > size:
                        break
                    seg_len = struct.unpack(">H", data[idx : idx + 2])[0]
                    if marker in (0xC0, 0xC1, 0xC2, 0xC3) and idx + 7 <= size:
                        h, w = struct.unpack(">HH", data[idx + 3 : idx + 7])
                        return int(w), int(h)
                    idx += seg_len
            if mime == "image/gif" and len(data) >= 10 and data[:3] == b"GIF":
                w, h = struct.unpack("<HH", data[6:10])
                return int(w), int(h)
        except Exception:
            pass
        return 0, 0

    @staticmethod
    def _resolve_word_zip_path(target_rel: str) -> str:
        """Приводит Target связи к пути внутри пакета."""
        if not target_rel:
            return ""
        clean = target_rel.lstrip("/")
        if clean.startswith("word/"):
            return posixpath.normpath(clean)
        return posixpath.normpath(posixpath.join("word", clean))