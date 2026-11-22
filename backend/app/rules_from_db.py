"""Сборка набора правил движка из таблиц `ruleset`, `rule` и `rule_type`.

Правила в базе — источник истины для админки и для правил, извлечённых из
методички. Движок работает с неизменяемым `rule_engine.rules.RuleSet`, поэтому
между ними нужен компилятор: он читает строки базы, собирает из них полезную
нагрузку и отдаёт её штатной валидации движка. Любая ошибка данных (нет типа,
кривые параметры, пустой набор) превращается в `RuleSetCompilationError` с
указанием правила — молча пропускать правило нельзя.

Границы переноса заданы ERD: в таблице `rule` есть `params`, `scope`, `name`,
`strictness`, `quote`. Настройки выборки движка (`exclude_roles`, `style_ids`,
`exclude_contexts`, `min_heading_confidence`) в ERD не описаны и здесь не
восстанавливаются — это известное ограничение, см. `docs/ROADMAP.md`.
"""

from __future__ import annotations

from typing import Iterable

from pydantic import ValidationError
from rule_engine.rules import RuleSet as EngineRuleSet
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Rule, RuleSet, RuleType
from .rule_types import DEFAULT_SCOPE_BY_CODE

DEFAULT_COMPILED_STATUSES: tuple[str, ...] = ("approved", "confirmed")
"""Статусы правил, которые попадают в проверку: черновики и отклонённые — нет."""


class RuleSetCompilationError(ValueError):
    """Набор правил из базы нельзя превратить в набор движка."""


def compile_ruleset(
    session: Session,
    ruleset_id: int,
    *,
    statuses: Iterable[str] | None = DEFAULT_COMPILED_STATUSES,
) -> EngineRuleSet:
    """Собирает набор правил движка из строк базы.

    `statuses=None` отключает фильтр по статусу — так удобно смотреть черновик
    целиком. Итоговый набор проверяется штатной схемой движка, поэтому кривые
    `params` или несовместимая область не пройдут дальше этого вызова.
    """
    ruleset = session.get(RuleSet, ruleset_id)
    if ruleset is None:
        raise RuleSetCompilationError(f"Набор правил {ruleset_id} не найден")

    rows = session.execute(
        select(Rule, RuleType)
        .outerjoin(RuleType, RuleType.id == Rule.rule_type_id)
        .where(Rule.ruleset_id == ruleset_id)
        .order_by(Rule.id)
    ).all()

    allowed = None if statuses is None else {str(item) for item in statuses}
    payload_rules: list[dict[str, object]] = []
    for rule, rule_type in rows:
        if allowed is not None and (rule.status or "draft") not in allowed:
            continue
        if rule_type is None:
            raise RuleSetCompilationError(
                f"Правило {rule.id} ({rule.name!r}) не имеет известного вида проверки"
            )
        code = rule_type.code
        scope = rule.scope or DEFAULT_SCOPE_BY_CODE.get(code)
        if scope is None:
            raise RuleSetCompilationError(
                f"Правило {rule.id}: для кода {code!r} не задана область проверки"
            )
        payload_rules.append(
            {
                "rule_id": f"db-{rule.id}",
                "code": code,
                "strictness": rule.strictness or "error",
                "message": rule.name,
                "quote": rule.quote,
                "target": {"scope": scope},
                "params": rule.params or {},
            }
        )

    if not payload_rules:
        statuses_text = ", ".join(sorted(allowed)) if allowed else "любой"
        raise RuleSetCompilationError(
            f"В наборе {ruleset_id} нет правил со статусом из: {statuses_text}"
        )

    payload = {
        "ruleset_id": f"db-{ruleset.id}",
        "ruleset_version": str(ruleset.version),
        "description": ruleset.name,
        "rules": payload_rules,
    }
    try:
        return EngineRuleSet.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        raise RuleSetCompilationError(
            f"Набор {ruleset_id} не прошёл проверку схемы движка: {exc}"
        ) from exc


def compiled_rule_ids(ruleset: EngineRuleSet) -> list[str]:
    """Возвращает идентификаторы правил набора в порядке объявления."""
    return [rule.rule_id for rule in ruleset.rules]
