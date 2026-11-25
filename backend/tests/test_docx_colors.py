"""Разбор цвета текста, подсветки и заливок из DOCX.

Тесты фиксируют контракт, на который опираются движковые правила: цвет рана
приходит в ``TextRun.effective_props.color`` (с учетом стилей), подсветка —
в ``highlight_color``, заливка абзаца — в ``ParagraphPropertiesEffective.shading_fill``,
заливка ячейки — в ``TableCell.shading_fill`` без домысливания заливки из стиля
таблицы. Синтетический пакет собирается программно через ``DocxPackageBuilder``
(``tests/conftest.py``): python-docx в зависимостях проекта нет, а прямые
XML-фрагменты позволяют проверить themeColor/themeTint/themeShade, которые
python-docx не выставляет.
"""
from __future__ import annotations

from typing import Iterator

from parser.core.schema import ParagraphBlock, ParsedDocument, TableBlock, TableCell
from parser.docx.pipeline import DocxParserPipeline


def _parse(path) -> ParsedDocument:
    return DocxParserPipeline(path).parse_docx_only()


def _paragraphs(doc: ParsedDocument) -> Iterator[ParagraphBlock]:
    """Все абзацы документа, включая абзацы внутри ячеек таблиц."""
    for block in doc.blocks:
        if isinstance(block, ParagraphBlock):
            yield block
        elif isinstance(block, TableBlock):
            for row in block.rows:
                for cell in row.cells:
                    for inner in cell.blocks:
                        if isinstance(inner, ParagraphBlock):
                            yield inner


def _cells(doc: ParsedDocument) -> Iterator[TableCell]:
    for block in doc.blocks:
        if isinstance(block, TableBlock):
            for row in block.rows:
                yield from row.cells


def _single_paragraph(doc: ParsedDocument) -> ParagraphBlock:
    paragraphs = [p for p in _paragraphs(doc) if p.text_visible]
    assert len(paragraphs) == 1, f"ожидался один абзац, получено {len(paragraphs)}"
    return paragraphs[0]


def _single_run(doc: ParsedDocument):
    paragraph = _single_paragraph(doc)
    assert len(paragraph.runs) == 1, f"ожидался один ран, получено {len(paragraph.runs)}"
    return paragraph.runs[0]


# =====================================================================
# ЦВЕТ ТЕКСТА
# =====================================================================

def test_direct_text_color_is_read(docx_builder) -> None:
    """w:rPr/w:color w:val -> RGB в прямых и эффективных свойствах рана."""
    docx_builder.set_body(
        """
        <w:p><w:r><w:rPr><w:color w:val="FF0000"/></w:rPr><w:t>красный текст</w:t></w:r></w:p>
        """
    )
    run = _single_run(_parse(docx_builder.build("color_direct.docx")))

    assert run.direct_props.color is not None
    assert run.direct_props.color.rgb_hex == "FF0000"
    assert run.direct_props.color.source == "direct"

    color = run.effective_props.color
    assert color.is_auto is False
    assert color.rgb_hex == "FF0000"
    assert color.theme_color is None
    assert color.is_visually_black is False


def test_auto_text_color_is_flagged_auto(docx_builder) -> None:
    """w:val="auto" -> ColorValue(is_auto=True) без RGB."""
    docx_builder.set_body(
        """
        <w:p><w:r><w:rPr><w:color w:val="auto"/></w:rPr><w:t>автоцвет</w:t></w:r></w:p>
        """
    )
    color = _single_run(_parse(docx_builder.build("color_auto.docx"))).effective_props.color

    assert color.is_auto is True
    assert color.rgb_hex is None
    assert color.is_visually_black is True


def test_text_without_color_stays_auto(docx_builder) -> None:
    """Отсутствие w:color не меняет прежнее поведение: автоцвет."""
    docx_builder.set_body(
        """
        <w:p><w:r><w:t>обычный текст</w:t></w:r></w:p>
        """
    )
    color = _single_run(_parse(docx_builder.build("color_absent.docx"))).effective_props.color

    assert color.is_auto is True
    assert color.rgb_hex is None
    assert color.is_visually_black is True


