"""Модели отчета и единственный источник JSON-контракта.

Парсеры только заполняют эти структуры; имена полей и правила сериализации
образуют публичный контракт, поэтому переименование поля меняет JSON.

Порядок секций файла: базовые примитивы (цвета, рамки, источники свойств),
абзацы и раны, таблицы, визуальные объекты и формулы, секции и колонтитулы,
дерево документа, перекрестные ссылки, аномалии, корневая модель
ParsedDocxDocument.
"""
from __future__ import annotations
from typing import Annotated, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, model_serializer


# =====================================================================
# 1. БАЗОВЫЕ ПРИМИТИВЫ И ТИПЫ (ЦВЕТА, ГРАНИЦЫ, ИСТОЧНИКИ НАСЛЕДОВАНИЯ)
# =====================================================================

PropertySource = Literal[
    "direct",           # Задано напрямую в w:rPr / w:pPr элемента
    "character_style",  # Унаследовано из стиля символа (w:rStyle)
    "paragraph_style",  # Унаследовано из стиля абзаца (w:pStyle)
    "numbering_style",  # Переопределено уровнем списка в numbering.xml
    "table_style",      # Унаследовано из стиля таблицы / w:cnfStyle
    "doc_defaults",     # Взято из w:docDefaults в styles.xml
    "theme",            # Разрешено через theme/theme1.xml (+major/+minor)
    "engine_default",   # Вшитый дефолт MS Word (когда в XML пусто везде)
]


class ColorValue(BaseModel):
    """Цвет элемента: RGB, auto или значение темы с tint/shade."""

    is_auto: bool = Field(
        default=False,
        description="True, если в XML стоит w:val='auto' (штатный черный цвет при печати)"
    )
    rgb_hex: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9A-F]{6}$",
        description="Итоговый 6-символьный HEX (например, '000000' или '1F1F1F') с учетом themeTint/themeShade"
    )
    theme_color: Optional[str] = None
    theme_tint: Optional[str] = None
    theme_shade: Optional[str] = None
    source: PropertySource = "engine_default"

    @property
    def is_visually_black(self) -> bool:
        """True, если цвет визуально черный."""
        return self.is_auto or self.rgb_hex == "000000"


class BorderEdge(BaseModel):
    """Одна сторона рамки: стиль линии, толщина и цвет."""

    style: Literal["none", "nil", "single", "double", "dotted", "dashed", "thick", "other"] = "none"
    width_pt: float = 0.0
    space_pt: float = 0.0
    color: Optional[ColorValue] = None


class BoxBorders(BaseModel):
    """Рамки блока по четырем сторонам."""

    top: BorderEdge = Field(default_factory=BorderEdge)
    bottom: BorderEdge = Field(default_factory=BorderEdge)
    left: BorderEdge = Field(default_factory=BorderEdge)
    right: BorderEdge = Field(default_factory=BorderEdge)
    inside_h: Optional[BorderEdge] = None
    inside_v: Optional[BorderEdge] = None
    tl2br: Optional[BorderEdge] = None
    tr2bl: Optional[BorderEdge] = None


class TabStopSpec(BaseModel):
    """Позиция табуляции: выравнивание и заполнитель."""

    position_mm: float
    alignment: Literal["left", "center", "right", "decimal", "bar", "clear", "num"] = "left"
    leader: Literal["none", "dot", "hyphen", "underscore", "heavy", "middleDot"] = "none"


# =====================================================================
# 2. МЕТАДАННЫЕ, НАСТРОЙКИ, СТИЛИ И АУДИТ ФИЗИЧЕСКИХ СТРАНИЦ PDF
# =====================================================================

class DocumentMeta(BaseModel):
    """Сведения о файле и результате разбора, включая пагинацию и sync_score."""

    filename: str
    file_sha256: str
    source_format: Optional[str] = Field(
        default=None,
        description="Формат исходного файла (doc, rtf, odt, docx), если известно",
    )
    converted_via_lo: bool = Field(
        default=False,
        description="Файл был нормализован конвертацией LibreOffice перед разбором",
    )
    conversion_tool: Optional[str] = Field(
        default=None,
        description="Инструмент нормализации: passthrough, docker:<image> или local:<путь к soffice>",
    )
    conversion_duration_ms: Optional[int] = Field(
        default=None,
        description="Длительность конвертации legacy-формата в миллисекундах",
    )
    lo_version: Optional[str] = Field(
        default=None,
        description="Версия LibreOffice, выполнившего конвертацию",
    )
    lo_image: Optional[str] = Field(
        default=None,
        description="Docker-образ LibreOffice, если конвертация шла в контейнере",
    )
    pdf_filename: Optional[str] = None
    pdf_sha256: Optional[str] = None
    rendered_page_numbers_present: Optional[bool] = None
    pagination_source: Literal["pdf_exact", "docx_native_breaks", "geometric_fallback"] = Field(
        default="geometric_fallback",
        description="Источник разметки страниц: эталонный PDF, нативные маркеры Word или геометрическая оценка"
    )
    pdf_docx_sync_score: Optional[float] = Field(
        default=None,
        description="Доля совпадения текста между DOCX и PDF (0.0..1.0) для защиты от загрузки разных версий"
    )
    author: Optional[str] = None
    last_modified_by: Optional[str] = None
    created_iso: Optional[str] = None
    modified_iso: Optional[str] = None
    template_name: Optional[str] = None
    application: Optional[str] = None
    word_stat_pages: Optional[int] = None
    word_stat_words: Optional[int] = None
    word_stat_chars_no_spaces: Optional[int] = None
    word_stat_chars_with_spaces: Optional[int] = None


