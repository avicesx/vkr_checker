"""Извлечение текста, графики и таблиц из PDF через PyMuPDF.

Модуль отдает только факты листа: строки со спанами и координатами, встроенные
изображения, найденные таблицы и размеры страницы. Эвристики вёрстки и
структуры живут в layout.py и structure.py, поэтому extractor можно
переиспользовать в других режимах разбора без побочных эффектов: он не знает
ни про DOCX, ни про модели отчета.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pymupdf

from parser.docx.pdf_page_text import read_page_image_infos, read_page_lines
from parser.docx.pdf_text_utils import PT_TO_MM, normalize_compact, pt_to_mm

#: Координаты прямоугольника в пунктах PDF: (x0, y0, x1, y1).
BBoxPt = Tuple[float, float, float, float]

#: Минимум векторных объектов на странице, чтобы искать таблицы.
#: find_tables стоит порядка 0.3-1 с на страницу, поэтому для чистых текстовых
#: листов (без линий и заливок) он пропускается.
MIN_DRAWINGS_FOR_TABLE = 4

#: Допуск склейки фрагментов одного изображения в один визуальный объект, мм.
IMAGE_CLUSTER_TOLERANCE_MM = 2.0

#: Допуск совпадения базовой линии фрагментов одной визуальной строки, мм.
BASELINE_TOLERANCE_MM = 1.2

#: Разрыв по горизонтали, при котором между фрагментами строки ставится пробел, мм.
FRAGMENT_GAP_MM = 2.0

#: Расстояние между фрагментами, дальше которого они считаются разными строками, мм.
FRAGMENT_JOIN_LIMIT_MM = 60.0


@dataclass
class PdfBox:
    """Прямоугольник в пунктах PDF с границами в миллиметрах."""

    bbox_pt: BBoxPt

    @property
    def x0_mm(self) -> float:
        """Левый край прямоугольника в миллиметрах."""
        return pt_to_mm(self.bbox_pt[0])

    @property
    def y0_mm(self) -> float:
        """Верхний край прямоугольника в миллиметрах."""
        return pt_to_mm(self.bbox_pt[1])

    @property
    def x1_mm(self) -> float:
        """Правый край прямоугольника в миллиметрах."""
        return pt_to_mm(self.bbox_pt[2])

    @property
    def y1_mm(self) -> float:
        """Нижний край прямоугольника в миллиметрах."""
        return pt_to_mm(self.bbox_pt[3])

    @property
    def width_mm(self) -> float:
        """Ширина прямоугольника в миллиметрах."""
        return pt_to_mm(max(0.0, self.bbox_pt[2] - self.bbox_pt[0]))

    @property
    def height_mm(self) -> float:
        """Высота прямоугольника в миллиметрах."""
        return pt_to_mm(max(0.0, self.bbox_pt[3] - self.bbox_pt[1]))

    @property
    def center_x_mm(self) -> float:
        """Центр прямоугольника по горизонтали в миллиметрах."""
        return pt_to_mm((self.bbox_pt[0] + self.bbox_pt[2]) / 2.0)

    @property
    def center_y_mm(self) -> float:
        """Центр прямоугольника по вертикали в миллиметрах."""
        return pt_to_mm((self.bbox_pt[1] + self.bbox_pt[3]) / 2.0)


@dataclass
class PdfSpan(PdfBox):
    """Фрагмент строки PDF с одинаковым шрифтом и начертанием."""

    text: str
    font_name: str
    font_size_pt: float
    bold: bool = False
    italic: bool = False
    color_hex: str = "000000"

    @property
    def font_family(self) -> str:
        """Гарнитура без служебных суффиксов вроде ",Bold" и префикса подмножества."""
        return normalize_font_family(self.font_name)


@dataclass
class PdfLine(PdfBox):
    """Строка текста PDF: спаны, координаты и признак попадания в таблицу."""

    page_num: int
    spans: List[PdfSpan] = field(default_factory=list)
    in_table: bool = False

    @property
    def text(self) -> str:
        """Текст строки без краевых пробелов."""
        return "".join(span.text for span in self.spans).strip()

    @property
    def compact_text(self) -> str:
        """Сжатый текст строки для сравнения с DOCX и оглавлением."""
        return normalize_compact(self.text)

    @property
    def main_span(self) -> Optional[PdfSpan]:
        """Самый длинный спан строки: он задает шрифт и размер строки."""
        if not self.spans:
            return None
        return max(self.spans, key=lambda span: len(span.text))

    @property
    def font_name(self) -> str:
        """Гарнитура основного спана строки."""
        span = self.main_span
        return span.font_family if span else ""

    @property
    def font_size_pt(self) -> float:
        """Размер основного спана строки в пунктах."""
        span = self.main_span
        return span.font_size_pt if span else 0.0

    @property
    def is_bold(self) -> bool:
        """True, если основной спан строки полужирный."""
        span = self.main_span
        return bool(span.bold) if span else False

    @property
    def is_italic(self) -> bool:
        """True, если основной спан строки курсивный."""
        span = self.main_span
        return bool(span.italic) if span else False


@dataclass
class PdfImage(PdfBox):
    """Встроенное растровое изображение, отрисованное на листе."""

    page_num: int
    xref: Optional[int] = None
    xrefs: List[int] = field(default_factory=list)
    width_px: Optional[int] = None
    height_px: Optional[int] = None
    ext: str = "png"


@dataclass
class PdfTable(PdfBox):
    """Таблица, найденная PyMuPDF на листе."""

    page_num: int
    rows: List[List[str]] = field(default_factory=list)
    row_bboxes_pt: List[BBoxPt] = field(default_factory=list)
    row_cells_pt: List[List[BBoxPt]] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        """Число строк таблицы."""
        return len(self.rows)

    @property
    def col_count(self) -> int:
        """Число столбцов таблицы (по самой длинной строке)."""
        return max((len(row) for row in self.rows), default=0)

    def column_widths_mm(self) -> List[float]:
        """Ширины столбцов в миллиметрах по границам ячеек первой строки."""
        if not self.row_cells_pt:
            uniform = self.width_mm / max(1, self.col_count)
            return [round(uniform, 2)] * max(1, self.col_count)
        cells = self.row_cells_pt[0]
        widths = [pt_to_mm(cell[2] - cell[0]) for cell in cells]
        if len(widths) != self.col_count:
            uniform = self.width_mm / max(1, self.col_count)
            return [round(uniform, 2)] * max(1, self.col_count)
        return widths


@dataclass
class PdfPage:
    """Один лист PDF: размеры, строки, изображения и таблицы."""

    page_num: int
    width_pt: float
    height_pt: float
    rotation: int = 0
    lines: List[PdfLine] = field(default_factory=list)
    images: List[PdfImage] = field(default_factory=list)
    tables: List[PdfTable] = field(default_factory=list)

    @property
    def width_mm(self) -> float:
        """Ширина листа в миллиметрах."""
        return pt_to_mm(self.width_pt)

    @property
    def height_mm(self) -> float:
        """Высота листа в миллиметрах."""
        return pt_to_mm(self.height_pt)

    @property
    def text(self) -> str:
        """Весь текст листа построчно."""
        return "\n".join(line.text for line in self.lines if line.text)


@dataclass
class PdfDocument:
    """Результат извлечения: листы, хэш файла и метаданные PDF."""

    path: Path
    pages: List[PdfPage] = field(default_factory=list)
    sha256: str = ""
    metadata: Dict[str, str] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        """Число физических страниц PDF."""
        return len(self.pages)

    @property
    def filename(self) -> str:
        """Имя исходного PDF-файла без пути."""
        return self.path.name

    def text(self) -> str:
        """Весь текстовый слой документа по страницам."""
        return "\n".join(page.text for page in self.pages)


def normalize_font_family(font_name: str) -> str:
    """Приводит имя шрифта PDF к гарнитуре: без подмножества и начертания.

    PyMuPDF отдает имена вида ``"ABCDEF+TimesNewRomanPSMT,Bold"`` или
    ``"Times New Roman,BoldItalic"``; для отчета нужна чистая гарнитура, как в
    DOCX (``font_ascii``/``font_hansi``).
    """
    name = (font_name or "").strip()
    if "+" in name:
        prefix, _, rest = name.partition("+")
        if len(prefix) == 6 and prefix.isupper():
            name = rest
    family = name.split(",")[0].strip()
    for suffix in ("-BoldItalic", "-Bold", "-Italic", "-Regular", "-Roman"):
        if family.endswith(suffix):
            family = family[: -len(suffix)]
    family = family.replace("PSMT", "").replace("PS", "").strip()
    return family or (font_name or "").strip()


class PdfExtractor:
    """Читает PDF через PyMuPDF и отдает постраничные фактические данные."""

    def __init__(self, pdf_path: str | Path, detect_tables: bool = True) -> None:
        """Запоминает путь к PDF и признак поиска таблиц.

        detect_tables=False ускоряет разбор: find_tables стоит до секунды на
        страницу и нужен не всем потребителям (например, при быстрой проверке
        пагинации).
        """
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF-файл не найден: {self.pdf_path}")
        self.detect_tables = detect_tables

    def extract(self) -> PdfDocument:
        """Открывает PDF и возвращает все листы с фактами текста и графики."""
        document = pymupdf.open(str(self.pdf_path))
        try:
            if document.needs_pass:
                raise ValueError("PDF защищен паролем: разбор без пароля невозможен")
            if document.page_count < 1:
                raise ValueError("PDF не содержит страниц")
            pages = [self._extract_page(document, index) for index in range(document.page_count)]
            metadata = {
                str(key): str(value)
                for key, value in (document.metadata or {}).items()
                if value
            }
        finally:
            document.close()

        return PdfDocument(
            path=self.pdf_path,
            pages=pages,
            sha256=self.compute_sha256(self.pdf_path),
            metadata=metadata,
        )

    @staticmethod
    def compute_sha256(path: Path) -> str:
        """Считает SHA-256 файла порциями по 1 МБ."""
        digest = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _extract_page(self, document, index: int) -> PdfPage:
        """Собирает строки, таблицы и изображения одного листа."""
        page = document[index]
        rect = page.rect
        tables = self._extract_tables(page, index + 1)
        lines = self._extract_lines(page, index + 1, tables)
        images = self._extract_images(page, index + 1)
        return PdfPage(
            page_num=index + 1,
            width_pt=float(rect.width),
            height_pt=float(rect.height),
            rotation=int(page.rotation or 0),
            lines=lines,
            images=images,
            tables=tables,
        )

    def _extract_lines(
        self, page, page_num: int, tables: Sequence[PdfTable]
    ) -> List[PdfLine]:
        """Строит строки отчета из общих строк PDF (pdf_page_text.read_page_lines)."""
        lines: List[PdfLine] = []
        for raw_line in read_page_lines(page):
            spans = [
                PdfSpan(
                    bbox_pt=span.bbox_pt,
                    text=span.text,
                    font_name=span.font_name,
                    font_size_pt=round(span.font_size_pt, 2),
                    bold=span.bold,
                    italic=span.italic,
                    color_hex=span.color_hex,
                )
                for span in raw_line.spans
            ]
            lines.append(PdfLine(bbox_pt=raw_line.bbox_pt, page_num=page_num, spans=spans))

        for line in lines:
            line.in_table = self._line_in_tables(line, tables)
        return self._merge_same_baseline(lines)

    @staticmethod
    def _line_in_tables(line: PdfLine, tables: Sequence[PdfTable]) -> bool:
        """True, если центр строки лежит внутри найденной таблицы."""
        center_x = line.center_x_mm
        center_y = line.center_y_mm
        for table in tables:
            if (
                table.x0_mm - 0.5 <= center_x <= table.x1_mm + 0.5
                and table.y0_mm - 0.5 <= center_y <= table.y1_mm + 0.5
            ):
                return True
        return False

    def _merge_same_baseline(self, lines: List[PdfLine]) -> List[PdfLine]:
        """Склеивает фрагменты одной визуальной строки в один PdfLine.

        PyMuPDF разбивает строку на несколько объектов там, где есть табуляция
        или большой пробел: типичный пример — запись оглавления
        "2.2.\\tЭлементы\\tи ... 25". Без склейки такая запись распадается на
        отдельные строки, и структура документа теряет заголовки и оглавление.
        Фрагменты склеиваются только на одной базовой линии и в пределах
        разумного горизонтального разрыва, чтобы не сшивать колонки.
        """
        merged: List[PdfLine] = []
        for line in lines:
            if merged and self._is_same_visual_line(merged[-1], line):
                base = merged[-1]
                gap = line.x0_mm - base.x1_mm
                if gap > FRAGMENT_GAP_MM:
                    base.spans.append(self._make_space_span(base, line))
                base.spans.extend(line.spans)
                base.bbox_pt = (
                    min(base.bbox_pt[0], line.bbox_pt[0]),
                    min(base.bbox_pt[1], line.bbox_pt[1]),
                    max(base.bbox_pt[2], line.bbox_pt[2]),
                    max(base.bbox_pt[3], line.bbox_pt[3]),
                )
                base.in_table = base.in_table or line.in_table
                continue
            merged.append(
                PdfLine(
                    bbox_pt=line.bbox_pt,
                    page_num=line.page_num,
                    spans=list(line.spans),
                    in_table=line.in_table,
                )
            )
        return merged

    @staticmethod
    def _is_same_visual_line(left: PdfLine, right: PdfLine) -> bool:
        """True, если фрагменты стоят на одной базовой линии и рядом по горизонтали."""
        if left.page_num != right.page_num:
            return False
        if abs(left.y0_mm - right.y0_mm) > BASELINE_TOLERANCE_MM:
            return False
        gap = right.x0_mm - left.x1_mm
        if gap < -2.0 or gap > FRAGMENT_JOIN_LIMIT_MM:
            return False
        overlap = min(left.y1_mm, right.y1_mm) - max(left.y0_mm, right.y0_mm)
        return overlap >= 0.5 * min(left.height_mm, right.height_mm)

    @staticmethod
    def _make_space_span(left: PdfLine, right: PdfLine) -> PdfSpan:
        """Создает спан-пробел между двумя фрагментами одной визуальной строки."""
        source = right.spans[0] if right.spans else left.spans[-1]
        return PdfSpan(
            bbox_pt=(
                right.bbox_pt[0],
                right.bbox_pt[1],
                right.bbox_pt[0],
                right.bbox_pt[3],
            ),
            text=" ",
            font_name=source.font_name,
            font_size_pt=source.font_size_pt,
            bold=source.bold,
            italic=source.italic,
            color_hex=source.color_hex,
        )

    def _extract_tables(self, page, page_num: int) -> List[PdfTable]:
        """Ищет таблицы листа через find_tables и отбрасывает вырожденные.

        Таблицы ищутся только на страницах с векторной графикой: у чисто
        текстовых листов рамок нет, а find_tables на них дорог. Строки таблиц
        нормализуются по пробелам, потому что PyMuPDF склеивает переносы ячеек
        через "\\n".
        """
        if not self.detect_tables:
            return []
        try:
            if len(page.get_drawings()) < MIN_DRAWINGS_FOR_TABLE:
                return []
        except Exception:  # pragma: no cover - защита от неожиданного API PyMuPDF
            return []

        try:
            finder = page.find_tables(strategy="lines_strict")
        except TypeError:  # pragma: no cover - старые версии PyMuPDF без strategy
            finder = page.find_tables()
        except Exception:
            return []

        tables: List[PdfTable] = []
        for found in getattr(finder, "tables", []):
            try:
                raw_rows = found.extract()
            except Exception:
                continue
            rows = [[_normalize_cell(cell) for cell in row] for row in raw_rows]
            if len(rows) < 2 or max((len(row) for row in rows), default=0) < 2:
                continue
            row_bboxes: List[BBoxPt] = []
            row_cells: List[List[BBoxPt]] = []
            for row in getattr(found, "rows", []):
                bbox = getattr(row, "bbox", None)
                if bbox is not None:
                    row_bboxes.append(tuple(float(value) for value in bbox))
                cells = getattr(row, "cells", None)
                if cells:
                    row_cells.append(
                        [
                            tuple(float(value) for value in cell)
                            for cell in cells
                            if cell is not None
                        ]
                    )
            tables.append(
                PdfTable(
                    bbox_pt=tuple(float(value) for value in found.bbox),
                    page_num=page_num,
                    rows=rows,
                    row_bboxes_pt=row_bboxes,
                    row_cells_pt=row_cells,
                )
            )
        tables.sort(key=lambda item: (item.y0_mm, item.x0_mm))
        return tables

    def _extract_images(self, page, page_num: int) -> List[PdfImage]:
        """Собирает изображения листа и склеивает фрагменты одного визуала."""
        try:
            raw_images = page.get_images(full=True)
        except Exception:  # pragma: no cover - защита от неожиданного API PyMuPDF
            return []
        if not raw_images:
            return []

        info_by_xref: Dict[int, Tuple[Optional[int], Optional[int], str]] = {}
        for item in raw_images:
            xref = int(item[0])
            width_px = int(item[2]) if len(item) > 2 else None
            height_px = int(item[3]) if len(item) > 3 else None
            ext = str(item[8]) if len(item) > 8 and item[8] else "png"
            info_by_xref[xref] = (width_px, height_px, ext)

        boxes: List[PdfImage] = []
        for info in read_page_image_infos(page, with_xrefs=True):
            bbox = info.get("bbox")
            if not bbox:
                continue
            xref = info.get("xref")
            xref = int(xref) if xref is not None else None
            width_px, height_px, ext = info_by_xref.get(xref, (None, None, "png"))
            boxes.append(
                PdfImage(
                    bbox_pt=tuple(float(value) for value in bbox),
                    page_num=page_num,
                    xref=xref,
                    xrefs=[xref] if xref is not None else [],
                    width_px=width_px,
                    height_px=height_px,
                    ext=ext,
                )
            )
        return self._cluster_images(boxes)

    @staticmethod
    def _cluster_images(boxes: Sequence[PdfImage]) -> List[PdfImage]:
        """Склеивает касающиеся фрагменты одной картинки в общий bbox."""
        if not boxes:
            return []
        tolerance = IMAGE_CLUSTER_TOLERANCE_MM / PT_TO_MM
        clusters: List[PdfImage] = []
        for box in boxes:
            target: Optional[PdfImage] = None
            for cluster in clusters:
                if box.page_num != cluster.page_num:
                    continue
                left = cluster.bbox_pt[0] - tolerance
                if not (
                    box.bbox_pt[2] < left
                    or box.bbox_pt[0] > cluster.bbox_pt[2] + tolerance
                    or box.bbox_pt[3] < cluster.bbox_pt[1] - tolerance
                    or box.bbox_pt[1] > cluster.bbox_pt[3] + tolerance
                ):
                    target = cluster
                    break
            if target is None:
                clusters.append(
                    PdfImage(
                        bbox_pt=box.bbox_pt,
                        page_num=box.page_num,
                        xref=box.xref,
                        xrefs=list(box.xrefs),
                        width_px=box.width_px,
                        height_px=box.height_px,
                        ext=box.ext,
                    )
                )
                continue
            target.bbox_pt = (
                min(target.bbox_pt[0], box.bbox_pt[0]),
                min(target.bbox_pt[1], box.bbox_pt[1]),
                max(target.bbox_pt[2], box.bbox_pt[2]),
                max(target.bbox_pt[3], box.bbox_pt[3]),
            )
            target.xrefs.extend(box.xrefs)
            if target.xref is None:
                target.xref = box.xref
            if box.width_px and box.height_px:
                if not target.width_px or box.width_px * box.height_px > target.width_px * target.height_px:
                    target.width_px = box.width_px
                    target.height_px = box.height_px
                    target.ext = box.ext

        clusters.sort(key=lambda item: (item.page_num, item.y0_mm, item.x0_mm))
        return clusters


def _normalize_cell(value: Optional[str]) -> str:
    """Схлопывает переносы и лишние пробелы в тексте ячейки таблицы."""
    if not value:
        return ""
    collapsed = " ".join(str(value).split())
    return collapsed

