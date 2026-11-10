"""Модели и загрузчик JSON-набора правил нормоконтроля.

Набор правил декларативен: требование вуза описывается данными, а не кодом.
Здесь живут только схемы и валидация, поэтому некорректный JSON отвергается
на этапе загрузки, а движок (engine.py) получает уже проверенные правила.

Единицы измерения фиксированы и совпадают с ParsedDocxDocument:
    кегль — пункты;
    отступы абзаца и поля страницы — миллиметры.

Область действия правила задается RuleTarget: scope выбирает класс объектов
(абзацы основного текста, ячейки таблиц, секции, заголовки, структура, оглавление),
а include_* расширяют выборку заголовками, подписями, списками и пустыми
абзацами. Исключения по умолчанию действуют только для scope="body_text":
остальные области берут объекты как есть.

Контекст блока (``exclude_contexts``) — отдельный фильтр выборки основного
текста: титульный лист и листинги кода набраны по другим требованиям и не
должны давать нарушения шрифта и отступов. Контекст вычисляет
`rule_engine.context` по признакам самого абзаца, поэтому правило остается
декларативным: в наборе правил перечисляются только исключаемые контексты.

Структурные правила (уровни и нумерация заголовков, обязательные разделы,
оглавление) работают по document_tree и TocContainerBlock, которые готовит
`parser.docx`: роли разделов и номера уже вычислены на этапе разбора.

Геометрические правила (`page_number_position`, `page_format`) и межстрочный
интервал (`line_spacing`) добавлены для вузовских профилей оформления: наборы
`rules/mpgu.json`, `rules/urgpu.json`, `rules/urfu.json` загружаются по
идентификатору вуза через `load_profile_ruleset()`. Набор по умолчанию
(`rules/vkr_test_rules.json`) от этих правил не зависит.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, List, Literal, Optional, Set, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RuleKind = Literal[
    "font",
    "indent",
    "margin",
    "heading_levels",
    "heading_numbering",
    "heading_format",
    "required_sections",
    "toc",
    "page_number_position",
    "page_format",
    "line_spacing",
]
RuleSeverity = Literal["error", "warning", "info"]

HeadingRole = Literal[
    "title_page",
    "task_sheet",
    "abstract_referat",
    "toc",
    "abbreviations",
    "introduction",
    "main_chapter",
    "sub_section",
    "conclusion",
    "bibliography",
    "appendix",
    "unknown",
]
"""Семантические роли заголовков; совпадают с DocumentTreeNode.semantic_role парсера."""

NUMBERLESS_HEADING_ROLES: frozenset[HeadingRole] = frozenset(
    {
        "bibliography",
        "introduction",
        "conclusion",
        "appendix",
        "title_page",
        "task_sheet",
        "abbreviations",
    }
)
"""Роли, заголовки которых в ВКР не нумеруются.

