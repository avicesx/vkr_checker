"""Общие фикстуры и сборщик минимальных DOCX-пакетов для тестов.

DocxPackageBuilder собирает валидный zip-пакет (document.xml, styles.xml,
relationship-части, медиа) из отдельных XML-фрагментов, поэтому тесты не зависят
от бинарных образцов. make_fake_png_bytes отдает корректный заголовок PNG,
достаточный для чтения размеров и DPI.
"""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from typing import Dict
import pytest

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
WPS_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingShape"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
V_NS = "urn:schemas-microsoft-com:vml"

COMMON_XMLNS = (
    f'xmlns:w="{W_NS}" '
    f'xmlns:r="{R_NS}" '
    f'xmlns:a="{A_NS}" '
    f'xmlns:wp="{WP_NS}" '
    f'xmlns:pic="{PIC_NS}" '
    f'xmlns:wps="{WPS_NS}" '
    f'xmlns:mc="{MC_NS}" '
    f'xmlns:m="{M_NS}" '
    f'xmlns:v="{V_NS}"'
)


def make_fake_png_bytes(width: int = 800, height: int = 600) -> bytes:
    """Генерирует валидный 29-байтный заголовок PNG (IHDR) для проверки чтения размеров и DPI."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_len_and_type = b"\x00\x00\x00\rIHDR"
    dimensions = struct.pack(">II", width, height)
    tail = b"\x08\x02\x00\x00\x00"
    return signature + ihdr_len_and_type + dimensions + tail


class DocxPackageBuilder:
    """Вспомогательный сборщик .docx архивов для модульных и интеграционных тестов."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.files: Dict[str, bytes] = {}
        self.rels: Dict[str, str] = {}
        self._init_defaults()

    def _init_defaults(self) -> None:
        # Базовая тема (word/theme/theme1.xml) с кириллическим Times New Roman для заголовков и Calibri для текста
        self.set_xml(
            "word/theme/theme1.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
            <a:theme {COMMON_XMLNS} name="Office Theme">
              <a:themeElements>
                <a:clrScheme name="Office">
                  <a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>
                  <a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>
                  <a:accent1><a:srgbClr val="4472C4"/></a:accent1>
                  <a:hlink><a:srgbClr val="0563C1"/></a:hlink>
                </a:clrScheme>
                <a:fontScheme name="Office">
                  <a:majorFont>
                    <a:latin typeface="Times New Roman"/>
                    <a:font script="Cyrl" typeface="Times New Roman"/>
                  </a:majorFont>
                  <a:minorFont>
                    <a:latin typeface="Calibri"/>
                    <a:font script="Cyrl" typeface="Calibri"/>
                  </a:minorFont>
                </a:fontScheme>
              </a:themeElements>
            </a:theme>""",
        )

        # Базовые стили (word/styles.xml)
        self.set_xml(
            "word/styles.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
            <w:styles {COMMON_XMLNS}>
              <w:docDefaults>
                <w:rPrDefault>
                  <w:rPr>
                    <w:rFonts w:asciiTheme="minorHAnsi" w:hAnsiTheme="minorHAnsi"/>
                    <w:sz w:val="22"/>
                    <w:color w:val="auto"/>
                  </w:rPr>
                </w:rPrDefault>
                <w:pPrDefault>
                  <w:pPr>
                    <w:spacing w:after="160" w:line="259" w:lineRule="auto"/>
                  </w:pPr>
                </w:pPrDefault>
              </w:docDefaults>
              <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
                <w:name w:val="Normal"/>
                <w:pPr>
                  <w:jc w:val="both"/>
                  <w:ind w:firstLine="709"/>
                  <w:spacing w:after="0" w:line="360" w:lineRule="auto"/>
                </w:pPr>
                <w:rPr>
                  <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>
                  <w:sz w:val="28"/>
                </w:rPr>
              </w:style>
              <w:style w:type="paragraph" w:styleId="Heading1">
                <w:name w:val="heading 1"/>
                <w:basedOn w:val="Normal"/>
                <w:pPr>
                  <w:keepNext/>
                  <w:pageBreakBefore/>
                  <w:outlineLvl w:val="0"/>
                </w:pPr>
                <w:rPr>
                  <w:b/>
                  <w:sz w:val="32"/>
                </w:rPr>
              </w:style>
              <w:style w:type="character" w:styleId="EmphasisToggle">
                <w:name w:val="Emphasis Toggle"/>
                <w:rPr>
                  <w:b/>
                  <w:i/>
                </w:rPr>
              </w:style>
              <w:style w:type="table" w:default="1" w:styleId="TableGrid">
                <w:name w:val="Table Grid"/>
                <w:tblPr>
                  <w:tblBorders>
                    <w:top w:val="single" w:sz="4" w:space="0" w:color="000000"/>
                    <w:left w:val="single" w:sz="4" w:space="0" w:color="000000"/>
                    <w:bottom w:val="single" w:sz="4" w:space="0" w:color="000000"/>
                    <w:right w:val="single" w:sz="4" w:space="0" w:color="000000"/>
                    <w:insideH w:val="single" w:sz="4" w:space="0" w:color="000000"/>
                    <w:insideV w:val="single" w:sz="4" w:space="0" w:color="000000"/>
                  </w:tblBorders>
                </w:tblPr>
                <w:tblStylePr w:type="firstRow">
                  <w:rPr><w:b/></w:rPr>
                </w:tblStylePr>
              </w:style>
            </w:styles>""",
        )

    def set_xml(self, arcname: str, xml_content: str) -> "DocxPackageBuilder":
        self.files[arcname] = xml_content.strip().encode("utf-8")
        return self

    def set_bytes(self, arcname: str, raw_bytes: bytes) -> "DocxPackageBuilder":
        self.files[arcname] = raw_bytes
        return self

    def add_rel(self, r_id: str, target: str) -> "DocxPackageBuilder":
        self.rels[r_id] = target
        return self

    def set_body(self, body_inner_xml: str) -> "DocxPackageBuilder":
        doc_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <w:document {COMMON_XMLNS}>
          <w:body>
            {body_inner_xml}
          </w:body>
        </w:document>"""
        return self.set_xml("word/document.xml", doc_xml)

    def build(self, filename: str = "test_vkr.docx") -> Path:
        if self.rels:
            rels_items = "\n".join(
                f'<Relationship Id="{r_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom" Target="{target}"/>'
                for r_id, target in self.rels.items()
            )
            rels_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              {rels_items}
            </Relationships>"""
            self.set_xml("word/_rels/document.xml.rels", rels_xml)

        out_path = self.tmp_path / filename
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for arcname, data in self.files.items():
                zf.writestr(arcname, data)
        return out_path


@pytest.fixture
def docx_builder(tmp_path: Path) -> DocxPackageBuilder:
    return DocxPackageBuilder(tmp_path)