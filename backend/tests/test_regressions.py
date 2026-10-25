"""Регрессионные тесты разбора DOCX без PDF.

Каждый тест описывает одно правило: каскад стилей и нумерации, микротипографика,
изоляция связей частей, обёртки w:sdt в таблицах, служебные поля модели.
Ожидаемое поведение сформулировано как контракт, а не как история правок.
"""
from __future__ import annotations

import pytest

from conftest import COMMON_XMLNS, DocxPackageBuilder, make_fake_png_bytes
from docx_parser.pipeline import DocxParserPipeline
from docx_parser.schema import ParagraphBlock, TableBlock, TocContainerBlock

def test_libreoffice_zero_hanging_does_not_overwrite_first_line_indent(
    docx_builder: DocxPackageBuilder,
) -> None:
    """
    LibreOffice пишет w:ind одновременно w:hanging="0" и w:firstLine="709".
    Нулевой hanging не должен затирать реальный красный отступ первой строки.
    """
    body_xml = """
    <w:p>
      <w:pPr><w:ind w:left="0" w:hanging="0" w:firstLine="709"/></w:pPr>
      <w:r><w:t>Абзац с красной строкой из LibreOffice.</w:t></w:r>
    </w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("lo_indent.docx")
    doc = DocxParserPipeline(docx_path).parse()

    para = next(b for b in doc.blocks if isinstance(b, ParagraphBlock))
    # 709 twips = 12.507 мм
    assert para.effective_props.indent_first_line_mm == pytest.approx(12.507, abs=0.01)


def test_numbering_cascade_style_based_shared_abstract_and_numid_zero_cancel(
    docx_builder: DocxPackageBuilder,
) -> None:
    """
    Три свойства каскада нумерации:
    1. w:numPr, заданный стилем абзаца, наследуется;
    2. разные w:num с общим w:abstractNum продолжают один счетчик;
    3. прямое w:numId="0" отменяет нумерацию, унаследованную из стиля.
    """
    docx_builder.set_xml(
        "word/styles.xml",
        f"""<?xml version="1.0" encoding="UTF-8"?>
        <w:styles {COMMON_XMLNS}>
          <w:docDefaults>
            <w:rPrDefault>
              <w:rPr>
                <w:rFonts w:asciiTheme="minorHAnsi" w:hAnsiTheme="minorHAnsi"/>
                <w:sz w:val="22"/>
              </w:rPr>
            </w:rPrDefault>
          </w:docDefaults>
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
        </w:styles>""",
    )
    docx_builder.set_xml(
        "word/numbering.xml",
        f"""<?xml version="1.0" encoding="UTF-8"?>
        <w:numbering {COMMON_XMLNS}>
          <w:abstractNum w:abstractNumId="0">
            <w:lvl w:ilvl="0">
              <w:start w:val="1"/>
              <w:numFmt w:val="decimal"/>
              <w:lvlText w:val="%1."/>
              <w:suff w:val="tab"/>
            </w:lvl>
          </w:abstractNum>
          <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
          <w:num w:numId="2"><w:abstractNumId w:val="0"/></w:num>
        </w:numbering>""",
    )

    body_xml = """
    <w:p>
      <w:pPr><w:pStyle w:val="ListStyle"/></w:pPr>
      <w:r><w:t>Первый пункт списка</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr>
      <w:r><w:t>Второй пункт списка</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr>
        <w:pStyle w:val="ListStyle"/>
        <w:numPr><w:numId w:val="0"/></w:numPr>
      </w:pPr>
      <w:r><w:t>Абзац без нумерации</w:t></w:r>
    </w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("numbering_cascade.docx")
    doc = DocxParserPipeline(docx_path).parse()

    paragraphs = [b for b in doc.blocks if isinstance(b, ParagraphBlock)]
    first, second, third = paragraphs[0], paragraphs[1], paragraphs[2]

    assert first.list_info is not None and first.list_info.rendered_marker == "1."
    # Общий abstractNum: невзирая на другой numId, счетчик продолжается
    assert second.list_info is not None and second.list_info.rendered_marker == "2."
    # numId="0" полностью отменяет список из стиля
    assert third.list_info is None



