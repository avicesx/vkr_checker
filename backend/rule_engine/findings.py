"""Модели нарушений и отчета о проверке документа по набору правил.

Отчет разделяет машинно-читаемые значения (expected/found) и текстовые
формулировки для человека (expected_text/found_text): первые нужны интеграциям
и тестам, вторые — выгрузке для нормоконтролера.

Отчет детерминирован: в нем нет таймстемпов и путей, поэтому два прогона на
одном документе дают идентичный JSON, пригодный для сравнения «до/после».
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .rules import RuleKind, RuleSeverity


class DocumentRef(BaseModel):
    """Краткая ссылка на проверенный документ и его пагинацию."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    file_sha256: str
    source_format: Optional[str] = None
    pagination_source: Literal["pdf_exact", "docx_native_breaks", "geometric_fallback"] = (
        "geometric_fallback"
    )
    total_pages_est: int = Field(default=1, ge=0)


class ViolationLocation(BaseModel):
    """Адрес нарушения: блок или секция, страницы и фрагмент текста."""

    model_config = ConfigDict(extra="forbid")

    block_id: Optional[str] = Field(default=None, description="block_id абзаца, если нарушение в абзаце")
    section_idx: Optional[int] = Field(default=None, description="Индекс секции, если нарушение в секции")
    page_start_est: Optional[int] = Field(default=None, ge=1)
    page_end_est: Optional[int] = Field(default=None, ge=1)
    container_type: Optional[str] = Field(
        default=None,
        description="body, table_cell, header, footer, footnote и т.п.",
    )
    style_id: Optional[str] = Field(default=None, description="styleId абзаца-нарушителя")
    run_id: Optional[str] = Field(default=None, description="run_id рана, если нарушение в ране")
    char_start: Optional[int] = Field(default=None, ge=0)
    char_end: Optional[int] = Field(default=None, ge=0)
    snippet: str = Field(default="", description="Фрагмент текста для быстрой навигации")


class Violation(BaseModel):
    """Одно нарушение: правило, адрес, ожидание и фактическое значение."""

    model_config = ConfigDict(extra="forbid")

    violation_id: str = Field(description="Идентификатор нарушения: rule_id и адрес объекта")
    rule_id: str
    kind: RuleKind
    severity: RuleSeverity
    message: str
    requirement_ref: Optional[str] = None

    location: ViolationLocation
    expected: Dict[str, Any] = Field(default_factory=dict, description="Ожидаемые значения полей")
    found: Dict[str, Any] = Field(default_factory=dict, description="Фактические значения полей")
    expected_text: str = Field(description="Ожидание в виде текста для отчета")
    found_text: str = Field(description="Факт в виде текста для отчета")
    occurrences: int = Field(
        default=1,
        ge=1,
        description="Сколько объектов внутри адреса не соответствует правилу (раны абзаца)",
    )


class RuleCheckSummary(BaseModel):
    """Сводка проверки: сколько правил применено и сколько нарушений найдено."""

    model_config = ConfigDict(extra="forbid")

    rules_applied: int = Field(default=0, ge=0)
    objects_checked: int = Field(default=0, ge=0, description="Суммарное число проверенных объектов")
    violations_total: int = Field(default=0, ge=0)
    by_severity: Dict[str, int] = Field(default_factory=dict)
    by_kind: Dict[str, int] = Field(default_factory=dict)
    by_rule: Dict[str, int] = Field(default_factory=dict)


class RuleCheckReport(BaseModel):
    """Результат проверки документа по набору правил нормоконтроля."""

    model_config = ConfigDict(extra="forbid")

    ruleset_id: str
    ruleset_version: str
    parser_schema_version: str = Field(
        description="Версия схемы JSON парсера, на которой шел разбор"
    )
    document: DocumentRef
    summary: RuleCheckSummary = Field(default_factory=RuleCheckSummary)
    violations: List[Violation] = Field(default_factory=list)
    passed: bool = Field(description="True, если нет нарушений уровня error")


def build_summary(
    violations: List[Violation],
    rules_applied: int,
    objects_checked: int,
) -> RuleCheckSummary:
    """Собирает сводку отчета по списку нарушений."""
    by_severity: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    by_rule: Dict[str, int] = {}
    for violation in violations:
        by_severity[violation.severity] = by_severity.get(violation.severity, 0) + 1
        by_kind[violation.kind] = by_kind.get(violation.kind, 0) + 1
        by_rule[violation.rule_id] = by_rule.get(violation.rule_id, 0) + 1

    return RuleCheckSummary(
        rules_applied=rules_applied,
        objects_checked=objects_checked,
        violations_total=len(violations),
        by_severity=dict(sorted(by_severity.items())),
        by_kind=dict(sorted(by_kind.items())),
        by_rule=dict(sorted(by_rule.items())),
    )
