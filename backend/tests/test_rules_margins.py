"""Проверки правила полей страницы на разобранных DOCX-пакетах.

Правило работает на уровне секций: сравниваются поля каждой секции, а в отчет
попадают только те края, которые выходят за допуск. Ошибка адресуется секцией
и диапазоном страниц, чтобы нормоконтролер нашел место правки.
"""
from __future__ import annotations

from typing import Dict

import pytest
from conftest import DocxPackageBuilder
from docx_parser.pipeline import DocxParserPipeline
from docx_parser.schema import ParsedDocxDocument
from rule_engine import RuleEngine, RuleSet

PLAIN_PARAGRAPH = """
<w:p><w:r><w:t>Основной текст документа для проверки полей</w:t></w:r></w:p>
"""

NARROW_MARGINS = """
<w:sectPr>
  <w:pgSz w:w="11906" w:h="16838"/>
  <w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"
           w:header="708" w:footer="708" w:gutter="0"/>
</w:sectPr>
"""

NEAR_TOLERANCE_MARGINS = """
<w:sectPr>
  <w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1645"/>
</w:sectPr>
"""

FAR_TOLERANCE_MARGINS = """
<w:sectPr>
  <w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1600"/>
</w:sectPr>
"""

GUTTER_MARGINS = """
<w:sectPr>
  <w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701" w:gutter="283"/>
</w:sectPr>
"""

TWO_SECTIONS = """
<w:p>
  <w:pPr>
    <w:sectPr>
      <w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/>
    </w:sectPr>
  </w:pPr>
  <w:r><w:t>Первый раздел с узкими полями</w:t></w:r>
</w:p>
<w:p><w:r><w:t>Второй раздел с полями по норме</w:t></w:r></w:p>
<w:sectPr>
  <w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701"/>
</w:sectPr>
"""


def _parse(builder: DocxPackageBuilder, body: str) -> ParsedDocxDocument:
    """Собирает DOCX из тела и возвращает результат разбора."""
    builder.set_body(body)
    return DocxParserPipeline(builder.build()).parse()


def _margin_engine(**expected: object) -> RuleEngine:
    """Собирает движок с единственным правилом полей страницы."""
    margin_expected: Dict[str, object] = {
        "left_mm": 30.0,
        "right_mm": 15.0,
        "top_mm": 20.0,
        "bottom_mm": 20.0,
        "tolerance_mm": 1.0,
    }
    margin_expected.update(expected)
    payload = {
        "ruleset_id": "margins-test",
        "ruleset_version": "1.0.0",
        "rules": [
            {
                "rule_id": "page_margins",
                "kind": "margin",
                "message": "Поля страницы: левое 30, правое 15, верхнее и нижнее 20 мм",
                "target": {"scope": "sections"},
                "expected": margin_expected,
            }
        ],
    }
    return RuleEngine(RuleSet.model_validate(payload))


def test_default_section_margins_pass(docx_builder: DocxPackageBuilder) -> None:
    """Секция без w:pgMar получает нормативные поля 30/15/20/20 мм."""
    document = _parse(docx_builder, PLAIN_PARAGRAPH)

    report = _margin_engine().check(document)

    assert report.passed is True
    assert report.violations == []
    assert report.summary.objects_checked == 1


def test_narrow_margins_are_reported(docx_builder: DocxPackageBuilder) -> None:
    """Секция с полями 20 мм по всем краям ловится правилом."""
    document = _parse(docx_builder, PLAIN_PARAGRAPH + NARROW_MARGINS)

    report = _margin_engine().check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.rule_id == "page_margins"
    assert violation.kind == "margin"
    assert violation.expected == {"left_mm": 30.0, "right_mm": 15.0}
    assert violation.found["left_mm"] == pytest.approx(20.0, abs=0.02)
    assert violation.found["right_mm"] == pytest.approx(20.0, abs=0.02)
    assert "top_mm" not in violation.found
    assert violation.expected_text == "левое поле 30 мм, правое поле 15 мм"
    assert violation.found_text == "левое поле 20 мм, правое поле 20 мм"
    assert violation.location.section_idx == 0
    assert violation.location.snippet.startswith("секция 1")


def test_margins_within_tolerance_pass(docx_builder: DocxPackageBuilder) -> None:
    """Левое поле 29,02 мм укладывается в допуск 1 мм."""
    document = _parse(docx_builder, PLAIN_PARAGRAPH + NEAR_TOLERANCE_MARGINS)

    report = _margin_engine().check(document)

    assert report.violations == []


def test_margins_outside_tolerance_are_reported(docx_builder: DocxPackageBuilder) -> None:
    """Левое поле 28,22 мм выходит за допуск 1 мм."""
    document = _parse(docx_builder, PLAIN_PARAGRAPH + FAR_TOLERANCE_MARGINS)

    report = _margin_engine().check(document)

    assert len(report.violations) == 1
    assert report.violations[0].found["left_mm"] == pytest.approx(28.22, abs=0.02)


def test_gutter_is_checked_when_expected(docx_builder: DocxPackageBuilder) -> None:
    """Корешковый отступ проверяется, если он задан в ожидании."""
    document = _parse(docx_builder, PLAIN_PARAGRAPH + GUTTER_MARGINS)

    report = _margin_engine(left_mm=None, right_mm=None, top_mm=None, bottom_mm=None,
                            gutter_mm=0.0).check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert set(violation.expected) == {"gutter_mm"}
    assert violation.found["gutter_mm"] == pytest.approx(5.0, abs=0.02)
    assert violation.found_text == "корешковый отступ 5 мм"


def test_only_mismatched_section_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """В отчет попадает только секция с нарушением."""
    document = _parse(docx_builder, TWO_SECTIONS)

    report = _margin_engine().check(document)

    assert len(document.sections) == 2
    assert report.summary.objects_checked == 2
    assert len(report.violations) == 1
    assert report.violations[0].location.section_idx == 0
    assert report.violations[0].violation_id == "page_margins:section:0"


def test_summary_counts_violations_by_rule_and_kind(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Сводка группирует нарушения по важности, виду и правилу."""
    document = _parse(docx_builder, PLAIN_PARAGRAPH + NARROW_MARGINS)

    report = _margin_engine().check(document)

    assert report.summary.violations_total == 1
    assert report.summary.by_severity == {"error": 1}
    assert report.summary.by_kind == {"margin": 1}
    assert report.summary.by_rule == {"page_margins": 1}