class PdfPageAudit(BaseModel):
    """
    Физический слепок листа из PDF-файла (зонирование верхнего/нижнего колонтитулов и рабочей области).
    Позволяет проверять реальные номера страниц в углах/по центру и заполнение листа.
    """
    page_num: int = Field(ge=1, description="Физический порядковый номер листа в PDF (1..N)")
    width_mm: float
    height_mm: float

    # Что реально отрендерено в зоне колонтитулов (в полях страницы)
    has_rendered_page_number: bool = False
    page_number_location: Literal["header", "footer", "none"] = "none"
    page_number_alignment: Literal["left", "center", "right", "none"] = "none"
    rendered_page_number_raw: Optional[str] = Field(
        default=None,
        description="Сырая строка в колонтитуле PDF, например '2', '- 2 -', 'Стр. 2'"
    )
    rendered_page_number_int: Optional[int] = None
    matches_physical_page_num: Optional[bool] = Field(
        default=None,
        description="True, если напечатанная цифра совпадает с физическим номером страницы"
    )
    page_number_font_name: Optional[str] = None
    page_number_font_size_pt: Optional[float] = None
    page_number_y_mm: Optional[float] = None
    page_number_x_mm: Optional[float] = None

    header_extra_text: Optional[str] = None
    footer_extra_text: Optional[str] = None

    # Границы основного текста на странице (для проверки висячих строк и пустых дыр внизу листа)
    body_top_y_mm: Optional[float] = None
    body_bottom_y_mm: Optional[float] = None
    body_left_x_mm: Optional[float] = Field(
        default=None,
        description="Левый край реального текста на листе (мин. x начала строк тела) в мм"
    )
    body_right_x_mm: Optional[float] = Field(
        default=None,
        description="Правый край реального текста на листе (макс. x окончания строк тела) в мм"
    )
    bottom_free_space_mm: Optional[float] = Field(
        default=None,
        description="Свободное место от нижней строки текста до начала нижнего поля в мм"
    )


class GlobalSettings(BaseModel):
    """Настройки word/settings.xml, влияющие на верстку."""

    auto_hyphenation: bool = False
    hyphenation_zone_mm: Optional[float] = None
    consecutive_hyphens_limit: Optional[int] = None
    mirror_margins: bool = False
    even_and_odd_headers: bool = False
    default_tab_stop_mm: float = 12.5
    track_revisions_enabled: bool = False
    has_document_protection: bool = False
    decimal_symbol: Optional[str] = None


class ThemeData(BaseModel):
    """Шрифты и цвета темы оформления."""

    major_font_latin: str = "Calibri Light"
    major_font_cyrillic: Optional[str] = None
    minor_font_latin: str = "Calibri"
    minor_font_cyrillic: Optional[str] = None
    color_scheme_hex: Dict[str, str] = Field(default_factory=dict)


class StyleDefinition(BaseModel):
    """Разобранное определение стиля и его свойства."""

    style_id: str
    name_ui: str
    style_type: Literal["paragraph", "character", "table", "numbering"]
    based_on_id: Optional[str] = None
    next_style_id: Optional[str] = None
    linked_style_id: Optional[str] = None
    is_default: bool = False
    is_custom: bool = False
    outline_lvl: Optional[int] = None
    raw_p_props: Optional[ParagraphPropertiesPartial] = None
    raw_r_props: Optional[RunFontPropertiesPartial] = None


# =====================================================================
# 3. ШРИФТЫ, РАНЫ И МИКРОТИПОГРАФИКА
# =====================================================================

class RunFontPropertiesPartial(BaseModel):
    """Частично заданные свойства символа (один слой каскада)."""

    font_ascii: Optional[str] = None
    font_hansi: Optional[str] = None
    font_cs: Optional[str] = None
    font_east_asia: Optional[str] = None
    size_pt: Optional[float] = None
    size_cs_pt: Optional[float] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[str] = None
    strike: Optional[bool] = None
    double_strike: Optional[bool] = None
    caps: Optional[bool] = None
    small_caps: Optional[bool] = None
    vert_align: Optional[Literal["baseline", "superscript", "subscript"]] = None
    color: Optional[ColorValue] = None
    highlight_color: Optional[str] = None
    shading_fill: Optional[ColorValue] = None
    char_spacing_pt: Optional[float] = None
    char_scale_pct: Optional[int] = None
    position_offset_pt: Optional[float] = None
    vanish_hidden: Optional[bool] = None


class RunFontPropertiesEffective(BaseModel):
    """Итоговые свойства символа с источниками значений."""

    font_ascii: str
    font_hansi: str
    font_cs: str
    font_source: PropertySource = "engine_default"

    size_pt: float
    size_source: PropertySource = "engine_default"

    bold: bool = False
    italic: bool = False
    underline: str = "none"
    strike: bool = False
    caps: bool = False
    small_caps: bool = False
    vert_align: Literal["baseline", "superscript", "subscript"] = "baseline"

    color: ColorValue = Field(default_factory=lambda: ColorValue(is_auto=True))
    highlight_color: Optional[str] = None
    shading_fill: Optional[ColorValue] = None

    char_spacing_pt: float = 0.0
    char_scale_pct: int = 100
    position_offset_pt: float = 0.0
    vanish_hidden: bool = False


