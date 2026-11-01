"""Движок правил нормоконтроля: проверка разбора DOCX по JSON-набору требований.

Пакет работает поверх docx_parser: движок получает ParsedDocxDocument с уже
разрешенным каскадом стилей и нормализованными единицами, применяет правила из
JSON-набора и возвращает отчет с локацией, ожиданием и фактическим значением.

Точка входа:
    RuleEngine(load_ruleset("rules.json")).check(document) -> RuleCheckReport
"""
from __future__ import annotations

from .engine import RuleEngine
from .findings import (
    DocumentRef,
    RuleCheckReport,
    RuleCheckSummary,
    Violation,
    ViolationLocation,
)
from .rules import (
    DEFAULT_RULESET_PATH,
    FontExpectation,
    FontRule,
    IndentExpectation,
    IndentRule,
    MarginExpectation,
    MarginRule,
    Rule,
    RuleSet,
    RuleTarget,
    load_default_ruleset,
    load_ruleset,
)

__all__ = [
    "DEFAULT_RULESET_PATH",
    "DocumentRef",
    "FontExpectation",
    "FontRule",
    "IndentExpectation",
    "IndentRule",
    "MarginExpectation",
    "MarginRule",
    "Rule",
    "RuleCheckReport",
    "RuleCheckSummary",
    "RuleEngine",
    "RuleSet",
    "RuleTarget",
    "Violation",
    "ViolationLocation",
    "load_default_ruleset",
    "load_ruleset",
]
