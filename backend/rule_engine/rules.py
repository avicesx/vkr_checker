"""Модели и загрузчик JSON-набора правил нормоконтроля.

Набор правил декларативен: требование вуза описывается данными, а не кодом.
Здесь живут только схемы и валидация, поэтому некорректный JSON отвергается
на этапе загрузки, а движок (engine.py) получает уже проверенные правила.

Единицы измерения фиксированы и совпадают с ParsedDocxDocument:
    кегль — пункты;
    отступы абзаца и поля страницы — миллиметры.

Область действия правила задается RuleTarget: scope выбирает класс объектов
(абзацы основного текста, ячейки таблиц, секции), а include_* расширяют выборку
заголовками, подписями, списками и пустыми абзацами. Исключения по умолчанию
действуют только для scope="body_text": остальные области берут объекты как есть.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, List, Literal, Optional, Set, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RuleKind = Literal["font", "indent", "margin"]
RuleSeverity = Literal["error", "warning", "info"]

Scope = Literal[
    "body_text",        # абзацы основного текста без заголовков, подписей и списков
    "body_paragraphs",  # все абзацы верхнего уровня w:body как есть
    "all_paragraphs",   # body + таблицы + колонтитулы + сноски
    "table_cells",      # только абзацы внутри ячеек таблиц
    "headers_footers",  # только колонтитулы
    "footnotes",        # только сноски и концевые сноски
    "sections",         # секции: поля страницы, размер листа
]

PARAGRAPH_SCOPES: frozenset[str] = frozenset(
    {
        "body_text",
        "body_paragraphs",
        "all_paragraphs",
        "table_cells",
        "headers_footers",
        "footnotes",
    }
)

DEFAULT_RULESET_PATH = Path(__file__).resolve().parent / "rules" / "vkr_test_rules.json"


class RuleTarget(BaseModel):
    """Область документа, к которой применяется правило.

    Флаги include_* действуют только для scope="body_text": это семантическая
    выборка «основного текста». Остальные области берут объекты как есть, чтобы
    правило «все ячейки таблиц» не пропускало элементы списков внутри ячеек.
    """

    model_config = ConfigDict(extra="forbid")

    scope: Scope = Field(
        default="body_text",
        description="Класс проверяемых объектов: абзацы основного текста, таблицы, секции и т.п.",
    )
    style_ids: List[str] = Field(
        default_factory=list,
        description="Ограничить выборку перечисленными styleId абзацев (пусто — без ограничения)",
    )
    include_headings: bool = Field(
        default=False,
        description="Проверять абзацы-заголовки (по умолчанию исключены)",
    )
    include_captions: bool = Field(
        default=False,
        description="Проверять абзацы-подписи рисунков, таблиц и приложений",
    )
    include_list_items: bool = Field(
        default=False,
        description="Проверять элементы списков (по умолчанию исключены)",
    )
    include_empty: bool = Field(
        default=False,
        description="Проверять пустые абзацы и абзацы только с графикой/формулой",
    )


class FontExpectation(BaseModel):
    """Ожидаемое шрифтовое оформление: гарнитура, кегль и начертание."""

    model_config = ConfigDict(extra="forbid")

    family: Optional[str] = Field(
        default=None,
        description="Гарнитура, например 'Times New Roman'; сравнение без учета регистра",
    )
    size_pt: Optional[float] = Field(
        default=None,
        ge=1.0,
        le=200.0,
        description="Кегль в пунктах",
    )
    bold: Optional[bool] = Field(default=None, description="Ожидаемое начертание: полужирный")
    italic: Optional[bool] = Field(default=None, description="Ожидаемое начертание: курсив")
    size_tolerance_pt: float = Field(
        default=0.5,
        ge=0.0,
        description="Допуск по кеглю в пунктах (Word хранит half-points)",
    )
    check_complex_script: bool = Field(
        default=False,
        description="Учитывать complex script (font_cs): источники шума, по умолчанию выключено",
    )

    @field_validator("family")
    @classmethod
    def _normalize_family(cls, value: Optional[str]) -> Optional[str]:
        """Отбрасывает пустую гарнитуру и нормализует пробелы."""
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @model_validator(mode="after")
    def _require_checked_property(self) -> "FontExpectation":
        """Требует хотя бы одно проверяемое свойство шрифта."""
        if (
            self.family is None
            and self.size_pt is None
            and self.bold is None
            and self.italic is None
        ):
            raise ValueError(
                "FontExpectation пусто: задайте хотя бы одно из family, size_pt, bold, italic"
            )
        return self


class IndentExpectation(BaseModel):
    """Ожидаемые отступы абзаца в миллиметрах."""

    model_config = ConfigDict(extra="forbid")

    first_line_mm: Optional[float] = Field(
        default=None,
        description="Красная строка; отрицательное значение означает висячий отступ",
    )
    left_mm: Optional[float] = Field(default=None, description="Отступ слева")
    right_mm: Optional[float] = Field(default=None, description="Отступ справа")
    tolerance_mm: float = Field(default=0.5, ge=0.0, description="Допуск в миллиметрах")

    @model_validator(mode="after")
    def _require_checked_property(self) -> "IndentExpectation":
        """Требует хотя бы один проверяемый отступ."""
        if self.first_line_mm is None and self.left_mm is None and self.right_mm is None:
            raise ValueError(
                "IndentExpectation пусто: задайте first_line_mm, left_mm или right_mm"
            )
        return self


class MarginExpectation(BaseModel):
    """Ожидаемые поля страницы в миллиметрах."""

    model_config = ConfigDict(extra="forbid")

    left_mm: Optional[float] = Field(default=None)
    right_mm: Optional[float] = Field(default=None)
    top_mm: Optional[float] = Field(default=None)
    bottom_mm: Optional[float] = Field(default=None)
    gutter_mm: Optional[float] = Field(default=None, description="Дополнительный корешковый отступ")
    tolerance_mm: float = Field(default=1.0, ge=0.0, description="Допуск в миллиметрах")

    @model_validator(mode="after")
    def _require_checked_property(self) -> "MarginExpectation":
        """Требует хотя бы одно проверяемое поле страницы."""
        if (
            self.left_mm is None
            and self.right_mm is None
            and self.top_mm is None
            and self.bottom_mm is None
            and self.gutter_mm is None
        ):
            raise ValueError(
                "MarginExpectation пусто: задайте хотя бы один край (left/right/top/bottom/gutter)"
            )
        return self


class _RuleBase(BaseModel):
    """Общие поля правила: идентификатор, важность, сообщение и область."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(description="Уникальный идентификатор правила внутри набора")
    severity: RuleSeverity = "error"
    message: str = Field(description="Формулировка требования для отчета")
    requirement_ref: Optional[str] = Field(
        default=None,
        description="Ссылка на пункт методических указаний",
    )
    target: RuleTarget = Field(default_factory=RuleTarget)

    @field_validator("rule_id")
    @classmethod
    def _validate_rule_id(cls, value: str) -> str:
        """Проверяет, что rule_id непустой и без пробелов по краям."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("rule_id не может быть пустым")
        return normalized

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        """Проверяет, что сообщение правила непустое."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("message не может быть пустым")
        return normalized


