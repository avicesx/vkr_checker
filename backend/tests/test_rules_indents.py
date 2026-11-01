"""Проверки правила отступов абзаца на разобранных DOCX-пакетах.

Тесты фиксируют работу с эффективными отступами: значение из стиля, прямое
переопределение и висячий отступ. Красная строка проверяется отдельно от
боковых отступов, а допуск сравнивается в миллиметрах.
"""
from __future__ import annotations

from typing import Dict

import pytest
from conftest import DocxPackageBuilder
from docx_parser.pipeline import DocxParserPipeline
from docx_parser.schema import ParsedDocxDocument
from rule_engine import RuleEngine, RuleSet

PLAIN_PARAGRAPH = """
<w:p><w:r><w:t>Основной текст документа для проверки отступов</w:t></w:r></w:p>
"""

ZERO_FIRST_LINE_PARAGRAPH = """
<w:p>
  <w:pPr><w:ind w:firstLine="0"/></w:pPr>
  <w:r><w:t>Абзац без красной строки</w:t></w:r>
</w:p>
"""

LEFT_INDENT_PARAGRAPH = """
<w:p>
  <w:pPr><w:ind w:left="567"/></w:pPr>
  <w:r><w:t>Абзац с отступом слева десять миллиметров</w:t></w:r>
</w:p>
"""

HANGING_PARAGRAPH = """
<w:p>
  <w:pPr><w:ind w:hanging="360"/></w:pPr>
  <w:r><w:t>Абзац с висячим отступом</w:t></w:r>
</w:p>
"""

NEAR_TOLERANCE_PARAGRAPH = """
<w:p>
  <w:pPr><w:ind w:firstLine="695"/></w:pPr>
  <w:r><w:t>Абзац с красной строкой около двенадцати миллиметров</w:t></w:r>
</w:p>
"""

FAR_TOLERANCE_PARAGRAPH = """
<w:p>
  <w:pPr><w:ind w:firstLine="650"/></w:pPr>
  <w:r><w:t>Абзац с красной строкой около одиннадцати миллиметров</w:t></w:r>
</w:p>
"""

HEADING_PARAGRAPH = """
<w:p>
  <w:pPr><w:pStyle w:val="Heading1"/><w:ind w:firstLine="0"/></w:pPr>
  <w:r><w:t>1 Начало работы</w:t></w:r>
</w:p>
"""


def _parse(builder: DocxPackageBuilder, body: str) -> ParsedDocxDocument:
    """Собирает DOCX из тела и возвращает результат разбора."""
    builder.set_body(body)
    return DocxParserPipeline(builder.build()).parse()


def _indent_engine(
    scope: str = "body_text",
    include_headings: bool = False,
    **expected: object,
) -> RuleEngine:
    """Собирает движок с единственным правилом отступов."""
    indent_expected: Dict[str, object] = {"first_line_mm": 12.5, "tolerance_mm": 0.5}
    indent_expected.update(expected)
    payload = {
        "ruleset_id": "indents-test",
        "ruleset_version": "1.0.0",
        "rules": [
            {
                "rule_id": "indent_first_line",
                "kind": "indent",
                "message": "Абзац начинается с отступа 12,5 мм",
                "target": {"scope": scope, "include_headings": include_headings},
                "expected": indent_expected,
            }
        ],
    }
    return RuleEngine(RuleSet.model_validate(payload))


def test_style_first_line_indent_passes(docx_builder: DocxPackageBuilder) -> None:
    """Стиль Normal задает красную строку 709 twips, это в пределах допуска."""
    document = _parse(docx_builder, PLAIN_PARAGRAPH)

    report = _indent_engine().check(document)

    assert report.passed is True
    assert report.violations == []