class InlineSpecialChar(BaseModel):
    """Спецсимвол внутри рана: табуляция, разрыв, неразрывный дефис."""

    char_type: Literal[
        "page_break",
        "column_break",
        "line_break_shift_enter",
        "last_rendered_page_break",
        "tab",
        "soft_hyphen",
        "no_break_hyphen",
        "nbsp",
        "footnote_ref",
    ]
    char_offset_in_para: int


class TextRun(BaseModel):
    """Фрагмент текста с одинаковым форматированием."""

    run_id: str
    text: str
    visible_text: str
    start_char: int
    end_char: int
    char_style_id: Optional[str] = None

    direct_props: RunFontPropertiesPartial = Field(default_factory=RunFontPropertiesPartial)
    effective_props: RunFontPropertiesEffective

    is_hyperlink: bool = False
    hyperlink_target: Optional[str] = None
    hyperlink_anchor: Optional[str] = None

    in_field: bool = False
    field_instr: Optional[str] = None
    special_chars: List[InlineSpecialChar] = Field(default_factory=list)


class SpanLocation(BaseModel):
    """Положение спана внутри абзаца."""

    start_char: int
    end_char: int
    snippet: str
    issue_subtype: str


class MicroTypographyReport(BaseModel):
    """Нарушения микротипографики, найденные в абзаце."""

    leading_spaces_count: int = 0
    trailing_spaces_count: int = 0
    leading_tabs_count: int = 0
    soft_line_breaks_count: int = 0
    multiple_spaces: List[SpanLocation] = Field(default_factory=list)
    wrong_quotes: List[SpanLocation] = Field(default_factory=list)
    wrong_dashes: List[SpanLocation] = Field(default_factory=list)
    missing_nbsp: List[SpanLocation] = Field(default_factory=list)
    space_before_punctuation: List[SpanLocation] = Field(default_factory=list)


# =====================================================================
# 4. АБЗАЦЫ, СПИСКИ И ЗАГОЛОВКИ
# =====================================================================

ParagraphAlignment = Literal["left", "center", "right", "both", "distribute"]
LineSpacingRule = Literal["auto", "exact", "atLeast"]


class ParagraphPropertiesPartial(BaseModel):
    """Частично заданные свойства абзаца (один слой каскада)."""

    alignment: Optional[ParagraphAlignment] = None
    indent_left_mm: Optional[float] = None
    indent_right_mm: Optional[float] = None
    indent_first_line_mm: Optional[float] = None
    space_before_pt: Optional[float] = None
    space_after_pt: Optional[float] = None
    space_before_auto: Optional[bool] = None
    space_after_auto: Optional[bool] = None
    line_spacing_value: Optional[float] = None
    line_spacing_rule: Optional[LineSpacingRule] = None
    contextual_spacing: Optional[bool] = None
    keep_next: Optional[bool] = None
    keep_lines: Optional[bool] = None
    page_break_before: Optional[bool] = None
    widow_control: Optional[bool] = None
    outline_lvl: Optional[int] = None
    shading_fill: Optional[ColorValue] = None
    borders: Optional[BoxBorders] = None
    tab_stops: List[TabStopSpec] = Field(default_factory=list)
    num_id: Optional[int] = Field(
        default=None,
        description="w:numPr/w:numId (0 — явная отмена нумерации унаследованного стиля)"
    )
    ilvl: Optional[int] = Field(default=None, description="w:numPr/w:ilvl (0..8)")


class ParagraphPropertiesEffective(BaseModel):
    """Итоговые свойства абзаца с источниками значений."""

    alignment: ParagraphAlignment = "left"
    alignment_source: PropertySource = "engine_default"

    indent_left_mm: float = 0.0
    indent_right_mm: float = 0.0
    indent_first_line_mm: float = 0.0
    indent_first_line_source: PropertySource = "engine_default"

    space_before_pt: float = 0.0
    space_after_pt: float = 0.0
    space_before_auto: bool = False
    space_after_auto: bool = False

    line_spacing_value: float = 1.0
    line_spacing_rule: LineSpacingRule = "auto"
    line_spacing_source: PropertySource = "engine_default"

    contextual_spacing: bool = False
    keep_next: bool = False
    keep_lines: bool = False
    page_break_before: bool = False
    widow_control: bool = True
    outline_lvl: Optional[int] = None

    num_id: Optional[int] = Field(
        default=None,
        description="Итоговый w:numPr/w:numId после каскада (стиль абзаца -> прямое форматирование)"
    )
    ilvl: Optional[int] = Field(default=None, description="Итоговый w:numPr/w:ilvl после каскада")

    shading_fill: Optional[ColorValue] = None
    borders: BoxBorders = Field(default_factory=BoxBorders)
    tab_stops: List[TabStopSpec] = Field(default_factory=list)


