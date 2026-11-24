"""Покрытие поставляемых наборов правил и реестра чекеров движка.

Набор правил декларативен, поэтому единственная точка маршрутизации — реестр
``RuleEngine._CHECKERS``: вид правила без обработчика не попадает в отчет, и
проверка молча выпадает. Тест фиксирует четыре инварианта:

* каждый JSON-файл каталога ``rule_engine/rules`` загружается публичным API
  ``load_ruleset`` без ошибок валидации;
* каждому виду правила соответствует класс схемы и обработчик в реестре
  движка, а реестр не содержит лишних классов;
* штатный набор — актуальная база: он содержит все виды правил и версия не
  старше профилей вузов, которые используют только существующие виды;
* административный справочник ``DEFAULT_RULE_SCOPES`` покрывает все виды
  ``RuleCode``, задает допустимую для вида область и повторяет фактическую
  область, в которой правило применяется в поставленных наборах.

Поэтому новый вид правила без обработчика, без области в админке или с
областью, не совпадающей с наборами, роняет тест, а не проявляется пропуском
в отчете. Каталог ``manual_test/`` не хранится в git, поэтому смоук на реальном
документе пропускается при отсутствии образца.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set, Tuple, get_args

import pytest

from api.admin import DEFAULT_RULE_SCOPES
from rule_engine.engine import RuleEngine
from rule_engine.rules import (
    DEFAULT_RULESET_PATH,
    CODE_ALLOWED_SCOPES,
    PROFILE_RULESET_PATHS,
    Rule,
    RuleCode,
    load_ruleset,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = BACKEND_ROOT / "rule_engine" / "rules"
SAMPLES_DIR = BACKEND_ROOT / "manual_test"

#: Все виды правил, объявленные схемой RuleCode.
RULE_CODES: frozenset[str] = frozenset(get_args(RuleCode))

#: Классы правил из дискриминируемого объединения Rule.
RULE_CLASSES: Tuple[type, ...] = get_args(get_args(Rule)[0])

#: Код правила -> класс схемы (поле code у класса — Literal с одним значением).
CODE_TO_RULE_CLASS: Dict[str, type] = {
    rule_class.model_fields["code"].default: rule_class
    for rule_class in RULE_CLASSES
}

#: JSON-наборы, поставляемые в каталоге правил (штатный и профили вузов).
RULESET_FILES: List[Path] = sorted(RULES_DIR.rglob("*.json"))

#: Имена полей правила из словаря ERD (Rule/Rule_Type).
RULE_FIELDS = frozenset({"rule_id", "code", "params", "strictness", "quote", "message", "target"})

#: Имена, которые остались в прошлой схеме и не должны вернуться в наборы.
LEGACY_RULE_KEYS = frozenset({"kind", "expected", "severity"})


def _ids(path: Path) -> str:
    """Идентификатор параметризации — имя файла набора."""
    return path.name


def _version_key(version: str) -> Tuple[int, ...]:
    """Разбирает версию набора в числовой кортеж для сравнения."""
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        pytest.fail(f"версия набора '{version}' не числовая (ожидается x.y.z)")


def test_rules_directory_contains_default_and_profiles() -> None:
    """Каталог правил содержит штатный набор и файл каждого профиля вуза."""
    shipped = set(RULESET_FILES)
    expected = {DEFAULT_RULESET_PATH, *PROFILE_RULESET_PATHS.values()}

    missing = sorted(path.name for path in expected if path not in shipped)
    assert not missing, (
        "в rule_engine/rules нет файлов наборов: " + ", ".join(missing)
    )

    unregistered = sorted(path.name for path in shipped - expected)
    assert not unregistered, (
        "JSON-наборы в rule_engine/rules не зарегистрированы в DEFAULT_RULESET_PATH "
        "или PROFILE_RULESET_PATHS: " + ", ".join(unregistered)
    )


def _walk_keys(node: Any) -> Iterator[str]:
    """Обходит JSON-значение и отдает все имена ключей на любой глубине."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_keys(item)


