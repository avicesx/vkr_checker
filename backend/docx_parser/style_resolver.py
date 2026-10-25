"""Каскад стилей OpenXML и общие утилиты разбора XML.

Здесь живут единые конвертеры единиц (twips, half-points, 1/8 пункта), безопасный
парсер XML-частей и безопасные приведения типов, которыми пользуются остальные
модули пакета.

Каскад свойств: docDefaults -> стиль абзаца/символа -> стиль таблицы -> уровень
списка из numbering.xml -> прямое форматирование. Каждый слой помечается
PropertySource, чтобы в отчете было видно происхождение значения.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from lxml import etree

from .schema import (
    BorderEdge,
    BoxBorders,
    ColorValue,
    LineSpacingRule,
    ParagraphAlignment,
    ParagraphPropertiesEffective,
    ParagraphPropertiesPartial,
    PropertySource,
    RunFontPropertiesEffective,
    RunFontPropertiesPartial,
    StyleDefinition,
    TabStopSpec,
    ThemeData,
)

# =====================================================================
# 1. ПРОСТРАНСТВА ИМЕН OPENXML И КОНВЕРТЕРЫ ЕДИНИЦ ИЗМЕРЕНИЯ
# =====================================================================

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
W_NS = f"{{{NS['w']}}}"
A_NS = f"{{{NS['a']}}}"


def w_tag(local_name: str) -> str:
    """Собирает имя тега в пространстве имен w."""
    return f"{W_NS}{local_name}"


def a_tag(local_name: str) -> str:
    """Собирает имя тега в пространстве имен a (DrawingML)."""
    return f"{A_NS}{local_name}"


def w_attr(elem: etree._Element, attr_name: str) -> Optional[str]:
    """Безопасное чтение атрибута в пространстве имен w: (или без префикса)."""
    val = elem.get(f"{W_NS}{attr_name}")
    return val if val is not None else elem.get(attr_name)


# Безопасный парсер XML-частей DOCX: отключены сеть и внешние сущности (защита от XXE),
# включено мягкое восстановление после ошибок разметки (документы LibreOffice/Google Docs).
SAFE_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)


def safe_fromstring(xml_bytes: bytes | str) -> etree._Element:
    """Парсит XML через SAFE_XML_PARSER (единая точка входа для всех XML-частей пакета)."""
    if isinstance(xml_bytes, str):
        xml_bytes = xml_bytes.encode("utf-8")
    return etree.fromstring(xml_bytes, parser=SAFE_XML_PARSER)


def safe_float(value: Optional[str | int | float], default: float = 0.0) -> float:
    """Мягкое приведение значения из XML к float (переживает '', '12.0', ' 8 ' и мусор)."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Optional[str | int | float], default: Optional[int] = None) -> Optional[int]:
    """
    Мягкое приведение значения из XML к int.
    Критично для файлов LibreOffice, где целочисленные атрибуты иногда пишутся как '100.0'.
    """
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def dxa_to_mm(dxa_val: str | int | float) -> float:
    """Конвертация twips (1/20 pt, 1/1440 inch) в миллиметры."""
    return round(safe_float(dxa_val) * 25.4 / 1440.0, 3)


def dxa_to_pt(dxa_val: str | int | float) -> float:
    """Конвертация twips (1/20 pt) в пункты."""
    return round(safe_float(dxa_val) / 20.0, 2)


def hps_to_pt(hps_val: str | int | float) -> float:
    """Конвертация half-points (1/2 pt) в пункты."""
    return round(safe_float(hps_val) / 2.0, 2)


def eighths_to_pt(eighths_val: str | int | float) -> float:
    """Конвертация 1/8 пункта (толщина рамок) в пункты."""
    return round(safe_float(eighths_val) / 8.0, 2)


def parse_oxml_bool(elem: Optional[etree._Element]) -> Optional[bool]:
    """
    Парсинг булевых свойств OpenXML (<w:b/>, <w:b w:val="0"/>, <w:b w:val="true"/>).
    Возвращает None, если тег отсутствует.
    """
    if elem is None:
        return None
    val = w_attr(elem, "val")
    if val is None:
        return True
    return val.lower() not in ("0", "false", "off", "none")


# =====================================================================
# 2. ПАРСЕР ТЕМЫ ДОКУМЕНТА (word/theme/theme1.xml) И ЦВЕТОВОЙ МОДЕЛИ
# =====================================================================

THEME_COLOR_MAP: Dict[str, str] = {
    "dark1": "dk1",
    "text1": "dk1",
    "light1": "lt1",
    "background1": "lt1",
    "dark2": "dk2",
    "text2": "dk2",
    "light2": "lt2",
    "background2": "lt2",
    "accent1": "accent1",
    "accent2": "accent2",
    "accent3": "accent3",
    "accent4": "accent4",
    "accent5": "accent5",
    "accent6": "accent6",
    "hyperlink": "hlink",
    "followedHyperlink": "folHlink",
}


