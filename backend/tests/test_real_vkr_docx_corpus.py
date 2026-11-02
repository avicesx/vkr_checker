from __future__ import annotations
from pathlib import Path
from typing import Callable
from zipfile import ZIP_DEFLATED, ZipFile
import pytest
from lxml import etree
from docx_parser.pipeline import DocxParserPipeline
from docx_parser.schema import ParagraphBlock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VKR_DIR = PROJECT_ROOT.parent / "documents" / "vkr"
REAL_VKR_DOCX = sorted(
    path
    for path in VKR_DIR.glob("*.docx")
    if any(ord(char) > 127 for char in path.name)
)
POPOVA_DOCX = VKR_DIR / "Попова К.В. РИ-420943 Диплом2.docx"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


@pytest.mark.integration
@pytest.mark.parametrize(
    "docx_path",
    REAL_VKR_DOCX,
    ids=lambda path: path.name,
)
def test_real_vkr_docx_files_are_parsed(docx_path: Path) -> None:
    parsed = DocxParserPipeline(docx_path).parse()

    assert parsed.meta.filename == docx_path.name
    assert parsed.blocks
    assert any(
        isinstance(block, ParagraphBlock) and block.text_visible.strip()
        for block in parsed.blocks
    )


@pytest.mark.integration
def test_font_size_fault_in_a_temporary_copy_of_real_vkr(tmp_path: Path) -> None:
    if not POPOVA_DOCX.is_file():
        pytest.skip("Нет documents/vkr/Попова К.В. РИ-420943 Диплом2.docx")

    source_paragraph = next(
        block
        for block in DocxParserPipeline(POPOVA_DOCX).parse().blocks
        if isinstance(block, ParagraphBlock)
        and block.text_visible.startswith("ПРОЕКТИРОВАНИЕ СИСТЕМЫ, СБОР ТРЕБОВАНИЙ")
    )
    copy_path = tmp_path / "font_12_fault.docx"
    _rewrite_docx_paragraph(
        POPOVA_DOCX,
        copy_path,
        lambda paragraph: _set_first_run_size(paragraph, 24),
        lambda text: text.startswith("ПРОЕКТИРОВАНИЕ СИСТЕМЫ, СБОР ТРЕБОВАНИЙ"),
    )

    parsed_copy = DocxParserPipeline(copy_path).parse()
    changed = next(
        block
        for block in parsed_copy.blocks
        if isinstance(block, ParagraphBlock)
        and block.text_visible == source_paragraph.text_visible
    )

    assert source_paragraph.runs[0].effective_props.size_pt == pytest.approx(14.0)
    assert changed.runs[0].effective_props.size_pt == pytest.approx(12.0)
    assert POPOVA_DOCX.is_file()


@pytest.mark.integration
def test_missing_list_numbering_in_a_temporary_copy_of_real_vkr(
    tmp_path: Path,
) -> None:
    if not POPOVA_DOCX.is_file():
        pytest.skip("Нет documents/vkr/Попова К.В. РИ-420943 Диплом2.docx")

    source = DocxParserPipeline(POPOVA_DOCX).parse()
    source_paragraph = next(
        block
        for block in source.blocks
        if isinstance(block, ParagraphBlock)
        and block.text_visible.startswith("снижение затраченного времени:")
        and block.list_info is not None
    )
    copy_path = tmp_path / "missing_numbering_fault.docx"
    _rewrite_docx_paragraph(
        POPOVA_DOCX,
        copy_path,
        _cancel_numbering,
        lambda text: text.startswith("снижение затраченного времени:"),
    )

    parsed_copy = DocxParserPipeline(copy_path).parse()
    changed = next(
        block
        for block in parsed_copy.blocks
        if isinstance(block, ParagraphBlock)
        and block.text_visible == source_paragraph.text_visible
    )

    assert source_paragraph.list_info is not None
    assert changed.list_info is None


@pytest.mark.integration
def test_heading_punctuation_in_a_temporary_copy_of_real_vkr(
    tmp_path: Path,
) -> None:
    if not POPOVA_DOCX.is_file():
        pytest.skip("Нет documents/vkr/Попова К.В. РИ-420943 Диплом2.docx")

    copy_path = tmp_path / "heading_period_fault.docx"
    _rewrite_docx_paragraph(
        POPOVA_DOCX,
        copy_path,
        _append_period_to_text,
        lambda text: text.strip() == "ВВЕДЕНИЕ",
    )

    parsed_copy = DocxParserPipeline(copy_path).parse()
    changed = next(
        block
        for block in parsed_copy.blocks
        if isinstance(block, ParagraphBlock)
        and block.text_visible.strip() == "ВВЕДЕНИЕ."
    )

    assert changed.heading_info is not None


def _rewrite_docx_paragraph(
    source: Path,
    target: Path,
    mutate: Callable[[etree._Element], None],
    matches_text: Callable[[str], bool],
) -> None:
    with ZipFile(source, "r") as source_zip:
        document = etree.fromstring(source_zip.read("word/document.xml"))
        match = next(
            paragraph
            for paragraph in document.xpath(".//w:body/w:p", namespaces=NS)
            if matches_text("".join(paragraph.xpath(".//w:t/text()", namespaces=NS)))
        )
        mutate(match)
        document_xml = etree.tostring(
            document,
            encoding="UTF-8",
            xml_declaration=True,
            standalone=True,
        )
        with ZipFile(target, "w", compression=ZIP_DEFLATED) as target_zip:
            for item in source_zip.infolist():
                contents = (
                    document_xml
                    if item.filename == "word/document.xml"
                    else source_zip.read(item)
                )
                target_zip.writestr(item, contents)


def _set_first_run_size(paragraph: etree._Element, half_points: int) -> None:
    size = paragraph.find("w:r/w:rPr/w:sz", namespaces=NS)
    if size is None:
        raise AssertionError("Целевой абзац не содержит прямого размера шрифта")
    size.set(f"{{{W_NS}}}val", str(half_points))


def _cancel_numbering(paragraph: etree._Element) -> None:
    num_id = paragraph.find("w:pPr/w:numPr/w:numId", namespaces=NS)
    if num_id is None:
        raise AssertionError("Целевой абзац не содержит прямой нумерации")
    num_id.set(f"{{{W_NS}}}val", "0")


def _append_period_to_text(paragraph: etree._Element) -> None:
    text_nodes = paragraph.xpath(".//w:t", namespaces=NS)
    if "".join(node.text or "" for node in text_nodes).strip() != "ВВЕДЕНИЕ":
        raise AssertionError("Целевой заголовок не найден")
    text_node = next(node for node in reversed(text_nodes) if (node.text or "").strip())
    text_node.text = f"{text_node.text}."
