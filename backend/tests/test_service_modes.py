"""Тесты сервиса разбора документов: режимы, контракт результата и ошибки.

DOCX собирается `DocxPackageBuilder` из conftest, PDF рисуется через PyMuPDF,
поэтому тесты не зависят от бинарных образцов и проверяют все три режима
сквозь единую точку входа `parse_document`/`parse_to_json_file`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from conftest import DocxPackageBuilder
from parser.core.schema import ParsedDocument
from services.document_parsing import (
    DocumentParsingError,
    describe_parse_modes,
    parse_document,
    parse_to_json_file,
)

BODY_XML = """
<w:p>
  <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
  <w:r><w:t>Введение</w:t></w:r>
</w:p>
<w:p><w:r><w:t>Первый абзац основного текста документа.</w:t></w:r></w:p>
"""


def _build_docx(builder: DocxPackageBuilder, name: str = "service_source.docx") -> Path:
    """Собирает минимальный валидный DOCX с заголовком и абзацем."""
    return builder.set_body(BODY_XML).build(name)


def _build_pdf(path: Path, pages: int = 2) -> Path:
    """Рисует простой PDF с текстом на каждой странице (для режимов с PDF)."""
    pymupdf = pytest.importorskip(
        "pymupdf", reason="Для режимов с PDF требуется PyMuPDF"
    )
    document = pymupdf.open()
    try:
        for number in range(1, pages + 1):
            page = document.new_page(width=595, height=842)
            page.insert_text((72, 90), f"Introduction page {number}", fontsize=12)
        document.save(str(path))
    finally:
        document.close()
    return path


def test_auto_mode_with_docx_only(docx_builder: DocxPackageBuilder) -> None:
    """auto + один DOCX -> docx_only с полным DOCX-набором возможностей."""
    docx_path = _build_docx(docx_builder)

    document = parse_document(docx_path)

    assert isinstance(document, ParsedDocument)
    assert document.parse_mode == "docx_only"
    assert document.source.mode == "docx_only"
    assert document.source.has_docx is True
    assert document.source.has_pdf is False
    assert document.source.docx_filename == docx_path.name
    assert document.source.docx_sha256 == hashlib.sha256(docx_path.read_bytes()).hexdigest()
    assert document.source.pdf_filename is None

    capabilities = document.capabilities
    assert capabilities.styles_parsed is True
    assert capabilities.numbering_parsed is True
    assert capabilities.sections_parsed is True
    assert capabilities.pdf_layout_coordinates is False
    assert capabilities.exact_pagination is False
    assert capabilities.page_count_estimated is True
    assert document.page_count.total_pages >= 1


def test_docx_only_json_has_no_pdf_fields(docx_builder: DocxPackageBuilder) -> None:
    """В docx_only PDF-ключи удаляются из JSON, а контрактные поля остаются."""
    document = parse_document(_build_docx(docx_builder))

    payload = document.model_dump(mode="json")

    assert payload["schema_version"] == "3.0.0"
    assert payload["parse_mode"] == "docx_only"
    assert payload["source"]["has_pdf"] is False
    assert payload["capabilities"]["pdf_layout_coordinates"] is False
    assert "pdf_pages_audit" not in payload
    assert not any(key.startswith("pdf_") for key in payload["meta"])
    assert "rendered_page_numbers_present" not in payload["meta"]


def test_explicit_docx_only_ignores_pdf(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """Явный docx_only не читает PDF, даже если файл передан."""
    docx_path = _build_docx(docx_builder)
    pdf_path = _build_pdf(tmp_path / "reference.pdf")

    document = parse_document(docx_path, pdf_path=pdf_path, mode="docx_only")

    assert document.parse_mode == "docx_only"
    assert document.source.has_pdf is False
    assert document.source.pdf_filename is None
    assert document.pdf_pages_audit == []
    assert document.capabilities.pdf_layout_coordinates is False
    payload = document.model_dump(mode="json")
    assert "pdf_pages_audit" not in payload
    assert not any(key.startswith("pdf_") for key in payload["meta"])


def test_auto_mode_with_docx_and_pdf(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """auto + DOCX и PDF: число страниц измерено по PDF, но сверка не применена.

    PDF в этом тесте содержит посторонний текст, поэтому sync_score низкий:
    блоки остаются на оценочных страницах DOCX, координат вёрстки у них нет.
    """
    docx_path = _build_docx(docx_builder)
    pdf_path = _build_pdf(tmp_path / "reference.pdf", pages=3)

    document = parse_document(docx_path, pdf_path=pdf_path)

    assert document.parse_mode == "docx_pdf"
    assert document.source.mode == "docx_pdf"
    assert document.source.has_docx is True
    assert document.source.has_pdf is True
    assert document.source.docx_filename == docx_path.name
    assert document.source.pdf_filename == pdf_path.name
    assert document.source.pdf_sha256 and len(document.source.pdf_sha256) == 64

    # Число листов PDF измерено, поэтому page_count остается pdf_exact/high.
    assert document.page_count.method == "pdf_exact"
    assert document.page_count.confidence == "high"
    assert document.page_count.total_pages == 3
    assert len(document.pdf_pages_audit) == 3

    # Но выравнивание не применено: точная пагинация и координаты недоступны.
    assert document.capabilities.styles_parsed is True
    assert document.capabilities.pdf_layout_coordinates is False
    assert document.capabilities.exact_pagination is False
    assert document.page_count.note
    assert document.page_count.note != ""

    payload = document.model_dump(mode="json")
    assert payload["parse_mode"] == "docx_pdf"
    assert "pdf_pages_audit" in payload
    assert "pdf_filename" in payload["meta"]
    assert "pdf_sha256" in payload["source"]


def test_auto_mode_with_pdf_only(tmp_path: Path) -> None:
    """auto + единственный PDF (передан как input_path) -> pdf_only."""
    pdf_path = _build_pdf(tmp_path / "only.pdf", pages=2)

    document = parse_document(pdf_path)

    assert document.parse_mode == "pdf_only"
    assert document.source.mode == "pdf_only"
    assert document.source.has_docx is False
    assert document.source.docx_filename is None
    assert document.source.has_pdf is True
    assert document.source.pdf_filename == pdf_path.name

    assert document.page_count.method == "pdf_exact"
    assert document.page_count.confidence == "high"
    assert document.page_count.total_pages == 2

    capabilities = document.capabilities
    assert capabilities.styles_parsed is False
    assert capabilities.numbering_parsed is False
    assert capabilities.footnotes_parsed is False
    assert capabilities.pdf_layout_coordinates is True
    assert capabilities.page_count_estimated is False

    payload = document.model_dump(mode="json")
    assert payload["parse_mode"] == "pdf_only"
    assert "pdf_pages_audit" in payload


def test_pdf_only_accepts_pdf_in_pdf_argument(tmp_path: Path) -> None:
    """pdf_only работает и когда PDF передан отдельным аргументом pdf_path."""
    pdf_path = _build_pdf(tmp_path / "separate.pdf", pages=1)

    document = parse_document(None, pdf_path=pdf_path, mode="pdf_only")

    assert document.parse_mode == "pdf_only"
    assert document.source.has_pdf is True
    assert document.page_count.total_pages == 1


def test_invalid_mode_raises_value_error(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Неизвестный режим — ошибка запроса, а не падение парсера."""
    docx_path = _build_docx(docx_builder)

    with pytest.raises(ValueError, match="Неизвестный режим"):
        parse_document(docx_path, mode="pdf_and_docx")