class ThemeResolver:
    """Парсит word/theme/theme1.xml, разрешает шрифты темы и цвета с учетом themeTint/themeShade."""

    def __init__(self, theme_xml_bytes: Optional[bytes] = None) -> None:
        """Принимает байты theme1.xml и сразу разбирает тему."""
        self.theme_data = ThemeData()
        if theme_xml_bytes:
            self._parse_theme(theme_xml_bytes)

    def _parse_theme(self, xml_bytes: bytes) -> None:
        """Читает clrScheme и fontScheme темы в ThemeData."""
        root = safe_fromstring(xml_bytes)

        # 1. Цветовая схема (a:clrScheme)
        clr_scheme = root.find(".//a:themeElements/a:clrScheme", namespaces=NS)
        if clr_scheme is not None:
            for child in clr_scheme:
                slot_name = etree.QName(child).localname
                srgb = child.find("a:srgbClr", namespaces=NS)
                sys_clr = child.find("a:sysClr", namespaces=NS)
                if srgb is not None and srgb.get("val"):
                    self.theme_data.color_scheme_hex[slot_name] = srgb.get("val", "").upper()
                elif sys_clr is not None:
                    hex_val = sys_clr.get("lastClr") or sys_clr.get("val", "")
                    if len(hex_val) == 6:
                        self.theme_data.color_scheme_hex[slot_name] = hex_val.upper()

        # 2. Шрифтовая схема (a:fontScheme -> a:majorFont / a:minorFont)
        font_scheme = root.find(".//a:themeElements/a:fontScheme", namespaces=NS)
        if font_scheme is not None:
            major = font_scheme.find("a:majorFont", namespaces=NS)
            minor = font_scheme.find("a:minorFont", namespaces=NS)
            if major is not None:
                latin = major.find("a:latin", namespaces=NS)
                cyrl = major.find("a:font[@script='Cyrl']", namespaces=NS)
                if latin is not None and latin.get("typeface"):
                    self.theme_data.major_font_latin = latin.get("typeface", "Calibri Light")
                if cyrl is not None and cyrl.get("typeface"):
                    self.theme_data.major_font_cyrillic = cyrl.get("typeface")
            if minor is not None:
                latin = minor.find("a:latin", namespaces=NS)
                cyrl = minor.find("a:font[@script='Cyrl']", namespaces=NS)
                if latin is not None and latin.get("typeface"):
                    self.theme_data.minor_font_latin = latin.get("typeface", "Calibri")
                if cyrl is not None and cyrl.get("typeface"):
                    self.theme_data.minor_font_cyrillic = cyrl.get("typeface")

    def resolve_theme_font(self, theme_attr_val: str) -> str:
        """Преобразует ключ вроде 'minorHAnsi' или 'majorAscii' в конкретную гарнитуру."""
        key = theme_attr_val.lower()
        if key.startswith("major"):
            return self.theme_data.major_font_cyrillic or self.theme_data.major_font_latin
        return self.theme_data.minor_font_cyrillic or self.theme_data.minor_font_latin

    def resolve_color_element(
        self,
        elem: etree._Element,
        source: PropertySource,
        attr_name: str = "val",
    ) -> Optional[ColorValue]:
        """
        Разбирает <w:color> или <w:shd> (атрибут fill).
        Отличает 'auto' от #000000 и вычисляет итоговый RGB при наличии themeColor + themeTint/themeShade.
        """
        raw_val = w_attr(elem, attr_name)
        theme_color = w_attr(elem, "themeColor") or w_attr(elem, "themeFill")
        theme_tint = w_attr(elem, "themeTint") or w_attr(elem, "themeFillTint")
        theme_shade = w_attr(elem, "themeShade") or w_attr(elem, "themeFillShade")

        if raw_val is None and theme_color is None:
            return None

        if raw_val and raw_val.lower() in ("auto", "nil", "none") and not theme_color:
            return ColorValue(
                is_auto=(raw_val.lower() == "auto"),
                rgb_hex=None,
                source=source,
            )

        # Вычисляем базовый HEX
        base_hex: Optional[str] = None
        if theme_color:
            slot = THEME_COLOR_MAP.get(theme_color, theme_color)
            base_hex = self.theme_data.color_scheme_hex.get(slot)

        if not base_hex and raw_val and len(raw_val) == 6:
            base_hex = raw_val.upper()

        # Применяем модификаторы осветления (tint) или затемнения (shade) по спецификации MS-DOCX
        final_hex = self._apply_tint_or_shade(base_hex, theme_tint, theme_shade) if base_hex else None

        return ColorValue(
            is_auto=False,
            rgb_hex=final_hex,
            theme_color=theme_color,
            theme_tint=theme_tint,
            theme_shade=theme_shade,
            source=source,
        )

    @staticmethod
    def _apply_tint_or_shade(
        hex_rgb: str,
        tint_hex: Optional[str],
        shade_hex: Optional[str],
    ) -> str:
        """Применяет themeTint/themeShade к RGB-компонентам."""
        try:
            r = int(hex_rgb[0:2], 16)
            g = int(hex_rgb[2:4], 16)
            b = int(hex_rgb[4:6], 16)
        except ValueError:
            return hex_rgb

        if shade_hex:
            factor = int(shade_hex, 16) / 255.0
            r = int(round(r * factor))
            g = int(round(g * factor))
            b = int(round(b * factor))
        elif tint_hex:
            factor = int(tint_hex, 16) / 255.0
            r = int(round(r * factor + 255.0 * (1.0 - factor)))
            g = int(round(g * factor + 255.0 * (1.0 - factor)))
            b = int(round(b * factor + 255.0 * (1.0 - factor)))

        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        return f"{r:02X}{g:02X}{b:02X}"


# =====================================================================
# 3. КОНТЕКСТ УСЛОВНОГО ФОРМАТИРОВАНИЯ ТАБЛИЦ И НУМЕРАЦИИ СПИСКОВ
# =====================================================================

# Битовые позиции w:cnfStyle (12-битная строка в ячейке/строке таблицы)
CNF_BIT_MAP: List[Tuple[int, str]] = [
    (0, "firstRow"),
    (1, "lastRow"),
    (2, "firstCol"),
    (3, "lastCol"),
    (4, "band1Vert"),
    (5, "band2Vert"),
    (6, "band1Horz"),
    (7, "band2Horz"),
    (8, "neCell"),
    (9, "nwCell"),
    (10, "seCell"),
    (11, "swCell"),
]


@dataclass
class TableCascadeContext:
    """Контекст ячейки таблицы для наследования свойств из стиля таблицы (w:tblStyle)."""
    table_style_id: Optional[str] = None
    cnf_style_bits: Optional[str] = None  # Например, "100000000000" (firstRow)


