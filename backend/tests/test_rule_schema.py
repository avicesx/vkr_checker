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
    HeadingFormatRule,
    HeadingLevelsRule,
    HeadingNumberingRule,
    IndentRule,
    MarginRule,
    RequiredSectionsRule,
    RuleSet,
    TocRule,
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


def _heading_levels_rule(**overrides: object) -> dict:
    rule = {
        "rule_id": "heading_levels",
        "kind": "heading_levels",
        "message": "Иерархия заголовков",
        "target": {"scope": "headings"},
        "expected": {"first_level": 1, "allow_skipped_levels": False},
    }
    rule.update(overrides)
    return rule


def _toc_rule(**overrides: object) -> dict:
    rule = {
        "rule_id": "toc",
        "kind": "toc",
        "message": "Оглавление",
        "target": {"scope": "toc"},
        "expected": {
            "require_toc": True,
            "check_completeness": True,
            "required_entries": [{"label": "введение", "role": "introduction"}],
        },
    }
    rule.update(overrides)
    return rule


def _ruleset(*rules: dict) -> dict:
    return {
        "ruleset_id": "test-ruleset",
        "ruleset_version": "1.0.0",
        "rules": list(rules),
    }


def test_default_ruleset_describes_nine_checks() -> None:
    """Штатный набор описывает девять правил: текст, поля, заголовки, разделы и оглавление."""
    ruleset = load_default_ruleset()

    assert ruleset.ruleset_id == "vkr-rule-engine-test"
    assert ruleset.ruleset_version == "2.0.0"
    assert [rule.rule_id for rule in ruleset.rules] == [
        "font_body_text",
        "indent_first_line",
        "page_margins",
        "heading_levels",
        "heading_numbering",
        "heading_format_text",
        "heading_alignment",
        "required_sections",
        "toc",
    ]
    assert isinstance(ruleset.rules[0], FontRule)
    assert isinstance(ruleset.rules[1], IndentRule)
    assert isinstance(ruleset.rules[2], MarginRule)
    assert isinstance(ruleset.rules[3], HeadingLevelsRule)
    assert isinstance(ruleset.rules[4], HeadingNumberingRule)
    assert isinstance(ruleset.rules[5], HeadingFormatRule)
    assert isinstance(ruleset.rules[6], HeadingFormatRule)
    assert isinstance(ruleset.rules[7], RequiredSectionsRule)
    assert isinstance(ruleset.rules[8], TocRule)


def test_default_ruleset_excludes_front_matter_from_heading_rules() -> None:
    """Структурные правила не проверяют титульный лист, задание и приложения."""
    ruleset = load_default_ruleset()
    heading_rule = ruleset.get_rule("heading_numbering")

    assert heading_rule.target.scope == "headings"
    assert heading_rule.target.exclude_roles == ["title_page", "task_sheet", "appendix"]
    assert ruleset.get_rule("heading_alignment").severity == "warning"


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


def test_structure_rule_requires_own_scope() -> None:
    """Структурные правила не применяются к абзацам основного текста."""
    payload = _ruleset(_heading_levels_rule(target={"scope": "body_text"}))

    with pytest.raises(ValueError, match="scope должен быть одним из"):
        RuleSet.model_validate(payload)


def test_required_sections_rule_requires_structure_scope() -> None:
    """Правило разделов проверяет структуру, а не заголовки."""
    payload = _ruleset(
        {
            "rule_id": "required_sections",
            "kind": "required_sections",
            "message": "Разделы",
            "target": {"scope": "headings"},
            "expected": {"sections": [{"label": "введение", "role": "introduction"}]},
        }
    )

    with pytest.raises(ValueError, match="scope должен быть одним из"):
        RuleSet.model_validate(payload)


def test_exclude_roles_is_rejected_outside_heading_scopes() -> None:
    """Исключение ролей имеет смысл только для заголовков и структуры."""
    payload = _ruleset(_toc_rule(target={"scope": "toc", "exclude_roles": ["appendix"]}))

    with pytest.raises(ValueError, match="exclude_roles"):
        RuleSet.model_validate(payload)


def test_empty_heading_expectation_is_rejected() -> None:
    """Правило оформления без проверяемых свойств бесполезно."""
    payload = _ruleset(
        {
            "rule_id": "heading_format",
            "kind": "heading_format",
            "message": "Оформление заголовка",
            "target": {"scope": "headings"},
            "expected": {"trailing_dot": "any", "case": "any"},
        }
    )

    with pytest.raises(ValueError, match="пусто"):
        RuleSet.model_validate(payload)


def test_heading_levels_reject_max_level_below_first_level() -> None:
    """Ограничение глубины не может быть меньше стартового уровня."""
    payload = _ruleset(
        _heading_levels_rule(expected={"first_level": 3, "max_level": 2})
    )

    with pytest.raises(ValueError, match="max_level"):
        RuleSet.model_validate(payload)


def test_toc_completeness_requires_entries() -> None:
    """Полноту оглавления не с чем сверять без списка разделов."""
    payload = _ruleset(
        _toc_rule(
            expected={
                "require_toc": True,
                "check_completeness": True,
                "required_entries": [],
            }
        )
    )

    with pytest.raises(ValueError, match="required_entries"):
        RuleSet.model_validate(payload)


def test_toc_completeness_enables_itself_with_required_entries() -> None:
    """Список разделов сам включает проверку полноты оглавления."""
    payload = _ruleset(
        _toc_rule(
            expected={
                "require_toc": True,
                "required_entries": [{"label": "введение", "role": "introduction"}],
            }
        )
    )

    ruleset = RuleSet.model_validate(payload)

    assert ruleset.rules[0].expected.check_completeness is True


def test_minimal_toc_rule_checks_only_presence() -> None:
    """Минимальное правило оглавления проверяет только его наличие."""
    payload = _ruleset(_toc_rule(expected={"require_toc": True}))

    ruleset = RuleSet.model_validate(payload)

    assert ruleset.rules[0].expected.check_completeness is False


def test_section_spec_requires_role_or_titles() -> None:
    """Раздел без роли и названий нельзя найти в документе."""
    payload = _ruleset(
        {
            "rule_id": "required_sections",
            "kind": "required_sections",
            "message": "Разделы",
            "target": {"scope": "document_structure"},
            "expected": {"sections": [{"label": "введение"}]},
        }
    )

    with pytest.raises(ValueError, match="SectionSpec пусто"):
        RuleSet.model_validate(payload)


def test_required_sections_reject_duplicate_labels() -> None:
    """Метки разделов попадают в violation_id и должны быть уникальны."""
    payload = _ruleset(
        {
            "rule_id": "required_sections",
            "kind": "required_sections",
            "message": "Разделы",
            "target": {"scope": "document_structure"},
            "expected": {
                "sections": [
                    {"label": "введение", "role": "introduction"},
                    {"label": "введение", "role": "conclusion"},
                ]
            },
        }
    )

    with pytest.raises(ValueError, match="уникальны"):
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