Структурные элементы работы — «ВВЕДЕНИЕ», «ЗАКЛЮЧЕНИЕ», «СПИСОК
ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ», «ТЕРМИНЫ И ОПРЕДЕЛЕНИЯ», «ПРИЛОЖЕНИЕ А», лист
задания — не имеют номера раздела, поэтому требование номера к ним не
применяется, даже если роль перечислена в
`HeadingNumberingExpectation.require_number_for_roles`: перечень ролей задает,
какие заголовки проверять, а этот набор — какие из них структурно безномерные.
Нумеруются главы (`main_chapter`) и подразделы (`sub_section`).
"""

HeadingConfidence = Literal[
    "explicit_outline_or_style",
    "structural_keyword_match",
    "heuristic_visual",
]
"""Минимальная уверенность распознавания заголовка, при которой правило его видит."""

Scope = Literal[
    "body_text",          # абзацы основного текста без заголовков, подписей и списков
    "body_paragraphs",    # все абзацы верхнего уровня w:body как есть
    "all_paragraphs",     # body + таблицы + колонтитулы + сноски
    "table_cells",        # только абзацы внутри ячеек таблиц
    "headers_footers",    # только колонтитулы
    "footnotes",          # только сноски и концевые сноски
    "sections",           # секции: поля страницы, размер листа
    "headings",           # заголовки в порядке следования (document_tree)
    "document_structure", # структура документа: состав и порядок разделов
    "toc",                # блоки оглавления и их записи
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

HEADING_SCOPES: frozenset[str] = frozenset({"headings"})
STRUCTURE_SCOPES: frozenset[str] = frozenset({"document_structure"})
TOC_SCOPES: frozenset[str] = frozenset({"toc"})

SCOPE_LABELS: dict[str, str] = {
    "body_text": "основной текст",
    "body_paragraphs": "абзацы документа",
    "all_paragraphs": "все абзацы",
    "table_cells": "ячейки таблиц",
    "headers_footers": "колонтитулы",
    "footnotes": "сноски",
    "sections": "секции",
    "headings": "заголовки",
    "document_structure": "структуру разделов",
    "toc": "оглавление",
}

DEFAULT_RULESET_PATH = Path(__file__).resolve().parent / "rules" / "vkr_test_rules.json"

PROFILE_RULESET_PATHS: dict[str, Path] = {
    "mpgu": Path(__file__).resolve().parent / "rules" / "mpgu.json",
    "urgpu": Path(__file__).resolve().parent / "rules" / "urgpu.json",
    "urfu": Path(__file__).resolve().parent / "rules" / "urfu.json",
}
"""Наборы правил профилей вузов: идентификатор вуза → файл правил."""


class RuleTarget(BaseModel):
    """Область документа, к которой применяется правило.

    Флаги include_* действуют только для scope="body_text": это семантическая
    выборка «основного текста». Остальные области берут объекты как есть, чтобы
    правило «все ячейки таблиц» не пропускало элементы списков внутри ячеек.

    exclude_contexts отсекает служебные зоны (титульный лист, листинги) в
    областях body_text и body_paragraphs; в остальных областях поле не действует.
    """

    model_config = ConfigDict(extra="forbid")

    scope: Scope = Field(
        default="body_text",
        description="Класс проверяемых объектов: абзацы основного текста, заголовки, секции, оглавление",
    )
    style_ids: List[str] = Field(
        default_factory=list,
        description="Ограничить выборку перечисленными styleId абзацев (пусто — без ограничения)",
    )
    exclude_roles: List[HeadingRole] = Field(
        default_factory=list,
        description=(
            "Не проверять заголовки этих семантических ролей вместе с их поддеревом "
            "(scope='headings' и 'document_structure'); например приложения и задание"
        ),
    )
    min_heading_confidence: HeadingConfidence = Field(
        default="heuristic_visual",
        description=(
            "Минимальная уверенность распознавания заголовка: 'explicit_outline_or_style' "
            "берет только явные заголовки, 'heuristic_visual' — все найденные"
        ),
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
    exclude_contexts: List[str] = Field(
        default_factory=lambda: ["title_page", "code_listing"],
        description=(
            "Не проверять блоки этих контекстов в областях body_text и body_paragraphs: "
            "'title_page' — титульный лист, 'code_listing' — листинг кода. "
            "Пусто — прежнее поведение без контекстной фильтрации"
        ),
    )


class FontExpectation(BaseModel):
    """Ожидаемое шрифтовое оформление: гарнитура, кегль и начертание."""

    model_config = ConfigDict(extra="forbid")

    family: Optional[str] = Field(
        default=None,
        description="Гарнитура, например 'Times New Roman'; сравнение по нормализованному имени",
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
    normalize_family: bool = Field(
        default=True,
        description=(
            "Сравнивать гарнитуру по нормализованному имени: без учета регистра, пробелов, "
            "дефисов, subset-префикса 'XXXXXX+' и стилевых маркеров "
            "(MT/PS/PSMT/Bold/Italic/Regular/Roman). false — прежнее строгое сравнение"
        ),
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


class HeadingLevelsExpectation(BaseModel):
    """Ожидаемая иерархия заголовков: стартовый уровень, пропуски и глубина."""

    model_config = ConfigDict(extra="forbid")

    first_level: int = Field(
        default=1,
        ge=1,
        le=9,
        description="Уровень, с которого начинается нумерация заголовков документа",
    )
    allow_skipped_levels: bool = Field(
        default=False,
        description="Разрешить перескок уровня, например 1 -> 3 (по умолчанию запрещен)",
    )
    max_level: Optional[int] = Field(
        default=None,
        ge=1,
        le=9,
        description="Максимальная глубина вложенности заголовков (пусто — без ограничения)",
    )

    @model_validator(mode="after")
    def _validate_max_level(self) -> "HeadingLevelsExpectation":
        """Проверяет, что ограничение глубины не противоречит стартовому уровню."""
        if self.max_level is not None and self.max_level < self.first_level:
            raise ValueError("max_level не может быть меньше first_level")
        return self


class HeadingNumberingExpectation(BaseModel):
    """Ожидаемая нумерация заголовков: последовательность, родитель, глубина, запись номера."""

    model_config = ConfigDict(extra="forbid")

    check_sequence: bool = Field(
        default=True,
        description="Требовать сквозную последовательность номеров среди соседних заголовков: 1, 2, 3",
    )
    check_parent_number: bool = Field(
        default=True,
        description="Требовать, чтобы номер подраздела начинался с номера родителя: 1.2 внутри 1",
    )
    check_depth_matches_level: bool = Field(
        default=True,
        description="Требовать, чтобы глубина номера совпадала с уровнем заголовка: 1.1 — второй уровень",
    )
    number_ending: Literal["any", "with_dot", "without_dot"] = Field(
        default="any",
        description="Требование к точке после последней цифры номера",
    )
    require_number_for_roles: List[HeadingRole] = Field(
        default_factory=lambda: ["main_chapter", "sub_section"],
        description=(
            "Заголовки этих ролей обязаны иметь номер (пусто — нумерация не обязательна). "
            "Роли из NUMBERLESS_HEADING_ROLES (введение, заключение, список литературы, "
            "приложения, лист задания, перечень сокращений) номер не требуют"
        ),
    )

    @model_validator(mode="after")
    def _require_checked_property(self) -> "HeadingNumberingExpectation":
        """Требует хотя бы одну проверку, иначе правило ничего не проверяет."""
        if (
            not self.check_sequence
            and not self.check_parent_number
            and not self.check_depth_matches_level
            and self.number_ending == "any"
            and not self.require_number_for_roles
        ):
            raise ValueError(
                "HeadingNumberingExpectation пусто: включите хотя бы одну проверку нумерации"
            )
        return self


class HeadingFormatExpectation(BaseModel):
    """Ожидаемое оформление заголовка: точка в конце, регистр и выравнивание."""

    model_config = ConfigDict(extra="forbid")

    trailing_dot: Literal["any", "forbid", "require"] = Field(
        default="any",
        description="Точка в конце текста заголовка: 'forbid' — не допускается, 'require' — обязательна",
    )
    trailing_dot_in_number: Literal["any", "forbid", "require"] = Field(
        default="any",
        description="Точка после номера заголовка (например '1.' в начале строки)",
    )
    case: Literal["any", "uppercase", "capitalized", "not_uppercase"] = Field(
        default="any",
        description=(
            "'uppercase' — весь заголовок прописными, 'capitalized' — с прописной буквы "
            "и не целиком прописными, 'not_uppercase' — не целиком прописными"
        ),
    )
    alignment: Optional[Literal["left", "center", "right", "both", "distribute"]] = Field(
        default=None,
        description="Ожидаемое выравнивание абзаца заголовка (effective_props.alignment)",
    )

    @model_validator(mode="after")
    def _require_checked_property(self) -> "HeadingFormatExpectation":
        """Требует хотя бы одно проверяемое свойство оформления."""
        if (
            self.trailing_dot == "any"
            and self.trailing_dot_in_number == "any"
            and self.case == "any"
            and self.alignment is None
        ):
            raise ValueError(
                "HeadingFormatExpectation пусто: задайте trailing_dot, "
                "trailing_dot_in_number, case или alignment"
            )
        return self


class SectionSpec(BaseModel):
    """Ожидаемый раздел документа: роль, допустимые названия и обязательность."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="Название раздела для отчета, например 'введение'")
    role: Optional[HeadingRole] = Field(
        default=None,
        description="Семантическая роль заголовка из document_tree (пусто — роль не важна)",
    )
    titles: List[str] = Field(
        default_factory=list,
        description=(
            "Допустимые названия заголовка; сравнение без учета регистра, точки и лишних "
            "пробелов. Пусто — раздел определяется только по роли"
        ),
    )
    required: bool = Field(default=True, description="Раздел обязателен в документе")

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        """Проверяет, что метка раздела непустая."""
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("label раздела не может быть пустым")
        return normalized

    @field_validator("titles")
    @classmethod
    def _normalize_titles(cls, value: List[str]) -> List[str]:
        """Убирает пустые названия и нормализует пробелы."""
        normalized = [" ".join(title.split()) for title in value]
        return [title for title in normalized if title]

    @model_validator(mode="after")
    def _require_matcher(self) -> "SectionSpec":
        """Требует роль или название, иначе раздел нельзя найти."""
        if self.role is None and not self.titles:
            raise ValueError("SectionSpec пусто: задайте role или titles")
        return self


