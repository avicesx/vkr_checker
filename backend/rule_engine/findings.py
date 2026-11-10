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


CheckStatus = Literal["pass", "fail", "not_assessed"]
Confidence = Literal["high", "medium", "low"]


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
    confidence: Optional[Confidence] = Field(
        default=None,
        description="Насколько проверка надежна: high — точные данные, low — оценка по косвенным признакам",
    )


class RuleAssessment(BaseModel):
    """Результат проверки, который не всегда выражается нарушением.

    Для проверок, которые могут не иметь данных (положение номера страницы,
    формат листа, межстрочный интервал), нужен явный статус: `pass` — требование
    выполнено, `fail` — есть нарушение (оно же попадает в `violations`),
    `not_assessed` — данных недостаточно, и движок не выдумывает вердикт.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(description="Идентификатор правила")
    kind: RuleKind
    status: CheckStatus = Field(description="pass, fail или not_assessed")
    expected_text: str = Field(default="", description="Ожидание словами")
    observed_text: str = Field(default="", description="Факт или причина, почему проверка не выполнена")
    confidence: Confidence = Field(
        default="medium",
        description="high — точные данные, medium — оценка, low — почти нет данных",
    )
    details: Dict[str, Any] = Field(default_factory=dict, description="Машинно-читаемые подробности")
    note: Optional[str] = Field(default=None, description="Пояснение для человека, если оно нужно")


class RuleCheckSummary(BaseModel):
    """Сводка проверки: сколько правил применено и сколько нарушений найдено."""

    model_config = ConfigDict(extra="forbid")

    rules_applied: int = Field(default=0, ge=0)
    objects_checked: int = Field(default=0, ge=0, description="Суммарное число проверенных объектов")
    violations_total: int = Field(default=0, ge=0)
    by_severity: Dict[str, int] = Field(default_factory=dict)
    by_kind: Dict[str, int] = Field(default_factory=dict)
    by_rule: Dict[str, int] = Field(default_factory=dict)
    not_assessed_total: int = Field(
        default=0,
        ge=0,
        description="Сколько правил не удалось проверить из-за нехватки данных",
    )


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
    assessments: List[RuleAssessment] = Field(
        default_factory=list,
        description="Статусы проверок, которые могут быть не оценены: pass, fail, not_assessed",
    )
    passed: bool = Field(description="True, если нет нарушений уровня error")

    @property
    def not_assessed(self) -> List[RuleAssessment]:
        """Проверки, которые не удалось выполнить: не хватило данных парсера."""
        return [
            assessment
            for assessment in self.assessments
            if assessment.status == "not_assessed"
        ]


def build_summary(
    violations: List[Violation],
    rules_applied: int,
    objects_checked: int,
    not_assessed_total: int = 0,
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
        not_assessed_total=not_assessed_total,
    )
