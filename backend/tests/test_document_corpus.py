from __future__ import annotations
import pytest
from conftest import DocxPackageBuilder
from docx_parser.pipeline import DocxParserPipeline
from docx_parser.schema import ParagraphBlock


def _build_variant(
    docx_builder: DocxPackageBuilder,
    *,
    title_text: str = "Введение.",
    body_text: str = "Основной текст.",
    body_size_pt: float = 14.0,
    numbered: bool = False,
) -> str:
    """Создает минимальный DOCX с заголовком и абзацем текста"""
    title_size = 32
    body_size = int(round(body_size_pt * 2))

    if numbered:
        docx_builder.set_xml(
            "word/styles.xml",
            f'''<?xml version="1.0" encoding="UTF-8"?>
            <w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                      xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
                      xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"
                      xmlns:wps="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingShape"
                      xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
                      xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"
                      xmlns:v="urn:schemas-microsoft-com:vml">
              <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
                <w:name w:val="Normal"/>
              </w:style>
              <w:style w:type="paragraph" w:styleId="ListStyle">
                <w:name w:val="List Style"/>
                <w:basedOn w:val="Normal"/>
                <w:pPr>
                  <w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>
                </w:pPr>
              </w:style>
              <w:style w:type="paragraph" w:styleId="Heading1">
                <w:name w:val="heading 1"/>
                <w:pPr><w:outlineLvl w:val="0"/></w:pPr>
                <w:rPr><w:b/><w:sz w:val="{title_size}"/></w:rPr>
              </w:style>
            </w:styles>''',
        )
        docx_builder.set_xml(
            "word/numbering.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                         xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                         xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <w:abstractNum w:abstractNumId="0">
                <w:lvl w:ilvl="0">
                  <w:start w:val="1"/>
                  <w:numFmt w:val="decimal"/>
                  <w:lvlText w:val="%1."/>
                  <w:suff w:val="tab"/>
                </w:lvl>
              </w:abstractNum>
              <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
            </w:numbering>""",
        )

    body_xml = f'''
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r>
        <w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/><w:sz w:val="{title_size}"/></w:rPr>
        <w:t>{title_text}</w:t>
      </w:r>
    </w:p>
    <w:p>
      <w:pPr>{'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if numbered else ""}</w:pPr>
      <w:r>
        <w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/><w:sz w:val="{body_size}"/></w:rPr>
        <w:t>{body_text}</w:t>
      </w:r>
    </w:p>
    '''
    return docx_builder.set_body(body_xml).build(
        f"corpus_{body_text.replace('.', '').replace(' ', '_')}.docx"
    )


@pytest.mark.parametrize(
    (
        "variant",
        "expected_title",
        "expected_body",
        "expected_body_pt",
        "expected_list_marker",
    ),
    [
        ("baseline", "Введение.", "Основной текст.", 14.0, None),
        ("font_12", "Введение.", "Основной текст.", 12.0, None),
        ("missing_title_period", "Введение", "Основной текст.", 14.0, None),
        ("missing_numbering", "Введение.", "Пункт 1", 14.0, None),
        ("numbered_ok", "Введение.", "Пункт 1", 14.0, "1."),
    ],
)
def test_docx_corpus_variants(
    docx_builder: DocxPackageBuilder,
    variant: str,
    expected_title: str,
    expected_body: str,
    expected_body_pt: float,
    expected_list_marker: str | None,
) -> None:
    """Корпус корректного документа и его вариаций с одной ошибкой читается предсказуемо."""
    if variant == "font_12":
        body_size_pt = 12.0
    else:
        body_size_pt = 14.0

    numbered = variant == "numbered_ok"
    if variant == "missing_numbering":
        body_text = "Пункт 1"
    else:
        body_text = expected_body

    docx_path = _build_variant(
        docx_builder,
        title_text=expected_title,
        body_text=body_text,
        body_size_pt=body_size_pt,
        numbered=numbered,
    )

    doc = DocxParserPipeline(docx_path).parse()
    paragraphs = [b for b in doc.blocks if isinstance(b, ParagraphBlock)]
    assert len(paragraphs) >= 2

    title = paragraphs[0]
    body = paragraphs[1]

    assert title.text_visible == expected_title
    assert title.runs[0].effective_props.size_pt == pytest.approx(16.0)
    assert body.text_visible == expected_body
    assert body.runs[0].effective_props.size_pt == pytest.approx(expected_body_pt)

    if expected_list_marker is None:
        assert body.list_info is None
    else:
        assert body.list_info is not None
        assert body.list_info.rendered_marker == expected_list_marker


def test_dirty_doc_corpus_keeps_manual_formatting_issues_detectable(
    docx_builder: DocxPackageBuilder,
) -> None:
    """«Грязный» документ с руками расставленными пробелами фиксируется как аномалия, но текст читается"""
    body_xml = """
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Введение.</w:t></w:r></w:p>
    <w:p><w:r><w:t xml:space="preserve">  Основной  текст и   лишние  пробелы.  </w:t></w:r></w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("dirty_manual.docx")
    doc = DocxParserPipeline(docx_path).parse()

    paragraphs = [b for b in doc.blocks if isinstance(b, ParagraphBlock)]
    assert paragraphs[0].text_visible == "Введение."
    assert paragraphs[1].text_visible.startswith("  Основной")
    assert paragraphs[1].typography.multiple_spaces
    assert len(paragraphs[1].typography.multiple_spaces) >= 1
