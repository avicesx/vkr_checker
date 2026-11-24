"""Тесты справочника видов проверок и сборки набора правил из базы.

Каждый тест работает на отдельной временной базе рядом с TEST_DATABASE_URL и
удаляет её после проверки. Без TEST_DATABASE_URL тесты пропускаются: справочник
и компилятор живут в таблицах, подменять их моками бессмысленно.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable
from uuid import uuid4

import pytest
from rule_engine.engine import RuleEngine
from rule_engine.rules import (
    CODE_ALLOWED_SCOPES,
    DEFAULT_RULESET_PATH,
    load_ruleset,
)
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.database import create_database_engine
from app.migrations import apply_migrations
from app.models import Guideline, Rule, RuleSet, RuleType
from app.rule_types import (
    DEFAULT_SCOPE_BY_CODE,
    RULE_TYPE_CATALOGUE,
    seed_rule_types,
)
from app.rules_from_db import RuleSetCompilationError, compile_ruleset

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Для тестов правил в базе задайте TEST_DATABASE_URL PostgreSQL",
)


@pytest.fixture()
def engine() -> Iterable[Engine]:
    """Создаёт временную базу со схемой из миграций и удаляет её после теста."""
    url = make_url(TEST_DATABASE_URL or "")
    if not url.database:
        pytest.skip("TEST_DATABASE_URL не содержит имени базы")
    name = f"{url.database}_rules_{uuid4().hex[:8]}"
    if not re.fullmatch(r"[a-z0-9_]+", name):
        pytest.skip("Имя базы из TEST_DATABASE_URL не подходит для временной")
    admin_url = url.set(database="postgres").render_as_string(hide_password=False)
    temporary_url = url.set(database=name).render_as_string(hide_password=False)
    admin_engine = create_database_engine(admin_url).execution_options(
        isolation_level="AUTOCOMMIT"
    )
    try:
        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
        except ProgrammingError as exc:
            pytest.skip(f"Нет прав на создание временной базы: {exc}")
        temporary_engine = create_database_engine(temporary_url)
        try:
            apply_migrations(temporary_engine)
            yield temporary_engine
        finally:
            temporary_engine.dispose()
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin_engine.dispose()


def _store_ruleset(
    engine: Engine,
    rules: Iterable[dict[str, Any]],
    *,
    status: str = "approved",
    version: int = 1,
) -> int:
    """Кладёт правила из JSON-набора в базу и возвращает идентификатор набора."""
    seed_rule_types(engine)
    with Session(engine) as session:
        guideline = Guideline(title=f"Методичка {uuid4().hex[:6]}")
        session.add(guideline)
        session.flush()
        ruleset = RuleSet(
            guideline_id=guideline.id,
            name="Набор из методички",
            version=version,
            status="active",
        )
        session.add(ruleset)
        session.flush()
        type_ids = {
            rule_type.code: rule_type.id
            for rule_type in session.scalars(select(RuleType)).all()
        }
        for rule in rules:
            session.add(
                Rule(
                    ruleset_id=ruleset.id,
                    rule_type_id=type_ids[rule["code"]],
                    name=rule["message"],
                    params=rule["params"],
                    scope=rule["target"]["scope"],
                    quote=rule.get("quote"),
                    strictness=rule["strictness"],
                    status=status,
                )
            )
        session.commit()
        return ruleset.id


def _packaged_rules() -> list[dict[str, Any]]:
    """Возвращает правила штатного набора в виде словарей."""
    return [rule.model_dump() for rule in load_ruleset(DEFAULT_RULESET_PATH).rules]


def _comparable(rules: Iterable[dict[str, Any]]) -> list[tuple[str, str, str, str, str, str | None]]:
    """Оставляет поля, которые ERD хранит в таблице rule, в сравнимом виде."""
    return sorted(
        (
            rule["code"],
            json.dumps(rule["params"], sort_keys=True, ensure_ascii=False),
            rule["target"]["scope"],
            rule["message"],
            rule["strictness"],
            rule.get("quote"),
        )
        for rule in rules
    )


def test_catalogue_matches_engine_codes() -> None:
    """Каталог справочника совпадает с кодами движка один в один."""
    catalogue_codes = {spec.code for spec in RULE_TYPE_CATALOGUE}
    assert catalogue_codes == set(CODE_ALLOWED_SCOPES)
    assert len(RULE_TYPE_CATALOGUE) == len(catalogue_codes) == 49
    for spec in RULE_TYPE_CATALOGUE:
        assert spec.name.strip(), f"у кода {spec.code} нет названия"
        assert spec.description.strip(), f"у кода {spec.code} нет описания"
        assert spec.default_scope in CODE_ALLOWED_SCOPES[spec.code], (
            f"область {spec.default_scope!r} недопустима для кода {spec.code}"
        )


def test_seed_creates_whole_catalogue(engine: Engine) -> None:
    """Сид создаёт все виды проверок каталога."""
    created = seed_rule_types(engine)
    assert created == len(RULE_TYPE_CATALOGUE)
    with Session(engine) as session:
        assert session.scalar(select(func.count(RuleType.id))) == len(RULE_TYPE_CATALOGUE)
        codes = set(session.scalars(select(RuleType.code)).all())
    assert codes == {spec.code for spec in RULE_TYPE_CATALOGUE}


def test_seed_is_idempotent(engine: Engine) -> None:
    """Повторный сид не создаёт дублей и не меняет идентификаторы."""
    seed_rule_types(engine)
    with Session(engine) as session:
        before = dict(session.execute(select(RuleType.code, RuleType.id)).all())
    assert seed_rule_types(engine) == 0
    with Session(engine) as session:
        after = dict(session.execute(select(RuleType.code, RuleType.id)).all())
        assert session.scalar(select(func.count(RuleType.id))) == len(RULE_TYPE_CATALOGUE)
    assert before == after


def test_seed_restores_catalogue_names(engine: Engine) -> None:
    """Правки названий вручную возвращаются к каталогу, лишние коды не удаляются."""
    seed_rule_types(engine)
    with Session(engine) as session:
        rule_type = session.scalar(select(RuleType).where(RuleType.code == "font"))
        rule_type.name = "Переименовано вручную"
        session.add(RuleType(code="legacy_code", name="Старый вид проверки"))
        session.commit()
    seed_rule_types(engine)
    with Session(engine) as session:
        rule_type = session.scalar(select(RuleType).where(RuleType.code == "font"))
        assert rule_type.name == "Шрифт текста"
        assert session.scalar(
            select(func.count(RuleType.id)).where(RuleType.code == "legacy_code")
        ) == 1


def test_compiled_ruleset_matches_packaged_set(engine: Engine) -> None:
    """Набор из базы повторяет штатный JSON по полям, которые хранит ERD."""
    packaged = _packaged_rules()
    ruleset_id = _store_ruleset(engine, packaged)
    with Session(engine) as session:
        compiled = compile_ruleset(session, ruleset_id)
    compiled_rules = [rule.model_dump() for rule in compiled.rules]
    assert len(compiled_rules) == len(packaged)
    assert _comparable(compiled_rules) == _comparable(packaged)
    assert compiled.ruleset_version == "1"
    assert compiled.ruleset_id == f"db-{ruleset_id}"


def test_compiled_ruleset_drives_engine(engine: Engine) -> None:
    """Собранный из базы набор принимается движком без доработок."""
    ruleset_id = _store_ruleset(engine, _packaged_rules())
    with Session(engine) as session:
        compiled = compile_ruleset(session, ruleset_id)
    assert RuleEngine(compiled).ruleset.ruleset_id == f"db-{ruleset_id}"


def test_compilation_skips_drafts(engine: Engine) -> None:
    """Черновики не попадают в проверку, а набор без утверждённых правил не собирается."""
    packaged = _packaged_rules()
    ruleset_id = _store_ruleset(engine, packaged[:3], status="draft")
    with Session(engine) as session:
        with pytest.raises(RuleSetCompilationError, match="нет правил со статусом"):
            compile_ruleset(session, ruleset_id)
        compiled = compile_ruleset(session, ruleset_id, statuses=None)
    assert len(compiled.rules) == 3


def test_compilation_rejects_broken_params(engine: Engine) -> None:
    """Кривые params отвергаются схемой движка с понятной ошибкой."""
    ruleset_id = _store_ruleset(
        engine,
        [
            {
                "rule_id": "broken_font",
                "code": "font",
                "strictness": "error",
                "message": "Шрифт основного текста",
                "quote": None,
                "target": {"scope": "body_text"},
                "params": {"family": "Times New Roman", "size_pt": "четырнадцать"},
            }
        ],
    )
    with Session(engine) as session:
        with pytest.raises(RuleSetCompilationError, match="не прошёл проверку схемы"):
            compile_ruleset(session, ruleset_id)


def test_compilation_requires_rule_type(engine: Engine) -> None:
    """Правило без вида проверки останавливает сборку, а не пропускается молча."""
    ruleset_id = _store_ruleset(engine, _packaged_rules()[:1])
    with Session(engine) as session:
        rule = session.scalar(select(Rule).where(Rule.ruleset_id == ruleset_id))
        rule.rule_type_id = None
        session.commit()
        with pytest.raises(RuleSetCompilationError, match="не имеет известного вида"):
            compile_ruleset(session, ruleset_id)


def test_compilation_unknown_ruleset(engine: Engine) -> None:
    """Несуществующий набор даёт отдельную ошибку."""
    with Session(engine) as session:
        with pytest.raises(RuleSetCompilationError, match="не найден"):
            compile_ruleset(session, 10_000)


def test_target_roles_are_not_representable_in_erd(engine: Engine) -> None:
    """Ограничение ERD зафиксировано: exclude_roles в таблицу rule не переносится.

    В штатном наборе шесть правил заголовков исключают приложения и задание
    через `target.exclude_roles`. В ERD такого поля нет, поэтому набор из базы
    проверяет эти роли как обычные заголовки. Тест напоминает об этом при
    попытке считать паритет полным.
    """
    packaged = _packaged_rules()
    rules_with_roles = [rule for rule in packaged if rule["target"].get("exclude_roles")]
    assert len(rules_with_roles) == 6
    ruleset_id = _store_ruleset(engine, packaged)
    with Session(engine) as session:
        compiled = compile_ruleset(session, ruleset_id)
    # exclude_contexts приходит из умолчания классов правил, а роли-исключения
    # (титул, задание, приложения) в базе хранить негде — они теряются.
    assert all(not rule.target.exclude_roles for rule in compiled.rules)
    assert all(not rule.target.style_ids for rule in compiled.rules)
    assert DEFAULT_SCOPE_BY_CODE["heading_levels"] == "headings"
