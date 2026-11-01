"""Проверки загрузки и валидации JSON-наборов правил нормоконтроля.

Тесты фиксируют контракт схемы правил: неизвестные поля и виды правил
отвергаются при загрузке, поэтому движок не встречает противоречивых наборов.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rule_engine import (
    DEFAULT_RULESET_PATH,
    FontRule,
    IndentRule,
    MarginRule,
    RuleSet,
    load_default_ruleset,
    load_ruleset,
)


def _font_rule(**overrides: object) -> dict:
    rule = {
        "rule_id": "font_body_text",
        "kind": "font",
        "message": "Шрифт основного текста",
        "target": {"scope": "body_text"},
        "expected": {"family": "Times New Roman", "size_pt": 14.0},
    }
    rule.update(overrides)
    return rule


def _margin_rule(**overrides: object) -> dict:
    rule = {
        "rule_id": "page_margins",
        "kind": "margin",
        "message": "Поля страницы",
        "target": {"scope": "sections"},
        "expected": {"left_mm": 30.0, "right_mm": 15.0},
    }
    rule.update(overrides)
    return rule


def _ruleset(*rules: dict) -> dict:
    return {
        "ruleset_id": "test-ruleset",
        "ruleset_version": "1.0.0",
        "rules": list(rules),
    }


def test_default_ruleset_describes_three_checks() -> None:
    """Штатный набор содержит по одному правилу шрифта, отступа и полей."""
    ruleset = load_default_ruleset()

    assert ruleset.ruleset_id == "vkr-rule-engine-test"
    assert ruleset.ruleset_version == "1.0.0"
    assert [rule.rule_id for rule in ruleset.rules] == [
        "font_body_text",
        "indent_first_line",
        "page_margins",
    ]
    assert isinstance(ruleset.rules[0], FontRule)
    assert isinstance(ruleset.rules[1], IndentRule)
    assert isinstance(ruleset.rules[2], MarginRule)


def test_default_ruleset_matches_parser_defaults() -> None:
    """Ожидания набора совпадают с дефолтами парсера для пустого документа."""
    ruleset = load_default_ruleset()
    font_rule = ruleset.get_rule("font_body_text")
    indent_rule = ruleset.get_rule("indent_first_line")
    margin_rule = ruleset.get_rule("page_margins")

    assert font_rule.expected.family == "Times New Roman"
    assert font_rule.expected.size_pt == 14.0
    assert indent_rule.expected.first_line_mm == 12.5
    assert margin_rule.expected.left_mm == 30.0
    assert margin_rule.expected.right_mm == 15.0
    assert margin_rule.expected.top_mm == 20.0
    assert margin_rule.expected.bottom_mm == 20.0


def test_default_ruleset_file_is_present() -> None:
    """Путь к поставляемому набору правил существует в пакете."""
    assert DEFAULT_RULESET_PATH.is_file()
    assert DEFAULT_RULESET_PATH.parent.name == "rules"



def test_duplicate_rule_id_is_rejected() -> None:
    """Одинаковые rule_id внутри набора запрещены."""
    payload = _ruleset(_font_rule(), _font_rule())

    with pytest.raises(ValueError, match="более одного раза"):
        RuleSet.model_validate(payload)


def test_unknown_rule_kind_is_rejected() -> None:
    """Неизвестный вид правила не проходит дискриминированный union."""
    payload = _ruleset(
        {
            "rule_id": "line_spacing",
            "kind": "spacing",
            "message": "Интервал",
            "target": {"scope": "body_text"},
            "expected": {"value": 1.5},
        }
    )

    with pytest.raises(ValueError):
        RuleSet.model_validate(payload)


def test_unknown_scope_is_rejected() -> None:
    """Неизвестная область действия правила отвергается."""
    payload = _ruleset(_font_rule(target={"scope": "endnotes"}))

    with pytest.raises(ValueError):
        RuleSet.model_validate(payload)


def test_unknown_expected_key_is_rejected() -> None:
    """Лишний ключ в expected не должен молча игнорироваться."""
    payload = _ruleset(_font_rule(expected={"family": "Times New Roman", "size": 14}))

    with pytest.raises(ValueError):
        RuleSet.model_validate(payload)


def test_empty_expectation_is_rejected() -> None:
    """Правило без проверяемых полей бесполезно и отвергается."""
    payload = _ruleset(_font_rule(expected={"size_tolerance_pt": 0.5}))

    with pytest.raises(ValueError, match="пусто"):
        RuleSet.model_validate(payload)


def test_negative_tolerance_is_rejected() -> None:
    """Отрицательный допуск не имеет смысла."""
    payload = _ruleset(_font_rule(expected={"size_pt": 14.0, "size_tolerance_pt": -1.0}))

    with pytest.raises(ValueError):
        RuleSet.model_validate(payload)



def test_margin_rule_requires_sections_scope() -> None:
    """Правило полей не может проверять абзацы."""
    payload = _ruleset(_margin_rule(target={"scope": "body_text"}))

    with pytest.raises(ValueError, match="scope должен быть 'sections'"):
        RuleSet.model_validate(payload)


def test_paragraph_rule_rejects_sections_scope() -> None:
    """Правило шрифта не может проверять секции."""
    payload = _ruleset(_font_rule(target={"scope": "sections"}))

    with pytest.raises(ValueError, match="не применим к абзацам"):
        RuleSet.model_validate(payload)


def test_empty_rules_list_is_rejected() -> None:
    """Набор без правил не создается."""
    with pytest.raises(ValueError):
        RuleSet.model_validate(_ruleset())


def test_load_ruleset_reads_json_file(tmp_path: Path) -> None:
    """load_ruleset читает и валидирует набор из файла."""
    ruleset_path = tmp_path / "rules.json"
    ruleset_path.write_text(json.dumps(_ruleset(_margin_rule())), encoding="utf-8")

    ruleset = load_ruleset(ruleset_path)

    assert ruleset.rules[0].rule_id == "page_margins"
    assert isinstance(ruleset.rules[0], MarginRule)


def test_load_ruleset_reports_broken_json(tmp_path: Path) -> None:
    """Синтаксическая ошибка JSON сообщается как ValueError с путем к файлу."""
    ruleset_path = tmp_path / "broken.json"
    ruleset_path.write_text("{rules: []}", encoding="utf-8")

    with pytest.raises(ValueError, match="некорректный JSON"):
        load_ruleset(ruleset_path)


def test_load_ruleset_missing_file(tmp_path: Path) -> None:
    """Отсутствующий файл набора правил дает FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_ruleset(tmp_path / "absent.json")


def test_get_rule_unknown_id_raises_key_error() -> None:
    """Обращение к отсутствующему правилу поднимает KeyError."""
    ruleset = load_default_ruleset()

    with pytest.raises(KeyError):
        ruleset.get_rule("absent_rule")