class NumberingCascadeLookup:
    """
    Извлекает из word/numbering.xml отступы абзаца (w:pPr) и свойства шрифта маркера (w:rPr)
    для каждого уровня списка (numId, ilvl).
    """

    def __init__(
        self,
        numbering_xml_bytes: Optional[bytes],
        prop_extractor: "StyleCascadeResolver",
    ) -> None:
        """Принимает байты numbering.xml и строит карту уровней."""
        self._extractor = prop_extractor
        # (num_id, ilvl) -> (ParagraphPropertiesPartial, RunFontPropertiesPartial)
        self._level_cache: Dict[
            Tuple[int, int],
            Tuple[Optional[ParagraphPropertiesPartial], Optional[RunFontPropertiesPartial]],
        ] = {}
        if numbering_xml_bytes:
            self._parse_numbering(numbering_xml_bytes)

    def _parse_numbering(self, xml_bytes: bytes) -> None:
        """Разбирает w:abstractNum и w:num в карты уровней."""
        root = safe_fromstring(xml_bytes)
        abstract_levels: Dict[Tuple[int, int], etree._Element] = {}

        for abs_num in root.findall("w:abstractNum", namespaces=NS):
            abs_id_str = w_attr(abs_num, "abstractNumId")
            if abs_id_str is None:
                continue
            abs_id = int(abs_id_str)
            for lvl in abs_num.findall("w:lvl", namespaces=NS):
                ilvl_str = w_attr(lvl, "ilvl")
                if ilvl_str is not None:
                    abstract_levels[(abs_id, int(ilvl_str))] = lvl

        for num_elem in root.findall("w:num", namespaces=NS):
            num_id_str = w_attr(num_elem, "numId")
            abs_id_elem = num_elem.find("w:abstractNumId", namespaces=NS)
            if num_id_str is None or abs_id_elem is None:
                continue
            num_id = int(num_id_str)
            abs_id = int(w_attr(abs_id_elem, "val") or -1)

            # Базовые уровни из abstractNum
            for ilvl in range(9):
                lvl_elem = abstract_levels.get((abs_id, ilvl))
                if lvl_elem is not None:
                    p_pr = lvl_elem.find("w:pPr", namespaces=NS)
                    r_pr = lvl_elem.find("w:rPr", namespaces=NS)
                    self._level_cache[(num_id, ilvl)] = (
                        self._extractor.parse_pPr_partial(p_pr, "numbering_style") if p_pr is not None else None,
                        self._extractor.parse_rPr_partial(r_pr, "numbering_style") if r_pr is not None else None,
                    )

            # Переопределения w:lvlOverride
            for override in num_elem.findall("w:lvlOverride", namespaces=NS):
                ilvl_str = w_attr(override, "ilvl")
                lvl_elem = override.find("w:lvl", namespaces=NS)
                if ilvl_str is not None and lvl_elem is not None:
                    ilvl = int(ilvl_str)
                    p_pr = lvl_elem.find("w:pPr", namespaces=NS)
                    r_pr = lvl_elem.find("w:rPr", namespaces=NS)
                    self._level_cache[(num_id, ilvl)] = (
                        self._extractor.parse_pPr_partial(p_pr, "numbering_style") if p_pr is not None else None,
                        self._extractor.parse_rPr_partial(r_pr, "numbering_style") if r_pr is not None else None,
                    )

    def get_level_props(
        self, num_id: int, ilvl: int
    ) -> Tuple[Optional[ParagraphPropertiesPartial], Optional[RunFontPropertiesPartial]]:
        """Возвращает свойства уровня списка для numId и ilvl."""
        return self._level_cache.get((num_id, ilvl), (None, None))


# =====================================================================
# 4. ГЛАВНЫЙ ДВИЖОК РАЗРЕШЕНИЯ КАСКАДА СТИЛЕЙ (StyleCascadeResolver)
# =====================================================================

@dataclass
class _TableStyleConditional:
    """Условный слой стиля таблицы из w:cnfStyle."""

    p_props: Optional[ParagraphPropertiesPartial]
    r_props: Optional[RunFontPropertiesPartial]


