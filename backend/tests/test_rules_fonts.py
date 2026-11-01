"""Проверки правила шрифта на разобранных DOCX-пакетах.

Тесты фиксируют область действия правила «основной текст»: заголовки, ячейки
таблиц и скрытый текст не должны давать ложных нарушений, а расхождения в
ранах одного абзаца агрегируются в одно нарушение с числом вхождений.
"""
from __future__ import annotations

import json
from typing import Dict

import pytest
from conftest import DocxPackageBuilder
from docx_parser.pipeline import DocxParserPipeline
from docx_parser.schema import ParsedDocxDocument
from rule_engine import RuleEngine, RuleSet

PLAIN_PARAGRAPH = """
<w:p><w:r><w:t>Основной текст документа для проверки шрифта</w:t></w:r></w:p>
"""

CALIBRI_PARAGRAPH = """
<w:p>
  <w:r>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr>
    <w:t>Фрагмент набран другим шрифтом</w:t>
  </w:r>
</w:p>
"""

HEADING_PARAGRAPH = """
<w:p>
  <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
  <w:r>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr>
    <w:t>1 Начало работы</w:t>
  </w:r>
</w:p>
"""

TABLE_WITH_CALIBRI = """
<w:tbl>
  <w:tblPr>
    <w:tblStyle w:val="TableGrid"/>
    <w:tblW w:w="0" w:type="auto"/>
  </w:tblPr>
  <w:tblGrid><w:gridCol w:w="4252"/></w:tblGrid>
  <w:tr>
    <w:tc>
      <w:tcPr><w:tcW w:w="4252" w:type="dxa"/></w:tcPr>
      <w:p>
        <w:r>
          <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr>
          <w:t>Ячейка другим шрифтом</w:t>
        </w:r>
      </w:p>
    </w:tc>
  </w:tr>
</w:tbl>
"""

HALF_POINT_PARAGRAPH = """
<w:p>
  <w:r>
    <w:rPr><w:sz w:val="27"/></w:rPr>
    <w:t>Текст с кеглем 13,5 pt</w:t>
  </w:r>
</w:p>
"""

SMALL_SIZE_PARAGRAPH = """
<w:p>
  <w:r>
    <w:rPr><w:sz w:val="26"/></w:rPr>
    <w:t>Текст с кеглем 13 pt</w:t>
  </w:r>
</w:p>
"""

HIDDEN_RUN_PARAGRAPH = """
<w:p>
  <w:r><w:t>Видимый текст абзаца для проверки</w:t></w:r>
  <w:r>
    <w:rPr><w:vanish/><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr>
    <w:t>Скрытая служебная пометка</w:t>
  </w:r>
</w:p>
"""


def _parse(builder: DocxPackageBuilder, body: str) -> ParsedDocxDocument:
    """Собирает DOCX из тела и возвращает результат разбора."""
    builder.set_body(body)
    return DocxParserPipeline(builder.build()).parse()


def _font_engine(
    scope: str = "body_text",
    include_headings: bool = False,
    **expected: object,
) -> RuleEngine:
    """Собирает движок с единственным правилом шрифта."""
    font_expected: Dict[str, object] = {"family": "Times New Roman", "size_pt": 14.0}
    font_expected.update(expected)
    payload = {
        "ruleset_id": "fonts-test",
        "ruleset_version": "1.0.0",
        "rules": [
            {
                "rule_id": "font_body_text",
                "kind": "font",
                "message": "Основной текст набирается Times New Roman, 14 pt",
                "target": {"scope": scope, "include_headings": include_headings},
                "expected": font_expected,
            }
        ],
    }
    return RuleEngine(RuleSet.model_validate(payload))



def test_default_document_passes_font_rule(docx_builder: DocxPackageBuilder) -> None:
    """Абзац стиля Normal уже соответствует Times New Roman 14 pt."""
    document = _parse(docx_builder, PLAIN_PARAGRAPH)

    report = _font_engine().check(document)

    assert report.passed is True
    assert report.violations == []
    assert report.summary.objects_checked == 1


