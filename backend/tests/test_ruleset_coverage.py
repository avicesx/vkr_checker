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
  ``RuleKind``, задает допустимую для вида область и повторяет фактическую
  область, в которой правило применяется в поставленных наборах.

Поэтому новый вид правила без обработчика, без области в админке или с
областью, не совпадающей с наборами, роняет тест, а не проявляется пропуском
в отчете. Каталог ``manual_test/`` не хранится в git, поэтому смоук на реальном
документе пропускается при отсутствии образца.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Set, Tuple, get_args

import pytest

from api.admin import DEFAULT_RULE_SCOPES
from rule_engine.engine import RuleEngine
from rule_engine.rules import (
    DEFAULT_RULESET_PATH,
    KIND_ALLOWED_SCOPES,
    PROFILE_RULESET_PATHS,
    Rule,
    RuleKind,
    load_ruleset,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = BACKEND_ROOT / "rule_engine" / "rules"
SAMPLES_DIR = BACKEND_ROOT / "manual_test"

#: Все виды правил, объявленные схемой RuleKind.
RULE_KINDS: frozenset[str] = frozenset(get_args(RuleKind))

#: Классы правил из дискриминируемого объединения Rule.
RULE_CLASSES: Tuple[type, ...] = get_args(get_args(Rule)[0])

#: Вид правила -> класс схемы (поле kind у класса — Literal с одним значением).
KIND_TO_RULE_CLASS: Dict[str, type] = {
    rule_class.model_fields["kind"].default: rule_class
    for rule_class in RULE_CLASSES
}

#: JSON-наборы, поставляемые в каталоге правил (штатный и профили вузов).
RULESET_FILES: List[Path] = sorted(RULES_DIR.glob("*.json"))


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


@pytest.mark.parametrize("path", RULESET_FILES, ids=_ids)
def test_shipped_ruleset_loads(path: Path) -> None:
    """Каждый поставленный набор загружается и проходит валидацию схемы."""
    ruleset = load_ruleset(path)

    assert ruleset.rules, f"набор {path.name} не содержит ни одного правила"
    assert ruleset.ruleset_id.strip(), f"в наборе {path.name} пустой ruleset_id"
    assert ruleset.ruleset_version.strip(), f"в наборе {path.name} пустая версия"


def test_rule_schema_covers_every_rule_kind() -> None:
    """Каждый вид RuleKind имеет свой класс правила и наоборот."""
    schema_kinds = set(KIND_TO_RULE_CLASS)

    missing_kinds = sorted(RULE_KINDS - schema_kinds)
    assert not missing_kinds, (
        "RuleKind объявляет виды без класса правила: " + ", ".join(missing_kinds)
    )

    extra_kinds = sorted(schema_kinds - RULE_KINDS)
    assert not extra_kinds, (
        "объединение Rule содержит виды, отсутствующие в RuleKind: "
        + ", ".join(extra_kinds)
    )


def test_registry_has_checker_for_every_rule_kind() -> None:
    """Реестр движка покрывает все виды правил и не содержит лишних классов."""
    registered_classes = set(RuleEngine._CHECKERS)

    missing_handlers = sorted(
        kind
        for kind, rule_class in KIND_TO_RULE_CLASS.items()
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
        f"{rule.rule_id} ({rule.kind})"
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
    default_kinds: Set[str] = {rule.kind for rule in default_ruleset.rules}
    default_version = _version_key(default_ruleset.ruleset_version)

    missing_kinds = sorted(RULE_KINDS - default_kinds)
    assert not missing_kinds, (
        "штатный набор vkr_test_rules.json не содержит правил видов: "
        + ", ".join(missing_kinds)
        + ". Новый вид правила должен попасть в набор по умолчанию"
    )

    for profile_id, profile_path in PROFILE_RULESET_PATHS.items():
        profile_ruleset = load_ruleset(profile_path)
        profile_version = _version_key(profile_ruleset.ruleset_version)
        assert profile_version <= default_version, (
            f"профиль {profile_id} ({profile_ruleset.ruleset_version}) новее штатного "
            f"набора ({default_ruleset.ruleset_version}): обновите версию штатного набора"
        )

        profile_kinds = {rule.kind for rule in profile_ruleset.rules}
        extra_kinds = sorted(profile_kinds - default_kinds)
        assert not extra_kinds, (
            f"профиль {profile_id} использует виды, которых нет в штатном наборе: "
            + ", ".join(extra_kinds)
        )


def test_default_rule_scopes_cover_every_rule_kind() -> None:
    """DEFAULT_RULE_SCOPES в админке знает область для каждого вида правила."""
    missing = sorted(RULE_KINDS - set(DEFAULT_RULE_SCOPES))
    assert not missing, (
        "DEFAULT_RULE_SCOPES в backend/api/admin.py не задает область для видов: "
        + ", ".join(missing)
        + ". Добавьте вид в словарь, иначе админка валидирует правило с областью "
        "по умолчанию и оно не применяется"
    )

    unknown = sorted(set(DEFAULT_RULE_SCOPES) - RULE_KINDS)
    assert not unknown, (
        "DEFAULT_RULE_SCOPES содержит виды, отсутствующие в RuleKind: "
        + ", ".join(unknown)
    )


def test_default_rule_scopes_are_allowed_for_rule_kind() -> None:
    """Область из админки допустима для своего вида правила."""
    for kind, scope in DEFAULT_RULE_SCOPES.items():
        allowed = KIND_ALLOWED_SCOPES[kind]
        assert scope in allowed, (
            f"DEFAULT_RULE_SCOPES['{kind}'] = '{scope}', а схема допускает "
            f"только {sorted(allowed)}"
        )


def test_default_rule_scopes_match_shipped_rulesets() -> None:
    """Область в админке повторяет фактическую область правил из наборов."""
    used_scopes: Dict[str, Set[str]] = {}
    for path in RULESET_FILES:
        for rule in load_ruleset(path).rules:
            used_scopes.setdefault(rule.kind, set()).add(rule.target.scope)

    drifted = []
    for kind, scope in DEFAULT_RULE_SCOPES.items():
        actual = used_scopes.get(kind, set())
        if scope not in actual:
            drifted.append(
                f"{kind}: в админке '{scope}', в наборах {sorted(actual) or 'правил нет'}"
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
