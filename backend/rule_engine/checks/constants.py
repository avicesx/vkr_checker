"""Подписи, допуски и регулярные выражения правил нормоконтроля."""
from __future__ import annotations

import re

from typing import Dict, Tuple


INDENT_LABELS: Dict[str, str] = {
    "first_line_mm": "отступ первой строки",
    "left_mm": "отступ слева",
    "right_mm": "отступ справа",
}

MARGIN_LABELS: Dict[str, str] = {
    "left_mm": "левое поле",
    "right_mm": "правое поле",
    "top_mm": "верхнее поле",
    "bottom_mm": "нижнее поле",
    "gutter_mm": "корешковый отступ",
}

ALIGNMENT_LABELS: Dict[str, str] = {
    "left": "по левому краю",
    "center": "по центру",
    "right": "по правому краю",
    "both": "по ширине",
    "distribute": "по ширине с разрядкой",
    "justify": "по ширине",
}

BIBLIOGRAPHY_CATEGORY_LABELS: Dict[str, str] = {
    "book_monograph": "книга или монография",
    "journal_article": "статья в журнале",
    "conference_paper": "доклад конференции",
    "electronic_resource": "электронный ресурс",
    "normative_act_law": "нормативный акт",
    "standard_gost": "стандарт",
    "dissertation_autoref": "диссертация или автореферат",
    "patent": "патент",
    "unknown": "категория не определена",
}

BIBLIOGRAPHY_FORMAT_LABELS: Dict[str, str] = {
    "urls": "URL источника",
    "access_date_raw": "дата обращения",
    "year_extracted": "год издания",
    "pages_extracted": "объём в страницах",
    "isbn_or_issn": "ISBN или ISSN",
    "has_double_slash_source": "разделитель «//» перед источником",
    "has_text_type_designation": "обозначение вида издания",
    "dot_dash_count": "тире между элементами описания",
    "author": "автор или ответственное лицо",
    "journal_title": "название журнала или сборника",
    "publication_place": "место издания",
    "publication_source": "источник публикации",
    "archive_details": "архивные реквизиты диссертации",
    "description_schema": "порядок областей описания",
}

CAPTION_ENTITY_LABELS: Dict[str, str] = {
    "figure": "рисунок",
    "table": "таблица",
    "appendix": "приложение",
    "any": "объект с подписью",
}

CAPTION_NUMBERING_ISSUE_LABELS: Dict[str, str] = {
    "duplicate": "повтор номера",
    "gap": "пропуск номера",
    "out_of_order": "нарушение порядка номеров",
}

CAPTION_SCHEME_LABELS: Dict[str, str] = {
    "continuous_arabic": "сквозная арабская нумерация",
    "section_based": "нумерация по разделам",
    "appendix_based": "нумерация буквами приложений",
    "invalid": "номер не распознан",
}

CAPTION_SEPARATOR_LABELS: Dict[str, str] = {
    "dash": "тире",
    "dot": "точка",
    "space": "пробел",
    "none": "без разделителя",
}

CAPTION_PREFIX_FAMILIES: Dict[str, str] = {
    "рис": "figure",
    "рисунок": "figure",
    "табл": "table",
    "таблица": "table",
    "прил": "appendix",
    "приложение": "appendix",
}
"""Слово-префикс подписи → семейство нумерации: рисунки и таблицы нумеруются отдельно."""

CAPTION_GAP_LIMIT = 10
"""Сколько пропущенных номеров подряд перечислять: разрыв 1..1000 не должен раздувать отчет."""

ZONE_LABELS: Dict[str, str] = {
    "header": "верхний колонтитул",
    "footer": "нижний колонтитул",
}

ORIENTATION_LABELS: Dict[str, str] = {
    "portrait": "книжная ориентация",
    "landscape": "альбомная ориентация",
}

CASE_LABELS: Dict[str, str] = {
    "uppercase": "заголовок прописными буквами",
    "capitalized": "заголовок с прописной буквы",
    "not_uppercase": "заголовок не целиком прописными буквами",
}

FOUND_CASE_LABELS: Dict[str, str] = {
    "uppercase": "прописными буквами",
    "capitalized": "с прописной буквы",
    "lowercase_start": "со строчной буквы",
}