def test_no_files_raises_value_error() -> None:
    """Без файлов режим не выбирается."""
    with pytest.raises(ValueError):
        parse_document(None)
    with pytest.raises(ValueError):
        parse_document("")


@pytest.mark.parametrize(
    "mode",
    ["docx_pdf", "pdf_only"],
)
def test_explicit_mode_without_required_file(
    docx_builder: DocxPackageBuilder, mode: str
) -> None:
    """Явный режим без обязательного файла — ValueError."""
    docx_path = _build_docx(docx_builder)

    with pytest.raises(ValueError):
        parse_document(docx_path, mode=mode)


def test_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    """Несуществующий файл — FileNotFoundError (в HTTP это 400)."""
    with pytest.raises(FileNotFoundError):
        parse_document(tmp_path / "absent.docx")


def test_incompatible_pdf_pair_raises_value_error(tmp_path: Path) -> None:
    """Два разных PDF в роли документа и эталона — несовместимая пара."""
    first = _build_pdf(tmp_path / "first.pdf", pages=1)
    second = _build_pdf(tmp_path / "second.pdf", pages=2)

    with pytest.raises(ValueError, match="Несовместимая пара"):
        parse_document(first, pdf_path=second)


def test_pdf_argument_must_be_pdf(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """В поле pdf нельзя передать DOCX."""
    docx_path = _build_docx(docx_builder)

    with pytest.raises(ValueError, match="должен иметь расширение .pdf"):
        parse_document(docx_path, pdf_path=docx_path)


def test_unsupported_document_format(tmp_path: Path) -> None:
    """Формат вне docx/doc/rtf/odt и pdf отклоняется до разбора."""
    text_file = tmp_path / "notes.txt"
    text_file.write_text("не документ", encoding="utf-8")

    with pytest.raises(ValueError, match="Неподдерживаемый формат"):
        parse_document(text_file)


def test_corrupt_docx_raises_parsing_error(tmp_path: Path) -> None:
    """Битый DOCX не роняет сервис наружу: ошибка классифицируется как 422."""
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a zip archive at all")

    with pytest.raises(DocumentParsingError):
        parse_document(broken)


def test_parse_to_json_file_writes_utf8_contract(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """parse_to_json_file пишет JSON UTF-8 с контрактными полями и возвращает модель."""
    docx_path = _build_docx(docx_builder)
    output_path = tmp_path / "nested" / "result.json"

    document = parse_to_json_file(docx_path, output_path)

    assert isinstance(document, ParsedDocument)
    assert output_path.is_file()
    raw = output_path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["parse_mode"] == "docx_only"
    assert "source" in payload
    assert "capabilities" in payload
    assert payload["source"]["docx_filename"] == docx_path.name
    # Кириллица сохраняется в UTF-8, а не экранируется.
    assert "Введение" in raw


def test_legacy_conversion_source_describes_original_file(
    docx_builder: DocxPackageBuilder,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """source.docx_* описывает исходный загруженный файл, meta — сконвертированный.

    convert_to_docx подменяется: байты «сконвертированного» DOCX отличаются от
    входного .doc, иначе проверка не отличила бы хэш исходника от хэша результата
    LibreOffice.
    """
    original = tmp_path / "legacy.doc"
    original.write_bytes(b"legacy-doc-original-bytes")
    converted = DocxPackageBuilder(tmp_path).set_body(BODY_XML).build("legacy.docx")
    assert original.read_bytes() != converted.read_bytes()

    import parser.docx.converter as converter_module

    def fake_convert(path, **kwargs):
        assert Path(path) == original
        return converter_module.ConversionResult(
            docx_path=converted,
            source_format="doc",
            converted_via_lo=True,
            tool="fake-converter",
            duration_ms=1,
        )

    monkeypatch.setattr(converter_module, "convert_to_docx", fake_convert)

    document = parse_document(original, mode="docx_only")

    assert document.parse_mode == "docx_only"
    assert document.source.docx_filename == original.name
    assert document.source.docx_sha256 == hashlib.sha256(original.read_bytes()).hexdigest()
    assert document.source.docx_sha256 != hashlib.sha256(converted.read_bytes()).hexdigest()
    # meta продолжает описывать уже разобранный DOCX, а не исходный .doc.
    assert document.meta.filename == converted.name
    assert document.meta.file_sha256 == hashlib.sha256(converted.read_bytes()).hexdigest()


def test_describe_parse_modes_contract() -> None:
    """Описание режимов совпадает с контрактом и содержит capabilities."""
    modes = describe_parse_modes()

    assert [item["mode"] for item in modes] == ["docx_only", "pdf_only", "docx_pdf"]
    by_mode = {item["mode"]: item for item in modes}
    assert by_mode["docx_only"]["required_files"] == ["document"]
    assert by_mode["pdf_only"]["required_files"] == ["pdf"]
    assert by_mode["docx_pdf"]["required_files"] == ["document", "pdf"]

    for item in modes:
        capabilities = item["capabilities"]
        assert set(capabilities) == {
            "styles_parsed",
            "numbering_parsed",
            "sections_parsed",
            "exact_pagination",
            "pdf_layout_coordinates",
            "visual_registry",
            "footnotes_parsed",
            "cross_references",
            "word_metadata",
            "page_count_estimated",
        }

    assert by_mode["docx_only"]["capabilities"]["pdf_layout_coordinates"] is False
    assert by_mode["pdf_only"]["capabilities"]["pdf_layout_coordinates"] is True
    assert by_mode["pdf_only"]["capabilities"]["styles_parsed"] is False