class RequiredSectionsExpectation(BaseModel):
    """Ожидаемый состав и порядок разделов документа."""

    model_config = ConfigDict(extra="forbid")

    sections: List[SectionSpec] = Field(
        min_length=1,
        description="Разделы в требуемом порядке следования",
    )

    @model_validator(mode="after")
    def _validate_unique_labels(self) -> "RequiredSectionsExpectation":
        """Проверяет уникальность меток разделов: они попадают в violation_id."""
        labels = [section.label for section in self.sections]
        duplicates = {label for label in labels if labels.count(label) > 1}
        if duplicates:
            raise ValueError(f"Метки разделов должны быть уникальны: {sorted(duplicates)}")
        return self


class TocExpectation(BaseModel):
    """Ожидаемое оглавление: наличие, автоматизация, записи и их сверка с заголовками."""

    model_config = ConfigDict(extra="forbid")

    require_toc: bool = Field(default=True, description="Требовать наличие оглавления")
    require_automated: bool = Field(
        default=False,
        description="Требовать автоматическое оглавление (поле TOC или SDT) вместо ручного списка",
    )
    require_page_numbers: bool = Field(
        default=True,
        description="Требовать номер страницы в каждой записи оглавления",
    )
    check_page_numbers: bool = Field(
        default=False,
        description="Сверять номера страниц с фактическими; имеет смысл только при точной пагинации",
    )
    check_titles: bool = Field(
        default=True,
        description="Сверять названия записей с найденными заголовками документа",
    )
    require_dot_leader: bool = Field(
        default=False,
        description="Требовать отточие между названием и номером страницы",
    )
    check_completeness: Optional[bool] = Field(
        default=None,
        description=(
            "Требовать, чтобы обязательные разделы были перечислены в оглавлении; "
            "по умолчанию включается, если задан required_entries"
        ),
    )
    required_entries: List[SectionSpec] = Field(
        default_factory=list,
        description=(
            "Разделы, которые обязаны быть в оглавлении: роль и/или допустимые названия. "
            "Названия отделяют структурный раздел «Заключение» от главы «Выводы»"
        ),
    )
    max_depth: Optional[int] = Field(
        default=None,
        ge=1,
        le=9,
        description=(
            "До какой глубины заголовки обязаны быть в оглавлении: при 2 сверяются "
            "главы и разделы, подразделы глубже второго уровня пропуском не считаются. "
            "Пусто — все уровни. Записи оглавления глубже этой глубины допускаются"
        ),
    )

    @model_validator(mode="after")
    def _require_checked_property(self) -> "TocExpectation":
        """Требует хотя бы одну проверку и выводит полноту из состава разделов."""
        if self.check_completeness is None:
            self.check_completeness = bool(self.required_entries)
        if (
            not self.require_toc
            and not self.require_automated
            and not self.require_page_numbers
            and not self.check_page_numbers
            and not self.check_titles
            and not self.require_dot_leader
            and not self.check_completeness
        ):
            raise ValueError("TocExpectation пусто: включите хотя бы одну проверку оглавления")
        if self.check_completeness and not self.required_entries:
            raise ValueError(
                "TocExpectation: check_completeness требует required_entries, "
                "иначе полноту не с чем сверять"
            )
        if self.max_depth is not None and not self.check_completeness:
            raise ValueError(
                "TocExpectation: max_depth задает глубину сверки полноты и требует "
                "check_completeness с required_entries"
            )
        return self