NUMBER_ENDING_LABELS: Dict[str, str] = {
    "with_dot": "точка после номера",
    "without_dot": "без точки после номера",
}

CONFIDENCE_RANK: Dict[str, int] = {
    "explicit_outline_or_style": 0,
    "structural_keyword_match": 1,
    "heuristic_visual": 2,
}

RE_SECTION_NUMBER = re.compile(r"^\d+(?:\.\d+)*$")

#: Группы синонимов названий разделов: один и тот же раздел ВКР называется
#: по-разному. Сравнение нормализованных названий идет внутри группы, поэтому
#: «СПИСОК ИСТОЧНИКОВ», «БИБЛИОГРАФИЧЕСКИЙ СПИСОК» и «ИСТОЧНИКИ» удовлетворяют
#: требованию «список литературы» из набора правил.
SECTION_TITLE_SYNONYMS: Tuple[frozenset[str], ...] = (
    frozenset(
        {
            "СПИСОК ЛИТЕРАТУРЫ",
            "СПИСОК ИСПОЛЬЗОВАННОЙ ЛИТЕРАТУРЫ",
            "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ",
            "СПИСОК ИСПОЛЬЗУЕМЫХ ИСТОЧНИКОВ",
            "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ И ЛИТЕРАТУРЫ",
            "СПИСОК ИСТОЧНИКОВ",
            "БИБЛИОГРАФИЧЕСКИЙ СПИСОК",
            "БИБЛИОГРАФИЯ",
            "ИСТОЧНИКИ",
            "ЛИТЕРАТУРА",
        }
    ),
)
"""Синонимы названий разделов; сравнение идет по нормализованному названию."""

RE_APPENDIX_CAPTION = re.compile(
    r"^\s*(?:(?P<continuation>продолжение|окончание)\s+)?"
    r"приложени[ея]\s+(?P<letter>[А-ЯЁA-Z0-9]+)\s*"
    r"(?:\(\s*(?:обязательное|справочное|рекомендуемое)\s*\)\s*)?"
    r"(?P<title>.*)$",
    re.IGNORECASE | re.DOTALL,
)
"""Подпись приложения: «ПРИЛОЖЕНИЕ А», «ПРИЛОЖЕНИЕ А (обязательное) Название».

Буква приложения — идентификатор: в оглавлении ВКР запись «ПРИЛОЖЕНИЕ А»
указывает на приложение, а не на заголовок с таким текстом. Продолжение
приложения («Продолжение ПРИЛОЖЕНИЯ А») доказывает, что приложение существует,
даже если подпись первого листа набрана без буквы.
"""

RE_APPENDIX_KIND_MARKER = re.compile(
    r"^\s*\(\s*(?:обязательное|справочное|рекомендуемое)\s*\)\s*(?P<title>.*)$",
    re.IGNORECASE | re.DOTALL,
)
"""Титульная строка приложения без буквы: «(обязательное) Название приложения»."""

TOC_TITLE_MIN_CHARS = 8
"""Минимальная длина названия для сверки записи оглавления с заголовком."""

APPENDIX_LETTERS = frozenset("АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ")
"""Буквы, которыми нумеруют приложения ВКР (кроме Ё, Й, Ъ, Ы, Ь)."""

SNIPPET_LIMIT = 80
EPSILON = 1e-9

#: Допуск полей страницы в pdf_only, мм: поля измерены по геометрии текста.
PDF_ONLY_MARGIN_TOLERANCE_MM = 1.5

#: Одиночная строка Word в типографской шкале «шаг / кегль»: для Times New Roman
#: 14 pt шаг базовых линий при одинарном интервале ≈ 1,15 кегля. PDF-слой
#: измеряет именно типографическое отношение, а expected.ratio задан множителем
#: Word (1,5 — полуторный), поэтому перед сравнением делим на этот коэффициент.
#: Для других гарнитур коэффициент отличается: он зависит от встроенного
#: межстрочного расстояния шрифта (у Times New Roman ≈1,15 em, у Arial ≈1,2 em).
PDF_SINGLE_LINE_FACTOR = 1.15
