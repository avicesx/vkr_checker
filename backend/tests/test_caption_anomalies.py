"""Аномалии подписей таблиц: нумерация и отсутствие ссылок в тексте.

Проверки рисунков (``test_figure_captions.py``) обобщены на таблицы: те же
issue_kind ``duplicate``/``gap``/``out_of_order`` попадают в общее поле
``figure_numbering_issues`` с ``entity_type="table"``, а таблицы без упоминания
в тексте — в ``unreferenced_table_ids``. Существующее поведение рисунков при
этом сохраняется, расширения схемы аддитивны.
"""
from __future__ import annotations

from conftest import DocxPackageBuilder, make_fake_png_bytes
from parser.core.schema import DocumentAnomalies, FigureNumberingIssue, ParsedDocument
from parser.docx.pipeline import DocxParserPipeline


IMAGE_PARAGRAPH = """
<w:p>
  <w:r>
    <w:drawing>
      <wp:inline>
        <wp:extent cx="914400" cy="914400"/>
        <wp:docPr id="{doc_pr}" name="Picture {doc_pr}"/>
        <a:graphic>
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
            <pic:pic>
              <pic:blipFill><a:blip r:embed="{r_id}"/></pic:blipFill>
            </pic:pic>
          </a:graphicData>
        </a:graphic>
      </wp:inline>
    </w:drawing>
  </w:r>
</w:p>
"""

TEXT_PARAGRAPH = "<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"

TABLE_XML = """
<w:tbl>
  <w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>
  <w:tblGrid><w:gridCol w:w="4252"/></w:tblGrid>
  <w:tr>
    <w:tc><w:tcPr><w:tcW w:w="4252" w:type="dxa"/></w:tcPr>
      <w:p><w:r><w:t>{text}</w:t></w:r></w:p>
    </w:tc>
  </w:tr>
</w:tbl>
"""


def _paragraph(text: str) -> str:
    """Возвращает XML обычного абзаца с указанным текстом."""
    return TEXT_PARAGRAPH.format(text=text)


def _table(text: str) -> str:
    """Возвращает XML минимальной таблицы с одной ячейкой."""
    return TABLE_XML.format(text=text)


def _image(builder: DocxPackageBuilder, r_id: str, filename: str, doc_pr: int) -> str:
    """Регистрирует PNG и возвращает XML абзаца с inline-картинкой."""
    builder.add_rel(r_id, f"media/{filename}")
    builder.set_bytes(f"media/{filename}", make_fake_png_bytes(40, 30))
    return IMAGE_PARAGRAPH.format(doc_pr=doc_pr, r_id=r_id)


def _issue_keys(document: ParsedDocument) -> list[tuple[str, str, str]]:
    """Пары «вид нарушения, номер, тип объекта» в порядке обнаружения."""
    return [
        (issue.issue_kind, issue.number_str, issue.entity_type)
        for issue in document.anomalies.figure_numbering_issues
    ]