def test_theme_color_with_tint_and_shade(docx_builder) -> None:
    """themeColor + themeTint/themeShade дают итоговый RGB и сохраняют атрибуты темы."""
    docx_builder.set_body(
        """
        <w:p><w:r><w:rPr>
          <w:color w:themeColor="accent1" w:themeTint="99"/>
        </w:rPr><w:t>осветленный акцент</w:t></w:r></w:p>
        <w:p><w:r><w:rPr>
          <w:color w:themeColor="accent1" w:themeShade="99"/>
        </w:rPr><w:t>затемненный акцент</w:t></w:r></w:p>
        <w:p><w:r><w:rPr><w:color w:themeColor="accent1"/></w:rPr><w:t>чистый акцент</w:t></w:r></w:p>
        """
    )
    runs = _paragraphs(_parse(docx_builder.build("color_theme.docx")))
    colors = [p.runs[0].effective_props.color for p in runs]

    # accent1 в теме теста — 4472C4
    assert colors[0].rgb_hex == "8FAADC"
    assert (colors[0].theme_color, colors[0].theme_tint) == ("accent1", "99")
    assert colors[1].rgb_hex == "294476"
    assert (colors[1].theme_color, colors[1].theme_shade) == ("accent1", "99")
    assert colors[2].rgb_hex == "4472C4"
    assert all(c.is_auto is False for c in colors)


def test_character_style_color_reaches_effective_props(docx_builder) -> None:
    """Цвет из стиля символа попадает в эффективные свойства с source=character_style."""
    docx_builder.add_style_xml(
        """
        <w:style w:type="character" w:styleId="RedChar">
          <w:name w:val="Red Char"/>
          <w:rPr><w:color w:val="FF0000"/></w:rPr>
        </w:style>
        """
    )
    docx_builder.set_body(
        """
        <w:p><w:r><w:rPr><w:rStyle w:val="RedChar"/></w:rPr><w:t>стилевой цвет</w:t></w:r></w:p>
        """
    )
    run = _single_run(_parse(docx_builder.build("color_char_style.docx")))

    assert run.char_style_id == "RedChar"
    assert run.effective_props.color.rgb_hex == "FF0000"
    assert run.effective_props.color.source == "character_style"
    assert run.direct_props.color is None


def test_runs_with_different_colors_are_not_merged(docx_builder) -> None:
    """Разный цвет соседних ранов сохраняется как разные TextRun."""
    docx_builder.set_body(
        """
        <w:p>
          <w:r><w:rPr><w:color w:val="FF0000"/></w:rPr><w:t>красный</w:t></w:r>
          <w:r><w:rPr><w:color w:val="0000FF"/></w:rPr><w:t>синий</w:t></w:r>
          <w:r><w:rPr><w:color w:val="FF0000"/></w:rPr><w:t>снова красный</w:t></w:r>
        </w:p>
        """
    )
    paragraph = _single_paragraph(_parse(docx_builder.build("color_runs_split.docx")))

    assert [r.text for r in paragraph.runs] == ["красный", "синий", "снова красный"]
    assert [r.effective_props.color.rgb_hex for r in paragraph.runs] == [
        "FF0000",
        "0000FF",
        "FF0000",
    ]


# =====================================================================
# ПОДСВЕТКА ТЕКСТА
# =====================================================================

def test_highlight_is_read(docx_builder) -> None:
    """w:rPr/w:highlight w:val -> highlight_color."""
    docx_builder.set_body(
        """
        <w:p><w:r><w:rPr><w:highlight w:val="yellow"/></w:rPr><w:t>подсвеченный</w:t></w:r></w:p>
        """
    )
    run = _single_run(_parse(docx_builder.build("highlight.docx")))

    assert run.direct_props.highlight_color == "yellow"
    assert run.effective_props.highlight_color == "yellow"


def test_highlight_none_is_not_reported(docx_builder) -> None:
    """w:highlight val="none" — это снятие подсветки, а не цвет."""
    docx_builder.set_body(
        """
        <w:p><w:r><w:rPr><w:highlight w:val="none"/></w:rPr><w:t>без подсветки</w:t></w:r></w:p>
        """
    )
    run = _single_run(_parse(docx_builder.build("highlight_none.docx")))

    assert run.direct_props.highlight_color is None
    assert run.effective_props.highlight_color is None


# =====================================================================
# ЗАЛИВКА АБЗАЦА
# =====================================================================

