"""Распознавание OLE-формул без ProgID после конвертации LibreOffice."""
from __future__ import annotations

from pathlib import Path

from docx_parser import parse_docx_to_json_file

OLE_MARKUP = """
<w:p>
  <w:r>
    <w:object xmlns:o="urn:schemas-microsoft-com:office:office">
      <v:shape id="_x0000_i1025" type="#_x0000_t75"
               style="width:60pt;height:20pt"/>
      <o:OLEObject Type="Embed" ProgID="{prog_id}" ShapeID="_x0000_i1025"
                   DrawAspect="Content" ObjectID="_1" r:id="rId1"/>
    </w:object>
  </w:r>
</w:p>
"""


def _build_with_ole(builder, payload: bytes, prog_id: str = "") -> Path:
    """Собирает docx с одним OLE-объектом и указанным содержимым бинарника."""
    builder.set_bytes("word/embeddings/oleObject1.bin", payload)
    builder.add_rel("rId1", "embeddings/oleObject1.bin")
    builder.set_body(OLE_MARKUP.format(prog_id=prog_id))
    return builder.build("ole_sample.docx")


def test_empty_progid_equation_native_detected_as_equation(docx_builder, tmp_path: Path) -> None:
    """OLE-контейнер с потоком Equation Native распознается как MathType-формула."""
    docx = _build_with_ole(
        docx_builder,
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"Equation Native" + b"\x00" * 64,
    )
    doc = parse_docx_to_json_file(docx, tmp_path / "out.json")
    assert len(doc.cross_ref_index.equations) == 1
    assert doc.cross_ref_index.equations[0].representation_kind == "ole_mathtype_eq3"
    assert doc.anomalies.embedded_ole_block_ids == []


def test_embedded_workbook_reported_as_ole_anomaly(docx_builder, tmp_path: Path) -> None:
    """Внедренная Excel-книга не формула: абзац попадает в embedded_ole_block_ids."""
    docx = _build_with_ole(
        docx_builder,
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"Workbook" + b"\x00" * 64,
    )
    doc = parse_docx_to_json_file(docx, tmp_path / "out.json")
    assert doc.cross_ref_index.equations == []
    assert len(doc.anomalies.embedded_ole_block_ids) == 1


def test_explicit_equation_progid_still_detected(docx_builder, tmp_path: Path) -> None:
    """ProgID Equation.DSMT4 работает без анализа бинарника."""
    docx = _build_with_ole(docx_builder, b"\x00" * 64, prog_id="Equation.DSMT4")
    doc = parse_docx_to_json_file(docx, tmp_path / "out.json")
    assert len(doc.cross_ref_index.equations) == 1
