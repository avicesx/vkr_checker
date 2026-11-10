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
from rule_engine.rules import (
    PROFILE_RULESET_PATHS,
    LineSpacingRule,
    PageFormatRule,
    PageNumberPositionRule,
    available_profiles,
    load_profile_ruleset,
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


def _line_spacing_rule(**overrides: object) -> dict:
    rule = {
        "rule_id": "line_spacing_body",
        "kind": "line_spacing",
        "message": "Полуторный межстрочный интервал",
        "target": {"scope": "body_text"},
        "expected": {"ratio": 1.5, "tolerance": 0.25},
    }
    rule.update(overrides)
    return rule


def _page_number_rule(**overrides: object) -> dict:
    rule = {
        "rule_id": "page_number_position",
        "kind": "page_number_position",
        "message": "Номер страницы в нижнем колонтитуле по центру",
        "target": {"scope": "headers_footers"},
        "expected": {"zone": "footer", "alignment": "center"},
    }
    rule.update(overrides)
    return rule


def _page_format_rule(**overrides: object) -> dict:
    rule = {
        "rule_id": "page_format",
        "kind": "page_format",
        "message": "Лист A4",
        "target": {"scope": "sections"},
        "expected": {"width_mm": 210.0, "height_mm": 297.0, "tolerance_mm": 1.0},
    }
    rule.update(overrides)
    return rule


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


def test_line_spacing_rule_describes_multiplier() -> None:
    """Интервал задается множителем и допуском в тех же единицах."""
    ruleset = RuleSet.model_validate(_ruleset(_line_spacing_rule()))
    rule = ruleset.rules[0]

    assert isinstance(rule, LineSpacingRule)
    assert rule.target.scope == "body_text"
    assert rule.expected.ratio == 1.5
    assert rule.expected.tolerance == 0.25


def test_line_spacing_rule_accepts_heading_scope() -> None:
    """Заголовки тоже абзацы, поэтому интервал для них задается тем же правилом."""
    payload = _ruleset(_line_spacing_rule(target={"scope": "headings"}))

    assert isinstance(RuleSet.model_validate(payload).rules[0], LineSpacingRule)


def test_line_spacing_rule_rejects_sections_scope() -> None:
    """Межстрочный интервал не является свойством секции."""
    payload = _ruleset(_line_spacing_rule(target={"scope": "sections"}))

    with pytest.raises(ValueError, match="scope должен быть одним из"):
        RuleSet.model_validate(payload)


@pytest.mark.parametrize(
    "expected",
    [
        {"ratio": 0.0},
        {"ratio": -1.5},
        {"ratio": 20.0},
        {"ratio": 1.5, "tolerance": -0.1},
    ],
)
def test_line_spacing_rule_rejects_impossible_values(expected: dict) -> None:
    """Нулевой, отрицательный и чрезмерный множитель не имеют смысла."""
    payload = _ruleset(_line_spacing_rule(expected=expected))

    with pytest.raises(ValueError):
        RuleSet.model_validate(payload)


def test_page_number_position_rule_defaults_to_footer() -> None:
    """Номер страницы по умолчанию ожидается снизу по центру."""
    ruleset = RuleSet.model_validate(_ruleset(_page_number_rule()))
    rule = ruleset.rules[0]

    assert isinstance(rule, PageNumberPositionRule)
    assert rule.target.scope == "headers_footers"
    assert rule.expected.zone == "footer"
    assert rule.expected.alignment == "center"


def test_page_number_position_rule_accepts_header_and_alignment() -> None:
    """Профиль МПГУ ставит номер сверху; выравнивание можно задать явно."""
    payload = _ruleset(
        _page_number_rule(expected={"zone": "header", "alignment": "right"})
    )
    rule = RuleSet.model_validate(payload).rules[0]

    assert rule.expected.zone == "header"
    assert rule.expected.alignment == "right"


def test_page_number_position_rule_requires_zone() -> None:
    """Без указания колонтитула правило ничего не проверяет."""
    payload = _ruleset(_page_number_rule(expected={"alignment": "center"}))

    with pytest.raises(ValueError, match="zone"):
        RuleSet.model_validate(payload)


def test_page_number_position_rejects_pdf_location_names() -> None:
    """Названия положений из PDF-профилей не являются схемой DOCX-правила."""
    payload = _ruleset(_page_number_rule(expected={"zone": "top_center"}))

    with pytest.raises(ValueError):
        RuleSet.model_validate(payload)


def test_page_number_position_requires_headers_footers_scope() -> None:
    """Номер страницы живет в колонтитуле, а не в основном тексте."""
    payload = _ruleset(_page_number_rule(target={"scope": "body_text"}))

    with pytest.raises(ValueError, match="scope должен быть одним из"):
        RuleSet.model_validate(payload)


def test_page_format_rule_defaults_to_a4_portrait() -> None:
    """Формат по умолчанию — A4 книжной ориентации с допуском ±1 мм."""
    ruleset = RuleSet.model_validate(_ruleset(_page_format_rule(expected={})))
    rule = ruleset.rules[0]

    assert isinstance(rule, PageFormatRule)
    assert rule.target.scope == "sections"
    assert rule.expected.width_mm == 210.0
    assert rule.expected.height_mm == 297.0
    assert rule.expected.tolerance_mm == 1.0
    assert rule.expected.orientation == "portrait"


def test_page_format_rule_accepts_landscape() -> None:
    """Альбомный лист описывается теми же полями с переставленными сторонами."""
    payload = _ruleset(
        _page_format_rule(
            expected={
                "width_mm": 297.0,
                "height_mm": 210.0,
                "orientation": "landscape",
            }
        )
    )
    rule = RuleSet.model_validate(payload).rules[0]

    assert rule.expected.orientation == "landscape"
    assert rule.expected.width_mm == 297.0


@pytest.mark.parametrize(
    "expected",
    [
        {"width_mm": 297.0, "height_mm": 210.0},
        {"width_mm": 210.0, "height_mm": 297.0, "orientation": "landscape"},
        {"width_mm": 210.0, "height_mm": 297.0, "tolerance_mm": -1.0},
        {"width_mm": 0.0, "height_mm": 297.0},
    ],
)
def test_page_format_rule_rejects_impossible_sheet(expected: dict) -> None:
    """Размеры должны согласовываться с ориентацией, допуск — быть неотрицательным."""
    payload = _ruleset(_page_format_rule(expected=expected))

    with pytest.raises(ValueError):
        RuleSet.model_validate(payload)


def test_page_format_rule_requires_sections_scope() -> None:
    """Формат листа — свойство секции."""
    payload = _ruleset(_page_format_rule(target={"scope": "headers_footers"}))

    with pytest.raises(ValueError, match="scope должен быть одним из"):
        RuleSet.model_validate(payload)


@pytest.mark.parametrize(
    ("profile_id", "margins_mm", "zone"),
    [
        ("mpgu", (30.0, 15.0, 20.0, 20.0), "header"),
        ("urgpu", (30.0, 20.0, 25.0, 25.0), "footer"),
        ("urfu", (30.0, 15.0, 20.0, 20.0), "footer"),
    ],
)
def test_profile_rulesets_load(
    profile_id: str,
    margins_mm: tuple[float, float, float, float],
    zone: str,
) -> None:
    """Профиль вуза загружается по id и содержит поля и положение номера."""
    ruleset = load_profile_ruleset(profile_id)

    assert ruleset.ruleset_id == f"{profile_id}-vkr-profile"
    margin_rule = ruleset.get_rule("page_margins")
    assert isinstance(margin_rule, MarginRule)
    observed = (
        margin_rule.expected.left_mm,
        margin_rule.expected.right_mm,
        margin_rule.expected.top_mm,
        margin_rule.expected.bottom_mm,
    )
    assert observed == margins_mm
    assert margin_rule.expected.tolerance_mm == 3.0

    number_rule = ruleset.get_rule("page_number_position")
    assert isinstance(number_rule, PageNumberPositionRule)
    assert number_rule.expected.zone == zone
    assert number_rule.expected.alignment == "center"


def test_profile_common_thresholds_match_methodical_guides() -> None:
    """Общие пороги профилей: шрифт, отступ и интервал из методичек."""
    for profile_id in available_profiles():
        ruleset = load_profile_ruleset(profile_id)
        font_expectation = ruleset.get_rule("font_body_text").expected
        indent_expectation = ruleset.get_rule("indent_first_line").expected
        spacing_expectation = ruleset.get_rule("line_spacing_body").expected

        assert font_expectation.family == "Times New Roman"
        assert font_expectation.size_pt == 14.0
        assert font_expectation.size_tolerance_pt == 0.5
        assert indent_expectation.first_line_mm == 12.5
        assert indent_expectation.tolerance_mm == 2.5
        assert spacing_expectation.ratio == 1.5
        assert spacing_expectation.tolerance == 0.25


def test_only_urfu_profile_requires_a4() -> None:
    """Формат листа обязателен только в профиле УрФУ."""
    format_rule = load_profile_ruleset("urfu").get_rule("page_format")
    assert isinstance(format_rule, PageFormatRule)
    assert format_rule.expected.height_mm == 297.0

    for profile_id in ("mpgu", "urgpu"):
        with pytest.raises(KeyError):
            load_profile_ruleset(profile_id).get_rule("page_format")


def test_available_profiles_point_to_existing_files() -> None:
    """Каждый профиль поставляется отдельным файлом в каталоге rules."""
    assert available_profiles() == ("mpgu", "urgpu", "urfu")
    for profile_id in available_profiles():
        path = PROFILE_RULESET_PATHS[profile_id]
        assert path.is_file()
        assert path.name == f"{profile_id}.json"
        assert path.parent.name == "rules"


def test_profile_id_is_normalized() -> None:
    """Регистр и пробелы по краям идентификатора профиля не важны."""
    assert load_profile_ruleset(" UrFU ").ruleset_id == "urfu-vkr-profile"


def test_unknown_profile_is_rejected() -> None:
    """Опечатка в id вуза не должна молча подменяться набором по умолчанию."""
    with pytest.raises(ValueError, match="Неизвестный профиль"):
        load_profile_ruleset("mgu")


def test_profile_rulesets_do_not_change_default_ruleset() -> None:
    """Набор по умолчанию остается прежним и не подмешивает профильные правила."""
    default_ruleset = load_default_ruleset()
    default_kinds = {rule.kind for rule in default_ruleset.rules}

    assert "page_number_position" not in default_kinds
    assert "page_format" not in default_kinds
    assert "line_spacing" not in default_kinds
    assert default_ruleset.ruleset_id == "vkr-rule-engine-test"