def test_paragraph_shading_is_read(docx_builder) -> None:
    """w:pPr/w:shd w:fill -> shading_fill абзаца (прямые и эффективные свойства)."""
    docx_builder.set_body(
        """
        <w:p><w:pPr><w:shd w:val="clear" w:color="auto" w:fill="D9D9D9"/></w:pPr>
          <w:r><w:t>залитый абзац</w:t></w:r></w:p>
        """
    )
    paragraph = _single_paragraph(_parse(docx_builder.build("para_shading.docx")))

    assert paragraph.direct_props.shading_fill is not None
    assert paragraph.direct_props.shading_fill.rgb_hex == "D9D9D9"
    assert paragraph.effective_props.shading_fill is not None
    assert paragraph.effective_props.shading_fill.rgb_hex == "D9D9D9"
    assert paragraph.effective_props.shading_fill.is_auto is False


def test_paragraph_shading_is_inherited_from_style(docx_builder) -> None:
    """Заливка из стиля абзаца доезжает до effective_props через каскад."""
    docx_builder.add_style_xml(
        """
        <w:style w:type="paragraph" w:styleId="FilledPara">
          <w:name w:val="Filled Para"/>
          <w:basedOn w:val="Normal"/>
          <w:pPr><w:shd w:val="clear" w:color="auto" w:fill="EAF1DD"/></w:pPr>
        </w:style>
        """
    )
    docx_builder.set_body(
        """
        <w:p><w:pPr><w:pStyle w:val="FilledPara"/></w:pPr><w:r><w:t>стилевая заливка</w:t></w:r></w:p>
        """
    )
    paragraph = _single_paragraph(_parse(docx_builder.build("para_shading_style.docx")))

    assert paragraph.direct_props.shading_fill is None
    assert paragraph.effective_props.shading_fill is not None
    assert paragraph.effective_props.shading_fill.rgb_hex == "EAF1DD"


def test_run_shading_is_read(docx_builder) -> None:
    """w:rPr/w:shd -> фон рана в effective_props.shading_fill."""
    docx_builder.set_body(
        """
        <w:p><w:r><w:rPr><w:shd w:val="clear" w:color="auto" w:fill="00B050"/></w:rPr>
          <w:t>фон рана</w:t></w:r></w:p>
        """
    )
    run = _single_run(_parse(docx_builder.build("run_shading.docx")))

    assert run.direct_props.shading_fill is not None
    assert run.direct_props.shading_fill.rgb_hex == "00B050"
    assert run.effective_props.shading_fill is not None
    assert run.effective_props.shading_fill.rgb_hex == "00B050"


# =====================================================================
# ЗАЛИВКА ЯЧЕЙКИ
# =====================================================================

def _table_body(first_cell_props: str, second_cell_props: str) -> str:
    return f"""
    <w:tbl>
      <w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>
      <w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>
      <w:tr>
        <w:tc><w:tcPr>{first_cell_props}</w:tcPr><w:p><w:r><w:t>первая</w:t></w:r></w:p></w:tc>
        <w:tc><w:tcPr>{second_cell_props}</w:tcPr><w:p><w:r><w:t>вторая</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    """


def test_table_cell_shading_is_read(docx_builder) -> None:
    """w:tcPr/w:shd w:fill -> TableCell.shading_fill."""
    docx_builder.set_body(
        _table_body(
            '<w:shd w:val="clear" w:color="auto" w:fill="FFFF00"/>',
            '<w:shd w:val="clear" w:color="auto" w:fill="D9E2F3"/>',
        )
    )
    cells = list(_cells(_parse(docx_builder.build("cell_shading.docx"))))

    assert len(cells) == 2
    assert cells[0].shading_fill is not None
    assert cells[0].shading_fill.rgb_hex == "FFFF00"
    assert cells[1].shading_fill is not None
    assert cells[1].shading_fill.rgb_hex == "D9E2F3"


def test_theme_shading_of_cell_uses_theme_fill(docx_builder) -> None:
    """Заливка ячейки через themeFill + themeFillTint тоже раскрывается в RGB."""
    docx_builder.set_body(
        _table_body(
            '<w:shd w:val="clear" w:color="auto" w:themeFill="accent1" w:themeFillTint="33"/>',
            "",
        )
    )
    cells = list(_cells(_parse(docx_builder.build("cell_shading_theme.docx"))))

    assert cells[0].shading_fill is not None
    assert cells[0].shading_fill.theme_color == "accent1"
    assert cells[0].shading_fill.theme_tint == "33"
    assert cells[0].shading_fill.rgb_hex == "DAE3F3"


def test_cell_without_explicit_shading_has_no_fill(docx_builder) -> None:
    """Заливка из стиля таблицы не домысливается: только явная разметка w:tcPr."""
    docx_builder.set_body(_table_body("", ""))
    cells = list(_cells(_parse(docx_builder.build("cell_shading_none.docx"))))

    assert [c.shading_fill for c in cells] == [None, None]