class PageNumberPositionExpectation(BaseModel):
    """Ожидаемое положение печатного номера страницы.

    В DOCX номер страницы живет в колонтитуле, поэтому проверяются две
    наблюдаемые вещи: в каком колонтитуле напечатан номер (верхний или нижний)
    и как выровнен абзац с номером. Положение ``top_center`` из профилей
    вузов соответствует паре ``zone="header"`` и ``alignment="center"``,
    ``bottom_center`` — паре ``zone="footer"`` и ``alignment="center"``.
    """

    model_config = ConfigDict(extra="forbid")

    zone: Literal["header", "footer"] = Field(
        description="Колонтитул с номером: 'header' — верхний, 'footer' — нижний",
    )
    alignment: Literal["left", "center", "right", "both", "distribute"] = Field(
        default="center",
        description="Ожидаемое выравнивание абзаца с номером страницы",
    )


class PageFormatExpectation(BaseModel):
    """Ожидаемый формат листа в миллиметрах.

    Значения по умолчанию описывают A4 в книжной ориентации; допуск задается
    отдельно и по умолчанию равен ±1 мм на каждую сторону.
    """

    model_config = ConfigDict(extra="forbid")

    width_mm: float = Field(default=210.0, gt=0.0, le=2000.0, description="Ширина листа, мм")
    height_mm: float = Field(default=297.0, gt=0.0, le=2000.0, description="Высота листа, мм")
    tolerance_mm: float = Field(
        default=1.0,
        ge=0.0,
        description="Допуск к ширине и высоте листа, мм",
    )
    orientation: Literal["portrait", "landscape", "any"] = Field(
        default="portrait",
        description="Ориентация листа: книжная, альбомная или любая",
    )

    @model_validator(mode="after")
    def _validate_orientation(self) -> "PageFormatExpectation":
        """Проверяет, что размеры листа согласуются с заявленной ориентацией."""
        if self.orientation == "portrait" and self.width_mm >= self.height_mm:
            raise ValueError(
                "PageFormatExpectation: для книжной ориентации ширина должна быть "
                "меньше высоты"
            )
        if self.orientation == "landscape" and self.width_mm <= self.height_mm:
            raise ValueError(
                "PageFormatExpectation: для альбомной ориентации ширина должна быть "
                "больше высоты"
            )
        return self


