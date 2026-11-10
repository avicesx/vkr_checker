"""Обратная совместимость наборов правил и детерминизм отчета.

Тесты фиксируют контракт поля `target.exclude_contexts` на уровне загрузки:

* набор, написанный до появления поля, загружается и получает значение по
  умолчанию — миграция JSON не нужна;
* явный список и пустой список принимаются как есть;
* некорректное значение (строка или число вместо списка строк) отвергается
  загрузкой с понятной ошибкой, а не молча игнорируется.

Отдельный смоук прогоняет движок на документе «титул + листинг + основной
текст» несколько раз и требует побайтового совпадения отчетов: контекстный
фильтр не должен вносить недетерминизм.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pytest
from conftest import DocxPackageBuilder
from parser.core.schema import ParsedDocxDocument
from parser.docx.pipeline import DocxParserPipeline
from pydantic import ValidationError
from rule_engine import (
    DEFAULT_RULESET_PATH,
    RuleEngine,
    RuleSet,
    load_default_ruleset,
    load_ruleset,
)
from rule_engine.context import classify_document_contexts

#: Титульная строка: по центру, 11 pt, реквизит вуза.
TITLE_PAGE_PARAGRAPH = """
<w:p>
  <w:pPr><w:jc w:val="center"/></w:pPr>
  <w:r>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr>
    <w:t>Министерство науки и высшего образования Российской Федерации</w:t>
  </w:r>
</w:p>
"""

#: Строка листинга: моноширинный Courier New 12 pt.
CODE_LISTING_PARAGRAPH = """
<w:p>
  <w:r>
    <w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/><w:sz w:val="24"/></w:rPr>
    <w:t>public class Calculator</w:t>
  </w:r>
</w:p>
"""

#: Обычный абзац основного текста в соответствии с требованиями.
PLAIN_PARAGRAPH = """
<w:p><w:r><w:t>Основной текст документа для проверки шрифта</w:t></w:r></w:p>
"""

#: Заголовок первого уровня: структурные правила должны его видеть.
HEADING_PARAGRAPH = """
<w:p>
  <w:pPr><w:pStyle w:val="Heading1"/><w:jc w:val="center"/></w:pPr>
  <w:r><w:t>1 ВВЕДЕНИЕ</w:t></w:r>