class ListItemMetadata(BaseModel):
    """Данные списка: уровень, отрендеренный маркер, формат нумерации."""

    is_list_item: bool = True
    mechanism: Literal["openxml_numPr", "manual_text_prefix"]
    num_id: Optional[int] = None
    abstract_num_id: Optional[int] = None
    level: int = Field(default=0, ge=0, le=8)
    num_fmt: Literal[
        "bullet", "decimal", "lowerLetter", "upperLetter",
        "lowerRoman", "upperRoman", "russianLower", "russianUpper", "none", "other"
    ] = "none"
    lvl_text_template: Optional[str] = None
    rendered_marker: str
    marker_font_name: Optional[str] = None
    suffix_type: Literal["tab", "space", "nothing", "manual_spaces"] = "tab"
    body_text_without_marker: str
    first_letter_case: Literal["lowercase", "uppercase", "digit", "symbol", "empty"]
    ending_punctuation: Literal["semicolon", "period", "comma", "colon", "exclamation", "question", "none"]


class HeadingMetadata(BaseModel):
    """Данные заголовка: уровень, номер и семантическая роль."""

    is_heading: bool = True
    detection_confidence: Literal["explicit_outline_or_style", "structural_keyword_match", "heuristic_visual"]
    heading_kind: Literal[
        "structural_unnumbered",
        "numbered_section",
        "appendix_heading",
        "other"
    ]
    level: int = Field(ge=1, le=9)
    numbering_method: Literal["none", "plain_text", "openxml_autonum"] = "none"
    section_number_raw: Optional[str] = None
    has_trailing_dot_in_number: bool = False
    clean_title_text: str
    has_trailing_dot_in_title: bool = False
    has_word_glava_or_razdel: bool = False


class ParagraphBlock(BaseModel):
    """Абзац документа: текст, спаны, свойства, вложенные объекты."""

    block_id: str
    block_type: Literal["paragraph"] = "paragraph"
    section_idx: int
    container_type: Literal["body", "table_cell", "textbox", "footnote", "endnote", "header", "footer"] = "body"

    page_start_est: int = Field(ge=1)
    page_end_est: int = Field(ge=1)
    rendered_page_breaks_offsets: List[int] = Field(default_factory=list)

    # Точные физические координаты абзаца на листе из PDF (если передан PDF)
    pdf_y_top_mm: Optional[float] = None
    pdf_y_bottom_mm: Optional[float] = None
    pdf_x_left_mm: Optional[float] = None
    pdf_x_right_mm: Optional[float] = None
    pdf_lines_count_on_start_page: Optional[int] = Field(
        default=None,
        description="Число строк абзаца на первой странице (для детекции висячих строк при разрыве)"
    )
    pdf_lines_count_on_end_page: Optional[int] = Field(
        default=None,
        description="Число строк абзаца на второй странице (если абзац разорван между страницами)"
    )
    pdf_total_lines_count: Optional[int] = Field(
        default=None,
        description="Суммарное число строк PDF, покрывающих абзац (для оценки плотности/висячих строк)"
    )
    pdf_has_hyphenated_break: bool = Field(
        default=False,
        description="True, если хотя бы одна строка абзаца (кроме последней) в PDF заканчивается переносом"
    )

    style_id: Optional[str] = None
    style_name_ui: Optional[str] = None

    text_raw: str
    text_visible: str
    is_empty_or_whitespace: bool = False
    contains_only_visuals_or_math: bool = False

    direct_props: ParagraphPropertiesPartial = Field(default_factory=ParagraphPropertiesPartial)
    effective_props: ParagraphPropertiesEffective = Field(default_factory=ParagraphPropertiesEffective)
    para_mark_font_effective: Optional[RunFontPropertiesEffective] = None

    runs: List[TextRun] = Field(default_factory=list)
    list_info: Optional[ListItemMetadata] = None
    heading_info: Optional[HeadingMetadata] = None
    typography: MicroTypographyReport = Field(default_factory=MicroTypographyReport)

    embedded_visual_ids: List[str] = Field(default_factory=list)
    embedded_equation_ids: List[str] = Field(default_factory=list)
    footnote_ids: List[str] = Field(default_factory=list)


# =====================================================================
# 5. ТАБЛИЦЫ (МАТРИЦА ЯЧЕЕК, ГРАНИЦЫ, ПЕРЕНОСЫ И КООРДИНАТЫ В PDF)
# =====================================================================

class CellMarginsMm(BaseModel):
    """Внутренние отступы ячейки в миллиметрах."""

    top_mm: float = 0.0
    bottom_mm: float = 0.0
    left_mm: float = 1.9
    right_mm: float = 1.9


class TableCell(BaseModel):
    """Ячейка таблицы: содержимое, объединения, рамки и заливка."""

    cell_id: str
    row_idx: int
    col_idx: int
    col_span: int = Field(default=1, ge=1)
    row_span: int = Field(default=1, ge=1)
    v_merge_state: Literal["none", "restart", "continue"] = "none"
    is_merged_shadow: bool = False

    width_mm: Optional[float] = None
    v_align: Literal["top", "center", "bottom"] = "top"
    text_direction: Literal["lrTb", "tbRl", "btLr", "other"] = "lrTb"
    no_wrap: bool = False
    shading_fill: Optional[ColorValue] = None
    margins_mm: CellMarginsMm = Field(default_factory=CellMarginsMm)
    effective_borders: BoxBorders = Field(default_factory=BoxBorders)

    text_combined: str = ""
    is_visually_empty: bool = False
    content_type_heuristic: Literal["empty", "dash_placeholder", "numeric", "text", "mixed"] = "text"
    blocks: List[BlockUnion] = Field(default_factory=list)