class LineSpacingExpectation(BaseModel):
    """Ожидаемый межстрочный интервал как множитель кегля.

    Множитель 1.5 означает полуторный интервал, 1.0 — одинарный. Допуск
    задается в тех же единицах, что и множитель: профили вузов берут ±0.25.
    """

    model_config = ConfigDict(extra="forbid")

    ratio: float = Field(
        default=1.5,
        gt=0.0,
        le=10.0,
        description="Множитель межстрочного интервала: 1.5 — полуторный",
    )
    tolerance: float = Field(
        default=0.25,
        ge=0.0,
        le=5.0,
        description="Допуск по множителю интервала",
    )


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


class HeadingLevelsRule(_RuleBase):
    """Правило иерархии уровней заголовков."""

    kind: Literal["heading_levels"] = "heading_levels"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="headings"))
    expected: HeadingLevelsExpectation


class HeadingNumberingRule(_RuleBase):
    """Правило нумерации заголовков."""

    kind: Literal["heading_numbering"] = "heading_numbering"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="headings"))
    expected: HeadingNumberingExpectation


class HeadingFormatRule(_RuleBase):
    """Правило оформления заголовка: точка, регистр, выравнивание."""

    kind: Literal["heading_format"] = "heading_format"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="headings"))
    expected: HeadingFormatExpectation


class RequiredSectionsRule(_RuleBase):
    """Правило состава и порядка разделов документа."""

    kind: Literal["required_sections"] = "required_sections"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="document_structure"))
    expected: RequiredSectionsExpectation


class TocRule(_RuleBase):
    """Правило оглавления и его сверки с заголовками."""

    kind: Literal["toc"] = "toc"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="toc"))
    expected: TocExpectation


