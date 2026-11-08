"""Контракт режима docx_only: режим, возможности и оценка страниц без PDF.

Проверяется, что конвейер без PDF сообщает parse_mode="docx_only", заполняет
source/capabilities/page_count, честно помечает пагинацию как оценочную и не
выдумывает PDF-данные; meta.pagination_source сохраняет прежнее значение.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

from conftest import DocxPackageBuilder
from parser.docx.pipeline import DocxParserPipeline, parse_docx_to_json_file
from parser.core.schema import ParsedDocument

#: Ключ возможностей, который остается в JSON даже без PDF (контрактный флаг).
ALLOWED_PDF_KEY = "pdf_layout_coordinates"


def _two_page_body() -> str:
    """Тело DOCX с принудительным разрывом страницы перед вторым абзацем."""
    return """
    <w:p><w:r><w:t>Титульный лист</w:t></w:r></w:p>
    <w:p>
      <w:pPr><w:pageBreakBefore/></w:pPr>
      <w:r><w:t>Основной текст второй страницы</w:t></w:r></w:p>
    """


def _collect_pdf_keys(payload: Any) -> List[str]:
    """Собирает имена pdf_*-ключей на всех уровнях JSON-совместимой структуры."""
    found: List[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.startswith("pdf_") and key != ALLOWED_PDF_KEY:
                found.append(key)
            found.extend(_collect_pdf_keys(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_collect_pdf_keys(item))
    return found


def test_docx_only_reports_mode_source_and_capabilities(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Без PDF отчет собран в режиме docx_only и заполняет контрактные блоки."""
    body_xml = """
    <w:p><w:r><w:t>Первый абзац отчета без разрывов страниц.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Второй абзац того же листа.</w:t></w:r></w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("docx_only_contract.docx")

    doc = DocxParserPipeline(docx_path).parse()

    assert doc.parse_mode == "docx_only"
    assert doc.source.mode == "docx_only"
    assert doc.source.has_docx is True
    assert doc.source.has_pdf is False
    assert doc.source.docx_filename == "docx_only_contract.docx"
    assert doc.source.docx_sha256 is not None
    assert len(doc.source.docx_sha256) == 64
    assert doc.source.pdf_filename is None
    assert doc.source.pdf_sha256 is None

    assert doc.capabilities.styles_parsed is True
    assert doc.capabilities.numbering_parsed is True
    assert doc.capabilities.sections_parsed is True
    assert doc.capabilities.exact_pagination is False
    assert doc.capabilities.pdf_layout_coordinates is False
    assert doc.capabilities.visual_registry is True
    assert doc.capabilities.footnotes_parsed is True
    assert doc.capabilities.cross_references is True
    assert doc.capabilities.word_metadata is True
    assert doc.capabilities.page_count_estimated is True

    assert doc.page_count.total_pages == 1
    assert doc.page_count.method == "geometric_estimation"
    assert doc.page_count.confidence == "low"
    assert doc.page_count.note is not None
    assert "PDF не передан" in doc.page_count.note

    assert doc.meta.pagination_source == "geometric_fallback"
    assert doc.meta.pdf_filename is None
    assert doc.meta.pdf_sha256 is None
    assert doc.meta.word_stat_pages is None
    assert doc.meta.rendered_page_numbers_present is None


def test_docx_only_page_count_matches_last_section(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Оценка страниц учитывает разрыв страницы и синхронизирована с последней секцией."""
    docx_path = docx_builder.set_body(_two_page_body()).build("docx_only_pages.docx")

    doc = DocxParserPipeline(docx_path).parse()

    assert doc.page_count.total_pages == 2
    assert doc.sections[-1].page_end_est == doc.page_count.total_pages
    assert doc.page_count.total_pages >= 1
    assert doc.page_count.method == "geometric_estimation"
    assert doc.page_count.confidence == "low"
    assert doc.meta.pagination_source == "geometric_fallback"