def test_inline_omml_formula_does_not_produce_false_double_space(
    docx_builder: DocxPackageBuilder,
) -> None:
    """
    «где » + <m:oMath>x</m:oMath> + « — величина» не является двойным пробелом:
    по обе стороны формулы пробелы обязательны. Раны не склеиваются через формулу,
    иначе сканер микротипографики дает ложное срабатывание.
    """
    body_xml = """
    <w:p>
      <w:r><w:t xml:space="preserve">где </w:t></w:r>
      <m:oMath><m:r><m:t>x</m:t></m:r></m:oMath>
      <w:r><w:t xml:space="preserve"> — величина</w:t></w:r>
    </w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("inline_math.docx")
    doc = DocxParserPipeline(docx_path).parse()

    para = next(b for b in doc.blocks if isinstance(b, ParagraphBlock))
    assert not para.typography.multiple_spaces
    assert len(para.embedded_equation_ids) == 1



def test_table_rows_inside_sdt_and_tblprex_row_borders(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Строка, обёрнутая в w:sdt, должна попадать в разбор, а w:tblPrEx — задавать рамки строки."""
    body_xml = """
    <w:tbl>
      <w:tblPr>
        <w:tblStyle w:val="TableGrid"/>
        <w:tblW w:w="0" w:type="auto"/>
      </w:tblPr>
      <w:tblGrid><w:gridCol w:w="4252"/></w:tblGrid>
      <w:tr>
        <w:tc>
          <w:tcPr><w:tcW w:w="4252" w:type="dxa"/></w:tcPr>
          <w:p><w:r><w:t>Обычная строка</w:t></w:r></w:p>
        </w:tc>
      </w:tr>
      <w:sdt>
        <w:sdtContent>
          <w:tr>
            <w:tblPrEx>
              <w:tblBorders>
                <w:top w:val="double" w:sz="6" w:space="0" w:color="FF0000"/>
              </w:tblBorders>
            </w:tblPrEx>
            <w:tc>
              <w:tcPr><w:tcW w:w="4252" w:type="dxa"/></w:tcPr>
              <w:p><w:r><w:t>Строка внутри sdt</w:t></w:r></w:p>
            </w:tc>
          </w:tr>
        </w:sdtContent>
      </w:sdt>
    </w:tbl>
    """
    docx_path = docx_builder.set_body(body_xml).build("table_sdt.docx")
    doc = DocxParserPipeline(docx_path).parse()

    table = next(b for b in doc.blocks if isinstance(b, TableBlock))
    assert len(table.rows) == 2
    assert "Строка внутри sdt" in table.rows[1].cells[0].text_combined
    # Рамка из w:tblPrEx применилась к ячейке второй строки
    assert table.rows[1].cells[0].effective_borders.top.style == "double"


def test_table_continuation_backlink_points_to_primary_table(
    docx_builder: DocxPackageBuilder,
) -> None:
    body_xml = """
    <w:p><w:r><w:t>Таблица 1 — Результаты испытаний</w:t></w:r></w:p>
    <w:tbl>
      <w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>
      <w:tblGrid><w:gridCol w:w="4252"/></w:tblGrid>
      <w:tr>
        <w:tc><w:tcPr><w:tcW w:w="4252" w:type="dxa"/></w:tcPr>
          <w:p><w:r><w:t>Начало таблицы</w:t></w:r></w:p>
        </w:tc>
      </w:tr>
    </w:tbl>
    <w:p><w:r><w:t>Продолжение таблицы 1</w:t></w:r></w:p>
    <w:tbl>
      <w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>
      <w:tblGrid><w:gridCol w:w="4252"/></w:tblGrid>
      <w:tr>
        <w:tc><w:tcPr><w:tcW w:w="4252" w:type="dxa"/></w:tcPr>
          <w:p><w:r><w:t>Конец таблицы</w:t></w:r></w:p>
        </w:tc>
      </w:tr>
    </w:tbl>
    """
    docx_path = docx_builder.set_body(body_xml).build("table_continuation.docx")
    doc = DocxParserPipeline(docx_path).parse()

    tables = [b for b in doc.blocks if isinstance(b, TableBlock)]
    assert len(tables) == 2
    primary, continuation = tables[0], tables[1]
    assert continuation.continuation_of_table_id == primary.block_id
    assert primary.spans_multiple_pages is True