class TableRow(BaseModel):
    """Строка таблицы: ячейки, высота и признак заголовка."""

    row_idx: int
    height_mm: Optional[float] = None
    height_rule: Literal["auto", "exact", "atLeast"] = "auto"
    is_tbl_header: bool = False
    cant_split: bool = False
    page_start_est: int = 1
    page_end_est: int = 1
    has_page_break_inside_or_before: bool = False

    # Физические координаты строки таблицы из PDF
    pdf_y_top_mm: Optional[float] = None
    pdf_y_bottom_mm: Optional[float] = None
    pdf_x_left_mm: Optional[float] = None
    pdf_x_right_mm: Optional[float] = None
    pdf_repeated_header_above: bool = Field(
        default=False,
        description="True, если при переносе строки на новую страницу в PDF над ней реально отрендерилась повторная шапка"
    )

    cells: List[TableCell] = Field(default_factory=list)


class TableBlock(BaseModel):
    """Таблица: сетка, строки, стиль, подпись и связь с продолжением."""

    block_id: str
    block_type: Literal["table"] = "table"
    section_idx: int
    page_start_est: int = 1
    page_end_est: int = 1
    spans_multiple_pages: bool = False

    pdf_y_top_mm: Optional[float] = None
    pdf_y_bottom_mm: Optional[float] = None
    pdf_x_left_mm: Optional[float] = None
    pdf_x_right_mm: Optional[float] = None

    style_id: Optional[str] = None
    alignment: Literal["left", "center", "right"] = "left"
    is_floating_table: bool = False
    indent_left_mm: float = 0.0

    total_width_mm: Optional[float] = None
    width_type: Literal["dxa", "pct", "auto", "nil"] = "auto"
    grid_column_widths_mm: List[float] = Field(default_factory=list)
    exceeds_page_text_width: bool = False

    table_borders: BoxBorders = Field(default_factory=BoxBorders)
    default_cell_margins_mm: CellMarginsMm = Field(default_factory=CellMarginsMm)
    rows: List[TableRow] = Field(default_factory=list)

    caption_block_id: Optional[str] = None
    continuation_block_ids: List[str] = Field(default_factory=list)
    continuation_of_table_id: Optional[str] = Field(
        default=None,
        description="block_id основной таблицы, продолжением которой является этот блок (по 'Продолжение таблицы N')"
    )
    has_column_numbers_row: bool = False


# =====================================================================
# 6. ВИЗУАЛЬНЫЕ ОБЪЕКТЫ И МАТЕМАТИЧЕСКИЕ ФОРМУЛЫ
# =====================================================================

class ShapeTextboxElement(BaseModel):
    """Фигура DrawingML с геометрией и вложенными абзацами."""

    shape_id: str
    geometry_preset: Optional[str] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    fill_color: Optional[ColorValue] = None
    stroke_color: Optional[ColorValue] = None
    stroke_width_pt: Optional[float] = None
    vertical_anchor: Literal["top", "center", "bottom", "unknown"] = "unknown"
    has_text: bool = False
    paragraphs: List[ParagraphBlock] = Field(default_factory=list)


class ChartDetails(BaseModel):
    """Диаграмма: тип, серии и подписи."""

    chart_rel_id: str
    chart_type: List[str] = Field(default_factory=list)
    has_internal_title: bool = False
    internal_title_text: Optional[str] = None
    detected_fonts: List[str] = Field(default_factory=list)
    detected_font_sizes_pt: List[float] = Field(default_factory=list)


class RasterImageDetails(BaseModel):
    """Растровое изображение: файл в пакете, размеры, DPI, обрезка."""

    media_target: str
    content_type: str
    pixel_width: Optional[int] = None
    pixel_height: Optional[int] = None
    effective_dpi_x: Optional[float] = None
    effective_dpi_y: Optional[float] = None
    is_aspect_ratio_distorted: bool = False
    crop_top_pct: float = 0.0
    crop_bottom_pct: float = 0.0
    crop_left_pct: float = 0.0
    crop_right_pct: float = 0.0


class VisualObject(BaseModel):
    """Визуальный объект документа: картинка, фигура, диаграмма или OLE."""

    visual_id: str
    parent_block_id: str
    section_idx: int
    page_est: int = 1

    # Физические координаты иллюстрации на листе из PDF
    pdf_x_left_mm: Optional[float] = None
    pdf_y_top_mm: Optional[float] = None
    pdf_x_right_mm: Optional[float] = None
    pdf_y_bottom_mm: Optional[float] = None

    visual_kind: Literal[
        "raster_image",
        "vector_shape",
        "shape_group_canvas",
        "smart_art",
        "chart",
        "ole_object",
        "vml_legacy",
    ]

    placement: Literal["inline", "anchor_floating"]
    wrap_type: Literal["inline", "none", "square", "tight", "through", "topAndBottom"] = "inline"
    horizontal_relative_from: Optional[str] = None
    horizontal_align_anchor: Optional[str] = None

    width_mm: float
    height_mm: float
    exceeds_page_margins: bool = False
    has_visible_border_frame: bool = False
    border_frame: Optional[BorderEdge] = None

    parent_para_alignment: ParagraphAlignment = "left"
    parent_para_first_line_indent_mm: float = 0.0
    parent_para_line_spacing_rule: LineSpacingRule = "auto"
    parent_para_keep_next: bool = False

    image_details: Optional[RasterImageDetails] = None
    chart_details: Optional[ChartDetails] = None
    shapes: List[ShapeTextboxElement] = Field(default_factory=list)

    caption_block_id: Optional[str] = None