def test_docx_only_uses_native_rendered_page_breaks(
    docx_builder: DocxPackageBuilder,
) -> None:
    """w:lastRenderedPageBreak дает метод docx_native_breaks и достоверность medium."""
    body_xml = """
    <w:p><w:r><w:lastRenderedPageBreak/><w:t>Текст после нативного разрыва.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Продолжение текста.</w:t></w:r></w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("docx_only_native_breaks.docx")

    doc = DocxParserPipeline(docx_path).parse()

    assert doc.meta.pagination_source == "docx_native_breaks"
    assert doc.page_count.method == "docx_native_breaks"
    assert doc.page_count.confidence == "medium"
    assert doc.page_count.total_pages == doc.sections[-1].page_end_est
    assert doc.page_count.note is not None
    # ARCHITECTURE.md, 4.2: измеренные страницы по маркерам Word не считаются оценкой.
    assert doc.capabilities.page_count_estimated is False


def test_docx_only_json_has_no_pdf_fields(
    docx_builder: DocxPackageBuilder,
) -> None:
    """В JSON режима docx_only нет pdf_*-ключей, кроме контрактного флага возможностей."""
    docx_path = docx_builder.set_body(_two_page_body()).build("docx_only_json.docx")

    doc = DocxParserPipeline(docx_path).parse()
    payload = doc.model_dump(mode="json")

    assert payload["parse_mode"] == "docx_only"
    assert payload["source"]["has_docx"] is True
    assert payload["source"]["has_pdf"] is False
    assert payload["capabilities"]["pdf_layout_coordinates"] is False
    assert payload["page_count"]["total_pages"] == doc.page_count.total_pages
    assert "pdf_pages_audit" not in payload
    assert "rendered_page_numbers_present" not in payload["meta"]
    assert _collect_pdf_keys(payload) == []

    restored = ParsedDocument.model_validate(payload)
    assert restored.model_dump(mode="json") == payload


def test_parse_docx_only_ignores_pdf_path(
    docx_builder: DocxPackageBuilder,
    tmp_path: Path,
) -> None:
    """parse_docx_only не открывает PDF: несуществующий путь не мешает разбору."""
    docx_path = docx_builder.set_body(_two_page_body()).build("docx_only_ignore_pdf.docx")
    pipeline = DocxParserPipeline(docx_path, pdf_path=tmp_path / "absent.pdf")

    doc = pipeline.parse_docx_only()

    assert doc.parse_mode == "docx_only"
    assert doc.source.has_pdf is False
    assert doc.meta.pdf_filename is None
    assert doc.meta.pagination_source != "pdf_exact"
    assert doc.capabilities.exact_pagination is False


def test_docx_only_keeps_word_stat_pages_from_app_xml(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Pages из docProps/app.xml остается в meta, но не подменяет page_count без PDF."""
    docx_builder.set_body(_two_page_body())
    docx_builder.set_xml(
        "docProps/app.xml",
        """<?xml version="1.0" encoding="UTF-8"?>
        <Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
          <Pages>7</Pages>
          <Words>120</Words>
        </Properties>""",
    )
    docx_path = docx_builder.build("docx_only_app_xml.docx")

    doc = DocxParserPipeline(docx_path).parse()

    assert doc.meta.word_stat_pages == 7
    assert doc.page_count.total_pages == 2
    assert doc.page_count.method == "geometric_estimation"
    assert doc.meta.rendered_page_numbers_present is None


def test_parse_docx_to_json_file_writes_docx_only_json(
    docx_builder: DocxPackageBuilder,
    tmp_path: Path,
) -> None:
    """parse_docx_to_json_file без PDF пишет валидный JSON режима docx_only."""
    docx_path = docx_builder.set_body(_two_page_body()).build("docx_only_file.docx")
    output_path = tmp_path / "docx_only_report.json"

    parsed_doc = parse_docx_to_json_file(docx_path, output_path)
    raw = json.loads(output_path.read_text(encoding="utf-8"))

    assert isinstance(parsed_doc, ParsedDocument)
    assert raw["parse_mode"] == "docx_only"
    assert raw["source"]["has_pdf"] is False
    assert raw["page_count"]["total_pages"] >= 1
    assert raw["page_count"]["method"] == "geometric_estimation"
    assert "pdf_pages_audit" not in raw
    assert _collect_pdf_keys(raw) == []
    assert ParsedDocument.model_validate(raw).page_count.total_pages >= 1
