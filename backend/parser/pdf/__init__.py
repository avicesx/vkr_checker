"""Автономный разбор PDF в единую модель ParsedDocument (режим pdf_only).

Публичный API пакета:
    PdfDocumentParser(pdf_path).parse() -> ParsedDocument
    parse_pdf(pdf_path) -> ParsedDocument

Пакет не требует DOCX и LibreOffice, не использует OCR: единственный источник
данных — сам PDF. Из `parser.docx` пакет использует только общие модели
(`parser.core.schema`) и общие примитивы чтения PDF
(`parser.docx.pdf_page_text`, `parser.docx.pdf_text_utils`); `parser.docx.pipeline`
и `parser.docx.converter` не импортируются (в том числе благодаря ленивому
`parser/docx/__init__.py`, PEP 562), поэтому `pdf_only` не поднимает lxml и
DOCX-конвейер. Страницы здесь точные (``page_count.method="pdf_exact"``), а
структура абзацев, заголовков, списков и оглавления восстановлена по геометрии
и помечена как ``pdf_estimate``.
"""
from __future__ import annotations

from .extractor import (
    PdfDocument,
    PdfExtractor,
    PdfImage,
    PdfLine,
    PdfPage,
    PdfSpan,
    PdfTable,
)
from .layout import (
    PageNumberInfo,
    PageZones,
    PdfLayout,
    PdfLayoutEstimator,
    SectionLayout,
)
from .parser import PdfDocumentParser, parse_pdf
from .structure import PdfStructure, PdfStructureBuilder

#: Исторический короткий алиас класса парсера.
PdfParser = PdfDocumentParser

__all__ = [
    "PageNumberInfo",
    "PageZones",
    "PdfDocument",
    "PdfDocumentParser",
    "PdfExtractor",
    "PdfImage",
    "PdfLayout",
    "PdfLayoutEstimator",
    "PdfLine",
    "PdfPage",
    "PdfParser",
    "PdfSpan",
    "PdfStructure",
    "PdfStructureBuilder",
    "PdfTable",
    "SectionLayout",
    "parse_pdf",
]