# =====================================================================
# РЕГРЕССИИ: ДОКУМЕНТ БЕЗ ЦВЕТА И ПУСТЫЕ СВОЙСТВА
# =====================================================================

def test_document_without_colors_keeps_previous_values(docx_builder) -> None:
    """Файл без разметки цвета даёт автоцвет и пустые подсветку с заливками."""
    docx_builder.set_body(
        """
        <w:p><w:r><w:t>первый абзац</w:t></w:r></w:p>
        <w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>второй абзац</w:t></w:r></w:p>
        <w:tbl>
          <w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>
          <w:tblGrid><w:gridCol w:w="3000"/></w:tblGrid>
          <w:tr><w:tc><w:p><w:r><w:t>ячейка</w:t></w:r></w:p></w:tc></w:tr>
        </w:tbl>
        """
    )
    doc = _parse(docx_builder.build("no_colors.docx"))

    paragraphs = list(_paragraphs(doc))
    assert paragraphs
    for paragraph in paragraphs:
        assert paragraph.effective_props.shading_fill is None
        for run in paragraph.runs:
            assert run.effective_props.color.is_auto is True
            assert run.effective_props.color.rgb_hex is None
            assert run.effective_props.highlight_color is None
            assert run.effective_props.shading_fill is None

    cells = list(_cells(doc))
    assert len(cells) == 1
    assert cells[0].shading_fill is None


def test_empty_properties_do_not_break_parsing(docx_builder) -> None:
    """Пустые w:rPr/w:pPr и ячейка без w:tcPr разбираются без ошибок."""
    docx_builder.set_body(
        """
        <w:p/>
        <w:p><w:pPr/></w:p>
        <w:p><w:r><w:rPr/></w:r><w:r><w:t xml:space="preserve">пустые свойства</w:t></w:r></w:p>
        <w:p><w:r><w:rPr><w:color w:val=""/></w:rPr><w:t>пустой цвет</w:t></w:r></w:p>
        <w:tbl>
          <w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>
          <w:tblGrid><w:gridCol w:w="3000"/></w:tblGrid>
          <w:tr><w:tc><w:p/></w:tc><w:tc><w:tcPr/><w:p/></w:tc></w:tr>
        </w:tbl>
        """
    )
    doc = _parse(docx_builder.build("empty_props.docx"))

    paragraphs = list(_paragraphs(doc))
    assert paragraphs
    for paragraph in paragraphs:
        assert paragraph.effective_props.shading_fill is None
        for run in paragraph.runs:
            # Пустой w:color не считается цветом и не роняет разбор
            assert run.effective_props.color.rgb_hex is None
    assert [c.shading_fill for c in _cells(doc)] == [None, None]


def test_header_text_color_is_read(docx_builder) -> None:
    """Цвет и подсветка в колонтитуле читаются тем же каскадом."""
    docx_builder.set_xml(
        "word/header1.xml",
        """<?xml version="1.0" encoding="UTF-8"?>
        <w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:p><w:r><w:rPr><w:color w:val="C00000"/><w:highlight w:val="yellow"/></w:rPr>
            <w:t>колонтитул</w:t></w:r></w:p>
        </w:hdr>""",
    )
    docx_builder.set_xml(
        "[Content_Types].xml",
        """<?xml version="1.0" encoding="UTF-8"?>
        <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Default Extension="xml" ContentType="application/xml"/>
          <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
          <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
          <Override PartName="/word/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
          <Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
        </Types>""",
    )
    docx_builder.set_xml(
        "word/_rels/document.xml.rels",
        """<?xml version="1.0" encoding="UTF-8"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rIdHdr" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
        </Relationships>""",
    )
    docx_builder.set_body(
        """
        <w:p><w:r><w:t>тело</w:t></w:r></w:p>
        <w:sectPr>
          <w:headerReference w:type="default" r:id="rIdHdr"/>
          <w:pgSz w:w="11906" w:h="16838"/>
        </w:sectPr>
        """
    )
    doc = _parse(docx_builder.build("header_color.docx"))
    header = doc.sections[0].headers["default"]
    runs = [run for paragraph in header.blocks for run in paragraph.runs]

    assert len(runs) == 1
    assert runs[0].effective_props.color.rgb_hex == "C00000"
    assert runs[0].effective_props.highlight_color == "yellow"
