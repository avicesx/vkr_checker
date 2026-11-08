"""Оценка вёрстки PDF: поля, зоны колонтитулов, номера страниц и аудит листов.

Модуль восстанавливает то, что в DOCX задано в ``w:sectPr`` и колонтитулах:
границы текстовой области, зоны верхнего и нижнего колонтитула, наличие и
положение напечатанного номера страницы, свободное место внизу листа и группы
страниц одного размера. Все значения измерены по координатам текста, поэтому
поля и зоны считаются оценкой: точными остаются только номера листов.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from parser.core.schema import PageMarginsMm, PdfPageAudit, SectionProperties
from parser.docx.pdf_text_utils import parse_page_number

from .extractor import PdfDocument, PdfLine, PdfPage

#: Верхняя доля листа, где может стоять номер в верхнем колонтитуле.
HEADER_BAND_FRACTION = 0.15

#: Нижняя доля листа, где может стоять номер в нижнем колонтитуле.
FOOTER_BAND_FRACTION = 0.85

#: Допуск, с которым колонтитул отделяется от оцененной границы текста, мм.
ZONE_TOLERANCE_MM = 1.0

#: Максимальная длина строки, которую еще можно считать колонтитулом.
MAX_COLONTITLE_LENGTH = 120

#: Допустимые границы оценки полей: вне них значение считается артефактом.
MIN_MARGIN_MM = 3.0
MAX_MARGIN_MM = 80.0

#: Полуширина центральной зоны листа для выравнивания номера "по центру", мм.
CENTER_ALIGNMENT_TOLERANCE_MM = 18.0

#: Ширина и высота листа A4 в миллиметрах.
A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0

#: Допуск распознавания формата A4, мм.
A4_TOLERANCE_MM = 2.0

#: Минимальный кегль строки, которую можно принять за номер страницы.
#: Маркеры сносок набирают мелким кеглем (6-7 pt) и номером страницы не являются.
MIN_PAGE_NUMBER_FONT_SIZE_PT = 7.5

#: Значение поля по умолчанию, если текста на листах нет вовсе.
DEFAULT_MARGINS_MM = PageMarginsMm()


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Считает процентиль линейной интерполяцией (fraction — доля от 0 до 1)."""
    if not values:
        raise ValueError("Процентиль не определен для пустого набора значений")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    weight = position - lower_index
    return ordered[lower_index] * (1.0 - weight) + ordered[upper_index] * weight


def _clamp_margin(value_mm: float) -> float:
    """Ограничивает поле страницы разумным диапазоном и округляет до 0.1 мм."""
    return round(min(MAX_MARGIN_MM, max(MIN_MARGIN_MM, value_mm)), 1)


def _median(values: Sequence[float]) -> Optional[float]:
    """Медиана набора значений или None для пустого набора."""
    if not values:
        return None
    return round(_percentile(values, 0.5), 2)


@dataclass
class PageNumberInfo:
    """Напечатанный на листе номер страницы и его положение."""

    line: PdfLine
    location: Literal["header", "footer"]
    raw_text: str
    value: Optional[int]
    alignment: Literal["left", "center", "right"]


@dataclass
class PageZones:
    """Разделение строк листа на колонтитулы и тело документа."""

    page_num: int
    width_mm: float
    height_mm: float
    header_lines: List[PdfLine] = field(default_factory=list)
    footer_lines: List[PdfLine] = field(default_factory=list)
    body_lines: List[PdfLine] = field(default_factory=list)
    page_number: Optional[PageNumberInfo] = None

    @property
    def body_top_mm(self) -> Optional[float]:
        """Верхняя граница текста тела документа в мм."""
        return min((line.y0_mm for line in self.body_lines), default=None)

    @property
    def body_bottom_mm(self) -> Optional[float]:
        """Нижняя граница текста тела документа в мм."""
        return max((line.y1_mm for line in self.body_lines), default=None)

    @property
    def body_left_mm(self) -> Optional[float]:
        """Левый край текста тела документа в мм."""
        return min((line.x0_mm for line in self.body_lines), default=None)

    @property
    def body_right_mm(self) -> Optional[float]:
        """Правый край текста тела документа в мм."""
        return max((line.x1_mm for line in self.body_lines), default=None)

    @property
    def has_rendered_page_number(self) -> bool:
        """True, если на листе напечатан распознанный номер страницы."""
        return self.page_number is not None

    @property
    def header_extra_text(self) -> Optional[str]:
        """Текст верхнего колонтитула без номера страницы."""
        texts = [line.text for line in self.header_lines if line.text]
        return " | ".join(texts) if texts else None

    @property
    def footer_extra_text(self) -> Optional[str]:
        """Текст нижнего колонтитула без номера страницы."""
        texts = [line.text for line in self.footer_lines if line.text]
        return " | ".join(texts) if texts else None


