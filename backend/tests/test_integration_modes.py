"""Сквозные тесты трёх режимов: DOCX+PDF, DOCX-only и PDF-only.

Проверяются точки входа, которые обязан поддерживать один контракт:
Python-API пакетов и сервис `parse_document`; результат каждого режима
сериализуется в общий JSON-контракт `ParsedDocument`.
PDF рисуется PyMuPDF, DOCX собирается `DocxPackageBuilder`, поэтому тесты
работают без бинарных образцов и без LibreOffice.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

import parser.docx as docx_package
from conftest import DocxPackageBuilder
from parser.docx.pipeline import DocxParserPipeline
from parser.core.schema import ParagraphBlock, ParsedDocument
from parser.docx import (
    convert_to_docx,
    parse_any_to_json_file,
    parse_docx_to_json_file,
)
from services.document_parsing import parse_document

MM_TO_PT = 72.0 / 25.4

#: Тело DOCX, которое дублируется в PDF: синхронность текста должна быть высокой.
BODY_XML = """
<w:p><w:r><w:t>ТИТУЛЬНЫЙ ЛИСТ ОТЧЕТА ПО ПРАКТИКЕ</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>ВВЕДЕНИЕ</w:t></w:r></w:p>
<w:p><w:r><w:t>Первый абзац введения описывает цель работы и задачи исследования.</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Раздел 1 АРХИТЕКТУРА СИСТЕМЫ</w:t></w:r></w:p>
<w:p><w:r><w:t>Описание базы данных и сервисов платформы.</w:t></w:r></w:p>
"""

#: Тексты страниц эталонного PDF: те же строки, что в DOCX, плюс номера листов.
PDF_PAGES: List[List[str]] = [
    ["ТИТУЛЬНЫЙ ЛИСТ ОТЧЕТА ПО ПРАКТИКЕ"],
    [
        "ВВЕДЕНИЕ",
        "Первый абзац введения описывает цель работы и задачи исследования.",
        "2",
    ],
    [
        "Раздел 1 АРХИТЕКТУРА СИСТЕМЫ",
        "Описание базы данных и сервисов платформы.",
        "3",
    ],
]


def _build_docx(builder: DocxPackageBuilder, name: str = "report.docx") -> Path:
    """Собирает DOCX, текст которого совпадает с эталонным PDF."""
    return builder.set_body(BODY_XML).build(name)


def _draw_line(pymupdf: Any, page: Any, x_mm: float, y_mm: float, text: str) -> None:
    """Рисует строку PDF в координатах страницы, заданных в миллиметрах."""
    rect = pymupdf.Rect(
        x_mm * MM_TO_PT,
        y_mm * MM_TO_PT,
        (x_mm + 170.0) * MM_TO_PT,
        (y_mm + 18.0) * MM_TO_PT,
    )
    if hasattr(page, "insert_htmlbox"):
        css = "* { font-family: sans-serif; font-size: 12pt; margin: 0; padding: 0; }"
        page.insert_htmlbox(rect, text, css=css)
    else:  # pragma: no cover - запасной путь для старых версий PyMuPDF
        page.insert_text((x_mm * MM_TO_PT, (y_mm + 4.0) * MM_TO_PT), text, fontsize=12.0)


def _build_matching_pdf(path: Path) -> Path:
    """Рисует эталонный PDF, тексты страниц которого совпадают с DOCX."""
    pymupdf = pytest.importorskip("pymupdf", reason="Для режимов с PDF нужен PyMuPDF")
    document = pymupdf.open()
    try:
        for lines in PDF_PAGES:
            page = document.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
            y_mm = 25.0
            for line in lines:
                _draw_line(pymupdf, page, 30.0, y_mm, line)
                y_mm += 12.0
        document.save(str(path))
    finally:
        document.close()
    return path


def _build_unrelated_pdf(path: Path, pages: int = 2) -> Path:
    """Рисует PDF с посторонним текстом: сверка DOCX/PDF даст низкий sync_score."""
    pymupdf = pytest.importorskip("pymupdf", reason="Для режимов с PDF нужен PyMuPDF")
    document = pymupdf.open()
    try:
        for number in range(1, pages + 1):
            page = document.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
            page.insert_text((72, 90), f"Unrelated appendix page {number}", fontsize=12)
        document.save(str(path))
    finally:
        document.close()
    return path


def _pdf_data_keys(payload: Dict[str, Any]) -> set:
    """Собирает ключи pdf_* на всех уровнях JSON, кроме контрактного флага.

    capabilities.pdf_layout_coordinates остается в JSON всегда (это флаг
    матрицы возможностей, а не PDF-данные), поэтому в проверку не входит.
    """
    found = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.startswith("pdf_") and key != "pdf_layout_coordinates":
                    found.add(key)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return found


def test_docx_pdf_aligned_pair_gets_exact_pages(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """docx_pdf при совпадении текстов: точные страницы, координаты и sync_score."""
    docx_path = _build_docx(docx_builder)
    pdf_path = _build_matching_pdf(tmp_path / "report.pdf")

    document = parse_document(docx_path, pdf_path=pdf_path, mode="docx_pdf")

    assert document.parse_mode == "docx_pdf"
    assert document.source.has_docx is True
    assert document.source.has_pdf is True
    assert document.source.docx_filename == docx_path.name
    assert document.source.pdf_filename == pdf_path.name
    assert document.source.pdf_sha256 and len(document.source.pdf_sha256) == 64

    assert document.capabilities.styles_parsed is True
    assert document.capabilities.exact_pagination is True
    assert document.capabilities.pdf_layout_coordinates is True
    assert document.capabilities.page_count_estimated is False

    assert document.meta.pagination_source == "pdf_exact"
    assert document.meta.pdf_docx_sync_score is not None
    assert document.meta.pdf_docx_sync_score >= 0.8
    assert document.anomalies.pdf_docx_mismatch_warning is None

    assert document.page_count.method == "pdf_exact"
    assert document.page_count.confidence == "high"
    assert document.page_count.total_pages == len(PDF_PAGES)
    assert len(document.pdf_pages_audit) == len(PDF_PAGES)
    assert document.sections[-1].page_end_est == document.page_count.total_pages

    paragraphs = [block for block in document.blocks if isinstance(block, ParagraphBlock)]
    assert paragraphs
    assert all(1 <= block.page_start_est <= len(PDF_PAGES) for block in paragraphs)
    assert any(
        block.pdf_y_top_mm is not None and block.pdf_x_left_mm is not None
        for block in paragraphs
    )

    payload = document.model_dump(mode="json")
    assert payload["parse_mode"] == "docx_pdf"
    assert "pdf_pages_audit" in payload
    assert "pdf_filename" in payload["meta"]
    assert "pdf_docx_sync_score" in payload["meta"]
    assert _pdf_data_keys(payload)

    round_trip = ParsedDocument.model_validate_json(document.model_dump_json())
    assert round_trip.model_dump(mode="json") == payload


def test_docx_pdf_mismatch_keeps_exact_page_count_with_note(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """docx_pdf при расхождении текстов: страницы PDF точные, блоки — оценка DOCX."""
    docx_path = _build_docx(docx_builder)
    pdf_path = _build_unrelated_pdf(tmp_path / "appendix.pdf", pages=2)

    document = parse_document(docx_path, pdf_path=pdf_path, mode="docx_pdf")

    assert document.parse_mode == "docx_pdf"
    assert document.meta.pagination_source != "pdf_exact"
    assert document.meta.pdf_docx_sync_score is not None
    assert document.meta.pdf_docx_sync_score < 0.8
    assert document.anomalies.pdf_docx_mismatch_warning

    # Число страниц измерено по PDF, поэтому метод и достоверность не понижаются.
    assert document.page_count.method == "pdf_exact"
    assert document.page_count.confidence == "high"
    assert document.page_count.total_pages == 2
    assert document.page_count.note
    assert "не привязаны" in document.page_count.note
    assert "расхождени" in document.page_count.note

    # Но точная пагинация и координаты вёрстки не подтверждены: сверка не удалась.
    assert document.capabilities.exact_pagination is False
    assert document.capabilities.pdf_layout_coordinates is False
    # Секции синхронизированы с PDF, а страницы блоков остались оценкой DOCX
    # и могут превышать page_count.total_pages.
    assert document.sections[-1].page_end_est == 2
    assert max(block.page_end_est for block in document.blocks) > 2
    assert all(
        block.pdf_y_top_mm is None and block.pdf_x_left_mm is None
        for block in document.blocks
        if isinstance(block, ParagraphBlock)
    )


def test_docx_pdf_mismatch_block_pages_may_exceed_pdf(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """DOCX оценивает больше страниц, чем в PDF: блоки не выходят за total_pages.

    Регресс к контракту alignment_applied=False: количество страниц берётся из
    PDF (факт), а page_start_est/page_end_est блоков — оценка DOCX. Поэтому
    секции заканчиваются на странице PDF, а номера блоков могут быть больше:
    это задокументированное расхождение, а pdf_layout_coordinates=False
    сообщает, что координат вёрстки у блоков нет.
    """
    body_xml = """
    <w:p><w:r><w:t>TITLE PAGE</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>SECTION ONE</w:t></w:r></w:p>
    <w:p><w:r><w:t>First section body text.</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>SECTION TWO</w:t></w:r></w:p>
    <w:p><w:r><w:t>Second section body text.</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>SECTION THREE</w:t></w:r></w:p>
    <w:p><w:r><w:t>Third section body text.</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>SECTION FOUR</w:t></w:r></w:p>
    <w:p><w:r><w:t>Fourth section body text.</w:t></w:r></w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("long_estimate.docx")
    pdf_path = _build_unrelated_pdf(tmp_path / "other.pdf", pages=3)

    estimated = parse_document(docx_path, mode="docx_only")
    assert estimated.page_count.total_pages > 3

    document = parse_document(docx_path, pdf_path=pdf_path, mode="docx_pdf")

    assert document.parse_mode == "docx_pdf"
    assert document.page_count.method == "pdf_exact"
    assert document.page_count.total_pages == 3
    assert document.page_count.note

    assert document.capabilities.pdf_layout_coordinates is False
    assert document.capabilities.exact_pagination is False

    assert document.sections[-1].page_end_est == 3
    block_pages = [block.page_end_est for block in document.blocks]
    assert block_pages
    assert max(block_pages) > document.page_count.total_pages
    assert all(
        block.pdf_y_top_mm is None and block.pdf_x_left_mm is None
        for block in document.blocks
        if isinstance(block, ParagraphBlock)
    )