class MathTokenStyleIssue(BaseModel):
    """Нарушение оформления в математическом токене."""

    token_text: str
    issue: Literal[
        "cyrillic_in_italic",
        "function_in_italic",
        "wrong_font_family",
        "wrong_font_size",
    ]


class EquationObject(BaseModel):
    """Формула: OMML-разметка, линейный текст, номер и шрифты."""

    equation_id: str
    parent_block_id: str
    section_idx: int
    page_est: int = 1

    representation_kind: Literal[
        "omml_para",
        "omml_inline",
        "ole_mathtype_eq3",
        "plain_text_pseudo",
        "image_screenshot",
    ]
    is_standalone_line: bool

    text_linearized: str
    latex_approx: Optional[str] = None

    alignment: Literal["center", "left", "right", "centerGroup"] = "center"
    parent_para_first_line_indent_mm: float = 0.0

    has_number: bool = False
    number_raw: Optional[str] = None
    number_clean: Optional[str] = None
    number_positioning_method: Literal[
        "right_tab_stop",
        "invisible_table",
        "spaces_hack",
        "inline_next_to_eq",
        "none"
    ] = "none"
    numbering_scheme: Optional[
        Literal["continuous_arabic", "section_based", "appendix_based", "invalid"]
    ] = None

    punctuation_after_equation: Optional[str] = None

    fonts_detected: List[str] = Field(default_factory=list)
    base_size_pt: Optional[float] = None
    style_issues: List[MathTokenStyleIssue] = Field(default_factory=list)

    has_where_explication: bool = False
    where_block_ids: List[str] = Field(default_factory=list)
    where_starts_lowercase_no_colon: Optional[bool] = None

    is_mentioned_in_text: bool = False
    first_mention_block_id: Optional[str] = None
    first_mention_page_est: Optional[int] = None
    is_mentioned_before_appearance: bool = False
    all_mention_ids: List[str] = Field(default_factory=list)


# =====================================================================
# 7. ОГЛАВЛЕНИЕ (TOC), СНОСКИ И РАЗДЕЛЫ (SECTIONS)
# =====================================================================

class TocEntryItem(BaseModel):
    """Запись оглавления и результат ее сверки с заголовком."""

    entry_index: int
    block_id: str
    raw_text: str
    extracted_number: Optional[str] = None
    extracted_title: str
    extracted_page_str: Optional[str] = None
    indent_level: int = 1
    has_dot_leader: bool = True
    has_hyperlink_blue_or_underline: bool = False
    matched_heading_block_id: Optional[str] = None
    actual_heading_page_est: Optional[int] = None
    page_number_delta: Optional[int] = Field(
        default=None,
        description="Разность «номер в оглавлении минус фактическая страница заголовка»",
    )
    is_title_exact_match: Optional[bool] = None
    is_page_number_match: Optional[bool] = None


class TocContainerBlock(BaseModel):
    """Свернутое оглавление вместе с исходными абзацами и их форматированием."""

    block_id: str
    block_type: Literal["toc_block"] = "toc_block"
    section_idx: int
    page_start_est: int = 1
    page_end_est: int = 1
    is_automated_field_or_sdt: bool
    toc_heading_block_id: Optional[str] = None
    entries: List[TocEntryItem] = Field(default_factory=list)
    raw_paragraphs: List[ParagraphBlock] = Field(
        default_factory=list,
        description="Исходные абзацы оглавления с форматированием (отступы, табуляции, раны)"
    )


BlockUnion = Annotated[
    Union[ParagraphBlock, TableBlock, TocContainerBlock],
    Field(discriminator="block_type")
]


class HeaderFooterContent(BaseModel):
    """Содержимое колонтитула: блоки и признаки пустоты."""

    hf_type: Literal["default", "first", "even"]
    is_linked_to_previous: bool
    is_empty: bool = True
    has_page_field: bool = False
    has_hardcoded_number: bool = False
    page_number_alignment: Optional[ParagraphAlignment] = None
    page_number_font_effective: Optional[RunFontPropertiesEffective] = None
    page_number_affixes: Optional[str] = None
    has_shapes_or_stamps: bool = False
    blocks: List[BlockUnion] = Field(default_factory=list)


class PageMarginsMm(BaseModel):
    """Поля страницы в миллиметрах."""

    top_mm: float = 20.0
    bottom_mm: float = 20.0
    left_mm: float = 30.0
    right_mm: float = 15.0
    gutter_mm: float = 0.0
    header_dist_mm: float = 12.5
    footer_dist_mm: float = 12.5