</w:p>
"""


def _parse(builder: DocxPackageBuilder, body: str) -> ParsedDocxDocument:
    """Собирает DOCX из тела и возвращает результат разбора."""
    builder.set_body(body)
    return DocxParserPipeline(builder.build()).parse()


def _font_ruleset(target: Dict[str, object]) -> Dict[str, object]:
    """Собирает набор с единственным правилом шрифта и заданной областью."""
    return {
        "ruleset_id": "context-compat-test",
        "ruleset_version": "1.0.0",
        "rules": [
            {
                "rule_id": "font_body_text",
                "kind": "font",
                "message": "Основной текст набирается Times New Roman, 14 pt",
                "target": target,
                "expected": {"family": "Times New Roman", "size_pt": 14.0},
            }
        ],
    }


def _write_ruleset(tmp_path: Path, payload: Dict[str, object]) -> Path:
    """Сохраняет набор правил в файл и возвращает путь к нему."""
    ruleset_path = tmp_path / "rules.json"
    ruleset_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return ruleset_path


def test_legacy_ruleset_without_field_gets_defaults() -> None:
    """Штатный JSON без нового поля загружается и получает фильтр по умолчанию."""
    raw_payload = json.loads(DEFAULT_RULESET_PATH.read_text(encoding="utf-8"))
    assert all(
        "exclude_contexts" not in rule.get("target", {}) for rule in raw_payload["rules"]
    )

    ruleset = load_ruleset(DEFAULT_RULESET_PATH)

    assert ruleset.get_rule("font_body_text").target.exclude_contexts == [
        "title_page",
        "code_listing",
    ]
    assert ruleset.get_rule("indent_first_line").target.exclude_contexts == [
        "title_page",
        "code_listing",
    ]


def test_explicit_exclude_contexts_is_loaded(tmp_path: Path) -> None:
    """Явный список контекстов сохраняется без изменений."""
    ruleset_path = _write_ruleset(
        tmp_path,
        _font_ruleset({"scope": "body_text", "exclude_contexts": ["code_listing"]}),
    )

    target = load_ruleset(ruleset_path).get_rule("font_body_text").target

    assert target.exclude_contexts == ["code_listing"]


def test_empty_exclude_contexts_is_accepted(tmp_path: Path) -> None:
    """Пустой список допустим: он выключает фильтрацию, а не поле."""
    ruleset_path = _write_ruleset(
        tmp_path,
        _font_ruleset({"scope": "body_text", "exclude_contexts": []}),
    )

    target = load_ruleset(ruleset_path).get_rule("font_body_text").target

    assert target.exclude_contexts == []


def test_string_instead_of_list_is_rejected_with_field_path(tmp_path: Path) -> None:
    """Строка вместо списка падает на загрузке с указанием поля и типа."""
    ruleset_path = _write_ruleset(
        tmp_path,
        _font_ruleset({"scope": "body_text", "exclude_contexts": "title_page"}),
    )

    with pytest.raises(ValidationError) as error:
        load_ruleset(ruleset_path)

    message = str(error.value)
    assert "rules.0.font.target.exclude_contexts" in message
    assert "valid list" in message


def test_non_string_item_is_rejected(tmp_path: Path) -> None:
    """Число в списке контекстов отвергается: элементы — имена контекстов."""
    ruleset_path = _write_ruleset(
        tmp_path,
        _font_ruleset({"scope": "body_text", "exclude_contexts": [1]}),
    )

    with pytest.raises(ValidationError) as error:
        load_ruleset(ruleset_path)

    message = str(error.value)
    assert "rules.0.font.target.exclude_contexts.0" in message
    assert "valid string" in message


def test_override_checks_listings_but_keeps_title_excluded(
    docx_builder: DocxPackageBuilder,
    tmp_path: Path,
) -> None:
    """Переопределение из документации: листинги проверяются, титул — нет."""
    document = _parse(
        docx_builder,
        TITLE_PAGE_PARAGRAPH + CODE_LISTING_PARAGRAPH + PLAIN_PARAGRAPH,
    )
    ruleset_path = _write_ruleset(
        tmp_path,
        _font_ruleset({"scope": "body_text", "exclude_contexts": ["title_page"]}),
    )
    listing_id = next(
        block.block_id
        for block in document.blocks
        if getattr(block, "text_visible", "").strip().startswith("public class")
    )

    report = RuleEngine(load_ruleset(ruleset_path)).check(document)

    assert [violation.location.block_id for violation in report.violations] == [listing_id]
    assert report.violations[0].found == {"family": "Courier New", "size_pt": 12.0}


def test_no_filter_ruleset_reports_title_and_listing(
    docx_builder: DocxPackageBuilder,
    tmp_path: Path,
) -> None:
    """exclude_contexts=[] в файле возвращает проверку всех абзацев области."""
    document = _parse(
        docx_builder,
        TITLE_PAGE_PARAGRAPH + CODE_LISTING_PARAGRAPH + PLAIN_PARAGRAPH,
    )
    ruleset_path = _write_ruleset(
        tmp_path,
        _font_ruleset({"scope": "body_text", "exclude_contexts": []}),
    )

    report = RuleEngine(load_ruleset(ruleset_path)).check(document)

    assert report.summary.by_rule == {"font_body_text": 2}
    assert report.summary.objects_checked == 3


def test_report_is_stable_between_runs(docx_builder: DocxPackageBuilder) -> None:
    """Два прогона фикстуры «титул + листинг + текст + заголовок» дают один JSON.

    Проверяется весь отчет целиком, включая локации, сработавшие контексты и
    счетчики: контекст-фильтр не должен зависеть от порядка правил, словарей или
    времени запуска.
    """
    document = _parse(
        docx_builder,
        TITLE_PAGE_PARAGRAPH
        + CODE_LISTING_PARAGRAPH
        + PLAIN_PARAGRAPH
        + HEADING_PARAGRAPH,
    )
    engine = RuleEngine(load_default_ruleset())

    first = engine.check(document)
    second = engine.check(document)
    third = engine.check(document)

    first_json = first.model_dump_json(indent=2)
    assert first_json == second.model_dump_json(indent=2)
    assert first_json == third.model_dump_json(indent=2)
    assert first.summary.violations_total == second.summary.violations_total
    assert first.summary.objects_checked == second.summary.objects_checked
    assert first.violations == second.violations
    assert first.violations, "фикстура должна давать нарушения, иначе смоук пустой"
    assert "font_body_text" not in first.summary.by_rule
    assert "indent_first_line" not in first.summary.by_rule


def test_contexts_are_stable_between_runs(docx_builder: DocxPackageBuilder) -> None:
    """Набор контекстов фикстуры воспроизводится без изменений."""
    document = _parse(
        docx_builder,
        TITLE_PAGE_PARAGRAPH + CODE_LISTING_PARAGRAPH + PLAIN_PARAGRAPH,
    )

    first = classify_document_contexts(document.blocks)
    second = classify_document_contexts(document.blocks)

    assert first == second
    assert sorted(first.values()) == ["body", "code_listing", "title_page"]


def test_default_ruleset_still_checks_body_paragraphs(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Фильтр не задевает основной текст: неверный шрифт остается в отчете."""
    document = _parse(
        docx_builder,
        TITLE_PAGE_PARAGRAPH
        + CODE_LISTING_PARAGRAPH
        + "<w:p><w:r><w:rPr><w:rFonts w:ascii=\"Arial\" w:hAnsi=\"Arial\"/></w:rPr>"
        + "<w:t>Фрагмент основного текста набран другой гарнитурой.</w:t></w:r></w:p>",
    )

    report = RuleEngine(load_ruleset(DEFAULT_RULESET_PATH)).check(document)

    assert report.summary.by_rule.get("font_body_text") == 1
    font_violation = next(
        violation
        for violation in report.violations
        if violation.rule_id == "font_body_text"
    )
    assert font_violation.found == {"family": "Arial"}