def test_pdf_only_complete_without_docx(tmp_path: Path) -> None:
    """pdf_only: точные страницы, блоки, секции и аудит без DOCX и LibreOffice."""
    pdf_path = _build_matching_pdf(tmp_path / "standalone.pdf")

    document = parse_document(pdf_path, mode="pdf_only")

    assert document.parse_mode == "pdf_only"
    assert document.source.has_docx is False
    assert document.source.has_pdf is True
    assert document.source.docx_filename is None
    assert document.source.pdf_filename == pdf_path.name

    assert document.meta.source_format == "pdf"
    assert document.meta.converted_via_lo is False
    assert document.meta.pagination_source == "pdf_exact"

    assert document.page_count.method == "pdf_exact"
    assert document.page_count.total_pages == len(PDF_PAGES)
    assert document.capabilities.styles_parsed is False
    assert document.capabilities.sections_parsed is False
    assert document.capabilities.visual_registry is False
    assert document.capabilities.pdf_layout_coordinates is True

    assert document.blocks
    assert document.sections
    assert len(document.pdf_pages_audit) == len(PDF_PAGES)

    payload = document.model_dump(mode="json")
    assert payload["parse_mode"] == "pdf_only"
    assert "pdf_pages_audit" in payload
    assert _pdf_data_keys(payload)

    round_trip = ParsedDocument.model_validate_json(document.model_dump_json())
    assert round_trip.model_dump(mode="json") == payload