def test_calibri_run_is_reported_with_location_and_text(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Прямое форматирование Calibri 11 pt дает нарушение с адресом и фактом."""
    document = _parse(docx_builder, PLAIN_PARAGRAPH + CALIBRI_PARAGRAPH)

    report = _font_engine().check(document)

    assert report.passed is False
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.rule_id == "font_body_text"
    assert violation.kind == "font"
    assert violation.severity == "error"
    assert violation.expected["family"] == "Times New Roman"
    assert violation.found["family"] == "Calibri"
    assert violation.expected["size_pt"] == pytest.approx(14.0)
    assert violation.found["size_pt"] == pytest.approx(11.0)
    assert violation.expected_text == "Times New Roman, 14 pt"
    assert violation.found_text == "Calibri, 11 pt"
    assert violation.location.block_id == document.blocks[-1].block_id
    assert violation.location.container_type == "body"
    assert violation.location.style_id == "Normal"
    assert violation.location.snippet.startswith("Фрагмент набран")
    assert violation.occurrences == 1


def test_mismatched_runs_are_aggregated_per_paragraph(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Несколько плохих ранов одного абзаца дают одно нарушение с числом вхождений."""
    body = """
    <w:p>
      <w:r>
        <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/></w:rPr>
        <w:t>Первый фрагмент</w:t>
      </w:r>
      <w:r>
        <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:i/></w:rPr>
        <w:t> и второй фрагмент</w:t>
      </w:r>
    </w:p>
    """
    document = _parse(docx_builder, body)

    report = _font_engine().check(document)

    assert len(report.violations) == 1
    assert report.violations[0].occurrences == 2
    assert report.violations[0].found == {"family": "Calibri"}


def test_heading_is_skipped_by_default(docx_builder: DocxPackageBuilder) -> None:
    """Заголовок с другим шрифтом не попадает в правило основного текста."""
    document = _parse(docx_builder, HEADING_PARAGRAPH + PLAIN_PARAGRAPH)

    report = _font_engine().check(document)

    assert report.violations == []


def test_heading_is_checked_with_include_headings(docx_builder: DocxPackageBuilder) -> None:
    """Флаг include_headings возвращает заголовки в выборку правила."""
    document = _parse(docx_builder, HEADING_PARAGRAPH + PLAIN_PARAGRAPH)

    report = _font_engine(include_headings=True).check(document)

    assert len(report.violations) == 1
    assert report.violations[0].location.style_id == "Heading1"



def test_table_cell_is_skipped_in_body_text_scope(docx_builder: DocxPackageBuilder) -> None:
    """Текст таблицы не относится к основному тексту."""
    document = _parse(docx_builder, TABLE_WITH_CALIBRI + PLAIN_PARAGRAPH)

    report = _font_engine().check(document)

    assert report.violations == []


def test_table_cell_is_checked_in_table_cells_scope(docx_builder: DocxPackageBuilder) -> None:
    """Область table_cells проверяет абзацы ячеек."""
    document = _parse(docx_builder, TABLE_WITH_CALIBRI + PLAIN_PARAGRAPH)

    report = _font_engine(scope="table_cells").check(document)

    assert len(report.violations) == 1
    assert report.violations[0].location.container_type == "table_cell"


def test_hidden_run_is_skipped(docx_builder: DocxPackageBuilder) -> None:
    """Скрытый текст не виден в печати и не проверяется."""
    document = _parse(docx_builder, HIDDEN_RUN_PARAGRAPH)

    report = _font_engine().check(document)

    assert report.violations == []


def test_half_point_size_within_tolerance(docx_builder: DocxPackageBuilder) -> None:
    """Кегль 13,5 pt при допуске 0,5 pt считается соответствующим 14 pt."""
    document = _parse(docx_builder, HALF_POINT_PARAGRAPH)

    report = _font_engine(size_pt=14.0, size_tolerance_pt=0.5).check(document)

    assert report.violations == []


def test_size_below_tolerance_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Кегль 13 pt выходит за допуск 0,5 pt и попадает в отчет."""
    document = _parse(docx_builder, SMALL_SIZE_PARAGRAPH)

    report = _font_engine(size_pt=14.0, size_tolerance_pt=0.5).check(document)

    assert len(report.violations) == 1
    assert report.violations[0].found["size_pt"] == pytest.approx(13.0)
    assert report.violations[0].found_text == "13 pt"


def test_report_references_ruleset_and_parser_schema(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Отчет связывает набор правил, версию схемы парсера и проверенный файл."""
    document = _parse(docx_builder, PLAIN_PARAGRAPH)

    report = _font_engine().check(document)

    assert report.ruleset_id == "fonts-test"
    assert report.ruleset_version == "1.0.0"
    assert report.parser_schema_version == document.schema_version
    assert report.document.filename == document.meta.filename
    assert report.document.file_sha256 == document.meta.file_sha256
    assert report.document.pagination_source == document.meta.pagination_source


def test_check_json_file_matches_in_memory_check(
    docx_builder: DocxPackageBuilder,
    tmp_path,
) -> None:
    """Проверка сохраненного JSON парсера дает тот же результат."""
    document = _parse(docx_builder, CALIBRI_PARAGRAPH)
    json_path = tmp_path / "parsed.json"
    json_path.write_text(
        json.dumps(document.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )

    report = _font_engine().check_json_file(json_path)

    assert len(report.violations) == 1
    assert report.violations[0].found["family"] == "Calibri"
