"""Разбор абзацев: раны, поля, нумерация, микротипографика, заголовки.

Используется в фазе 3 для текста документа, а также для абзацев внутри таблиц,
колонтитулов и текстовых рамок.

Содержит трекер автонумерации по word/numbering.xml, анализатор заголовков
и списков, сканер микропробелов и ParagraphParser, который собирает
w:r / w:fld / w:hyperlink / w:drawing в ParagraphBlock со спанами и служебными
токенами разметки.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple
from lxml import etree

from .schema import (
    HeadingMetadata,
    InlineSpecialChar,
    ListItemMetadata,
    MicroTypographyReport,
    ParagraphBlock,
    ParagraphPropertiesEffective,
    RunFontPropertiesEffective,
    RunFontPropertiesPartial,
    SpanLocation,
    TextRun,
)
from .style_resolver import (
    NS,
    StyleCascadeResolver,
    TableCascadeContext,
    safe_fromstring,
    w_attr,
)

NS_EXT = {
    **NS,
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "v": "urn:schemas-microsoft-com:vml",
}

VisualExtractorCallback = Callable[[etree._Element, str, int, int], Optional[str]]
EquationExtractorCallback = Callable[[etree._Element, str, int, int], Optional[str]]
# Восстанавливает ProgID OLE-объекта по r:id, когда LibreOffice оставил его пустым.
OleProgIdResolver = Callable[[etree._Element], Optional[str]]


# =====================================================================
# 1. STATEFUL-ТРЕКЕР АВТОНУМЕРАЦИИ СПИСКОВ (word/numbering.xml)
# =====================================================================

@dataclass
class _LevelSpec:
    """Один уровень автонумерации из numbering.xml."""

    ilvl: int
    start: int = 1
    num_fmt: str = "decimal"
    lvl_text: str = "%1."
    suff: str = "tab"
    restart_after_level: Optional[int] = None
    marker_font: Optional[str] = None


class NumberingStateTracker:
    """Считает и форматирует маркеры списков по numbering.xml."""

    RUS_LOWER = "абвгдежзиклмнопрстуфхцчшщэюя"
    RUS_UPPER = RUS_LOWER.upper()

    def __init__(self, numbering_xml_bytes: Optional[bytes] = None) -> None:
        """Читает numbering.xml и сбрасывает счетчики уровней."""
        self._abstract_nums: Dict[int, Dict[int, _LevelSpec]] = {}
        self._num_to_abstract: Dict[int, int] = {}
        self._lvl_overrides: Dict[Tuple[int, int], _LevelSpec] = {}
        self._start_overrides: Dict[Tuple[int, int], int] = {}
        # Счётчики хранятся по ПАРЕ (abstractNumId, ilvl): по ECMA-376 нумерация принадлежит
        # abstractNum, а w:num лишь ссылается на него. LibreOffice/Google Docs создают отдельный
        # w:num на каждый абзац, поэтому ключ по num_id сбрасывал бы сквозную нумерацию.
        self._seen_num_ids: Set[int] = set()
        self._counters: Dict[Tuple[int, int], int] = {}

        if numbering_xml_bytes:
            self._parse_numbering_xml(numbering_xml_bytes)

    def _parse_numbering_xml(self, xml_bytes: bytes) -> None:
        """Разбирает w:abstractNum и w:num в карты уровней."""
        root = safe_fromstring(xml_bytes)

        for abs_el in root.findall("w:abstractNum", namespaces=NS):
            abs_id_str = w_attr(abs_el, "abstractNumId")
            if abs_id_str is None:
                continue
            abs_id = int(abs_id_str)
            levels: Dict[int, _LevelSpec] = {}
            for lvl_el in abs_el.findall("w:lvl", namespaces=NS):
                spec = self._parse_lvl_element(lvl_el)
                if spec is not None:
                    levels[spec.ilvl] = spec
            self._abstract_nums[abs_id] = levels

        for num_el in root.findall("w:num", namespaces=NS):
            num_id_str = w_attr(num_el, "numId")
            abs_id_el = num_el.find("w:abstractNumId", namespaces=NS)
            if num_id_str is None or abs_id_el is None:
                continue
            num_id = int(num_id_str)
            abs_id = int(w_attr(abs_id_el, "val") or -1)
            self._num_to_abstract[num_id] = abs_id

            for ovr_el in num_el.findall("w:lvlOverride", namespaces=NS):
                ilvl_str = w_attr(ovr_el, "ilvl")
                if ilvl_str is None:
                    continue
                ilvl = int(ilvl_str)
                start_ovr = ovr_el.find("w:startOverride", namespaces=NS)
                if start_ovr is not None and w_attr(start_ovr, "val") is not None:
                    self._start_overrides[(num_id, ilvl)] = int(w_attr(start_ovr, "val") or 1)
                lvl_inner = ovr_el.find("w:lvl", namespaces=NS)
                if lvl_inner is not None:
                    spec = self._parse_lvl_element(lvl_inner)
                    if spec is not None:
                        self._lvl_overrides[(num_id, ilvl)] = spec

    @staticmethod
    def _parse_lvl_element(lvl_el: etree._Element) -> Optional[_LevelSpec]:
        """Читает один w:lvl в _LevelSpec."""
        ilvl_str = w_attr(lvl_el, "ilvl")
        if ilvl_str is None:
            return None
        ilvl = int(ilvl_str)

        start_el = lvl_el.find("w:start", namespaces=NS)
        start_val = int(w_attr(start_el, "val") or 1) if start_el is not None else 1

        fmt_el = lvl_el.find("w:numFmt", namespaces=NS)
        num_fmt = (w_attr(fmt_el, "val") or "decimal") if fmt_el is not None else "decimal"

        txt_el = lvl_el.find("w:lvlText", namespaces=NS)
        lvl_text = (w_attr(txt_el, "val") or "") if txt_el is not None else ""

        suff_el = lvl_el.find("w:suff", namespaces=NS)
        suff = (w_attr(suff_el, "val") or "tab") if suff_el is not None else "tab"

        restart_el = lvl_el.find("w:lvlRestart", namespaces=NS)
        restart_val: Optional[int] = None
        if restart_el is not None and w_attr(restart_el, "val") is not None:
            restart_val = int(w_attr(restart_el, "val") or 0)

        marker_font: Optional[str] = None
        r_fonts = lvl_el.find("w:rPr/w:rFonts", namespaces=NS)
        if r_fonts is not None:
            marker_font = w_attr(r_fonts, "ascii") or w_attr(r_fonts, "hAnsi") or w_attr(r_fonts, "cs")

        return _LevelSpec(
            ilvl=ilvl,
            start=start_val,
            num_fmt=num_fmt,
            lvl_text=lvl_text,
            suff=suff,
            restart_after_level=restart_val,
            marker_font=marker_font,
        )

    def _get_spec(self, num_id: int, ilvl: int) -> Optional[_LevelSpec]:
        """Возвращает спецификацию уровня для numId и ilvl."""
        if (num_id, ilvl) in self._lvl_overrides:
            return self._lvl_overrides[(num_id, ilvl)]
        abs_id = self._num_to_abstract.get(num_id)
        if abs_id is None:
            return None
        return self._abstract_nums.get(abs_id, {}).get(ilvl)

    def step_and_format(
        self, num_id: int, ilvl: int
    ) -> Optional[Tuple[int, _LevelSpec, str]]:
        """Увеличивает счетчик уровня и возвращает abstractNumId, спецификацию и маркер."""
        if num_id <= 0 or num_id not in self._num_to_abstract:
            return None

        abs_id = self._num_to_abstract[num_id]
        spec = self._get_spec(num_id, ilvl)
        if spec is None:
            return None

        key = (abs_id, ilvl)
        num_key = (num_id, ilvl)
        first_for_num_id = num_id not in self._seen_num_ids

        if key not in self._counters:
            # Первое обращение к этому уровню abstractNum'а
            self._counters[key] = self._start_overrides.get(num_key, spec.start)
        elif first_for_num_id and num_key in self._start_overrides:
            # Новый numId с явным w:startOverride перезапускает общий счетчик abstractNum'а
            self._counters[key] = self._start_overrides[num_key]
        else:
            self._counters[key] += 1

        self._seen_num_ids.add(num_id)

        for sub_lvl in range(ilvl + 1, 9):
            sub_spec = self._get_spec(num_id, sub_lvl)
            restart_limit = (
                sub_spec.restart_after_level
                if (sub_spec and sub_spec.restart_after_level is not None)
                else sub_lvl - 1
            )
            if ilvl <= restart_limit:
                self._counters.pop((abs_id, sub_lvl), None)

        if spec.num_fmt == "bullet":
            rendered = self._normalize_bullet_char(spec.lvl_text, spec.marker_font)
        elif spec.num_fmt == "none":
            rendered = ""
        else:
            rendered = spec.lvl_text
            for lvl_idx in range(ilvl + 1):
                lvl_spec = self._get_spec(num_id, lvl_idx) or spec
                val = self._counters.get((abs_id, lvl_idx))
                if val is None:
                    val = self._start_overrides.get((num_id, lvl_idx), lvl_spec.start)
                    self._counters[(abs_id, lvl_idx)] = val
                formatted_num = self._format_number(val, lvl_spec.num_fmt)
                rendered = rendered.replace(f"%{lvl_idx + 1}", formatted_num)

        return abs_id, spec, rendered

    @classmethod
    def _format_number(cls, n: int, num_fmt: str) -> str:
        """Форматирует число по numFmt (decimal, roman, letters, bullet)."""
        if n <= 0:
            return str(n)
        if num_fmt == "decimal":
            return str(n)
        if num_fmt == "lowerLetter":
            return chr(ord("a") + (n - 1) % 26)
        if num_fmt == "upperLetter":
            return chr(ord("A") + (n - 1) % 26)
        if num_fmt == "russianLower":
            return cls.RUS_LOWER[(n - 1) % len(cls.RUS_LOWER)]
        if num_fmt == "russianUpper":
            return cls.RUS_UPPER[(n - 1) % len(cls.RUS_UPPER)]
        if num_fmt in ("lowerRoman", "upperRoman"):
            roman = cls._to_roman(n)
            return roman.lower() if num_fmt == "lowerRoman" else roman
        return str(n)

    @staticmethod
    def _to_roman(n: int) -> str:
        """Переводит число в римскую запись."""
        val_map = [
            (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
            (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
            (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
        ]
        res = []
        for val, sym in val_map:
            while n >= val:
                res.append(sym)
                n -= val
        return "".join(res)

    @staticmethod
    def _normalize_bullet_char(raw_text: str, marker_font: Optional[str]) -> str:
        """Подбирает символ маркера для bullet-уровня."""
        if not raw_text:
            return "•"
        ch = raw_text[0]
        code = ord(ch)
        if code == 0xF02D or ch in ("-", "–", "—", "\u2212"):
            return raw_text
        if code in (0xF0B7, 0xF0A7, 0xF076, 0xF0D8, 0xF0FC):
            return "•"
        return raw_text


# =====================================================================
# 2. ДЕТЕКТОР ЗАГОЛОВКОВ И ТЕКСТОВЫХ ПСЕВДОСПИСКОВ
# =====================================================================

STRUCTURAL_HEADINGS_SET = {
    "РЕФЕРАТ",
    "ABSTRACT",
    "АННОТАЦИЯ",
    "СОДЕРЖАНИЕ",
    "ОГЛАВЛЕНИЕ",
    "ВВЕДЕНИЕ",
    "ЗАКЛЮЧЕНИЕ",
    "ВЫВОДЫ",
    "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ",
    "СПИСОК ИСПОЛЬЗУЕМЫХ ИСТОЧНИКОВ",
    "СПИСОК ИСПОЛЬЗОВАННОЙ ЛИТЕРАТУРЫ",
    "СПИСОК ЛИТЕРАТУРЫ",
    "БИБЛИОГРАФИЧЕСКИЙ СПИСОК",
    "СПИСОК СОКРАЩЕНИЙ",
    "СПИСОК СОКРАЩЕНИЙ И УСЛОВНЫХ ОБОЗНАЧЕНИЙ",
    "ПЕРЕЧЕНЬ СОКРАЩЕНИЙ И УСЛОВНЫХ ОБОЗНАЧЕНИЙ",
    "ТЕРМИНЫ И ОПРЕДЕЛЕНИЯ",
    "СПИСОК ТЕРМИНОВ",
    "ПРИЛОЖЕНИЯ",
}

RE_APPENDIX_HEADING = re.compile(
    r"^\s*ПРИЛОЖЕНИЕ\s+([А-ЯЁA-Z0-9]+)(?:\s*[\.\—\–\-:]?\s*(.*))?$",
    re.IGNORECASE,
)

RE_NUMBERED_HEADING_TEXT = re.compile(
    r"^\s*(?:(?P<word>глава|раздел|параграф)\s+)?"
    r"(?P<num>[1-9]\d*(?:\.[1-9]\d*)*)(?P<dot>\.)?(?:\s*[\—\–\-:]?\s+|\t+)(?P<title>\S.*)$",
    re.IGNORECASE,
)

RE_MANUAL_LIST_PREFIX = re.compile(
    r"^(?P<lead_ws>\s*)(?:"
    r"(?P<dash>[\-\u2012\u2013\u2014\u2212•●▪◦])\s+|"
    r"(?P<paren>(?:\d+|[а-яёa-z])\))\s+|"
    r"(?P<dot_num>[1-9]\d*\.)\s+"
    r")(?P<body>\S.*)$"
)

RE_CAPTION_GUARD = re.compile(
    r"^\s*(?:рисунок|рис\.|таблица|табл\.|продолжение\s+таблицы|окончание\s+таблицы|формула)\b",
    re.IGNORECASE,
)


class HeadingAndListAnalyzer:
    """Определяет заголовки, списки и ручные маркеры в абзаце."""

    @staticmethod
    def analyze_heading(
        text_visible: str,
        style_id: Optional[str],
        style_name_ui: Optional[str],
        outline_lvl: Optional[int],
        is_all_bold: bool,
        alignment: str,
        autonum_marker: Optional[str] = None,
    ) -> Optional[HeadingMetadata]:
        """Возвращает HeadingMetadata для абзаца или None."""
        clean_full = text_visible.strip()
        if not clean_full or len(clean_full) > 300:
            return None
        if RE_CAPTION_GUARD.match(clean_full):
            return None

        style_str = f"{style_id or ''} {style_name_ui or ''}".lower()
        # Исключаем строки самого оглавления (стили "toc 1..9"), но оставляем "TOC Heading"
        if re.search(r"\b(?:toc|оглавление)\s*[1-9]\b", style_str):
            return None

        is_explicit_style = (outline_lvl is not None) or any(
            kw in style_str for kw in ("heading", "заголовок", "title", "глава", "раздел")
        )
        explicit_level = (outline_lvl + 1) if outline_lvl is not None else None
        if explicit_level is None and is_explicit_style:
            m_lvl = re.search(r"[1-9]", style_str)
            explicit_level = int(m_lvl.group(0)) if m_lvl else 1

        # 1. Структурные заголовки 1-го уровня ("ВВЕДЕНИЕ", "СОДЕРЖАНИЕ", "ЗАКЛЮЧЕНИЕ"...)
        normalized_upper = re.sub(r"\s+", " ", clean_full.rstrip(".")).upper()
        if normalized_upper in STRUCTURAL_HEADINGS_SET and not autonum_marker:
            has_dot = clean_full.endswith(".")
            return HeadingMetadata(
                is_heading=True,
                detection_confidence="explicit_outline_or_style" if is_explicit_style else "structural_keyword_match",
                heading_kind="structural_unnumbered",
                level=1,
                numbering_method="none",
                section_number_raw=None,
                has_trailing_dot_in_number=False,
                clean_title_text=clean_full.rstrip("."),
                has_trailing_dot_in_title=has_dot,
                has_word_glava_or_razdel=False,
            )

        # 2. Заголовки приложений ("ПРИЛОЖЕНИЕ А")
        app_match = RE_APPENDIX_HEADING.match(clean_full)
        if app_match and (is_explicit_style or is_all_bold or alignment == "center" or clean_full.isupper()):
            app_id = app_match.group(1)
            rest_title = (app_match.group(2) or "").strip()
            has_dot = clean_full.endswith(".")
            return HeadingMetadata(
                is_heading=True,
                detection_confidence="explicit_outline_or_style" if is_explicit_style else "structural_keyword_match",
                heading_kind="appendix_heading",
                level=1,
                numbering_method="plain_text",
                section_number_raw=app_id,
                has_trailing_dot_in_number=False,
                clean_title_text=rest_title.rstrip(".") if rest_title else f"ПРИЛОЖЕНИЕ {app_id}",
                has_trailing_dot_in_title=has_dot,
                has_word_glava_or_razdel=False,
            )

        # 3. Заголовок с автонумерацией через w:numPr
        if autonum_marker:
            marker_clean = autonum_marker.strip()
            is_section_pattern = bool(re.match(r"^[1-9]\d*(?:\.[1-9]\d*)+\.?$", marker_clean))
            if is_explicit_style or (is_section_pattern and (is_all_bold or len(clean_full) < 180)):
                has_num_dot = marker_clean.endswith(".")
                num_parts = [p for p in marker_clean.strip(".").split(".") if p.isdigit()]
                calc_lvl = explicit_level or max(1, min(9, len(num_parts)))
                has_title_dot = clean_full.endswith(".")
                return HeadingMetadata(
                    is_heading=True,
                    detection_confidence="explicit_outline_or_style" if is_explicit_style else "heuristic_visual",
                    heading_kind="numbered_section",
                    level=calc_lvl,
                    numbering_method="openxml_autonum",
                    section_number_raw=marker_clean,
                    has_trailing_dot_in_number=has_num_dot,
                    clean_title_text=clean_full.rstrip("."),
                    has_trailing_dot_in_title=has_title_dot,
                    has_word_glava_or_razdel=bool(re.match(r"^(?:глава|раздел)\b", marker_clean, re.I)),
                )
            return None

        # 4. Текстовая нумерация ("Раздел 1 СОЗДАНИЕ БАЗЫ ДАННЫХ...", "3.1. Архитектурное обоснование...")
        num_match = RE_NUMBERED_HEADING_TEXT.match(clean_full)
        if num_match:
            word_prefix = num_match.group("word")
            num_core = num_match.group("num")
            trailing_dot = num_match.group("dot") is not None
            title_body = num_match.group("title").strip()

            is_multi_level = "." in num_core
            ends_like_list = title_body.endswith(";") or (
                title_body.endswith(".") and not is_explicit_style and not is_all_bold and not is_multi_level
            )
            first_alpha = next((ch for ch in title_body if ch.isalpha()), "")
            starts_upper = first_alpha.isupper() if first_alpha else False

            if starts_upper and not ends_like_list and (
                is_explicit_style or is_all_bold or is_multi_level or word_prefix is not None
            ):
                num_parts = num_core.split(".")
                calc_lvl = explicit_level or max(1, min(9, len(num_parts)))
                raw_sec_num = f"{num_core}." if trailing_dot else num_core
                return HeadingMetadata(
                    is_heading=True,
                    detection_confidence="explicit_outline_or_style" if is_explicit_style else "heuristic_visual",
                    heading_kind="numbered_section",
                    level=calc_lvl,
                    numbering_method="plain_text",
                    section_number_raw=raw_sec_num,
                    has_trailing_dot_in_number=trailing_dot,
                    clean_title_text=title_body.rstrip("."),
                    has_trailing_dot_in_title=title_body.endswith("."),
                    has_word_glava_or_razdel=(word_prefix is not None),
                )

        # 5. Явный стиль Heading без номера
        if is_explicit_style:
            return HeadingMetadata(
                is_heading=True,
                detection_confidence="explicit_outline_or_style",
                heading_kind="other",
                level=explicit_level or 1,
                numbering_method="none",
                section_number_raw=None,
                has_trailing_dot_in_number=False,
                clean_title_text=clean_full.rstrip("."),
                has_trailing_dot_in_title=clean_full.endswith("."),
                has_word_glava_or_razdel=False,
            )

        return None

    @classmethod
    def build_list_metadata(
        cls,
        body_text: str,
        mechanism: str,
        rendered_marker: str,
        num_id: Optional[int] = None,
        abstract_num_id: Optional[int] = None,
        level: int = 0,
        num_fmt: str = "none",
        lvl_text_template: Optional[str] = None,
        marker_font_name: Optional[str] = None,
        suffix_type: str = "tab",
    ) -> ListItemMetadata:
        """Собирает ListItemMetadata по numPr и трекеру нумерации."""
        clean_body = body_text.strip()
        first_case = cls._detect_first_char_case(clean_body)
        end_punct = cls._detect_ending_punctuation(clean_body)

        valid_fmt = (
            num_fmt
            if num_fmt in (
                "bullet", "decimal", "lowerLetter", "upperLetter",
                "lowerRoman", "upperRoman", "russianLower", "russianUpper", "none",
            )
            else "other"
        )
        return ListItemMetadata(
            is_list_item=True,
            mechanism=mechanism,  # type: ignore[arg-type]
            num_id=num_id,
            abstract_num_id=abstract_num_id,
            level=max(0, min(8, level)),
            num_fmt=valid_fmt,  # type: ignore[arg-type]
            lvl_text_template=lvl_text_template,
            rendered_marker=rendered_marker,
            marker_font_name=marker_font_name,
            suffix_type=suffix_type if suffix_type in ("tab", "space", "nothing", "manual_spaces") else "tab",  # type: ignore[arg-type]
            body_text_without_marker=clean_body,
            first_letter_case=first_case,  # type: ignore[arg-type]
            ending_punctuation=end_punct,  # type: ignore[arg-type]
        )

    @classmethod
    def detect_manual_list_item(cls, text_visible: str) -> Optional[ListItemMetadata]:
        """Распознает список, набранный вручную без numPr."""
        m = RE_MANUAL_LIST_PREFIX.match(text_visible)
        if not m:
            return None

        dash_m = m.group("dash")
        paren_m = m.group("paren")
        dot_m = m.group("dot_num")
        body = m.group("body")

        if dash_m:
            marker = dash_m
            num_fmt = "bullet"
        elif paren_m:
            marker = paren_m
            core = paren_m[:-1]
            if core.isdigit():
                num_fmt = "decimal"
            elif core in NumberingStateTracker.RUS_LOWER:
                num_fmt = "russianLower"
            else:
                num_fmt = "lowerLetter"
        else:
            marker = dot_m
            num_fmt = "decimal"

        return cls.build_list_metadata(
            body_text=body,
            mechanism="manual_text_prefix",
            rendered_marker=marker,
            level=0,
            num_fmt=num_fmt,
            suffix_type="manual_spaces",
        )

    @staticmethod
    def _detect_first_char_case(text: str) -> str:
        """Определяет регистр первого значимого символа."""
        if not text:
            return "empty"
        for ch in text:
            if ch in '«"“„\'([<{':
                continue
            if ch.isalpha():
                return "uppercase" if ch.isupper() else "lowercase"
            if ch.isdigit():
                return "digit"
            return "symbol"
        return "symbol"

    @staticmethod
    def _detect_ending_punctuation(text: str) -> str:
        """Проверяет, заканчивается ли текст знаком препинания."""
        trimmed = text.rstrip('»"”\')]}> ')
        if not trimmed:
            return "none"
        last = trimmed[-1]
        return {
            ";": "semicolon",
            ".": "period",
            ",": "comma",
            ":": "colon",
            "!": "exclamation",
            "?": "question",
        }.get(last, "none")


# =====================================================================
# 3. ЛЕКСИЧЕСКИЙ СКАНЕР МИКРОТИПОГРАФИКИ (MicroTypographyScanner)
# =====================================================================

RE_MULTIPLE_SPACES = re.compile(r"[ ]{2,}")
RE_WRONG_QUOTES = re.compile(r'["“”\']')
RE_HYPHEN_AS_DASH = re.compile(r"(?<=\s)-(?=\s)")
RE_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+(?=[.,;:!?\)\]])")

# Негативный lookahead (?![ ]?[А-ЯЁ][а-яё]+) отсекает захват "России Б. Н." вместо инициалов.
# Необязательная группа инициалов обёрнута в атомарную (?>...), иначе backtracking
# укорачивает совпадение до "России Б." и обходит lookahead.
RE_MISSING_NBSP_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "initials",
        re.compile(
            r"\b[А-ЯЁ]\.[ ](?:[А-ЯЁ]\.[ ]?)?[А-ЯЁ][а-яё]+\b|"
            r"\b[А-ЯЁ][а-яё]+[ ][А-ЯЁ]\.(?>(?:[ ]?[А-ЯЁ]\.)?)(?![ ]?[А-ЯЁ][а-яё]+)"
        ),
    ),
    (
        "number_unit",
        re.compile(
            r"\b\d+(?:[.,]\d+)?[ ](?:%|°C|°|кг|г|мг|км|м|см|мм|мкм|нм|с|мс|мин|ч|Гц|кГц|МГц|ГГц|Вт|кВт|В|А|Ом|Па|кПа|МПа|байт|КБ|МБ|ГБ|ТБ|бит|г\.|гг\.|в\.|вв\.|с\.|стр\.|руб\.|тыс\.|млн|млрд)\b"
        ),
    ),
    (
        "doc_reference",
        re.compile(
            r"\b(?:рисунок|рисунка|рисунке|рисунку|рис\.|таблица|таблицы|таблице|таблицу|табл\.|формула|формулы|формуле|приложение|приложения|глава|раздел|№|п\.|пп\.|ч\.|ст\.|г\.|т\.)[ ]\d+",
            re.IGNORECASE,
        ),
    ),
]


class MicroTypographyScanner:
    """Ищет нарушения микротипографики в тексте абзаца."""

    @staticmethod
    def scan(
        text_visible: str,
        special_chars: List[InlineSpecialChar],
        excluded_gap_offsets: Optional[List[int]] = None,
    ) -> MicroTypographyReport:
        """
        Ищет микротипографические дефекты в видимом тексте абзаца.

        excluded_gap_offsets — позиции символов, на которых в исходном XML стоит невидимый объект
        (инлайн-формула OMML, картинка). Два пробела по разные стороны разрыва не являются
        дефектом ("где " + формула + " — величина"), поэтому такие совпадения пропускаются.
        """
        report = MicroTypographyReport()
        if not text_visible:
            return report

        gap_offsets = excluded_gap_offsets or []

        lead_spaces = 0
        for ch in text_visible:
            if ch in (" ", "\u00A0"):
                lead_spaces += 1
            else:
                break
        report.leading_spaces_count = lead_spaces

        trail_spaces = 0
        for ch in reversed(text_visible):
            if ch in (" ", "\u00A0"):
                trail_spaces += 1
            else:
                break
        report.trailing_spaces_count = trail_spaces

        lead_tabs = 0
        for ch in text_visible:
            if ch == "\t":
                lead_tabs += 1
            else:
                break
        report.leading_tabs_count = lead_tabs
        report.soft_line_breaks_count = sum(
            1 for sc in special_chars if sc.char_type == "line_break_shift_enter"
        )

        for m in RE_MULTIPLE_SPACES.finditer(text_visible):
            if m.start() < lead_spaces:
                continue
            if any(m.start() <= off <= m.end() for off in gap_offsets):
                continue
            report.multiple_spaces.append(
                SpanLocation(
                    start_char=m.start(),
                    end_char=m.end(),
                    snippet=text_visible[max(0, m.start() - 10) : min(len(text_visible), m.end() + 10)],
                    issue_subtype=f"{m.end() - m.start()}_spaces",
                )
            )

        for m in RE_WRONG_QUOTES.finditer(text_visible):
            report.wrong_quotes.append(
                SpanLocation(
                    start_char=m.start(),
                    end_char=m.end(),
                    snippet=text_visible[max(0, m.start() - 10) : min(len(text_visible), m.end() + 10)],
                    issue_subtype="straight_or_english_quote",
                )
            )

        for m in RE_HYPHEN_AS_DASH.finditer(text_visible):
            report.wrong_dashes.append(
                SpanLocation(
                    start_char=m.start(),
                    end_char=m.end(),
                    snippet=text_visible[max(0, m.start() - 12) : min(len(text_visible), m.end() + 12)],
                    issue_subtype="hyphen_surrounded_by_spaces",
                )
            )

        for subtype, pattern in RE_MISSING_NBSP_PATTERNS:
            for m in pattern.finditer(text_visible):
                report.missing_nbsp.append(
                    SpanLocation(
                        start_char=m.start(),
                        end_char=m.end(),
                        snippet=m.group(0),
                        issue_subtype=subtype,
                    )
                )

        for m in RE_SPACE_BEFORE_PUNCT.finditer(text_visible):
            report.space_before_punctuation.append(
                SpanLocation(
                    start_char=m.start(),
                    end_char=m.end(),
                    snippet=text_visible[max(0, m.start() - 8) : min(len(text_visible), m.end() + 8)],
                    issue_subtype="space_before_punct",
                )
            )

        return report


# =====================================================================
# 4. ВНУТРЕННИЕ СТРУКТУРЫ СОСТОЯНИЯ ПРИ ОБХОДЕ <w:p>
# =====================================================================

@dataclass
class _ActiveFieldState:
    """Состояние незакрытого поля Word при обходе ранов."""

    phase: str = "instr"
    instr_parts: List[str] = field(default_factory=list)

    @property
    def instr_text(self) -> str:
        """Возвращает накопленный текст инструкции поля."""
        return "".join(self.instr_parts).strip()


@dataclass
class _RawRunToken:
    """Служебный токен рана, из которого собираются спаны."""

    text: str
    visible_text: str
    char_style_id: Optional[str]
    direct_props: RunFontPropertiesPartial
    effective_props: RunFontPropertiesEffective
    is_hyperlink: bool
    hyperlink_target: Optional[str]
    hyperlink_anchor: Optional[str]
    in_field: bool
    field_instr: Optional[str]
    local_special_chars: List[InlineSpecialChar] = field(default_factory=list)
    # True, если сразу после этого токена в XML стоит невидимый объект (формула/картинка):
    # такие токены нельзя склеивать со следующим, иначе в text_visible появляются ложные двойные пробелы
    blocks_merge_after: bool = False


# =====================================================================
# 5. ГЛАВНЫЙ ПАРСЕР АБЗАЦЕВ (ParagraphParser)
# =====================================================================

class ParagraphParser:
    """Собирает ParagraphBlock из w:p: спаны, поля, визуалы, свойства."""

    def __init__(
        self,
        style_resolver: StyleCascadeResolver,
        numbering_tracker: NumberingStateTracker,
        rels_map: Optional[Dict[str, str]] = None,
        visual_extractor_cb: Optional[VisualExtractorCallback] = None,
        equation_extractor_cb: Optional[EquationExtractorCallback] = None,
        ole_prog_id_resolver: Optional[OleProgIdResolver] = None,
    ) -> None:
        """Принимает резолвер стилей, трекер нумерации и callback-и объектов."""
        self.style_resolver = style_resolver
        self.numbering_tracker = numbering_tracker
        self.rels_map = rels_map or {}
        self.visual_extractor_cb = visual_extractor_cb
        self.equation_extractor_cb = equation_extractor_cb
        self.ole_prog_id_resolver = ole_prog_id_resolver
        self._auto_visual_seq = 0
        self._auto_eq_seq = 0

    def parse_paragraph(
        self,
        p_elem: etree._Element,
        block_id: str,
        section_idx: int,
        start_page: int,
        container_type: str = "body",
        tbl_ctx: Optional[TableCascadeContext] = None,
    ) -> Tuple[ParagraphBlock, int]:
        """Разбирает один w:p в ParagraphBlock по контексту контейнера."""
        style_id, direct_p, effective_p = self.style_resolver.resolve_paragraph_props(p_elem, tbl_ctx)
        style_def = self.style_resolver.styles.get(style_id or "")
        style_name_ui = style_def.name_ui if style_def is not None else style_id
        para_mark_font = self.style_resolver.resolve_para_mark_props(p_elem, tbl_ctx)

        current_page = start_page
        if effective_p.page_break_before and container_type == "body":
            current_page += 1
        page_start_est = current_page

        raw_tokens: List[_RawRunToken] = []
        embedded_visual_ids: List[str] = []
        embedded_equation_ids: List[str] = []
        footnote_ids: List[str] = []
        field_stack: List[_ActiveFieldState] = []

        self._traverse_paragraph_children(
            parent_el=p_elem,
            p_elem=p_elem,
            block_id=block_id,
            section_idx=section_idx,
            current_page_ref=[current_page],
            tbl_ctx=tbl_ctx,
            field_stack=field_stack,
            raw_tokens=raw_tokens,
            embedded_visual_ids=embedded_visual_ids,
            embedded_equation_ids=embedded_equation_ids,
            footnote_ids=footnote_ids,
            is_hyperlink=False,
            hyperlink_target=None,
            hyperlink_anchor=None,
        )

        merged_runs, text_raw, text_visible, all_special_chars, gap_offsets = self._merge_adjacent_runs(
            raw_tokens, block_id
        )

        rendered_break_offsets: List[int] = []
        for sc in all_special_chars:
            if sc.char_type == "last_rendered_page_break":
                rendered_break_offsets.append(sc.char_offset_in_para)
                if not (sc.char_offset_in_para == 0 and effective_p.page_break_before):
                    current_page += 1
            elif sc.char_type == "page_break":
                current_page += 1

        page_end_est = max(page_start_est, current_page)

        num_id, ilvl = self._extract_effective_num_pr(p_elem, style_id, effective_p)
        autonum_result = (
            self.numbering_tracker.step_and_format(num_id, ilvl)
            if (num_id is not None and ilvl is not None)
            else None
        )
        autonum_marker_str = autonum_result[2] if autonum_result is not None else None

        non_ws_runs = [r for r in merged_runs if r.visible_text.strip()]
        is_all_bold = bool(non_ws_runs) and all(r.effective_props.bold for r in non_ws_runs)

        heading_info: Optional[HeadingMetadata] = None
        list_info: Optional[ListItemMetadata] = None

        if container_type == "body":
            heading_info = HeadingAndListAnalyzer.analyze_heading(
                text_visible=text_visible,
                style_id=style_id,
                style_name_ui=style_name_ui,
                outline_lvl=effective_p.outline_lvl,
                is_all_bold=is_all_bold,
                alignment=effective_p.alignment,
                autonum_marker=autonum_marker_str,
            )

        if heading_info is None:
            if autonum_result is not None and num_id is not None and ilvl is not None:
                abs_id, lvl_spec, rendered_marker = autonum_result
                list_info = HeadingAndListAnalyzer.build_list_metadata(
                    body_text=text_visible,
                    mechanism="openxml_numPr",
                    rendered_marker=rendered_marker,
                    num_id=num_id,
                    abstract_num_id=abs_id,
                    level=ilvl,
                    num_fmt=lvl_spec.num_fmt,
                    lvl_text_template=lvl_spec.lvl_text,
                    marker_font_name=lvl_spec.marker_font or para_mark_font.font_ascii,
                    suffix_type=lvl_spec.suff,
                )
            elif container_type in ("body", "table_cell"):
                list_info = HeadingAndListAnalyzer.detect_manual_list_item(text_visible)

        typography_report = MicroTypographyScanner.scan(
            text_visible, all_special_chars, excluded_gap_offsets=gap_offsets
        )

        is_empty = len(text_visible.strip()) == 0
        has_media_or_math = bool(embedded_visual_ids or embedded_equation_ids)

        para_block = ParagraphBlock(
            block_id=block_id,
            section_idx=section_idx,
            container_type=container_type,  # type: ignore[arg-type]
            page_start_est=page_start_est,
            page_end_est=page_end_est,
            rendered_page_breaks_offsets=rendered_break_offsets,
            style_id=style_id,
            style_name_ui=style_name_ui,
            text_raw=text_raw,
            text_visible=text_visible,
            is_empty_or_whitespace=(is_empty and not has_media_or_math),
            contains_only_visuals_or_math=(is_empty and has_media_or_math),
            direct_props=direct_p,
            effective_props=effective_p,
            para_mark_font_effective=para_mark_font,
            runs=merged_runs,
            list_info=list_info,
            heading_info=heading_info,
            typography=typography_report,
            embedded_visual_ids=embedded_visual_ids,
            embedded_equation_ids=embedded_equation_ids,
            footnote_ids=footnote_ids,
        )

        return para_block, page_end_est

    def _traverse_paragraph_children(
        self,
        parent_el: etree._Element,
        p_elem: etree._Element,
        block_id: str,
        section_idx: int,
        current_page_ref: List[int],
        tbl_ctx: Optional[TableCascadeContext],
        field_stack: List[_ActiveFieldState],
        raw_tokens: List[_RawRunToken],
        embedded_visual_ids: List[str],
        embedded_equation_ids: List[str],
        footnote_ids: List[str],
        is_hyperlink: bool,
        hyperlink_target: Optional[str],
        hyperlink_anchor: Optional[str],
    ) -> None:
        """Обходит дочерние узлы абзаца по порядку."""
        for child in parent_el:
            if not isinstance(child.tag, str):
                continue
            local_name = etree.QName(child).localname

            if local_name in (
                "pPr", "proofErr", "bookmarkStart", "bookmarkEnd",
                "commentRangeStart", "commentRangeEnd", "del", "moveFrom",
            ):
                continue

            if local_name == "AlternateContent":
                choice_el = child.find("mc:Choice", namespaces=NS_EXT)
                target_branch = choice_el if choice_el is not None else child.find("mc:Fallback", namespaces=NS_EXT)
                if target_branch is not None:
                    self._traverse_paragraph_children(
                        target_branch, p_elem, block_id, section_idx, current_page_ref,
                        tbl_ctx, field_stack, raw_tokens, embedded_visual_ids,
                        embedded_equation_ids, footnote_ids, is_hyperlink,
                        hyperlink_target, hyperlink_anchor,
                    )
                continue

            if local_name == "hyperlink":
                r_id = child.get(f"{{{NS['r']}}}id")
                target_url = self.rels_map.get(r_id) if r_id else None
                anchor = w_attr(child, "anchor")
                self._traverse_paragraph_children(
                    child, p_elem, block_id, section_idx, current_page_ref,
                    tbl_ctx, field_stack, raw_tokens, embedded_visual_ids,
                    embedded_equation_ids, footnote_ids,
                    is_hyperlink=True,
                    hyperlink_target=target_url,
                    hyperlink_anchor=anchor,
                )
                continue

            if local_name == "fldSimple":
                instr_val = w_attr(child, "instr") or ""
                field_stack.append(_ActiveFieldState(phase="result", instr_parts=[instr_val]))
                self._traverse_paragraph_children(
                    child, p_elem, block_id, section_idx, current_page_ref,
                    tbl_ctx, field_stack, raw_tokens, embedded_visual_ids,
                    embedded_equation_ids, footnote_ids, is_hyperlink,
                    hyperlink_target, hyperlink_anchor,
                )
                field_stack.pop()
                continue

            if local_name in ("ins", "moveTo", "smartTag", "customXml"):
                self._traverse_paragraph_children(
                    child, p_elem, block_id, section_idx, current_page_ref,
                    tbl_ctx, field_stack, raw_tokens, embedded_visual_ids,
                    embedded_equation_ids, footnote_ids, is_hyperlink,
                    hyperlink_target, hyperlink_anchor,
                )
                continue

            if local_name == "sdt":
                sdt_content = child.find("w:sdtContent", namespaces=NS)
                if sdt_content is not None:
                    self._traverse_paragraph_children(
                        sdt_content, p_elem, block_id, section_idx, current_page_ref,
                        tbl_ctx, field_stack, raw_tokens, embedded_visual_ids,
                        embedded_equation_ids, footnote_ids, is_hyperlink,
                        hyperlink_target, hyperlink_anchor,
                    )
                continue

            if local_name in ("oMathPara", "oMath"):
                eq_id = self._register_equation(child, block_id, section_idx, current_page_ref[0])
                if eq_id:
                    embedded_equation_ids.append(eq_id)
                # Формула — невидимый разрыв текста: запрещаем склейку соседних ранов,
                # иначе пробелы по её сторонам склеиваются в ложный "двойной пробел"
                self._mark_invisible_gap(raw_tokens)
                continue

            if local_name == "r":
                self._parse_single_run_element(
                    r_elem=child,
                    p_elem=p_elem,
                    block_id=block_id,
                    section_idx=section_idx,
                    current_page_ref=current_page_ref,
                    tbl_ctx=tbl_ctx,
                    field_stack=field_stack,
                    raw_tokens=raw_tokens,
                    embedded_visual_ids=embedded_visual_ids,
                    embedded_equation_ids=embedded_equation_ids,
                    footnote_ids=footnote_ids,
                    is_hyperlink=is_hyperlink,
                    hyperlink_target=hyperlink_target,
                    hyperlink_anchor=hyperlink_anchor,
                )

    def _parse_single_run_element(
        self,
        r_elem: etree._Element,
        p_elem: etree._Element,
        block_id: str,
        section_idx: int,
        current_page_ref: List[int],
        tbl_ctx: Optional[TableCascadeContext],
        field_stack: List[_ActiveFieldState],
        raw_tokens: List[_RawRunToken],
        embedded_visual_ids: List[str],
        embedded_equation_ids: List[str],
        footnote_ids: List[str],
        is_hyperlink: bool,
        hyperlink_target: Optional[str],
        hyperlink_anchor: Optional[str],
    ) -> None:
        """Разбирает один w:r и добавляет токены в буфер."""
        char_style_id, direct_r, effective_r = self.style_resolver.resolve_run_props(
            r_elem, p_elem, tbl_ctx
        )

        text_chunks: List[str] = []
        local_specials: List[InlineSpecialChar] = []
        cur_len = 0

        for child in r_elem:
            if not isinstance(child.tag, str):
                continue
            tag = etree.QName(child).localname

            if tag == "rPr":
                continue

            if tag == "fldChar":
                fld_type = w_attr(child, "fldCharType")
                if fld_type == "begin":
                    field_stack.append(_ActiveFieldState(phase="instr"))
                elif fld_type == "separate" and field_stack:
                    field_stack[-1].phase = "result"
                elif fld_type == "end" and field_stack:
                    field_stack.pop()
                continue

            if tag == "instrText":
                if field_stack and field_stack[-1].phase == "instr" and child.text:
                    field_stack[-1].instr_parts.append(child.text)
                continue

            if field_stack and field_stack[-1].phase == "instr":
                continue

            if tag == "t":
                t_val = child.text or ""
                if t_val:
                    for idx, ch in enumerate(t_val):
                        if ch == "\u00A0":
                            local_specials.append(
                                InlineSpecialChar(
                                    char_type="nbsp",
                                    char_offset_in_para=cur_len + idx,
                                )
                            )
                    text_chunks.append(t_val)
                    cur_len += len(t_val)

            elif tag == "tab":
                local_specials.append(
                    InlineSpecialChar(char_type="tab", char_offset_in_para=cur_len)
                )
                text_chunks.append("\t")
                cur_len += 1

            elif tag == "noBreakHyphen":
                local_specials.append(
                    InlineSpecialChar(char_type="no_break_hyphen", char_offset_in_para=cur_len)
                )
                text_chunks.append("\u2011")
                cur_len += 1

            elif tag == "softHyphen":
                local_specials.append(
                    InlineSpecialChar(char_type="soft_hyphen", char_offset_in_para=cur_len)
                )

            elif tag == "br":
                br_type = w_attr(child, "type") or "textWrapping"
                if br_type == "page":
                    local_specials.append(
                        InlineSpecialChar(char_type="page_break", char_offset_in_para=cur_len)
                    )
                elif br_type == "column":
                    local_specials.append(
                        InlineSpecialChar(char_type="column_break", char_offset_in_para=cur_len)
                    )
                else:
                    local_specials.append(
                        InlineSpecialChar(
                            char_type="line_break_shift_enter", char_offset_in_para=cur_len
                        )
                    )
                    text_chunks.append("\n")
                    cur_len += 1

            elif tag == "lastRenderedPageBreak":
                local_specials.append(
                    InlineSpecialChar(
                        char_type="last_rendered_page_break", char_offset_in_para=cur_len
                    )
                )

            elif tag in ("footnoteReference", "endnoteReference"):
                fn_id = w_attr(child, "id")
                if fn_id is not None and int(fn_id) > 0:
                    footnote_ids.append(fn_id)
                    local_specials.append(
                        InlineSpecialChar(char_type="footnote_ref", char_offset_in_para=cur_len)
                    )

            elif tag == "sym":
                char_hex = w_attr(child, "char")
                if char_hex:
                    try:
                        sym_ch = chr(int(char_hex, 16))
                    except ValueError:
                        sym_ch = "?"
                    text_chunks.append(sym_ch)
                    cur_len += 1

            elif tag in ("drawing", "pict", "object"):
                ole_el = child.find(".//{urn:schemas-microsoft-com:office:office}OLEObject")
                # LibreOffice выгружает OLEObject с пустым ProgID, поэтому формулу
                # приходится опознавать по бинарному embedding-файлу.
                prog_id = (ole_el.get("ProgID") or "") if ole_el is not None else ""
                if ole_el is not None and not prog_id and self.ole_prog_id_resolver is not None:
                    prog_id = self.ole_prog_id_resolver(ole_el) or ""
                if "Equation" in prog_id or "MathType" in prog_id:
                    eq_id = self._register_equation(child, block_id, section_idx, current_page_ref[0])
                    if eq_id:
                        embedded_equation_ids.append(eq_id)
                else:
                    vis_id = self._register_visual(child, block_id, section_idx, current_page_ref[0])
                    if vis_id:
                        embedded_visual_ids.append(vis_id)
                # Картинка/OLE-формула — невидимый разрыв текста (см. комментарий к oMath)
                self._mark_invisible_gap(raw_tokens)

            elif tag == "AlternateContent":
                choice_el = child.find("mc:Choice", namespaces=NS_EXT)
                branch = choice_el if choice_el is not None else child.find("mc:Fallback", namespaces=NS_EXT)
                if branch is not None:
                    for inner in branch:
                        inner_name = etree.QName(inner).localname
                        if inner_name in ("drawing", "pict", "object"):
                            vis_id = self._register_visual(inner, block_id, section_idx, current_page_ref[0])
                            if vis_id:
                                embedded_visual_ids.append(vis_id)
                            self._mark_invisible_gap(raw_tokens)

        raw_str = "".join(text_chunks)
        if not raw_str and not local_specials:
            return

        visible_str = raw_str.upper() if effective_r.caps else raw_str
        in_field = len(field_stack) > 0
        active_instr = field_stack[-1].instr_text if in_field else None

        raw_tokens.append(
            _RawRunToken(
                text=raw_str,
                visible_text=visible_str,
                char_style_id=char_style_id,
                direct_props=direct_r,
                effective_props=effective_r,
                is_hyperlink=is_hyperlink,
                hyperlink_target=hyperlink_target,
                hyperlink_anchor=hyperlink_anchor,
                in_field=in_field,
                field_instr=active_instr,
                local_special_chars=local_specials,
            )
        )

    @staticmethod
    def _mark_invisible_gap(raw_tokens: List[_RawRunToken]) -> None:
        """Помечает последний токен как границу с невидимым объектом (формула/картинка)."""
        if raw_tokens:
            raw_tokens[-1].blocks_merge_after = True

    @staticmethod
    def _can_merge_tokens(a: _RawRunToken, b: _RawRunToken) -> bool:
        """Проверяет, можно ли слить соседние токены в один спан."""
        return (
            a.char_style_id == b.char_style_id
            and a.is_hyperlink == b.is_hyperlink
            and a.hyperlink_target == b.hyperlink_target
            and a.hyperlink_anchor == b.hyperlink_anchor
            and a.in_field == b.in_field
            and a.field_instr == b.field_instr
            and a.effective_props == b.effective_props
            and not a.blocks_merge_after
        )

    @classmethod
    def _merge_adjacent_runs(
        cls,
        tokens: List[_RawRunToken],
        block_id: str,
    ) -> Tuple[List[TextRun], str, str, List[InlineSpecialChar], List[int]]:
        """
        Склеивает соседние токены с идентичным форматированием в TextRun'ы.

        Возвращает (runs, text_raw, text_visible, special_chars, gap_offsets), где gap_offsets —
        позиции в text_visible сразу после которых в XML стоял невидимый объект (формула/картинка).
        """
        if not tokens:
            return [], "", "", [], []

        merged_groups: List[_RawRunToken] = [tokens[0]]

        for nxt in tokens[1:]:
            prev = merged_groups[-1]
            if cls._can_merge_tokens(prev, nxt):
                base_offset = len(prev.text)
                for sc in nxt.local_special_chars:
                    prev.local_special_chars.append(
                        InlineSpecialChar(
                            char_type=sc.char_type,
                            char_offset_in_para=base_offset + sc.char_offset_in_para,
                        )
                    )
                prev.text += nxt.text
                prev.visible_text += nxt.visible_text
            else:
                merged_groups.append(nxt)

        final_runs: List[TextRun] = []
        all_specials: List[InlineSpecialChar] = []
        gap_offsets: List[int] = []
        raw_parts: List[str] = []
        vis_parts: List[str] = []
        global_cursor = 0

        for idx, grp in enumerate(merged_groups):
            start_c = global_cursor
            end_c = start_c + len(grp.text)

            # Граница с невидимым объектом (формула/картинка) — запоминаем позицию разрыва
            if idx > 0 and merged_groups[idx - 1].blocks_merge_after:
                gap_offsets.append(start_c)

            shifted_specials: List[InlineSpecialChar] = []
            for sc in grp.local_special_chars:
                g_sc = InlineSpecialChar(
                    char_type=sc.char_type,
                    char_offset_in_para=start_c + sc.char_offset_in_para,
                )
                shifted_specials.append(g_sc)
                all_specials.append(g_sc)

            final_runs.append(
                TextRun(
                    run_id=f"{block_id}_r{idx}",
                    text=grp.text,
                    visible_text=grp.visible_text,
                    start_char=start_c,
                    end_char=end_c,
                    char_style_id=grp.char_style_id,
                    direct_props=grp.direct_props,
                    effective_props=grp.effective_props,
                    is_hyperlink=grp.is_hyperlink,
                    hyperlink_target=grp.hyperlink_target,
                    hyperlink_anchor=grp.hyperlink_anchor,
                    in_field=grp.in_field,
                    field_instr=grp.field_instr,
                    special_chars=shifted_specials,
                )
            )

            raw_parts.append(grp.text)
            vis_parts.append(grp.visible_text)
            global_cursor = end_c

        return final_runs, "".join(raw_parts), "".join(vis_parts), all_specials, gap_offsets

    def _extract_effective_num_pr(
        self,
        p_elem: etree._Element,
        style_id: Optional[str],
        effective_p: Optional[ParagraphPropertiesEffective] = None,
    ) -> Tuple[Optional[int], Optional[int]]:
        """
        Определяет итоговую нумерацию абзаца.

        Основной путь — уже разрешенный каскад (ParagraphPropertiesEffective.num_id/ilvl):
        список может быть задан стилем абзаца, а не только прямым w:pPr/w:numPr.
        w:numId="0" в каскаде означает явную отмену нумерации.
        """
        if effective_p is not None:
            num_id_eff = effective_p.num_id
            if num_id_eff is None or num_id_eff <= 0:
                return None, None
            return num_id_eff, int(effective_p.ilvl or 0)

        pPr = p_elem.find("w:pPr", namespaces=NS)
        if pPr is not None:
            num_pr = pPr.find("w:numPr", namespaces=NS)
            if num_pr is not None:
                num_id_el = num_pr.find("w:numId", namespaces=NS)
                ilvl_el = num_pr.find("w:ilvl", namespaces=NS)
                if num_id_el is not None and w_attr(num_id_el, "val") is not None:
                    num_id = int(w_attr(num_id_el, "val") or 0)
                    if num_id == 0:
                        return None, None
                    ilvl = int(w_attr(ilvl_el, "val") or 0) if ilvl_el is not None else 0
                    return num_id, ilvl

        return None, None

    def _register_visual(
        self, elem: etree._Element, block_id: str, section_idx: int, page_est: int
    ) -> str:
        """Регистрирует визуальный объект и возвращает его id."""
        if self.visual_extractor_cb is not None:
            vis_id = self.visual_extractor_cb(elem, block_id, section_idx, page_est)
            if vis_id:
                return vis_id
        self._auto_visual_seq += 1
        return f"vis_{self._auto_visual_seq:04d}"

    def _register_equation(
        self, elem: etree._Element, block_id: str, section_idx: int, page_est: int
    ) -> str:
        """Регистрирует формулу и возвращает ее id."""
        if self.equation_extractor_cb is not None:
            eq_id = self.equation_extractor_cb(elem, block_id, section_idx, page_est)
            if eq_id:
                return eq_id
        self._auto_eq_seq += 1
        return f"eq_{self._auto_eq_seq:04d}"