@dataclass
class SectionLayout:
    """Оценка полей для группы страниц одинакового размера и ориентации."""

    page_nums: List[int]
    width_mm: float
    height_mm: float
    orientation: Literal["portrait", "landscape"]
    margins: PageMarginsMm
    text_area_width_mm: float

    @property
    def is_a4_standard(self) -> bool:
        """True, если размер листа совпадает с A4 в пределах допуска."""
        return (
            abs(self.width_mm - A4_WIDTH_MM) <= A4_TOLERANCE_MM
            and abs(self.height_mm - A4_HEIGHT_MM) <= A4_TOLERANCE_MM
        )

    def section_properties(self, section_idx: int) -> SectionProperties:
        """Собирает SectionProperties с точными границами страниц группы."""
        return SectionProperties(
            section_idx=section_idx,
            page_width_mm=self.width_mm,
            page_height_mm=self.height_mm,
            orientation=self.orientation,
            is_a4_standard=self.is_a4_standard,
            margins=self.margins,
            text_area_width_mm=self.text_area_width_mm,
            page_start_est=self.page_nums[0],
            page_end_est=self.page_nums[-1],
        )


@dataclass
class PdfLayout:
    """Итог оценки вёрстки: зоны листов, группы страниц, аудит и секции."""

    zones: Dict[int, PageZones]
    section_layouts: List[SectionLayout]
    pages_audit: List[PdfPageAudit]
    sections: List[SectionProperties]
    rendered_page_numbers_present: bool
    median_line_gap_mm: float

    def section_idx_for_page(self, page_num: int) -> int:
        """Возвращает индекс секции, к которой относится физическая страница."""
        for index, layout in enumerate(self.section_layouts):
            if layout.page_nums and layout.page_nums[0] <= page_num <= layout.page_nums[-1]:
                return index
        return 0

    def margin_for_page(self, page_num: int) -> PageMarginsMm:
        """Возвращает оцененные поля той секции, где лежит страница."""
        return self.section_layouts[self.section_idx_for_page(page_num)].margins