class FontRule(_RuleBase):
    """Правило шрифтового оформления ранов."""

    kind: Literal["font"] = "font"
    expected: FontExpectation


class IndentRule(_RuleBase):
    """Правило отступов абзаца."""

    kind: Literal["indent"] = "indent"
    expected: IndentExpectation


class MarginRule(_RuleBase):
    """Правило полей страницы секции."""

    kind: Literal["margin"] = "margin"
    expected: MarginExpectation


Rule = Annotated[Union[FontRule, IndentRule, MarginRule], Field(discriminator="kind")]


class RuleSet(BaseModel):
    """Набор правил с версией: единица поставки требований нормоконтроля."""

    model_config = ConfigDict(extra="forbid")

    ruleset_id: str = Field(description="Идентификатор набора, попадает в отчет")
    ruleset_version: str = Field(description="Версия набора правил")
    description: Optional[str] = Field(default=None, description="Назначение набора")
    rules: List[Rule] = Field(min_length=1, description="Перечень правил")

    @field_validator("ruleset_id", "ruleset_version")
    @classmethod
    def _validate_non_empty(cls, value: str) -> str:
        """Проверяет, что идентификатор и версия набора непустые."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("ruleset_id и ruleset_version не могут быть пустыми")
        return normalized

    @model_validator(mode="after")
    def _validate_rules_consistency(self) -> "RuleSet":
        """Проверяет уникальность rule_id и совместимость вида правила со scope."""
        seen: Set[str] = set()
        for rule in self.rules:
            if rule.rule_id in seen:
                raise ValueError(
                    f"rule_id '{rule.rule_id}' встречается в наборе более одного раза"
                )
            seen.add(rule.rule_id)

            if isinstance(rule, MarginRule) and rule.target.scope != "sections":
                raise ValueError(
                    f"Правило '{rule.rule_id}': margin проверяет секции, "
                    "scope должен быть 'sections'"
                )
            if not isinstance(rule, MarginRule) and rule.target.scope not in PARAGRAPH_SCOPES:
                raise ValueError(
                    f"Правило '{rule.rule_id}': scope '{rule.target.scope}' не применим к абзацам"
                )
        return self

    def get_rule(self, rule_id: str) -> Rule:
        """Возвращает правило по идентификатору или выбрасывает KeyError."""
        for rule in self.rules:
            if rule.rule_id == rule_id:
                return rule
        raise KeyError(f"Правило '{rule_id}' отсутствует в наборе '{self.ruleset_id}'")


def load_ruleset(path: str | Path) -> RuleSet:
    """Читает JSON-файл набора правил и валидирует его структуру."""
    ruleset_path = Path(path)
    raw_text = ruleset_path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{ruleset_path}: некорректный JSON ({exc})") from exc
    return RuleSet.model_validate(payload)


def load_default_ruleset() -> RuleSet:
    """Загружает тестовый набор правил, поставляемый вместе с пакетом."""
    return load_ruleset(DEFAULT_RULESET_PATH)
