"""Общее чтение строк и графики со страницы PDF (PyMuPDF).

Модуль — единственный источник правды о том, как извлекать текст листа PDF:
и автономный PDF-парсер (``parser.pdf.extractor``), и выравнивание DOCX с PDF
(``parser.docx.pdf_page_aligner``) получают строки, спаны и боксы изображений
отсюда. Поэтому координаты, порядок чтения и декодирование начертаний у двух
режимов совпадают, а обход ``page.get_text("dict")`` не дублируется.

PyMuPDF импортируется вызывающей стороной: функции работают с уже открытым
объектом страницы и не зависят от моделей отчета.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from .pdf_text_utils import normalize_compact, pt_to_mm

#: Координаты прямоугольника в пунктах PDF: (x0, y0, x1, y1).
BBoxPt = Tuple[float, float, float, float]

#: Допуск группировки строк по вертикали при сортировке в порядке чтения, мм.
LINE_SORT_TOLERANCE_MM = 1.5

#: Бит флагов спана PyMuPDF: курсив.
FLAG_ITALIC = 1 << 1

#: Бит флагов спана PyMuPDF: полужирный.
FLAG_BOLD = 1 << 4

#: HEX-цвет по умолчанию: черный.
DEFAULT_COLOR_HEX = "000000"


def color_to_hex(color: int) -> str:
    """Переводит целочисленный цвет спана PDF в HEX-строку 'RRGGBB'."""
    try:
        value = int(color)
    except (TypeError, ValueError):
        return DEFAULT_COLOR_HEX
    red = (value >> 16) & 0xFF
    green = (value >> 8) & 0xFF
    blue = value & 0xFF
    return f"{red:02X}{green:02X}{blue:02X}"


def safe_font_size(value: Any) -> float:
    """Приводит размер шрифта спана к неотрицательному float."""
    try:
        size = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, size)


@dataclass(frozen=True)
class RawSpan:
    """Фрагмент строки PDF с одинаковым шрифтом и начертанием."""

    text: str
    bbox_pt: BBoxPt
    font_name: str
    font_size_pt: float
    bold: bool = False
    italic: bool = False
    color_hex: str = DEFAULT_COLOR_HEX

    @property
    def x0_mm(self) -> float:
        """Левый край фрагмента в миллиметрах."""
        return pt_to_mm(self.bbox_pt[0])

    @property
    def x1_mm(self) -> float:
        """Правый край фрагмента в миллиметрах."""
        return pt_to_mm(self.bbox_pt[2])


@dataclass
class RawLine:
    """Строка текста PDF: прямоугольник и фрагменты из ``page.get_text("dict")``."""

    bbox_pt: BBoxPt
    spans: List[RawSpan] = field(default_factory=list)

    @property
    def x0_mm(self) -> float:
        """Левый край строки в миллиметрах."""
        return pt_to_mm(self.bbox_pt[0])

    @property
    def y0_mm(self) -> float:
        """Верхний край строки в миллиметрах."""
        return pt_to_mm(self.bbox_pt[1])

    @property
    def x1_mm(self) -> float:
        """Правый край строки в миллиметрах."""
        return pt_to_mm(self.bbox_pt[2])

    @property
    def y1_mm(self) -> float:
        """Нижний край строки в миллиметрах."""
        return pt_to_mm(self.bbox_pt[3])

    @property
    def text(self) -> str:
        """Текст строки без краевых пробелов."""
        return "".join(span.text for span in self.spans).strip()

    @property
    def compact_text(self) -> str:
        """Сжатый текст строки: нижний регистр, только буквы и цифры, ё -> е."""
        return normalize_compact(self.text)

    @property
    def main_span(self) -> Optional[RawSpan]:
        """Самый длинный фрагмент строки: он задает шрифт и размер строки."""
        if not self.spans:
            return None
        return max(self.spans, key=lambda span: len(span.text))

    @property
    def font_name(self) -> str:
        """Гарнитура основного фрагмента строки."""
        span = self.main_span
        return span.font_name if span else ""

    @property
    def font_size_pt(self) -> float:
        """Размер основного фрагмента строки в пунктах."""
        span = self.main_span
        return span.font_size_pt if span else 0.0


def _make_span(raw_span: Any) -> Optional[RawSpan]:
    """Собирает RawSpan из словаря спана PyMuPDF, отбрасывая пустой текст."""
    text = str(raw_span.get("text", ""))
    if not text.strip():
        return None
    bbox = tuple(float(value) for value in raw_span.get("bbox", (0.0, 0.0, 0.0, 0.0)))
    font_name = str(raw_span.get("font", ""))
    flags = int(raw_span.get("flags", 0) or 0)
    lowered = font_name.lower()
    return RawSpan(
        text=text,
        bbox_pt=bbox,
        font_name=font_name,
        font_size_pt=safe_font_size(raw_span.get("size", 0.0)),
        bold=bool(flags & FLAG_BOLD) or "bold" in lowered,
        italic=bool(flags & FLAG_ITALIC) or "italic" in lowered,
        color_hex=color_to_hex(raw_span.get("color", 0)),
    )


def read_page_lines(page: Any) -> List[RawLine]:
    """Читает строки со спанами со страницы PyMuPDF и сортирует их в порядке чтения.

    Пустые спаны отбрасываются, поэтому у строки всегда есть текст. Порядок
    чтения задается округлением верхней границы по вертикали с допуском
    LINE_SORT_TOLERANCE_MM и затем левым краем.
    """
    raw = page.get_text("dict")
    lines: List[RawLine] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for raw_line in block.get("lines", []):
            spans = [_make_span(span) for span in raw_line.get("spans", [])]
            present = [span for span in spans if span is not None]
            if not present:
                continue
            bbox = tuple(
                float(value) for value in raw_line.get("bbox", present[0].bbox_pt)
            )
            lines.append(RawLine(bbox_pt=bbox, spans=present))
    lines.sort(key=lambda item: (round(item.y0_mm / LINE_SORT_TOLERANCE_MM), item.x0_mm))
    return lines


def read_page_image_infos(page: Any, with_xrefs: bool = False) -> List[Any]:
    """Возвращает словари изображений страницы из ``page.get_image_info``.

    with_xrefs=True добавляет xref и размеры в пикселях: они нужны автономному
    PDF-парсеру для VisualObject, тогда как выравниванию DOCX достаточно bbox.
    """
    return list(page.get_image_info(xrefs=with_xrefs))


def read_page_image_boxes(page: Any) -> List[BBoxPt]:
    """Возвращает прямоугольники изображений, отрисованных на странице PDF."""
    boxes: List[BBoxPt] = []
    for info in read_page_image_infos(page):
        bbox = info.get("bbox")
        if bbox:
            boxes.append(tuple(float(value) for value in bbox))
    return boxes