class SectionProperties(BaseModel):
    """Свойства секции: лист, поля, колонтитулы и диапазон страниц."""

    section_idx: int
    start_type: Literal["nextPage", "continuous", "evenPage", "oddPage", "nextColumn"] = "nextPage"
    page_width_mm: float = 210.0
    page_height_mm: float = 297.0
    orientation: Literal["portrait", "landscape"] = "portrait"
    is_a4_standard: bool = True

    margins: PageMarginsMm = Field(default_factory=PageMarginsMm)
    text_area_width_mm: float = 165.0

    v_align: Literal["top", "center", "both", "bottom"] = "top"
    different_first_page: bool = False

    page_num_fmt: str = "decimal"
    page_num_start_override: Optional[int] = None

    page_borders: BoxBorders = Field(default_factory=BoxBorders)
    page_start_est: int = 1
    page_end_est: int = 1

    headers: Dict[Literal["default", "first", "even"], HeaderFooterContent] = Field(default_factory=dict)
    footers: Dict[Literal["default", "first", "even"], HeaderFooterContent] = Field(default_factory=dict)


class FootnoteItem(BaseModel):
    """Сноска или концевая сноска с текстом и страницей ссылки."""

    footnote_id: str
    kind: Literal["footnote", "endnote"] = "footnote"
    referenced_in_block_id: Optional[str] = None
    page_est: Optional[int] = None
    blocks: List[BlockUnion] = Field(default_factory=list)


# =====================================================================
# 8. СТРУКТУРНОЕ ДЕРЕВО, ПОДПИСИ, ПЕРЕКРЕСТНЫЕ ССЫЛКИ И БИБЛИОГРАФИЯ
# =====================================================================

class DocumentTreeNode(BaseModel):
    """Узел дерева документа: заголовок, роль, страницы и прямые блоки."""

    node_id: str
    heading_block_id: Optional[str] = None
    title: str
    section_number: Optional[str] = None
    level: int = Field(ge=0, le=9)
    semantic_role: Literal[
        "title_page", "task_sheet", "abstract_referat", "toc",
        "abbreviations", "introduction", "main_chapter", "sub_section",
        "conclusion", "bibliography", "appendix", "unknown"
    ] = "unknown"
    page_start_est: int
    page_end_est: int
    direct_block_ids: List[str] = Field(default_factory=list)
    children: List[DocumentTreeNode] = Field(default_factory=list)


class NumberedCaptionEntity(BaseModel):
    """Пронумерованный объект с подписью: рисунок, таблица, приложение."""

    entity_id: str
    entity_type: Literal["figure", "table", "appendix"]
    target_object_id: Optional[str] = None
    caption_block_id: str
    structure_node_id: Optional[str] = Field(
        default=None,
        description="Узел document_tree, к которому относится объект (для приложений — узел заголовка)",
    )
    page_est: int

    prefix_word: str
    number_str: str
    numbering_scheme: Literal["continuous_arabic", "section_based", "appendix_based", "invalid"]
    separator: Optional[str] = None
    title_text: str
    has_trailing_dot: bool = False

    is_mentioned_in_text: bool = False
    first_mention_block_id: Optional[str] = None
    first_mention_page_est: Optional[int] = None
    is_mentioned_before_appearance: bool = False
    all_mention_ids: List[str] = Field(default_factory=list)


class TextCrossReferenceMention(BaseModel):
    """Упоминание объекта в тексте и результат его разрешения."""

    mention_id: str
    block_id: str
    page_est: int
    start_char: int
    end_char: int
    raw_snippet: str

    ref_type: Literal[
        "figure", "table", "equation", "bibliography", "appendix", "section", "broken_word_field"
    ]
    extracted_targets: List[str] = Field(default_factory=list)
    resolved_entity_ids: List[str] = Field(default_factory=list)
    resolved_structure_node_ids: List[str] = Field(
        default_factory=list,
        description="Узлы document_tree, разрешенные для ссылок на разделы и главы",
    )
    uses_short_abbrev: bool = False
    has_nbsp_before_number: bool = False


class BibliographyEntry(BaseModel):
    """Запись списка литературы с разобранными полями."""

    bib_id: str
    block_id: str
    order_index: int = Field(ge=1)
    raw_number_prefix: Optional[str] = None
    raw_text: str

    detected_category: Literal[
        "book_monograph", "journal_article", "conference_paper",
        "electronic_resource", "normative_act_law", "standard_gost",
        "dissertation_autoref", "patent", "unknown"
    ] = "unknown"

    has_slash_responsibility: bool = False
    has_double_slash_source: bool = False
    dot_dash_count: int = 0
    year_extracted: Optional[int] = None
    pages_extracted: Optional[str] = None
    isbn_or_issn: Optional[str] = None
    urls: List[str] = Field(default_factory=list)
    access_date_raw: Optional[str] = None
    has_text_type_designation: bool = False

    is_cited_in_text: bool = False
    first_citation_appearance_rank: Optional[int] = None
    citation_mention_ids: List[str] = Field(default_factory=list)


class CrossReferenceIndex(BaseModel):
    """Сводный индекс перекрестных ссылок документа."""

    figures: List[NumberedCaptionEntity] = Field(default_factory=list)
    tables: List[NumberedCaptionEntity] = Field(default_factory=list)
    Apps: List[NumberedCaptionEntity] = Field(default_factory=list)
    equations: List[EquationObject] = Field(default_factory=list)
    bibliography: List[BibliographyEntry] = Field(default_factory=list)
    mentions: List[TextCrossReferenceMention] = Field(default_factory=list)


