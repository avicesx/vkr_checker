"""Интеграционные проверки движка: набор правил целиком на одном документе.

Тесты фиксируют поведение RuleEngine на уровне сценария: соответствующий
документ проходит все три правила, документ с нарушениями дает по нарушению на
каждое правило, а отчет воспроизводим между прогонами.
"""
from __future__ import annotations

import json

from conftest import DocxPackageBuilder
from docx_parser.pipeline import DocxParserPipeline
from docx_parser.schema import ParsedDocxDocument
from rule_engine import (
    DEFAULT_RULESET_PATH,
    RuleEngine,
    RuleSet,
    load_default_ruleset,
)

COMPLIANT_BODY = """
<w:p><w:r><w:t>Основной текст документа, оформленный по требованиям</w:t></w:r></w:p>
"""

BAD_FONT = """
<w:p>
  <w:r>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr>
    <w:t>Фрагмент набран другим шрифтом</w:t>
  </w:r>
</w:p>
"""

BAD_INDENT = """
<w:p>
  <w:pPr><w:ind w:firstLine="0"/></w:pPr>
  <w:r><w:t>Абзац без красной строки</w:t></w:r>
</w:p>
"""

BAD_MARGINS = """
<w:sectPr>
  <w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/>
</w:sectPr>
"""


def _parse(builder: DocxPackageBuilder, body: str, filename: str = "test_vkr.docx") -> ParsedDocxDocument:
    """Собирает DOCX из тела и возвращает результат разбора."""
    builder.set_body(body)
    return DocxParserPipeline(builder.build(filename)).parse()


def test_compliant_document_passes_all_default_rules(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Штатный набор не находит нарушений в корректно оформленном документе."""
    document = _parse(docx_builder, COMPLIANT_BODY)

    report = RuleEngine(load_default_ruleset()).check(document)

    assert report.passed is True
    assert report.summary.rules_applied == 3
    assert report.summary.violations_total == 0
    assert report.summary.by_kind == {}
    assert report.summary.objects_checked > 0


def test_document_with_three_problems_gets_three_violations(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Каждое правило дает свое нарушение с адресом в своей области."""
    body = COMPLIANT_BODY + BAD_FONT + BAD_INDENT + BAD_MARGINS
    document = _parse(docx_builder, body)

    report = RuleEngine(load_default_ruleset()).check(document)

    assert report.passed is False
    assert report.summary.violations_total == 3
    assert report.summary.by_kind == {"font": 1, "indent": 1, "margin": 1}
    assert report.summary.by_rule == {
        "font_body_text": 1,
        "indent_first_line": 1,
        "page_margins": 1,
    }
    kinds = {violation.kind for violation in report.violations}
    assert kinds == {"font", "indent", "margin"}


def test_report_is_reproducible_between_runs(docx_builder: DocxPackageBuilder) -> None:
    """Два прогона одного документа дают одинаковый JSON отчета."""
    document = _parse(docx_builder, COMPLIANT_BODY + BAD_FONT)
    engine = RuleEngine(load_default_ruleset())

    first = engine.check(document).model_dump_json(indent=2)
    second = engine.check(document).model_dump_json(indent=2)

    assert first == second
    assert json.loads(first)["passed"] is False


def test_engine_from_json_file_reads_ruleset(docx_builder: DocxPackageBuilder) -> None:
    """Движок можно собрать прямо из файла набора правил."""
    document = _parse(docx_builder, BAD_FONT)

    engine = RuleEngine.from_json_file(DEFAULT_RULESET_PATH)
    report = engine.check(document)

    assert isinstance(engine.ruleset, RuleSet)
    assert report.summary.violations_total == 1


def test_warning_severity_does_not_fail_document(docx_builder: DocxPackageBuilder) -> None:
    """Нарушение уровня warning попадает в отчет, но не блокирует документ."""
    payload = {
        "ruleset_id": "warnings",
        "ruleset_version": "1.0.0",
        "rules": [
            {
                "rule_id": "font_warning",
                "kind": "font",
                "severity": "warning",
                "message": "Желателен Times New Roman",
                "target": {"scope": "body_text"},
                "expected": {"family": "Times New Roman"},
            }
        ],
    }
    document = _parse(docx_builder, BAD_FONT)

    report = RuleEngine(RuleSet.model_validate(payload)).check(document)

    assert report.summary.violations_total == 1
    assert report.summary.by_severity == {"warning": 1}
    assert report.passed is True