def test_toc_raw_paragraphs_keep_formatting_facts_in_json(
    docx_builder: DocxPackageBuilder,
) -> None:
    body_xml = """
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Содержание</w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>ВВЕДЕНИЕ\t3</w:t></w:r>
    </w:p>

    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>ВВЕДЕНИЕ</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>Текст введения.</w:t></w:r></w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("toc_service_field.docx")
    doc = DocxParserPipeline(docx_path).parse()

    toc = next(b for b in doc.blocks if isinstance(b, TocContainerBlock))
    assert toc.raw_paragraphs, "Исходные абзацы оглавления должны сохраняться в модели"

    payload = doc.model_dump(mode="json")
    toc_json = next(
        block for block in payload["blocks"] if block["block_type"] == "toc_block"
    )
    raw_paragraphs = toc_json["raw_paragraphs"]
    assert len(raw_paragraphs) == len(toc.raw_paragraphs)
    assert raw_paragraphs[0]["text_visible"] == "ВВЕДЕНИЕ\t3"
    assert raw_paragraphs[0]["effective_props"]["tab_stops"] == []
    assert raw_paragraphs[0]["runs"], "Раны строки оглавления должны попасть в JSON"
    assert "effective_props" in raw_paragraphs[0]["runs"][0]



def test_toc_page_matches_stay_unknown_without_exact_pdf_pagination(
    docx_builder: DocxPackageBuilder,
) -> None:
    """
    Без PDF и без lastRenderedPageBreak страницы DOCX эвристические.
    Совпавшая цифра не должна превращаться в is_page_number_match=True,
    но фактическая разность page_number_delta сохраняется.
    """
    body_xml = """
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Содержание</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>ВВЕДЕНИЕ\t1</w:t></w:r></w:p>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>ВВЕДЕНИЕ</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>Текст введения.</w:t></w:r></w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("toc_unknown_pages.docx")
    doc = DocxParserPipeline(docx_path).parse()

    toc = next(b for b in doc.blocks if isinstance(b, TocContainerBlock))
    entry = toc.entries[0]
    assert doc.meta.pagination_source == "geometric_fallback"
    assert entry.matched_heading_block_id is not None
    assert entry.actual_heading_page_est is not None
    assert entry.page_number_delta == 1 - entry.actual_heading_page_est
    assert entry.page_number_delta != 0
    assert entry.is_page_number_match is None