class PageNumberPositionRule(_RuleBase):
    """Правило положения печатного номера страницы в колонтитуле."""

    kind: Literal["page_number_position"] = "page_number_position"
    target: RuleTarget = Field(
        default_factory=lambda: RuleTarget(scope="headers_footers")
    )
    expected: PageNumberPositionExpectation


class PageFormatRule(_RuleBase):
    """Правило формата листа секции."""

    kind: Literal["page_format"] = "page_format"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="sections"))
    expected: PageFormatExpectation


class LineSpacingRule(_RuleBase):
    """Правило межстрочного интервала абзаца."""

    kind: Literal["line_spacing"] = "line_spacing"
    expected: LineSpacingExpectation


Rule = Annotated[
    Union[
        FontRule,
        IndentRule,
        MarginRule,
        HeadingLevelsRule,
        HeadingNumberingRule,
        HeadingFormatRule,
        RequiredSectionsRule,
        TocRule,
        PageNumberPositionRule,
        PageFormatRule,
        LineSpacingRule,
    ],
    Field(discriminator="kind"),
]

KIND_ALLOWED_SCOPES: dict[str, frozenset[str]] = {
    "font": PARAGRAPH_SCOPES | HEADING_SCOPES,
    "indent": PARAGRAPH_SCOPES | HEADING_SCOPES,
    "margin": frozenset({"sections"}),
    "heading_levels": HEADING_SCOPES,
    "heading_numbering": HEADING_SCOPES,
    "heading_format": HEADING_SCOPES,
    "required_sections": STRUCTURE_SCOPES,
    "toc": TOC_SCOPES,
    "page_number_position": frozenset({"headers_footers"}),
    "page_format": frozenset({"sections"}),
    "line_spacing": PARAGRAPH_SCOPES | HEADING_SCOPES,
}
"""Какие scope допустимы для каждого вида правила (проверяется при загрузке набора)."""

HEADING_STRUCTURE_ROLE_SCOPES: frozenset[str] = HEADING_SCOPES | STRUCTURE_SCOPES


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

            allowed_scopes = KIND_ALLOWED_SCOPES[rule.kind]
            if isinstance(rule, MarginRule) and rule.target.scope != "sections":
                raise ValueError(
                    f"Правило '{rule.rule_id}': margin проверяет секции, "
                    "scope должен быть 'sections'"
                )
            if isinstance(rule, (FontRule, IndentRule)) and rule.target.scope not in allowed_scopes:
                raise ValueError(
                    f"Правило '{rule.rule_id}': scope '{rule.target.scope}' не применим к абзацам"
                )
            if rule.target.scope not in allowed_scopes:
                raise ValueError(
                    f"Правило '{rule.rule_id}': kind '{rule.kind}' проверяет "
                    f"{SCOPE_LABELS.get(rule.target.scope, rule.target.scope)}, "
                    f"scope должен быть одним из: {sorted(allowed_scopes)}"
                )
            if (
                rule.target.exclude_roles
                and rule.target.scope not in HEADING_STRUCTURE_ROLE_SCOPES
            ):
                raise ValueError(
                    f"Правило '{rule.rule_id}': exclude_roles применим только к scope "
                    f"'headings' и 'document_structure'"
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


def available_profiles() -> tuple[str, ...]:
    """Возвращает идентификаторы вузовских профилей оформления."""
    return tuple(PROFILE_RULESET_PATHS)


def load_profile_ruleset(profile_id: str) -> RuleSet:
    """Загружает набор правил профиля вуза по его идентификатору.

    Идентификатор сравнивается без учета регистра и пробелов по краям.
    Неизвестный профиль — ошибка, а не молчаливый переход к набору по
    умолчанию: проверять работу чужими требованиями опаснее, чем упасть.
    """
    if not isinstance(profile_id, str):
        raise ValueError(f"Идентификатор профиля должен быть строкой: {profile_id!r}")
    normalized = profile_id.strip().casefold()
    ruleset_path = PROFILE_RULESET_PATHS.get(normalized)
    if ruleset_path is None:
        known = ", ".join(sorted(PROFILE_RULESET_PATHS))
        raise ValueError(
            f"Неизвестный профиль оформления: {profile_id!r}; доступны: {known}"
        )
    return load_ruleset(ruleset_path)