def test_table_numbering_duplicate_gap_and_order_are_reported(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Дубль, пропуск и нарушение порядка нумерации таблиц дают те же issue_kind."""
    body = "".join(
        [
            _paragraph("Результаты испытаний сведены в таблицу 1."),
            _paragraph("Таблица 1 — Результаты испытаний"),
            _table("Первая таблица"),
            _paragraph("Таблица 3 — Сводные показатели"),
            _table("Вторая таблица"),
            _paragraph("Таблица 3 — Повтор номера без новой таблицы"),
            _table("Третья таблица"),
            _paragraph("Таблица 2 — Нарушение порядка нумерации"),
            _table("Четвертая таблица"),
        ]
    )
    docx_path = docx_builder.set_body(body).build("table_numbering.docx")
    doc = DocxParserPipeline(docx_path).parse()

    # Расширение схемы аддитивно: версия контракта не меняется.
    assert doc.schema_version == "3.0.0"

    tables = doc.cross_ref_index.tables
    assert [t.number_str for t in tables] == ["1", "3", "3", "2"]
    assert all(t.entity_type == "table" for t in tables)
    # Подпись «Таблица 1» упомянута в тексте, остальные — нет.
    assert [t.is_mentioned_in_text for t in tables] == [True, False, False, False]

    issues = doc.anomalies.figure_numbering_issues
    assert _issue_keys(doc) == [
        ("gap", "2", "table"),
        ("duplicate", "3", "table"),
        ("out_of_order", "2", "table"),
    ]
    # Нарушение привязано к подписи того объекта, на котором оно обнаружено.
    assert [issue.caption_block_id for issue in issues] == [
        tables[1].caption_block_id,
        tables[2].caption_block_id,
        tables[3].caption_block_id,
    ]
    assert all(issue.entity_type == "table" for issue in issues)

    assert doc.anomalies.unreferenced_table_ids == [
        tables[1].entity_id,
        tables[2].entity_id,
        tables[3].entity_id,
    ]
    # Расширение не затрагивает рисунки: их в документе нет.
    assert doc.anomalies.unreferenced_figure_ids == []
    assert doc.anomalies.uncaptioned_visual_ids == []


def test_referenced_tables_are_not_reported_as_unreferenced(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Таблицы, упомянутые в тексте, не попадают в unreferenced_table_ids."""
    body = "".join(
        [
            _paragraph("Показатели сведены в таблицу 1 и таблицу 2."),
            _paragraph("Таблица 1 — Первый набор показателей"),
            _table("Первая таблица"),
            _paragraph("Таблица 2 — Второй набор показателей"),
            _table("Вторая таблица"),
        ]
    )
    docx_path = docx_builder.set_body(body).build("tables_referenced.docx")
    doc = DocxParserPipeline(docx_path).parse()

    tables = doc.cross_ref_index.tables
    assert [t.number_str for t in tables] == ["1", "2"]
    assert all(t.is_mentioned_in_text for t in tables)
    assert doc.anomalies.unreferenced_table_ids == []
    assert doc.anomalies.figure_numbering_issues == []


def test_figures_and_tables_are_checked_as_separate_sequences(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Пропуск в нумерации рисунков не смешивается с нумерацией таблиц."""
    body = "".join(
        [
            _image(docx_builder, "rId1", "stand1.png", 1),
            _paragraph("Рисунок 1 — Схема стенда"),
            _image(docx_builder, "rId2", "stand3.png", 2),
            _paragraph("Рисунок 3 — Схема сети"),
            _paragraph("Таблица 1 — Параметры стенда"),
            _table("Параметры"),
            _paragraph("На рисунок 1 и на таблицу 1 ссылаются в тексте."),
        ]
    )
    docx_path = docx_builder.set_body(body).build("mixed_captions.docx")
    doc = DocxParserPipeline(docx_path).parse()

    figures = doc.cross_ref_index.figures
    tables = doc.cross_ref_index.tables
    assert [f.number_str for f in figures] == ["1", "3"]
    assert [t.number_str for t in tables] == ["1"]

    # У рисунков пропущен номер 2, у таблиц последовательность корректна.
    assert _issue_keys(doc) == [("gap", "2", "figure")]
    assert doc.anomalies.figure_numbering_issues[0].entity_type == "figure"

    assert doc.anomalies.unreferenced_figure_ids == [figures[1].entity_id]
    assert doc.anomalies.unreferenced_table_ids == []
    assert doc.anomalies.figure_captions_without_visual_ids == []


def test_section_based_table_numbering_is_not_flagged(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Нумерация «Таблица 2.1» по разделам не проверяется на сквозные пропуски."""
    body = "".join(
        [
            _paragraph("Таблица 2.1 — Сводка показателей"),
            _table("Первая таблица"),
            _paragraph("Таблица 2.2 — Продолжение сводки"),
            _table("Вторая таблица"),
        ]
    )
    docx_path = docx_builder.set_body(body).build("table_section_numbering.docx")
    doc = DocxParserPipeline(docx_path).parse()

    tables = doc.cross_ref_index.tables
    assert [t.numbering_scheme for t in tables] == ["section_based", "section_based"]
    assert doc.anomalies.figure_numbering_issues == []


def test_abbreviated_table_prefix_joins_the_same_number_family(
    docx_builder: DocxPackageBuilder,
) -> None:
    """«Табл. 1» и «Таблица 1» — одна последовательность, повтор номера виден."""
    body = "".join(
        [
            _paragraph("Таблица 1 — Первый набор"),
            _table("Первая таблица"),
            _paragraph("Табл. 1 — Повтор номера в сокращенной записи"),
            _table("Вторая таблица"),
        ]
    )
    docx_path = docx_builder.set_body(body).build("table_abbrev.docx")
    doc = DocxParserPipeline(docx_path).parse()

    tables = doc.cross_ref_index.tables
    assert [t.prefix_word.lower() for t in tables] == ["таблица", "табл."]
    assert _issue_keys(doc) == [("duplicate", "1", "table")]


def test_schema_extensions_are_additive_and_keep_figure_defaults() -> None:
    """Новые поля схемы имеют безопасные значения по умолчанию."""
    issue = FigureNumberingIssue(issue_kind="gap", number_str="2")
    assert issue.entity_type == "figure"

    anomalies = DocumentAnomalies()
    assert anomalies.unreferenced_table_ids == []
    payload = anomalies.model_dump(mode="json")
    assert payload["unreferenced_table_ids"] == []
    assert payload["figure_numbering_issues"] == []

    restored = DocumentAnomalies.model_validate(
        {
            "figure_numbering_issues": [
                {"issue_kind": "duplicate", "number_str": "3", "caption_block_id": "p-1"}
            ]
        }
    )
    assert restored.figure_numbering_issues[0].entity_type == "figure"
    assert restored.unreferenced_table_ids == []