@pytest.mark.parametrize("path", RULESET_FILES, ids=_ids)
def test_shipped_ruleset_uses_erd_field_names(path: Path) -> None:
    """Наборы используют только словарь ERD и не содержат ключей прошлой схемы."""
    payload = json.loads(path.read_text(encoding="utf-8"))

    legacy = sorted(set(_walk_keys(payload)) & LEGACY_RULE_KEYS)
    assert not legacy, (
        f"набор {path.name} содержит ключи прошлой схемы: " + ", ".join(legacy)
        + ". Поля правила — code, params, strictness, quote"
    )

    for index, rule in enumerate(payload["rules"]):
        unknown = sorted(set(rule) - RULE_FIELDS)
        assert not unknown, (
            f"в наборе {path.name} правило #{index} содержит неизвестные поля: "
            + ", ".join(unknown)
        )
        assert "code" in rule and "params" in rule, (
            f"в наборе {path.name} правило #{index} должно задавать code и params"
        )


@pytest.mark.parametrize("path", RULESET_FILES, ids=_ids)
def test_shipped_ruleset_loads(path: Path) -> None:
    """Каждый поставленный набор загружается и проходит валидацию схемы."""
    ruleset = load_ruleset(path)

    assert ruleset.rules, f"набор {path.name} не содержит ни одного правила"
    assert ruleset.ruleset_id.strip(), f"в наборе {path.name} пустой ruleset_id"
    assert ruleset.ruleset_version.strip(), f"в наборе {path.name} пустая версия"


def test_rule_schema_covers_every_rule_kind() -> None:
    """Каждый вид RuleCode имеет свой класс правила и наоборот."""
    schema_codes = set(CODE_TO_RULE_CLASS)

    missing_codes = sorted(RULE_CODES - schema_codes)
    assert not missing_codes, (
        "RuleCode объявляет виды без класса правила: " + ", ".join(missing_codes)
    )

    extra_codes = sorted(schema_codes - RULE_CODES)
    assert not extra_codes, (
        "объединение Rule содержит виды, отсутствующие в RuleCode: "
        + ", ".join(extra_codes)
    )


def test_registry_has_checker_for_every_rule_kind() -> None:
    """Реестр движка покрывает все виды правил и не содержит лишних классов."""
    registered_classes = set(RuleEngine._CHECKERS)

    missing_handlers = sorted(
        code
        for code, rule_class in CODE_TO_RULE_CLASS.items()
        if rule_class not in registered_classes
    )
    assert not missing_handlers, (
        "в RuleEngine._CHECKERS нет обработчика для видов правил: "
        + ", ".join(missing_handlers)
    )

    unknown_classes = sorted(
        rule_class.__name__
        for rule_class in registered_classes
        if rule_class not in set(RULE_CLASSES)
    )
    assert not unknown_classes, (
        "RuleEngine._CHECKERS содержит классы вне объединения Rule: "
        + ", ".join(unknown_classes)
    )


@pytest.mark.parametrize("path", RULESET_FILES, ids=_ids)
def test_every_rule_in_ruleset_has_checker(path: Path) -> None:
    """Ни одно правило набора не остается без обработчика в движке."""
    ruleset = load_ruleset(path)

    unhandled = sorted(
        f"{rule.rule_id} ({rule.code})"
        for rule in ruleset.rules
        if type(rule) not in RuleEngine._CHECKERS
    )
    assert not unhandled, (
        f"правила набора {path.name} не имеют обработчика в RuleEngine._CHECKERS: "
        + ", ".join(unhandled)
    )