def test_docx_only_round_trip_without_pdf_keys(
    docx_builder: DocxPackageBuilder,
) -> None:
    """docx_only: JSON без pdf_*, метод оценочный, round-trip устойчив."""
    document = parse_document(_build_docx(docx_builder), mode="docx_only")
    payload = document.model_dump(mode="json")

    assert payload["parse_mode"] == "docx_only"
    assert document.capabilities.exact_pagination is False
    assert document.page_count.total_pages >= 1
    assert document.page_count.method in ("docx_native_breaks", "geometric_estimation")
    assert document.page_count.confidence in ("medium", "low")
    assert "pdf_pages_audit" not in payload
    assert not _pdf_data_keys(payload)

    round_trip = ParsedDocument.model_validate_json(document.model_dump_json())
    assert round_trip.model_dump(mode="json") == payload


def test_legacy_json_without_new_fields_validates() -> None:
    """JSON версии 2.x без контрактных полей валидируется безопасными дефолтами."""
    legacy = {
        "meta": {
            "filename": "old.docx",
            "file_sha256": "0" * 64,
            "pagination_source": "geometric_fallback",
        },
        "settings": {},
        "theme": {},
    }

    document = ParsedDocument.model_validate(legacy)

    assert document.schema_version == "3.0.0"
    assert document.parse_mode == "docx_only"
    assert document.source.has_docx is True
    assert document.source.has_pdf is False
    assert document.page_count.total_pages >= 1
    assert document.capabilities.styles_parsed is True