class StyleCascadeResolver:
    """
    Загружает styles.xml, theme1.xml и numbering.xml из архива .docx (или из байтов)
    и вычисляет Direct + Effective свойства для любого абзаца w:p и рана w:r.
    """

    def __init__(
        self,
        styles_xml_bytes: Optional[bytes],
        theme_xml_bytes: Optional[bytes] = None,
        numbering_xml_bytes: Optional[bytes] = None,
    ) -> None:
        """Создает резолвер с пустыми картами стилей и темы."""
        self.theme_resolver = ThemeResolver(theme_xml_bytes)

        # Дефолты документа (w:docDefaults)
        self.doc_default_p: Optional[ParagraphPropertiesPartial] = None
        self.doc_default_r: Optional[RunFontPropertiesPartial] = None

        # Реестр стилей и дефолтные ID по типам
        self.styles: Dict[str, StyleDefinition] = {}
        self.default_style_ids: Dict[str, str] = {}

        # Условное форматирование табличных стилей: style_id -> {cond_type -> _TableStyleConditional}
        self._table_conditionals: Dict[str, Dict[str, _TableStyleConditional]] = {}

        # Кэш цепочек наследования basedOn (от корня к дочернему стилю)
        self._style_chain_cache: Dict[str, List[StyleDefinition]] = {}

        if styles_xml_bytes:
            self._parse_styles_xml(styles_xml_bytes)

        self.numbering_lookup = NumberingCascadeLookup(numbering_xml_bytes, self)

    @classmethod
    def from_docx_path(cls, docx_path: str) -> "StyleCascadeResolver":
        """Удобный конструктор напрямую из пути к файлу .docx."""
        with zipfile.ZipFile(docx_path, "r") as zf:
            names = set(zf.namelist())
            styles_bytes = zf.read("word/styles.xml") if "word/styles.xml" in names else None
            theme_bytes = zf.read("word/theme/theme1.xml") if "word/theme/theme1.xml" in names else None
            num_bytes = zf.read("word/numbering.xml") if "word/numbering.xml" in names else None
        return cls(styles_bytes, theme_bytes, num_bytes)

    # -----------------------------------------------------------------
    # Парсинг word/styles.xml
    # -----------------------------------------------------------------

    def _parse_styles_xml(self, xml_bytes: bytes) -> None:
        """Разбирает styles.xml: docDefaults, определения стилей и латентные стили."""
        root = safe_fromstring(xml_bytes)

        # 1. Парсим w:docDefaults
        doc_defaults = root.find("w:docDefaults", namespaces=NS)
        if doc_defaults is not None:
            p_def = doc_defaults.find("w:pPrDefault/w:pPr", namespaces=NS)
            r_def = doc_defaults.find("w:rPrDefault/w:rPr", namespaces=NS)
            if p_def is not None:
                self.doc_default_p = self.parse_pPr_partial(p_def, "doc_defaults")
            if r_def is not None:
                self.doc_default_r = self.parse_rPr_partial(r_def, "doc_defaults")

        # 2. Парсим все элементы w:style
        for style_elem in root.findall("w:style", namespaces=NS):
            style_id = w_attr(style_elem, "styleId")
            style_type = w_attr(style_elem, "type") or "paragraph"
            if not style_id or style_type not in ("paragraph", "character", "table", "numbering"):
                continue

            is_default = w_attr(style_elem, "default") in ("1", "true", "on")
            is_custom = w_attr(style_elem, "customStyle") in ("1", "true", "on")
            if is_default:
                self.default_style_ids[style_type] = style_id

            name_elem = style_elem.find("w:name", namespaces=NS)
            name_ui = w_attr(name_elem, "val") if name_elem is not None else style_id

            based_on_elem = style_elem.find("w:basedOn", namespaces=NS)
            based_on_id = w_attr(based_on_elem, "val") if based_on_elem is not None else None

            next_elem = style_elem.find("w:next", namespaces=NS)
            next_id = w_attr(next_elem, "val") if next_elem is not None else None

            link_elem = style_elem.find("w:link", namespaces=NS)
            link_id = w_attr(link_elem, "val") if link_elem is not None else None

            src_label: PropertySource = (
                "character_style" if style_type == "character"
                else "table_style" if style_type == "table"
                else "paragraph_style"
            )

            p_pr_elem = style_elem.find("w:pPr", namespaces=NS)
            r_pr_elem = style_elem.find("w:rPr", namespaces=NS)
            raw_p = self.parse_pPr_partial(p_pr_elem, src_label) if p_pr_elem is not None else None
            raw_r = self.parse_rPr_partial(r_pr_elem, src_label) if r_pr_elem is not None else None

            outline_lvl = raw_p.outline_lvl if raw_p is not None else None

            self.styles[style_id] = StyleDefinition(
                style_id=style_id,
                name_ui=name_ui or style_id,
                style_type=style_type,  # type: ignore[arg-type]
                based_on_id=based_on_id,
                next_style_id=next_id,
                linked_style_id=link_id,
                is_default=is_default,
                is_custom=is_custom,
                outline_lvl=outline_lvl,
                raw_p_props=raw_p,
                raw_r_props=raw_r,
            )

            # 3. Условное форматирование табличных стилей (w:tblStylePr)
            if style_type == "table":
                cond_map: Dict[str, _TableStyleConditional] = {}
                for tbl_pr in style_elem.findall("w:tblStylePr", namespaces=NS):
                    cond_type = w_attr(tbl_pr, "type")
                    if not cond_type:
                        continue
                    cp = tbl_pr.find("w:pPr", namespaces=NS)
                    cr = tbl_pr.find("w:rPr", namespaces=NS)
                    cond_map[cond_type] = _TableStyleConditional(
                        p_props=self.parse_pPr_partial(cp, "table_style") if cp is not None else None,
                        r_props=self.parse_rPr_partial(cr, "table_style") if cr is not None else None,
                    )
                if cond_map:
                    self._table_conditionals[style_id] = cond_map

    def _get_style_chain(self, style_id: Optional[str]) -> List[StyleDefinition]:
        """
        Возвращает цепочку наследования стиля от самого базового предка к дочернему:
        [BaseStyle, ParentStyle, ..., TargetStyle]. Защищена от циклических ссылок basedOn.
        """
        if not style_id or style_id not in self.styles:
            return []
        if style_id in self._style_chain_cache:
            return self._style_chain_cache[style_id]

        chain: List[StyleDefinition] = []
        visited: Set[str] = set()
        curr_id: Optional[str] = style_id

        while curr_id and curr_id in self.styles and curr_id not in visited:
            visited.add(curr_id)
            s_def = self.styles[curr_id]
            chain.append(s_def)
            curr_id = s_def.based_on_id

        chain.reverse()
        self._style_chain_cache[style_id] = chain
        return chain

    # -----------------------------------------------------------------
    # Парсинг узлов w:pPr и w:rPr в Partial-модели
    # -----------------------------------------------------------------

    def parse_pPr_partial(
        self,
        pPr: etree._Element,
        source: PropertySource,
    ) -> ParagraphPropertiesPartial:
        """Читает w:pPr в ParagraphPropertiesPartial."""
        partial = ParagraphPropertiesPartial()

        # 1. Выравнивание (w:jc)
        jc = pPr.find("w:jc", namespaces=NS)
        if jc is not None:
            jc_val = w_attr(jc, "val")
            if jc_val in ("left", "start"):
                partial.alignment = "left"
            elif jc_val in ("right", "end"):
                partial.alignment = "right"
            elif jc_val in ("both", "mediumKashida", "highKashida"):
                partial.alignment = "both"
            elif jc_val == "center":
                partial.alignment = "center"
            elif jc_val == "distribute":
                partial.alignment = "distribute"

        # 2. Отступы (w:ind).
        # По ECMA-376 w:firstLine и w:hanging взаимоисключающие, но LibreOffice/Google Docs
        # пишут оба атрибута, причем w:hanging="0" вместе с ненулевым w:firstLine
        # (например w:ind w:left="0" w:hanging="0" w:firstLine="709").
        # Нулевой hanging не затирает реальный firstLine.
        ind = pPr.find("w:ind", namespaces=NS)
        if ind is not None:
            left_val = w_attr(ind, "left") or w_attr(ind, "start")
            right_val = w_attr(ind, "right") or w_attr(ind, "end")
            hanging_val = w_attr(ind, "hanging")
            first_line_val = w_attr(ind, "firstLine")
            first_line_chars = w_attr(ind, "firstLineChars")

            if left_val is not None:
                partial.indent_left_mm = dxa_to_mm(left_val)
            if right_val is not None:
                partial.indent_right_mm = dxa_to_mm(right_val)

            hanging_int = safe_int(hanging_val, 0) or 0
            if hanging_val is not None and (hanging_int != 0 or first_line_val is None):
                partial.indent_first_line_mm = -dxa_to_mm(hanging_val)
            elif first_line_val is not None:
                partial.indent_first_line_mm = dxa_to_mm(first_line_val)
            elif first_line_chars is not None and (safe_int(first_line_chars, 0) or 0) != 0:
                # 100 единиц = 1 символ (~14 pt = 280 dxa)
                approx_dxa = (safe_float(first_line_chars) / 100.0) * 280.0
                partial.indent_first_line_mm = dxa_to_mm(approx_dxa)

        # 3. Интервалы (w:spacing)
        spacing = pPr.find("w:spacing", namespaces=NS)
        if spacing is not None:
            before = w_attr(spacing, "before")
            after = w_attr(spacing, "after")
            before_auto = w_attr(spacing, "beforeAutospacing")
            after_auto = w_attr(spacing, "afterAutospacing")
            line = w_attr(spacing, "line")
            line_rule = w_attr(spacing, "lineRule")

            if before is not None:
                partial.space_before_pt = dxa_to_pt(before)
            if after is not None:
                partial.space_after_pt = dxa_to_pt(after)
            if before_auto is not None:
                partial.space_before_auto = before_auto.lower() in ("1", "true", "on")
            if after_auto is not None:
                partial.space_after_auto = after_auto.lower() in ("1", "true", "on")

            if line is not None:
                rule_effective: LineSpacingRule = (
                    line_rule if line_rule in ("auto", "exact", "atLeast") else "auto"
                )
                partial.line_spacing_rule = rule_effective
                if rule_effective == "auto":
                    partial.line_spacing_value = round(float(line) / 240.0, 4)
                else:
                    partial.line_spacing_value = dxa_to_pt(line)

        # 4. Флаги разбивки и пагинации
        partial.contextual_spacing = parse_oxml_bool(pPr.find("w:contextualSpacing", namespaces=NS))
        partial.keep_next = parse_oxml_bool(pPr.find("w:keepNext", namespaces=NS))
        partial.keep_lines = parse_oxml_bool(pPr.find("w:keepLines", namespaces=NS))
        partial.page_break_before = parse_oxml_bool(pPr.find("w:pageBreakBefore", namespaces=NS))
        partial.widow_control = parse_oxml_bool(pPr.find("w:widowControl", namespaces=NS))

        # 5. Уровень структуры (w:outlineLvl): 0..8 -> уровни 1..9; 9 = основной текст (body text)
        outline = pPr.find("w:outlineLvl", namespaces=NS)
        if outline is not None:
            lvl_val = safe_int(w_attr(outline, "val"))
            if lvl_val is not None and 0 <= lvl_val < 9:
                partial.outline_lvl = lvl_val

        # 6. Заливка фона абзаца (w:shd) и границы (w:pBdr)
        shd = pPr.find("w:shd", namespaces=NS)
        if shd is not None:
            partial.shading_fill = self.theme_resolver.resolve_color_element(shd, source, attr_name="fill")

        p_bdr = pPr.find("w:pBdr", namespaces=NS)
        if p_bdr is not None:
            partial.borders = self._parse_box_borders(p_bdr, source)

        # 7. Позиции табуляции (w:tabs)
        tabs_elem = pPr.find("w:tabs", namespaces=NS)
        if tabs_elem is not None:
            for tab in tabs_elem.findall("w:tab", namespaces=NS):
                pos = w_attr(tab, "pos")
                val = w_attr(tab, "val") or "left"
                leader = w_attr(tab, "leader") or "none"
                if pos is not None:
                    partial.tab_stops.append(
                        TabStopSpec(
                            position_mm=dxa_to_mm(pos),
                            alignment=val if val in ("left", "center", "right", "decimal", "bar", "clear", "num") else "left",  # type: ignore[arg-type]
                            leader=leader if leader in ("none", "dot", "hyphen", "underscore", "heavy", "middleDot") else "none",  # type: ignore[arg-type]
                        )
                    )

        # 8. Нумерация (w:numPr). w:numId="0" — явная отмена нумерации, унаследованной из стиля.
        num_pr = pPr.find("w:numPr", namespaces=NS)
        if num_pr is not None:
            num_id_el = num_pr.find("w:numId", namespaces=NS)
            if num_id_el is not None:
                num_id_val = safe_int(w_attr(num_id_el, "val"))
                if num_id_val is not None:
                    partial.num_id = num_id_val
            ilvl_el = num_pr.find("w:ilvl", namespaces=NS)
            if ilvl_el is not None:
                ilvl_val = safe_int(w_attr(ilvl_el, "val"))
                if ilvl_val is not None:
                    partial.ilvl = max(0, min(8, ilvl_val))

        return partial

    def parse_rPr_partial(
        self,
        rPr: etree._Element,
        source: PropertySource,
    ) -> RunFontPropertiesPartial:
        """Читает w:rPr в RunFontPropertiesPartial."""
        partial = RunFontPropertiesPartial()

        # 1. Гарнитуры шрифтов (w:rFonts): явный атрибут перекрывает тему на том же уровне
        r_fonts = rPr.find("w:rFonts", namespaces=NS)
        if r_fonts is not None:
            ascii_f = w_attr(r_fonts, "ascii")
            hansi_f = w_attr(r_fonts, "hAnsi")
            cs_f = w_attr(r_fonts, "cs")
            ea_f = w_attr(r_fonts, "eastAsia")

            ascii_theme = w_attr(r_fonts, "asciiTheme")
            hansi_theme = w_attr(r_fonts, "hAnsiTheme")
            cs_theme = w_attr(r_fonts, "cstheme")
            ea_theme = w_attr(r_fonts, "eastAsiaTheme")

            partial.font_ascii = ascii_f or (self.theme_resolver.resolve_theme_font(ascii_theme) if ascii_theme else None)
            partial.font_hansi = hansi_f or (self.theme_resolver.resolve_theme_font(hansi_theme) if hansi_theme else None)
            partial.font_cs = cs_f or (self.theme_resolver.resolve_theme_font(cs_theme) if cs_theme else None)
            partial.font_east_asia = ea_f or (self.theme_resolver.resolve_theme_font(ea_theme) if ea_theme else None)

        # 2. Кегль шрифта (w:sz, w:szCs в полупунктах)
        sz = rPr.find("w:sz", namespaces=NS)
        if sz is not None and w_attr(sz, "val") is not None:
            partial.size_pt = hps_to_pt(w_attr(sz, "val") or 20)

        sz_cs = rPr.find("w:szCs", namespaces=NS)
        if sz_cs is not None and w_attr(sz_cs, "val") is not None:
            partial.size_cs_pt = hps_to_pt(w_attr(sz_cs, "val") or 20)

        # 3. Переключатели начертания
        partial.bold = parse_oxml_bool(rPr.find("w:b", namespaces=NS))
        partial.italic = parse_oxml_bool(rPr.find("w:i", namespaces=NS))
        partial.strike = parse_oxml_bool(rPr.find("w:strike", namespaces=NS))
        partial.double_strike = parse_oxml_bool(rPr.find("w:dstrike", namespaces=NS))
        partial.caps = parse_oxml_bool(rPr.find("w:caps", namespaces=NS))
        partial.small_caps = parse_oxml_bool(rPr.find("w:smallCaps", namespaces=NS))
        partial.vanish_hidden = parse_oxml_bool(rPr.find("w:vanish", namespaces=NS))

        # 4. Подчеркивание (w:u) и верхний/нижний индекс (w:vertAlign)
        u_elem = rPr.find("w:u", namespaces=NS)
        if u_elem is not None:
            partial.underline = w_attr(u_elem, "val") or "single"

        v_align = rPr.find("w:vertAlign", namespaces=NS)
        if v_align is not None:
            va_val = w_attr(v_align, "val")
            if va_val in ("baseline", "superscript", "subscript"):
                partial.vert_align = va_val  # type: ignore[assignment]

        # 5. Цвет текста (w:color), выделение маркером (w:highlight) и фон рана (w:shd)
        color_elem = rPr.find("w:color", namespaces=NS)
        if color_elem is not None:
            partial.color = self.theme_resolver.resolve_color_element(color_elem, source, attr_name="val")

        hl_elem = rPr.find("w:highlight", namespaces=NS)
        if hl_elem is not None:
            hl_val = w_attr(hl_elem, "val")
            if hl_val and hl_val.lower() != "none":
                partial.highlight_color = hl_val

        shd_elem = rPr.find("w:shd", namespaces=NS)
        if shd_elem is not None:
            partial.shading_fill = self.theme_resolver.resolve_color_element(shd_elem, source, attr_name="fill")

        # 6. Микротипографика (разрядка w:spacing, масштаб w:w, смещение w:position)
        spacing_elem = rPr.find("w:spacing", namespaces=NS)
        if spacing_elem is not None and w_attr(spacing_elem, "val") is not None:
            partial.char_spacing_pt = dxa_to_pt(w_attr(spacing_elem, "val") or 0)

        w_scale = rPr.find("w:w", namespaces=NS)
        if w_scale is not None and w_attr(w_scale, "val") is not None:
            partial.char_scale_pct = safe_int(w_attr(w_scale, "val"), 100)

        pos_elem = rPr.find("w:position", namespaces=NS)
        if pos_elem is not None and w_attr(pos_elem, "val") is not None:
            partial.position_offset_pt = hps_to_pt(w_attr(pos_elem, "val") or 0)

        return partial

    def _parse_box_borders(self, bdr_container: etree._Element, source: PropertySource) -> BoxBorders:
        """Читает рамки блока по четырем сторонам."""
        box = BoxBorders()
        for edge_tag, field_name in (
            ("top", "top"),
            ("bottom", "bottom"),
            ("left", "left"),
            ("right", "right"),
            ("insideH", "inside_h"),
            ("insideV", "inside_v"),
            ("tl2br", "tl2br"),
            ("tr2bl", "tr2bl"),
        ):
            edge_elem = bdr_container.find(f"w:{edge_tag}", namespaces=NS)
            if edge_elem is not None:
                val = w_attr(edge_elem, "val") or "none"
                sz = w_attr(edge_elem, "sz")
                space = w_attr(edge_elem, "space")
                color = self.theme_resolver.resolve_color_element(edge_elem, source, attr_name="color")
                setattr(
                    box,
                    field_name,
                    BorderEdge(
                        style=val if val in ("none", "nil", "single", "double", "dotted", "dashed", "thick") else "other",  # type: ignore[arg-type]
                        width_pt=eighths_to_pt(sz) if sz is not None else 0.0,
                        space_pt=float(space) if space is not None else 0.0,
                        color=color,
                    ),
                )
        return box

    # -----------------------------------------------------------------
    # Наложение слоев каскада (Merge Partials -> Effective)
    # -----------------------------------------------------------------

    @staticmethod
    def _apply_p_partial(
        eff: ParagraphPropertiesEffective,
        layer: Optional[ParagraphPropertiesPartial],
        source: PropertySource,
    ) -> None:
        """Накладывает частичные свойства абзаца на итоговые."""
        if layer is None:
            return

        if layer.alignment is not None:
            eff.alignment = layer.alignment
            eff.alignment_source = source
        if layer.indent_left_mm is not None:
            eff.indent_left_mm = layer.indent_left_mm
        if layer.indent_right_mm is not None:
            eff.indent_right_mm = layer.indent_right_mm
        if layer.indent_first_line_mm is not None:
            eff.indent_first_line_mm = layer.indent_first_line_mm
            eff.indent_first_line_source = source

        if layer.space_before_pt is not None:
            eff.space_before_pt = layer.space_before_pt
        if layer.space_after_pt is not None:
            eff.space_after_pt = layer.space_after_pt
        if layer.space_before_auto is not None:
            eff.space_before_auto = layer.space_before_auto
        if layer.space_after_auto is not None:
            eff.space_after_auto = layer.space_after_auto

        if layer.line_spacing_value is not None:
            eff.line_spacing_value = layer.line_spacing_value
            eff.line_spacing_source = source
        if layer.line_spacing_rule is not None:
            eff.line_spacing_rule = layer.line_spacing_rule

        if layer.contextual_spacing is not None:
            eff.contextual_spacing = layer.contextual_spacing
        if layer.keep_next is not None:
            eff.keep_next = layer.keep_next
        if layer.keep_lines is not None:
            eff.keep_lines = layer.keep_lines
        if layer.page_break_before is not None:
            eff.page_break_before = layer.page_break_before
        if layer.widow_control is not None:
            eff.widow_control = layer.widow_control
        if layer.outline_lvl is not None:
            eff.outline_lvl = layer.outline_lvl

        if layer.num_id is not None:
            eff.num_id = layer.num_id
        if layer.ilvl is not None:
            eff.ilvl = layer.ilvl

        if layer.shading_fill is not None:
            eff.shading_fill = layer.shading_fill
        if layer.borders is not None:
            eff.borders = layer.borders

        if layer.tab_stops:
            # Обработка w:tab val="clear" (удаляет унаследованный таб-стоп на этой позиции)
            existing = {round(t.position_mm, 1): t for t in eff.tab_stops}
            for t in layer.tab_stops:
                key = round(t.position_mm, 1)
                if t.alignment == "clear":
                    existing.pop(key, None)
                else:
                    existing[key] = t
            eff.tab_stops = sorted(existing.values(), key=lambda x: x.position_mm)

    @staticmethod
    def _apply_r_partial(
        eff: RunFontPropertiesEffective,
        layer: Optional[RunFontPropertiesPartial],
        source: PropertySource,
        use_xor_toggles: bool = False,
    ) -> None:
        """
        Накладывает частичный слой свойств шрифта.
        По стандарту ECMA-376 (§17.7.3), при комбинировании РАЗНЫХ типов стилей
        (например, стиль абзаца + стиль символа) бинарные свойства работают по XOR,
        а внутри одной цепочки basedOn и при прямом форматировании (direct) — перезаписывают значение.
        """
        if layer is None:
            return

        if layer.font_ascii is not None:
            eff.font_ascii = layer.font_ascii
            eff.font_source = source
        if layer.font_hansi is not None:
            eff.font_hansi = layer.font_hansi
            eff.font_source = source
        if layer.font_cs is not None:
            eff.font_cs = layer.font_cs

        # Если задан только ascii или только hAnsi, синхронизируем пустую пару (поведение Word для кириллицы)
        if layer.font_ascii is not None and layer.font_hansi is None:
            eff.font_hansi = layer.font_ascii
        elif layer.font_hansi is not None and layer.font_ascii is None:
            eff.font_ascii = layer.font_hansi

        if layer.size_pt is not None:
            eff.size_pt = layer.size_pt
            eff.size_source = source

        def _merge_toggle(current: bool, incoming: Optional[bool]) -> bool:
            if incoming is None:
                return current
            if use_xor_toggles and incoming is True:
                return not current
            return incoming

        eff.bold = _merge_toggle(eff.bold, layer.bold)
        eff.italic = _merge_toggle(eff.italic, layer.italic)
        eff.strike = _merge_toggle(eff.strike, layer.strike or layer.double_strike)
        eff.caps = _merge_toggle(eff.caps, layer.caps)
        eff.small_caps = _merge_toggle(eff.small_caps, layer.small_caps)
        eff.vanish_hidden = _merge_toggle(eff.vanish_hidden, layer.vanish_hidden)

        if layer.underline is not None:
            eff.underline = layer.underline
        if layer.vert_align is not None:
            eff.vert_align = layer.vert_align
        if layer.color is not None:
            eff.color = layer.color
        if layer.highlight_color is not None:
            eff.highlight_color = layer.highlight_color
        if layer.shading_fill is not None:
            eff.shading_fill = layer.shading_fill

        if layer.char_spacing_pt is not None:
            eff.char_spacing_pt = layer.char_spacing_pt
        if layer.char_scale_pct is not None:
            eff.char_scale_pct = layer.char_scale_pct
        if layer.position_offset_pt is not None:
            eff.position_offset_pt = layer.position_offset_pt

    def _collect_table_style_layers(
        self, tbl_ctx: Optional[TableCascadeContext]
    ) -> Tuple[List[ParagraphPropertiesPartial], List[RunFontPropertiesPartial]]:
        """Собирает базовые и условные (cnfStyle) слои из стиля таблицы."""
        if not tbl_ctx or not tbl_ctx.table_style_id:
            return [], []

        p_layers: List[ParagraphPropertiesPartial] = []
        r_layers: List[RunFontPropertiesPartial] = []

        # 1. Цепочка basedOn самого табличного стиля
        for s_def in self._get_style_chain(tbl_ctx.table_style_id):
            if s_def.raw_p_props:
                p_layers.append(s_def.raw_p_props)
            if s_def.raw_r_props:
                r_layers.append(s_def.raw_r_props)

        # 2. Условные секции (band1Horz -> firstCol -> firstRow -> nwCell) по битовой маске cnfStyle
        cond_map = self._table_conditionals.get(tbl_ctx.table_style_id)
        if cond_map and tbl_ctx.cnf_style_bits and len(tbl_ctx.cnf_style_bits) == 12:
            for bit_idx, cond_name in CNF_BIT_MAP:
                if tbl_ctx.cnf_style_bits[bit_idx] == "1" and cond_name in cond_map:
                    cond = cond_map[cond_name]
                    if cond.p_props:
                        p_layers.append(cond.p_props)
                    if cond.r_props:
                        r_layers.append(cond.r_props)

        return p_layers, r_layers

    # -----------------------------------------------------------------
    # Публичный API разрешения свойств абзаца и рана
    # -----------------------------------------------------------------

    def resolve_paragraph_props(
        self,
        p_elem: etree._Element,
        tbl_ctx: Optional[TableCascadeContext] = None,
    ) -> Tuple[Optional[str], ParagraphPropertiesPartial, ParagraphPropertiesEffective]:
        """
        Принимает узел <w:p> и возвращает:
        (style_id, direct_props, effective_props) со всеми разрешенными отступами и интервалами.
        """
        eff = ParagraphPropertiesEffective()

        # Уровень 1: docDefaults
        self._apply_p_partial(eff, self.doc_default_p, "doc_defaults")

        # Уровень 2: Стиль таблицы и условное форматирование ячейки (если абзац внутри таблицы)
        tbl_p_layers, _ = self._collect_table_style_layers(tbl_ctx)
        for tp in tbl_p_layers:
            self._apply_p_partial(eff, tp, "table_style")

        # Уровень 3: Цепочка стиля абзаца (w:pStyle или дефолтный стиль Normal)
        pPr = p_elem.find("w:pPr", namespaces=NS)
        style_id: Optional[str] = None
        if pPr is not None:
            p_style_elem = pPr.find("w:pStyle", namespaces=NS)
            if p_style_elem is not None:
                style_id = w_attr(p_style_elem, "val")

        target_style_id = style_id or self.default_style_ids.get("paragraph")
        for s_def in self._get_style_chain(target_style_id):
            self._apply_p_partial(eff, s_def.raw_p_props, "paragraph_style")

        # Уровень 4: Свойства уровня списка из numbering.xml.
        # Источник numPr — каскад стилей (стиль абзаца/таблицы может задавать список),
        # переопределяется прямым форматированием. numId="0" отменяет унаследованную нумерацию.
        direct = self.parse_pPr_partial(pPr, "direct") if pPr is not None else ParagraphPropertiesPartial()

        num_id: Optional[int] = direct.num_id if direct.num_id is not None else eff.num_id
        ilvl: int = direct.ilvl if direct.ilvl is not None else int(eff.ilvl or 0)

        if num_id is not None and num_id > 0:
            num_p_props, _ = self.numbering_lookup.get_level_props(num_id, max(0, min(8, ilvl)))
            self._apply_p_partial(eff, num_p_props, "numbering_style")

        # Уровень 5: Прямое форматирование абзаца (w:p/w:pPr)
        self._apply_p_partial(eff, direct, "direct")

        # Фиксируем итоговую нумерацию после каскада (нужна парсерам абзацев/списков)
        if num_id is not None:
            eff.num_id = num_id
            eff.ilvl = max(0, min(8, ilvl))

        return target_style_id, direct, eff

    def resolve_run_props(
        self,
        r_elem: Optional[etree._Element],
        p_elem: etree._Element,
        tbl_ctx: Optional[TableCascadeContext] = None,
    ) -> Tuple[Optional[str], RunFontPropertiesPartial, RunFontPropertiesEffective]:
        """
        Принимает узел <w:r> (или None для расчета базового шрифта абзаца) и родительский <w:p>.
        Возвращает (char_style_id, direct_props, effective_props).
        """
        # Уровень 0: Базовый шрифт темы / движка
        base_font = self.theme_resolver.theme_data.minor_font_cyrillic or self.theme_resolver.theme_data.minor_font_latin
        eff = RunFontPropertiesEffective(
            font_ascii=base_font,
            font_hansi=base_font,
            font_cs=base_font,
            font_source="theme",
            size_pt=10.0,
            size_source="engine_default",
            color=ColorValue(is_auto=True, source="engine_default"),
        )

        # Уровень 1: docDefaults (w:rPrDefault)
        self._apply_r_partial(eff, self.doc_default_r, "doc_defaults", use_xor_toggles=False)

        # Уровень 2: Стиль таблицы (w:tblStyle / w:cnfStyle)
        _, tbl_r_layers = self._collect_table_style_layers(tbl_ctx)
        for tr in tbl_r_layers:
            self._apply_r_partial(eff, tr, "table_style", use_xor_toggles=False)

        # Уровень 3: Цепочка стиля абзаца (w:pStyle -> w:rPr)
        pPr = p_elem.find("w:pPr", namespaces=NS)
        p_style_id: Optional[str] = None
        if pPr is not None:
            p_style_elem = pPr.find("w:pStyle", namespaces=NS)
            if p_style_elem is not None:
                p_style_id = w_attr(p_style_elem, "val")

        target_p_style = p_style_id or self.default_style_ids.get("paragraph")
        p_chain_r_props = RunFontPropertiesEffective(
            font_ascii=eff.font_ascii,
            font_hansi=eff.font_hansi,
            font_cs=eff.font_cs,
            font_source=eff.font_source,
            size_pt=eff.size_pt,
            size_source=eff.size_source,
            color=eff.color,
        )
        for s_def in self._get_style_chain(target_p_style):
            self._apply_r_partial(eff, s_def.raw_r_props, "paragraph_style", use_xor_toggles=False)
        del p_chain_r_props

        # Уровень 4: Цепочка стиля символа (w:rStyle -> w:rPr)
        rPr = r_elem.find("w:rPr", namespaces=NS) if r_elem is not None else None
        char_style_id: Optional[str] = None
        if rPr is not None:
            r_style_elem = rPr.find("w:rStyle", namespaces=NS)
            if r_style_elem is not None:
                char_style_id = w_attr(r_style_elem, "val")

        if char_style_id:
            # Сплющиваем цепочку символьного стиля внутри себя, затем накладываем поверх абзацного с XOR для toggle-свойств
            char_chain = self._get_style_chain(char_style_id)
            for idx, s_def in enumerate(char_chain):
                self._apply_r_partial(
                    eff,
                    s_def.raw_r_props,
                    "character_style",
                    use_xor_toggles=(idx == 0),
                )

        # Уровень 5: Прямое форматирование рана (w:r/w:rPr) — жестко перекрывает все стили
        direct = self.parse_rPr_partial(rPr, "direct") if rPr is not None else RunFontPropertiesPartial()
        self._apply_r_partial(eff, direct, "direct", use_xor_toggles=False)

        return char_style_id, direct, eff

    def resolve_para_mark_props(
        self,
        p_elem: etree._Element,
        tbl_ctx: Optional[TableCascadeContext] = None,
    ) -> RunFontPropertiesEffective:
        """
        Вычисляет эффективный шрифт знака конца абзаца (w:pPr/w:rPr).
        Именно этот шрифт Word использует для отрисовки автоматического номера списка (1., а)) и высоты пустых строк.
        """
        _, _, eff = self.resolve_run_props(None, p_elem, tbl_ctx)
        pPr = p_elem.find("w:pPr", namespaces=NS)
        if pPr is not None:
            # Учитываем шрифт маркера из numbering.xml
            num_pr = pPr.find("w:numPr", namespaces=NS)
            if num_pr is not None:
                num_id_el = num_pr.find("w:numId", namespaces=NS)
                ilvl_el = num_pr.find("w:ilvl", namespaces=NS)
                if num_id_el is not None and w_attr(num_id_el, "val") is not None:
                    num_id = int(w_attr(num_id_el, "val") or 0)
                    ilvl = int(w_attr(ilvl_el, "val") or 0) if ilvl_el is not None else 0
                    _, num_r_props = self.numbering_lookup.get_level_props(num_id, ilvl)
                    self._apply_r_partial(eff, num_r_props, "numbering_style", use_xor_toggles=False)

            # Прямой w:pPr/w:rPr
            para_mark_rPr = pPr.find("w:rPr", namespaces=NS)
            if para_mark_rPr is not None:
                r_style_el = para_mark_rPr.find("w:rStyle", namespaces=NS)
                if r_style_el is not None:
                    c_id = w_attr(r_style_el, "val")
                    for idx, s_def in enumerate(self._get_style_chain(c_id)):
                        self._apply_r_partial(eff, s_def.raw_r_props, "character_style", use_xor_toggles=(idx == 0))
                direct_mark = self.parse_rPr_partial(para_mark_rPr, "direct")
                self._apply_r_partial(eff, direct_mark, "direct", use_xor_toggles=False)

        return eff