def test_native_rendered_breaks_are_reported_as_pagination_source(
    docx_builder: DocxPackageBuilder,
) -> None:
    """lastRenderedPageBreak без PDF должен давать pagination_source='docx_native_breaks'."""
    body_xml = """
    <w:p>
      <w:r><w:lastRenderedPageBreak/><w:t>Первая страница с нативным разрывом.</w:t></w:r>
    </w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("native_breaks.docx")
    doc = DocxParserPipeline(docx_path).parse()

    assert doc.meta.pagination_source == "docx_native_breaks"



def test_equation_section_and_appendix_mentions_are_resolved(
    docx_builder: DocxPackageBuilder,
) -> None:
    """
    Упоминание формулы регистрирует ее в EquationObject, ссылка на раздел
    разрешается в узел document_tree, а приложение получает structure_node_id.
    """
    body_xml = """
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Раздел 1 МЕТОДИКА</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>В разделе 1 описана методика расчета.</w:t></w:r></w:p>
    <w:p>
      <m:oMathPara>
        <m:oMath>
          <m:r><m:t>E</m:t></m:r>
          <m:r><m:t>=</m:t></m:r>
          <m:r><m:t>m</m:t></m:r>
        </m:oMath>
      </m:oMathPara>
      <w:r><w:t xml:space="preserve">\t(1.1)</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>В формуле (1.1) используется зависимость энергии от массы.</w:t></w:r></w:p>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Приложение А СХЕМА</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>См. приложение А.</w:t></w:r></w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("mentions_and_appendix.docx")
    doc = DocxParserPipeline(docx_path).parse()

    assert len(doc.cross_ref_index.equations) == 1
    equation = doc.cross_ref_index.equations[0]
    assert equation.number_clean == "1.1"
    assert equation.numbering_scheme == "section_based"
    assert equation.is_mentioned_in_text is True
    assert len(equation.all_mention_ids) == 1
    assert equation.first_mention_block_id is not None
    assert equation.first_mention_page_est is not None

    section_mentions = [
        m
        for m in doc.cross_ref_index.mentions
        if m.ref_type == "section" and m.extracted_targets == ["1"]
    ]
    assert section_mentions, "Ссылка «в разделе 1» должна быть найдена"
    assert section_mentions[0].resolved_structure_node_ids
    node_ids = {node.node_id for node in doc.document_tree}
    assert set(section_mentions[0].resolved_structure_node_ids) <= node_ids

    assert len(doc.cross_ref_index.Apps) == 1
    appendix = doc.cross_ref_index.Apps[0]
    assert appendix.structure_node_id is not None
    assert appendix.structure_node_id in node_ids



def test_header_image_is_resolved_via_header_own_rels(
    docx_builder: DocxPackageBuilder,
) -> None:
    """
    Одинаковые rId в document.xml.rels и header1.xml.rels указывают на РАЗНЫЕ файлы.
    Картинка из колонтитула должна разрешаться по word/_rels/header1.xml.rels,
    а не по document.xml.rels (иначе в отчет попадает чужое изображение).
    """
    docx_builder.add_rel("rId1", "media/document_image.png")
    docx_builder.set_bytes("word/media/document_image.png", make_fake_png_bytes(10, 10))
    docx_builder.set_bytes("word/media/header_image.png", make_fake_png_bytes(20, 20))
    docx_builder.set_xml(
        "word/_rels/header1.xml.rels",
        """<?xml version="1.0" encoding="UTF-8"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
            Target="media/header_image.png"/>
        </Relationships>""",
    )
    docx_builder.set_xml(
        "word/header1.xml",
        f"""<?xml version="1.0" encoding="UTF-8"?>
        <w:hdr {COMMON_XMLNS}>
          <w:p>
            <w:r>
              <w:drawing>
                <wp:inline>
                  <wp:extent cx="914400" cy="914400"/>
                  <a:graphic>
                    <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
                      <pic:pic>
                        <pic:blipFill><a:blip r:embed="rId1"/></pic:blipFill>
                      </pic:pic>
                    </a:graphicData>
                  </a:graphic>
                </wp:inline>
              </w:drawing>
            </w:r>
          </w:p>
        </w:hdr>""",
    )
    docx_builder.add_rel("rId2", "header1.xml")

    body_xml = """
    <w:p><w:r><w:t>Основной текст документа.</w:t></w:r></w:p>
    <w:sectPr>
      <w:headerReference w:type="default" r:id="rId2"/>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701"/>
    </w:sectPr>
    """
    docx_path = docx_builder.set_body(body_xml).build("header_rels.docx")
    doc = DocxParserPipeline(docx_path).parse()

    header = doc.sections[0].headers["default"]
    visual_ids = [
        vis_id
        for block in header.blocks
        if isinstance(block, ParagraphBlock)
        for vis_id in block.embedded_visual_ids
    ]
    assert visual_ids, "Картинка из колонтитула должна быть зарегистрирована"
    image_details = doc.visuals[visual_ids[0]].image_details
    assert image_details is not None
    assert image_details.media_target.endswith("media/header_image.png")
    assert "document_image.png" not in image_details.media_target