def test_legacy_json_with_pdf_metadata_infers_docx_pdf() -> None:
    """Старый JSON с meta.pdf_filename достраивает режим docx_pdf и точные страницы."""
    legacy = {
        "meta": {
            "filename": "old.docx",
            "file_sha256": "0" * 64,
            "pdf_filename": "old.pdf",
            "pdf_sha256": "1" * 64,
            "pagination_source": "pdf_exact",
        },
        "settings": {},
        "theme": {},
        "pdf_pages_audit": [{"page_num": 1, "width_mm": 210.0, "height_mm": 297.0}],
    }

    document = ParsedDocument.model_validate(legacy)

    assert document.parse_mode == "docx_pdf"
    assert document.source.has_pdf is True
    assert document.page_count.method == "pdf_exact"
    assert document.page_count.total_pages == 1


def test_public_api_backward_compatibility() -> None:
    """Публичные имена и сигнатуры прежних точек входа не изменились."""
    assert docx_package.ParsedDocxDocument is docx_package.ParsedDocument
    assert docx_package.DocxParserPipeline is DocxParserPipeline

    signature = inspect.signature(parse_docx_to_json_file)
    assert list(signature.parameters) == [
        "docx_path",
        "output_json_path",
        "indent",
        "pdf_path",
    ]
    signature = inspect.signature(parse_any_to_json_file)
    assert list(signature.parameters) == [
        "input_path",
        "output_json_path",
        "indent",
        "pdf_path",
        "outdir",
        "backend",
        "timeout",
    ]
    signature = inspect.signature(convert_to_docx)
    assert list(signature.parameters) == ["path", "outdir", "timeout", "backend"]


def test_parse_docx_to_json_file_writes_docx_only_json(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """Историческая точка входа по-прежнему пишет валидный JSON без PDF."""
    docx_path = _build_docx(docx_builder)
    output_path = tmp_path / "legacy.json"

    document = parse_docx_to_json_file(docx_path, output_path)

    assert document.parse_mode == "docx_only"
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["parse_mode"] == "docx_only"
    assert "pdf_pages_audit" not in payload