class PdfLayoutEstimator:
    """Оценивает вёрстку по строкам PDF, не меняя извлеченные данные."""

    def __init__(self, document: PdfDocument) -> None:
        """Запоминает документ, для которого считается вёрстка."""
        self.document = document

    def estimate(self) -> PdfLayout:
        """Возвращает зоны листов, группы страниц, аудит и синтезированные секции."""
        candidates = {
            page.page_num: self._page_number_candidates(page) for page in self.document.pages
        }
        dominant = self._dominant_number_location(candidates)
        page_numbers = {
            page.page_num: self._find_page_number_for_page(
                page, candidates[page.page_num], dominant
            )
            for page in self.document.pages
        }
        preliminary = self._preliminary_margins(page_numbers)
        default_margins = (DEFAULT_MARGINS_MM.top_mm, DEFAULT_MARGINS_MM.bottom_mm)
        zones = {
            page.page_num: self._zone_page(
                page,
                page_numbers[page.page_num],
                preliminary.get(self._size_key(page), default_margins),
            )
            for page in self.document.pages
        }

        groups = self._group_pages(zones)
        page_offset = self._page_number_offset(zones.values())
        section_layouts: List[SectionLayout] = []
        sections: List[SectionProperties] = []
        for section_idx, page_nums in enumerate(groups):
            layout = self._estimate_section_layout(page_nums, zones)
            section_layouts.append(layout)
            sections.append(layout.section_properties(section_idx))

        section_by_page: Dict[int, SectionProperties] = {}
        for page_nums, section in zip(groups, sections):
            for page_num in page_nums:
                section_by_page[page_num] = section

        pages_audit = [
            self._build_audit(zones[page.page_num], section_by_page[page.page_num], page_offset)
            for page in self.document.pages
        ]

        return PdfLayout(
            zones=zones,
            section_layouts=section_layouts,
            pages_audit=pages_audit,
            sections=sections,
            rendered_page_numbers_present=any(
                zone.has_rendered_page_number for zone in zones.values()
            ),
            median_line_gap_mm=self._median_line_gap(zones.values()),
        )

    # -----------------------------------------------------------------
    # Зонирование листа: колонтитулы, номер страницы и тело документа
    # -----------------------------------------------------------------

    @staticmethod
    def _size_key(page: PdfPage) -> Tuple[float, float]:
        """Ключ группы страниц: размер листа в миллиметрах."""
        return (page.width_mm, page.height_mm)

    def _page_number_candidates(
        self, page: PdfPage
    ) -> Tuple[Optional[PdfLine], Optional[PdfLine]]:
        """Ищет строки-кандидаты на номер страницы в верхней и нижней зонах.

        Кандидатом считается короткая строка из цифр (возможно, в тире или с
        подписью "Стр."), набранная кеглем не меньше 7.5 pt и стоящая у самого
        края листа. В нижнем колонтитуле берется самая нижняя строка, в
        верхнем — самая верхняя; строки таблиц пропускаются.
        """
        header_limit = page.height_mm * HEADER_BAND_FRACTION
        footer_limit = page.height_mm * FOOTER_BAND_FRACTION

        header_candidates: List[PdfLine] = []
        footer_candidates: List[PdfLine] = []
        for line in page.lines:
            text = line.text
            if not text or len(text) > 20 or line.in_table:
                continue
            if line.font_size_pt < MIN_PAGE_NUMBER_FONT_SIZE_PT:
                continue
            if parse_page_number(text) is None:
                continue
            if line.y1_mm <= header_limit:
                header_candidates.append(line)
            elif line.y0_mm >= footer_limit:
                footer_candidates.append(line)

        header = min(header_candidates, key=lambda item: item.y0_mm) if header_candidates else None
        footer = max(footer_candidates, key=lambda item: item.y1_mm) if footer_candidates else None
        return header, footer

    @staticmethod
    def _dominant_number_location(
        candidates: Dict[int, Tuple[Optional[PdfLine], Optional[PdfLine]]]
    ) -> Literal["header", "footer"]:
        """Определяет, в каком колонтитуле документ нумерует страницы.

        Ориентация по всему документу важнее одной страницы: одиночный номер
        внизу листа (например, цифра в сноске) не должен перевесить сквозную
        нумерацию в верхнем колонтитуле.
        """
        header_pages = sum(1 for header, _ in candidates.values() if header is not None)
        footer_pages = sum(1 for _, footer in candidates.values() if footer is not None)
        return "footer" if footer_pages > header_pages else "header"

    def _find_page_number_for_page(
        self,
        page: PdfPage,
        lines: Tuple[Optional[PdfLine], Optional[PdfLine]],
        dominant: Literal["header", "footer"],
    ) -> Optional[PageNumberInfo]:
        """Выбирает номер страницы с приоритетом преобладающего колонтитула."""
        header, footer = lines
        if dominant == "footer":
            line, location = (footer, "footer") if footer is not None else (header, "header")
        else:
            line, location = (header, "header") if header is not None else (footer, "footer")
        if line is None:
            return None
        return self._build_page_number_info(page, line, location)

    def _build_page_number_info(
        self,
        page: PdfPage,
        line: PdfLine,
        location: Literal["header", "footer"],
    ) -> PageNumberInfo:
        """Собирает PageNumberInfo с выравниванием номера относительно центра."""
        center_x = line.center_x_mm
        page_center = page.width_mm / 2.0
        if abs(center_x - page_center) <= CENTER_ALIGNMENT_TOLERANCE_MM:
            alignment: Literal["left", "center", "right"] = "center"
        elif center_x < page_center:
            alignment = "left"
        else:
            alignment = "right"
        return PageNumberInfo(
            line=line,
            location=location,
            raw_text=line.text,
            value=parse_page_number(line.text),
            alignment=alignment,
        )

    def _preliminary_margins(
        self, page_numbers: Dict[int, Optional[PageNumberInfo]]
    ) -> Dict[Tuple[float, float], Tuple[float, float]]:
        """Черновая оценка верхнего и нижнего полей по первым/последним строкам.

        Номер страницы из подсчета исключается: он стоит в колонтитуле, а не на
        границе текстовой области. Верхнее поле берется по нижнему процентилю
        первых строк (чтобы заголовки с отбивкой не завышали оценку), нижнее —
        по верхнему процентилю последних строк.
        """
        collected: Dict[Tuple[float, float], Tuple[List[float], List[float], List[float]]] = {}
        for page in self.document.pages:
            key = self._size_key(page)
            number_line = page_numbers.get(page.page_num)
            number_line = number_line.line if number_line else None
            lines = [line for line in page.lines if line is not number_line and line.text]
            if not lines:
                continue
            tops, bottoms, heights = collected.setdefault(key, ([], [], []))
            tops.append(min(line.y0_mm for line in lines))
            bottoms.append(max(line.y1_mm for line in lines))
            heights.append(page.height_mm)

        preliminary: Dict[Tuple[float, float], Tuple[float, float]] = {}
        for key, (tops, bottoms, heights) in collected.items():
            top_margin = _clamp_margin(_percentile(tops, 0.25))
            bottom_margin = _clamp_margin(heights[0] - _percentile(bottoms, 0.95))
            preliminary[key] = (top_margin, bottom_margin)
        return preliminary

    def _zone_page(
        self,
        page: PdfPage,
        page_number: Optional[PageNumberInfo],
        preliminary_margins: Tuple[float, float],
    ) -> PageZones:
        """Делит строки листа на верхний/нижний колонтитул и тело документа.

        Колонтитулом считается строка, целиком лежащая выше оцененного верхнего
        поля или ниже оцененного нижнего поля: тело документа в эти зоны не
        заходит. Номер страницы хранится отдельно и в текст колонтитула не
        попадает.
        """
        top_margin, bottom_margin = preliminary_margins
        header_limit = top_margin - ZONE_TOLERANCE_MM
        footer_limit = page.height_mm - bottom_margin + ZONE_TOLERANCE_MM
        number_line = page_number.line if page_number else None

        header_lines: List[PdfLine] = []
        footer_lines: List[PdfLine] = []
        body_lines: List[PdfLine] = []
        for line in page.lines:
            if line is number_line:
                continue
            if not line.text:
                continue
            if line.y1_mm <= header_limit and len(line.text) <= MAX_COLONTITLE_LENGTH:
                header_lines.append(line)
            elif line.y0_mm >= footer_limit and len(line.text) <= MAX_COLONTITLE_LENGTH:
                footer_lines.append(line)
            else:
                body_lines.append(line)

        return PageZones(
            page_num=page.page_num,
            width_mm=page.width_mm,
            height_mm=page.height_mm,
            header_lines=header_lines,
            footer_lines=footer_lines,
            body_lines=body_lines,
            page_number=page_number,
        )

    # -----------------------------------------------------------------
    # Группы страниц и оценка полей
    # -----------------------------------------------------------------

    @staticmethod
    def _group_pages(zones: Dict[int, PageZones]) -> List[List[int]]:
        """Делит страницы на последовательные группы одинакового размера."""
        groups: List[List[int]] = []
        current_key: Optional[Tuple[float, float]] = None
        for page_num in sorted(zones):
            zone = zones[page_num]
            key = (zone.width_mm, zone.height_mm)
            if key != current_key:
                groups.append([])
                current_key = key
            groups[-1].append(page_num)
        return groups

    def _estimate_section_layout(
        self, page_nums: Sequence[int], zones: Dict[int, PageZones]
    ) -> SectionLayout:
        """Оценивает поля группы страниц по процентилям текстового bbox.

        Верхнее и левое поля считаются по нижним процентилям первых строк и
        левых краев (чтобы абзацный отступ не завышал оценку), нижнее и правое —
        по верхним, чтобы учесть самые заполненные листы. Значения ограничены
        разумным диапазоном: это оценка, а не данные w:sectPr.
        """
        zone_list = [zones[page_num] for page_num in page_nums]
        tops = [zone.body_top_mm for zone in zone_list if zone.body_top_mm is not None]
        bottoms = [zone.body_bottom_mm for zone in zone_list if zone.body_bottom_mm is not None]
        lefts = [zone.body_left_mm for zone in zone_list if zone.body_left_mm is not None]
        rights = [zone.body_right_mm for zone in zone_list if zone.body_right_mm is not None]

        width_mm = zone_list[0].width_mm
        height_mm = zone_list[0].height_mm
        orientation: Literal["portrait", "landscape"] = (
            "portrait" if height_mm >= width_mm else "landscape"
        )

        top_margin = _clamp_margin(_percentile(tops, 0.25)) if tops else DEFAULT_MARGINS_MM.top_mm
        left_margin = (
            _clamp_margin(_percentile(lefts, 0.05)) if lefts else DEFAULT_MARGINS_MM.left_mm
        )
        right_margin = (
            _clamp_margin(width_mm - _percentile(rights, 0.95))
            if rights
            else DEFAULT_MARGINS_MM.right_mm
        )
        bottom_margin = (
            _clamp_margin(height_mm - _percentile(bottoms, 0.95))
            if bottoms
            else DEFAULT_MARGINS_MM.bottom_mm
        )

        header_dists = [
            zone.page_number.line.y0_mm
            for zone in zone_list
            if zone.page_number is not None and zone.page_number.location == "header"
        ]
        footer_dists = [
            height_mm - zone.page_number.line.y1_mm
            for zone in zone_list
            if zone.page_number is not None and zone.page_number.location == "footer"
        ]

        margins = PageMarginsMm(
            top_mm=top_margin,
            bottom_mm=bottom_margin,
            left_mm=left_margin,
            right_mm=right_margin,
            gutter_mm=0.0,
            header_dist_mm=round(_median(header_dists) or DEFAULT_MARGINS_MM.header_dist_mm, 1),
            footer_dist_mm=round(_median(footer_dists) or DEFAULT_MARGINS_MM.footer_dist_mm, 1),
        )
        text_area_width = round(max(20.0, width_mm - left_margin - right_margin), 1)
        return SectionLayout(
            page_nums=list(page_nums),
            width_mm=width_mm,
            height_mm=height_mm,
            orientation=orientation,
            margins=margins,
            text_area_width_mm=text_area_width,
        )

    # -----------------------------------------------------------------
    # Аудит листов
    # -----------------------------------------------------------------

    @staticmethod
    def _page_number_offset(zones: Sequence[PageZones]) -> int:
        """Определяет сдвиг напечатанных номеров (титульный лист не нумеруется)."""
        deltas = [
            zone.page_number.value - zone.page_num
            for zone in zones
            if zone.page_number is not None and zone.page_number.value is not None
        ]
        if not deltas:
            return 0
        counts: Dict[int, int] = {}
        for delta in deltas:
            counts[delta] = counts.get(delta, 0) + 1
        return max(counts.items(), key=lambda item: (item[1], -abs(item[0])))[0]

    def _build_audit(
        self,
        zone: PageZones,
        section: SectionProperties,
        page_offset: int,
    ) -> PdfPageAudit:
        """Собирает PdfPageAudit по зонам листа и оцененным полям секции."""
        page_number = zone.page_number
        body_bottom = zone.body_bottom_mm
        free_bottom: Optional[float] = None
        if body_bottom is not None:
            printable_bottom = zone.height_mm - section.margins.bottom_mm
            free_bottom = round(max(0.0, printable_bottom - body_bottom), 2)

        matches: Optional[bool] = None
        if page_number is not None and page_number.value is not None:
            matches = page_number.value == zone.page_num + page_offset

        return PdfPageAudit(
            page_num=zone.page_num,
            width_mm=zone.width_mm,
            height_mm=zone.height_mm,
            has_rendered_page_number=zone.has_rendered_page_number,
            page_number_location=page_number.location if page_number else "none",
            page_number_alignment=page_number.alignment if page_number else "none",
            rendered_page_number_raw=page_number.raw_text if page_number else None,
            rendered_page_number_int=page_number.value if page_number else None,
            matches_physical_page_num=matches,
            page_number_font_name=page_number.line.font_name if page_number else None,
            page_number_font_size_pt=page_number.line.font_size_pt if page_number else None,
            page_number_y_mm=page_number.line.y0_mm if page_number else None,
            page_number_x_mm=page_number.line.center_x_mm if page_number else None,
            header_extra_text=zone.header_extra_text,
            footer_extra_text=zone.footer_extra_text,
            body_top_y_mm=zone.body_top_mm,
            body_bottom_y_mm=body_bottom,
            body_left_x_mm=zone.body_left_mm,
            body_right_x_mm=zone.body_right_mm,
            bottom_free_space_mm=free_bottom,
        )

    @staticmethod
    def _median_line_gap(zones: Sequence[PageZones]) -> float:
        """Медианный вертикальный просвет между соседними строками тела, мм."""
        gaps: List[float] = []
        for zone in zones:
            lines = sorted(zone.body_lines, key=lambda line: (line.y0_mm, line.x0_mm))
            for previous, current in zip(lines, lines[1:]):
                gap = current.y0_mm - previous.y1_mm
                if 0.0 <= gap <= 20.0:
                    gaps.append(gap)
        return round(_percentile(gaps, 0.5), 2) if gaps else 0.0