def test_zero_first_line_indent_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Прямое обнуление красной строки ловится правилом."""
    document = _parse(docx_builder, PLAIN_PARAGRAPH + ZERO_FIRST_LINE_PARAGRAPH)

    report = _indent_engine().check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.rule_id == "indent_first_line"
    assert violation.kind == "indent"
    assert violation.expected == {"first_line_mm": 12.5}
    assert violation.found["first_line_mm"] == pytest.approx(0.0)
    assert violation.expected_text == "отступ первой строки 12,5 мм"
    assert violation.found_text == "отступ первой строки 0 мм"
    assert violation.location.block_id == document.blocks[-1].block_id
    assert violation.location.snippet.startswith("Абзац без красной строки")


def test_style_indent_value_is_reported_as_found(docx_builder: DocxPackageBuilder) -> None:
    """При нарушении в found попадает фактическое значение из стиля."""
    document = _parse(docx_builder, PLAIN_PARAGRAPH)

    report = _indent_engine(first_line_mm=10.0).check(document)

    assert len(report.violations) == 1
    found_value = report.violations[0].found["first_line_mm"]
    assert found_value == pytest.approx(12.507, abs=0.01)
    assert report.violations[0].found["first_line_source"] == "paragraph_style"


def test_left_indent_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Правило может требовать нулевой отступ слева."""
    document = _parse(docx_builder, LEFT_INDENT_PARAGRAPH)

    report = _indent_engine(first_line_mm=None, left_mm=0.0).check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.expected == {"left_mm": 0.0}
    assert violation.found["left_mm"] == pytest.approx(10.0, abs=0.01)
    assert violation.expected_text == "отступ слева 0 мм"
    assert violation.found_text == "отступ слева 10 мм"


def test_hanging_indent_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Висячий отступ приходит отрицательной красной строкой."""
    document = _parse(docx_builder, HANGING_PARAGRAPH)

    report = _indent_engine().check(document)

    assert len(report.violations) == 1
    assert report.violations[0].found["first_line_mm"] == pytest.approx(-6.35, abs=0.01)


def test_multiple_mismatched_fields_form_one_violation(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Расхождения по нескольким отступам собираются в одно нарушение."""
    document = _parse(docx_builder, LEFT_INDENT_PARAGRAPH)

    report = _indent_engine(first_line_mm=12.5, left_mm=0.0).check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert set(violation.expected) == {"left_mm"}
    assert "отступ слева 10 мм" in violation.found_text


def test_indent_within_tolerance_passes(docx_builder: DocxPackageBuilder) -> None:
    """Отклонение 0,24 мм укладывается в допуск 0,5 мм."""
    document = _parse(docx_builder, NEAR_TOLERANCE_PARAGRAPH)

    report = _indent_engine().check(document)

    assert report.violations == []


def test_indent_outside_tolerance_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Отклонение больше 0,5 мм попадает в отчет."""
    document = _parse(docx_builder, FAR_TOLERANCE_PARAGRAPH)

    report = _indent_engine().check(document)

    assert len(report.violations) == 1
    assert report.violations[0].found["first_line_mm"] == pytest.approx(11.465, abs=0.01)


def test_heading_is_skipped_by_default(docx_builder: DocxPackageBuilder) -> None:
    """Заголовок без красной строки не проверяется правилом основного текста."""
    document = _parse(docx_builder, HEADING_PARAGRAPH + PLAIN_PARAGRAPH)

    report = _indent_engine().check(document)

    assert report.violations == []


def test_heading_is_checked_with_include_headings(docx_builder: DocxPackageBuilder) -> None:
    """Флаг include_headings проверяет и заголовки."""
    document = _parse(docx_builder, HEADING_PARAGRAPH + PLAIN_PARAGRAPH)

    report = _indent_engine(include_headings=True).check(document)

    assert len(report.violations) == 1
    assert report.violations[0].location.style_id == "Heading1"


def test_scope_body_paragraphs_checks_all_top_level_paragraphs(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Область body_paragraphs берет абзацы верхнего уровня."""
    document = _parse(docx_builder, PLAIN_PARAGRAPH + ZERO_FIRST_LINE_PARAGRAPH)

    report = _indent_engine(scope="body_paragraphs").check(document)

    assert len(report.violations) == 1
    assert report.summary.objects_checked == 2


def test_body_paragraphs_checks_headings_without_flags(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Область body_paragraphs берет заголовки как есть, без флагов include_*."""
    document = _parse(docx_builder, HEADING_PARAGRAPH + PLAIN_PARAGRAPH)

    report = _indent_engine(scope="body_paragraphs").check(document)

    assert len(report.violations) == 1
    assert report.violations[0].location.style_id == "Heading1"