# =====================================================================
# 9. АНОМАЛИИ, АРТЕФАКТЫ РЕЦЕНЗИРОВАНИЯ И КОРНЕВОЙ КОНТЕЙНЕР
# =====================================================================

class TrackChangeArtifact(BaseModel):
    """Артефакт правки: автор, дата и фрагмент."""

    artifact_type: Literal["insertion", "deletion", "move_from", "move_to", "format_change"]
    author: Optional[str] = None
    date_iso: Optional[str] = None
    block_id: str
    page_est: int
    text_snippet: str


class CommentArtifact(BaseModel):
    """Артефакт комментария: автор, дата и текст."""

    comment_id: str
    author: Optional[str] = None
    initials: Optional[str] = None
    date_iso: Optional[str] = None
    block_id: str
    page_est: int
    commented_text: str
    comment_body: str


class ConsecutiveEmptyParagraphs(BaseModel):
    """Серия пустых абзацев подряд."""

    start_block_id: str
    end_block_id: str
    section_idx: int
    page_est: int
    count: int = Field(ge=1)


class TocPageNumberWithoutRenderedNumber(BaseModel):
    """Номер страницы в оглавлении без номера в колонтитуле."""

    entry_block_id: str
    extracted_page_str: str
    matched_heading_block_id: Optional[str] = None


class FigureNumberingIssue(BaseModel):
    """Нарушение сквозной нумерации подписей рисунков."""

    issue_kind: Literal["duplicate", "gap", "out_of_order"]
    number_str: str
    caption_block_id: Optional[str] = None
    previous_number_str: Optional[str] = None


class DocumentAnomalies(BaseModel):
    """Все найденные аномалии оформления."""

    pdf_docx_mismatch_warning: Optional[str] = Field(
        default=None,
        description="Предупреждение, если загруженный PDF текстуально расходится с DOCX"
    )
    unaccepted_track_changes: List[TrackChangeArtifact] = Field(default_factory=list)
    leftover_comments: List[CommentArtifact] = Field(default_factory=list)
    empty_paragraph_clusters: List[ConsecutiveEmptyParagraphs] = Field(default_factory=list)
    hidden_text_block_ids: List[str] = Field(default_factory=list)
    embedded_ole_block_ids: List[str] = Field(
        default_factory=list,
        description="Абзацы с внедрёнными OLE-объектами (Excel и др.), требующими ручной проверки",
    )
    broken_field_block_ids: List[str] = Field(default_factory=list)
    shaded_background_block_ids: List[str] = Field(default_factory=list)
    non_black_text_block_ids: List[str] = Field(default_factory=list)
    toc_page_numbers_without_rendered_numbers: List[TocPageNumberWithoutRenderedNumber] = Field(
        default_factory=list
    )
    uncaptioned_visual_ids: List[str] = Field(
        default_factory=list,
        description="visual_id иллюстраций без подписи (рисунок/скриншот)",
    )
    figure_captions_without_visual_ids: List[str] = Field(
        default_factory=list,
        description="block_id подписей рисунков, для которых не найден сам объект",
    )
    unreferenced_figure_ids: List[str] = Field(
        default_factory=list,
        description="entity_id рисунков, на которые нет ссылок в тексте документа",
    )
    figure_numbering_issues: List[FigureNumberingIssue] = Field(
        default_factory=list,
        description="Пропуски, дубли и нарушения порядка в нумерации рисунков",
    )


class ParsedDocxDocument(BaseModel):
    """Корневая модель отчета: блоки, структура, метаданные и аномалии."""

    schema_version: str = "2.4.0"
    meta: DocumentMeta
    settings: GlobalSettings
    theme: ThemeData
    styles: Dict[str, StyleDefinition] = Field(default_factory=dict)

    sections: List[SectionProperties] = Field(default_factory=list)
    pdf_pages_audit: List[PdfPageAudit] = Field(
        default_factory=list,
        description="Постраничный аудит колонтитулов, номеров страниц и границ текста из PDF"
    )
    blocks: List[BlockUnion] = Field(default_factory=list)
    visuals: Dict[str, VisualObject] = Field(default_factory=dict)
    footnotes: List[FootnoteItem] = Field(default_factory=list)

    document_tree: List[DocumentTreeNode] = Field(default_factory=list)
    cross_ref_index: CrossReferenceIndex = Field(default_factory=CrossReferenceIndex)
    anomalies: DocumentAnomalies = Field(default_factory=DocumentAnomalies)

    @model_serializer(mode="wrap")
    def serialize_without_pdf_fields(self, handler):
        """Сериализует модель без полей, зависящих от PDF."""
        data = handler(self)
        if self.meta.pdf_filename is not None:
            return data

        def strip_pdf_fields(value):
            if isinstance(value, dict):
                return {
                    key: strip_pdf_fields(item)
                    for key, item in value.items()
                    if not key.startswith("pdf_")
                    and key != "rendered_page_numbers_present"
                }
            if isinstance(value, list):
                return [strip_pdf_fields(item) for item in value]
            return value

        return strip_pdf_fields(data)


StyleDefinition.model_rebuild()
TableCell.model_rebuild()
TableBlock.model_rebuild()
TocContainerBlock.model_rebuild()
HeaderFooterContent.model_rebuild()
FootnoteItem.model_rebuild()
DocumentTreeNode.model_rebuild()
ParsedDocxDocument.model_rebuild()