def test_default_ruleset_is_the_current_baseline() -> None:
    """Штатный набор — актуальная база: все виды и версия не старше профилей."""
    default_ruleset = load_ruleset(DEFAULT_RULESET_PATH)
    default_codes: Set[str] = {rule.code for rule in default_ruleset.rules}
    default_version = _version_key(default_ruleset.ruleset_version)

    missing_codes = sorted(RULE_CODES - default_codes)
    assert not missing_codes, (
        "штатный набор vkr_test_rules.json не содержит правил видов: "
        + ", ".join(missing_codes)
        + ". Новый вид правила должен попасть в набор по умолчанию"
    )

    for profile_id, profile_path in PROFILE_RULESET_PATHS.items():
        profile_ruleset = load_ruleset(profile_path)
        profile_version = _version_key(profile_ruleset.ruleset_version)
        assert profile_version <= default_version, (
            f"профиль {profile_id} ({profile_ruleset.ruleset_version}) новее штатного "
            f"набора ({default_ruleset.ruleset_version}): обновите версию штатного набора"
        )

        profile_kinds = {rule.code for rule in profile_ruleset.rules}
        extra_codes = sorted(profile_kinds - default_codes)
        assert not extra_codes, (
            f"профиль {profile_id} использует виды, которых нет в штатном наборе: "
            + ", ".join(extra_codes)
        )


def test_default_rule_scopes_cover_every_rule_kind() -> None:
    """DEFAULT_RULE_SCOPES в админке знает область для каждого вида правила."""
    missing = sorted(RULE_CODES - set(DEFAULT_RULE_SCOPES))
    assert not missing, (
        "DEFAULT_RULE_SCOPES в backend/api/admin.py не задает область для видов: "
        + ", ".join(missing)
        + ". Добавьте вид в словарь, иначе админка валидирует правило с областью "
        "по умолчанию и оно не применяется"
    )

    unknown = sorted(set(DEFAULT_RULE_SCOPES) - RULE_CODES)
    assert not unknown, (
        "DEFAULT_RULE_SCOPES содержит виды, отсутствующие в RuleCode: "
        + ", ".join(unknown)
    )


def test_default_rule_scopes_are_allowed_for_rule_kind() -> None:
    """Область из админки допустима для своего вида правила."""
    for code, scope in DEFAULT_RULE_SCOPES.items():
        allowed = CODE_ALLOWED_SCOPES[code]
        assert scope in allowed, (
            f"DEFAULT_RULE_SCOPES['{code}'] = '{scope}', а схема допускает "
            f"только {sorted(allowed)}"
        )


def test_default_rule_scopes_match_shipped_rulesets() -> None:
    """Область в админке повторяет фактическую область правил из наборов."""
    used_scopes: Dict[str, Set[str]] = {}
    for path in RULESET_FILES:
        for rule in load_ruleset(path).rules:
            used_scopes.setdefault(rule.code, set()).add(rule.target.scope)

    drifted = []
    for code, scope in DEFAULT_RULE_SCOPES.items():
        actual = used_scopes.get(code, set())
        if scope not in actual:
            drifted.append(
                f"{code}: в админке '{scope}', в наборах {sorted(actual) or 'правил нет'}"
            )
    assert not drifted, (
        "DEFAULT_RULE_SCOPES расходится с поставленными наборами правил: "
        + "; ".join(drifted)
    )


def test_default_ruleset_on_real_docx_smoke() -> None:
    """Штатный набор исполним на реальном DOCX: отчет полный и непустой."""
    sample = next(iter(sorted(SAMPLES_DIR.glob("*.docx"))), None)
    if sample is None:
        pytest.skip("каталог manual_test не хранится в git: нет ни одного DOCX")

    from parser.docx.pipeline import DocxParserPipeline

    ruleset = load_ruleset(DEFAULT_RULESET_PATH)
    document = DocxParserPipeline(sample).parse()
    report = RuleEngine(ruleset).check(document)

    assert len(report.assessments) == len(ruleset.rules), (
        f"в отчете {len(report.assessments)} оценок на {len(ruleset.rules)} правил: "
        "часть чекеров не отдала assessment"
    )
    assert report.violations, (
        f"на реальном документе {sample.name} штатный набор не нашел ни одного нарушения"
    )
