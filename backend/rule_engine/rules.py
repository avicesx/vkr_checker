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
интервал (`line_spacing`) входят и в набор по умолчанию
(`rules/vkr_test_rules.json`), и в вузовские профили оформления: наборы
`rules/mpgu.json`, `rules/urgpu.json`, `rules/urfu.json` загружаются по
идентификатору вуза через `load_profile_ruleset()` и отличаются ожидаемыми
значениями (например, колонтитулом номера страницы).
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
    "bibliography_link",
    "bibliography_cited",
    "bibliography_format",
    "caption_numbering",
    "caption_format",
    "caption_reference",
    "alignment",
    "widow_control",
    "keep_next",
    "heading_page_break",
    "document_volume",
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
    "bibliography",       # список литературы и ссылки на его источники
    "captions",           # подписи рисунков, таблиц и приложений
    "document",           # документ целиком: объем, количество страниц и слов
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
BIBLIOGRAPHY_SCOPES: frozenset[str] = frozenset({"bibliography"})
CAPTION_SCOPES: frozenset[str] = frozenset({"captions"})

ALIGNMENT_SCOPES: frozenset[str] = CAPTION_SCOPES | frozenset({"table_cells", "body_text"})
"""Области, где выравнивание абзаца осмысленно: подписи, ячейки таблиц и текст."""

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
    "bibliography": "список литературы",
    "captions": "подписи рисунков и таблиц",
    "document": "документ",
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
        default_factory=lambda: [
            "title_page",
            "code_listing",
            "structural_title",
            "appendix_label",
            "caption_like",
            "table_title",
            "text_formula",
        ],
        description=(
            "Не проверять блоки этих контекстов в областях body_text и body_paragraphs: "
            "'title_page' — титульный лист, 'code_listing' — листинг кода, "
            "'structural_title' — название элемента структуры («Содержание», «Приложение»), "
            "'appendix_label' — метка продолжения приложения, 'caption_like' — подпись "
            "рисунка/таблицы по текстовому маркеру, 'table_title' — заголовок таблицы, "
            "'text_formula' — формула, набранная текстом. "
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
    """Ожидаемое оформление заголовка: шрифт, точка, регистр и выравнивание."""

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
    font: Optional[FontExpectation] = Field(
        default=None,
        description=(
            "Ожидаемое шрифтовое оформление заголовка: гарнитура, кегль, полужирность. "
            "Сравниваются видимые раны заголовка, а при их отсутствии — знак абзаца"
        ),
    )

    @model_validator(mode="after")
    def _require_checked_property(self) -> "HeadingFormatExpectation":
        """Требует хотя бы одно проверяемое свойство оформления."""
        if (
            self.trailing_dot == "any"
            and self.trailing_dot_in_number == "any"
            and self.case == "any"
            and self.alignment is None
            and self.font is None
        ):
            raise ValueError(
                "HeadingFormatExpectation пусто: задайте trailing_dot, "
                "trailing_dot_in_number, case, alignment или font"
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


class WidowControlExpectation(BaseModel):
    """Требование контролировать висячие строки в абзацах.

    Абзац считается разделенным между страницами, если разрыв страницы попадает
    внутрь его текста. При выключенном контроле первая или последняя строка
    такого абзаца может остаться на соседней странице в одиночестве.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Ожидаемое значение widowControl: true — висячие строки запрещены",
    )


class KeepNextExpectation(BaseModel):
    """Требование не отрывать заголовок от следующего за ним текста.

    Проверяется свойство keepNext абзаца заголовка: Word переносит заголовок на
    следующую страницу вместе с первым абзацем, если свойство включено.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Ожидаемое значение keepNext: true — заголовок не отрывается от текста",
    )
    roles: List[HeadingRole] = Field(
        default_factory=lambda: ["main_chapter", "sub_section"],
        description="Роли заголовков, к которым применяется требование",
    )

    @field_validator("roles")
    @classmethod
    def _validate_roles(cls, value: List[HeadingRole]) -> List[HeadingRole]:
        """Проверяет, что перечень ролей непустой."""
        if not value:
            raise ValueError("KeepNextExpectation: перечень roles не может быть пустым")
        return value


class HeadingPageBreakExpectation(BaseModel):
    """Требование начинать структурный раздел с новой страницы.

    Начало раздела сравнивается со страницей, на которой заканчивается
    предыдущий непустой абзац документа: заголовок обязан оказаться на
    следующей странице.
    """

    model_config = ConfigDict(extra="forbid")

    roles: List[HeadingRole] = Field(
        default_factory=lambda: [
            "main_chapter",
            "introduction",
            "conclusion",
            "bibliography",
            "appendix",
        ],
        description="Роли заголовков, которые обязаны начинаться с новой страницы",
    )

    @field_validator("roles")
    @classmethod
    def _validate_roles(cls, value: List[HeadingRole]) -> List[HeadingRole]:
        """Проверяет, что перечень ролей непустой."""
        if not value:
            raise ValueError("HeadingPageBreakExpectation: перечень roles не может быть пустым")
        return value


class DocumentVolumeExpectation(BaseModel):
    """Требование к объему работы в страницах и словах.

    Границы задаются методическими указаниями и различаются для бакалаврской и
    магистерской ВКР, поэтому значения настраиваются правилом. Число слов
    берется из статистики DOCX или из извлеченного текста PDF; если парсер его
    не посчитал, проверка слов честно пропускается.
    """

    model_config = ConfigDict(extra="forbid")

    min_pages: Optional[int] = Field(default=None, ge=1, description="Минимум страниц")
    max_pages: Optional[int] = Field(default=None, ge=1, description="Максимум страниц")
    min_words: Optional[int] = Field(default=None, ge=1, description="Минимум слов")
    max_words: Optional[int] = Field(default=None, ge=1, description="Максимум слов")

    @model_validator(mode="after")
    def _validate_bounds(self) -> "DocumentVolumeExpectation":
        """Требует хотя бы одну границу и согласованность минимума с максимумом."""
        if (
            self.min_pages is None
            and self.max_pages is None
            and self.min_words is None
            and self.max_words is None
        ):
            raise ValueError(
                "DocumentVolumeExpectation пусто: задайте min_pages, max_pages, "
                "min_words или max_words"
            )
        if (
            self.min_pages is not None
            and self.max_pages is not None
            and self.min_pages > self.max_pages
        ):
            raise ValueError("DocumentVolumeExpectation: min_pages больше max_pages")
        if (
            self.min_words is not None
            and self.max_words is not None
            and self.min_words > self.max_words
        ):
            raise ValueError("DocumentVolumeExpectation: min_words больше max_words")
        return self


BibliographyCategory = Literal[
    "book_monograph",
    "journal_article",
    "conference_paper",
    "electronic_resource",
    "normative_act_law",
    "standard_gost",
    "dissertation_autoref",
    "patent",
    "unknown",
]
"""Категория источника; совпадает с BibliographyEntry.detected_category парсера."""


class BibliographyLinkExpectation(BaseModel):
    """Требование к ссылкам на источники списка литературы.

    Числовая ссылка вида ``[3]`` разрешается в запись списка по номеру: пустой
    ``resolved_entity_ids`` у упоминания означает, что источника с таким номером
    в списке нет. Конфигурация, отключающая проверку, отвергается на загрузке:
    правило, которое ничего не проверяет, — ошибка набора, а не «мягкий режим».
    """

    model_config = ConfigDict(extra="forbid")

    allow_unresolved: bool = Field(
        default=False,
        description="Разрешить ссылки на источники, которых нет в списке литературы",
    )

    @model_validator(mode="after")
    def _require_checked_property(self) -> "BibliographyLinkExpectation":
        """Запрещает набор, в котором проверка ссылок отключена."""
        if self.allow_unresolved:
            raise ValueError(
                "BibliographyLinkExpectation пусто: allow_unresolved=True отключает "
                "проверку ссылок на источники"
            )
        return self


class BibliographyCitedExpectation(BaseModel):
    """Требование цитирования источников: каждая запись списка упомянута в тексте.

    Запись с ``is_cited_in_text=False`` — источник, на который в работе нет ни
    одной ссылки. ``require_all_cited=False`` отключает проверку и поэтому
    отвергается: правило без проверки бесполезно.
    """

    model_config = ConfigDict(extra="forbid")

    require_all_cited: bool = Field(
        default=True,
        description="Требовать ссылку в тексте на каждый источник списка литературы",
    )

    @model_validator(mode="after")
    def _require_checked_property(self) -> "BibliographyCitedExpectation":
        """Запрещает набор, в котором проверка цитирования отключена."""
        if not self.require_all_cited:
            raise ValueError(
                "BibliographyCitedExpectation пусто: require_all_cited=False отключает "
                "проверку цитирования источников"
            )
        return self


class BibliographyFormatExpectation(BaseModel):
    """Требования к оформлению записи списка литературы.

    Проверяются разобранные поля ``BibliographyEntry``. ``categories``
    ограничивает выборку категориями источников (``None`` — все записи):
    например, URL и дата обращения обязательны для электронного ресурса, но не
    для книги. Каждый включенный флаг — отдельное требование, поэтому запись
    без URL и без года дает два нарушения, а не одно общее.
    """

    model_config = ConfigDict(extra="forbid")

    categories: Optional[List[BibliographyCategory]] = Field(
        default=None,
        description="Категории источников, к которым применяется правило (None — все)",
    )
    require_url: bool = Field(default=False, description="Требовать URL источника")
    require_access_date: bool = Field(
        default=False,
        description="Требовать дату обращения к источнику",
    )
    require_year: bool = Field(default=False, description="Требовать год издания")
    require_pages: bool = Field(default=False, description="Требовать объём в страницах")
    require_isbn_or_issn: bool = Field(
        default=False,
        description="Требовать ISBN или ISSN издания",
    )
    require_double_slash: bool = Field(
        default=False,
        description="Требовать разделитель «//» перед названием источника (статья, глава)",
    )
    require_text_type_designation: bool = Field(
        default=False,
        description="Требовать обозначение вида издания: [Текст], [Электронный ресурс] и т.п.",
    )

    @field_validator("categories")
    @classmethod
    def _validate_categories(
        cls, value: Optional[List[BibliographyCategory]]
    ) -> Optional[List[BibliographyCategory]]:
        """Убирает повторы категорий и отвергает пустой перечень."""
        if value is None:
            return None
        unique: List[BibliographyCategory] = []
        for category in value:
            if category not in unique:
                unique.append(category)
        if not unique:
            raise ValueError(
                "categories пуст: укажите категории источников или None (все категории)"
            )
        return unique

    @model_validator(mode="after")
    def _require_checked_property(self) -> "BibliographyFormatExpectation":
        """Требует хотя бы одно проверяемое требование к записи."""
        if not any(
            (
                self.require_url,
                self.require_access_date,
                self.require_year,
                self.require_pages,
                self.require_isbn_or_issn,
                self.require_double_slash,
                self.require_text_type_designation,
            )
        ):
            raise ValueError(
                "BibliographyFormatExpectation пусто: включите хотя бы одно требование "
                "(require_url, require_access_date, require_year, require_pages, "
                "require_isbn_or_issn, require_double_slash, require_text_type_designation)"
            )
        return self


class CaptionNumberingExpectation(BaseModel):
    """Требование к нумерации подписей: сквозная арабская, без дублей и пропусков.

    ``entity_type`` выбирает вид объектов: рисунки, таблицы, приложения или все
    вместе. Нумерация приложений буквенная, поэтому проверяется только на
    арабских номерах: буквенные номера участвуют в подсчете объектов, но не в
    последовательности. Разрешения (``allow_*``) нужны для документов, где
    разрывы нумерации допустимы методикой; конфигурация, разрешающая всё сразу,
    ничего не проверяет и отвергается.
    """

    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["figure", "table", "appendix", "any"] = Field(
        default="any",
        description="Вид объектов: рисунок, таблица, приложение или все вместе",
    )
    scheme: Literal["continuous_arabic"] = Field(
        default="continuous_arabic",
        description="Требуемая схема нумерации: сквозная арабская",
    )
    allow_gaps: bool = Field(default=False, description="Разрешить пропуски номеров")
    allow_duplicates: bool = Field(default=False, description="Разрешить повтор номеров")
    allow_out_of_order: bool = Field(
        default=False,
        description="Разрешить нарушение порядка номеров (номер меньше предыдущего)",
    )

    @model_validator(mode="after")
    def _require_checked_property(self) -> "CaptionNumberingExpectation":
        """Требует хотя бы одну включенную проверку нумерации."""
        if self.allow_gaps and self.allow_duplicates and self.allow_out_of_order:
            raise ValueError(
                "CaptionNumberingExpectation пусто: разрешены дубли, пропуски и порядок, "
                "проверять нечего"
            )
        return self


class CaptionFormatExpectation(BaseModel):
    """Требования к оформлению подписи: префикс, разделитель, точка, регистр.

    ``separator`` задает вид разделителя между номером и названием: ``dash`` —
    тире, ``dot`` — точка, ``space`` — пробел, ``none`` — разделителя нет.
    Пробельный разделитель разбор подписи хранит как ``separator=None`` при
    непустом названии, поэтому ``space`` означает и обычный пробел, и
    «номер, затем название» без пунктуации; ``none`` — подпись без названия.
    Разделитель, который не тире и не точка (``:``, ``|``), тоже считается
    пробельным: подпись с ним читается как «номер, затем название».
    ``prefix_words`` — допустимые слова-префиксы («Рисунок», «Рис.», «Таблица»);
    сравнение без учета регистра и завершающей точки, поэтому ``рис.`` и ``Рис``
    совпадают, а «Рисунок» и «Рис.» — разные варианты и перечисляются оба.
    ``uppercase`` проверяет регистр текста подписи по самому абзацу подписи.
    """

    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["figure", "table", "appendix", "any"] = Field(
        default="any",
        description="Вид объектов: рисунок, таблица, приложение или все вместе",
    )
    separator: Optional[Literal["dash", "dot", "space", "none"]] = Field(
        default=None,
        description="Вид разделителя между номером и названием (None — не проверять)",
    )
    require_trailing_dot: Optional[bool] = Field(
        default=None,
        description="Требовать точку в конце подписи (None — не проверять)",
    )
    prefix_words: Optional[List[str]] = Field(
        default=None,
        description="Допустимые слова-префиксы подписи (None — не проверять)",
    )
    uppercase: Optional[bool] = Field(
        default=None,
        description="Требовать текст подписи прописными буквами (None — не проверять)",
    )

    @field_validator("prefix_words")
    @classmethod
    def _normalize_prefix_words(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        """Убирает пустые варианты и лишние пробелы в словах-префиксах."""
        if value is None:
            return None
        normalized = [" ".join(word.split()) for word in value]
        result = [word for word in normalized if word]
        if not result:
            raise ValueError(
                "prefix_words пуст: перечислите хотя бы один допустимый префикс подписи"
            )
        return result

    @model_validator(mode="after")
    def _require_checked_property(self) -> "CaptionFormatExpectation":
        """Требует хотя бы одно проверяемое свойство подписи."""
        if (
            self.separator is None
            and self.require_trailing_dot is None
            and self.prefix_words is None
            and self.uppercase is None
        ):
            raise ValueError(
                "CaptionFormatExpectation пусто: задайте separator, require_trailing_dot, "
                "prefix_words или uppercase"
            )
        return self


class CaptionReferenceExpectation(BaseModel):
    """Требование ссылки на объект в тексте: у подписи есть упоминание в тексте.

    ``required=False`` отключает проверку и поэтому отвергается. Приложения в
    выборку не входят: ссылка «приложение А» проверяется отдельно от ссылок на
    рисунки и таблицы.
    """

    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["figure", "table", "any"] = Field(
        default="any",
        description="Вид объектов: рисунок, таблица или оба вида",
    )
    required: bool = Field(
        default=True,
        description="Требовать упоминание объекта в тексте работы",
    )

    @model_validator(mode="after")
    def _require_checked_property(self) -> "CaptionReferenceExpectation":
        """Запрещает набор, в котором проверка ссылок отключена."""
        if not self.required:
            raise ValueError(
                "CaptionReferenceExpectation пусто: required=False отключает "
                "проверку ссылок на объекты"
            )
        return self


class AlignmentExpectation(BaseModel):
    """Требование выравнивания абзаца.

    ``justify`` — выравнивание по ширине: в разборе DOCX оно записано как
    ``both`` (двустороннее), поэтому оба имени считаются одним требованием.
    """

    model_config = ConfigDict(extra="forbid")

    alignment: Literal["left", "center", "right", "justify"] = Field(
        description="Ожидаемое выравнивание: left, center, right или justify (по ширине)",
    )

    @field_validator("alignment", mode="before")
    @classmethod
    def _normalize_alignment(cls, value: object) -> object:
        """Приводит выравнивание к нижнему регистру и убирает пробелы по краям."""
        if isinstance(value, str):
            return value.strip().casefold()
        return value


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


class BibliographyLinkRule(_RuleBase):
    """Правило разрешения ссылок на источники списка литературы."""

    kind: Literal["bibliography_link"] = "bibliography_link"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="bibliography"))
    expected: BibliographyLinkExpectation


class BibliographyCitedRule(_RuleBase):
    """Правило цитирования источников: каждая запись списка упомянута в тексте."""

    kind: Literal["bibliography_cited"] = "bibliography_cited"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="bibliography"))
    expected: BibliographyCitedExpectation


class BibliographyFormatRule(_RuleBase):
    """Правило оформления записей списка литературы."""

    kind: Literal["bibliography_format"] = "bibliography_format"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="bibliography"))
    expected: BibliographyFormatExpectation


class CaptionNumberingRule(_RuleBase):
    """Правило нумерации подписей рисунков, таблиц и приложений."""

    kind: Literal["caption_numbering"] = "caption_numbering"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="captions"))
    expected: CaptionNumberingExpectation


class CaptionFormatRule(_RuleBase):
    """Правило оформления подписи: префикс, разделитель, точка, регистр."""

    kind: Literal["caption_format"] = "caption_format"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="captions"))
    expected: CaptionFormatExpectation


class CaptionReferenceRule(_RuleBase):
    """Правило ссылки на рисунок или таблицу в тексте работы."""

    kind: Literal["caption_reference"] = "caption_reference"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="captions"))
    expected: CaptionReferenceExpectation


class AlignmentRule(_RuleBase):
    """Правило выравнивания абзаца: подписи, ячейки таблиц, основной текст."""

    kind: Literal["alignment"] = "alignment"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="captions"))
    expected: AlignmentExpectation


class WidowControlRule(_RuleBase):
    """Правило контроля висячих строк в абзацах основного текста."""

    kind: Literal["widow_control"] = "widow_control"
    expected: WidowControlExpectation


class KeepNextRule(_RuleBase):
    """Правило неотрывности заголовка от следующего за ним текста."""

    kind: Literal["keep_next"] = "keep_next"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="headings"))
    expected: KeepNextExpectation


class HeadingPageBreakRule(_RuleBase):
    """Правило начала структурного раздела с новой страницы."""

    kind: Literal["heading_page_break"] = "heading_page_break"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="headings"))
    expected: HeadingPageBreakExpectation


class DocumentVolumeRule(_RuleBase):
    """Правило объема работы в страницах и словах."""

    kind: Literal["document_volume"] = "document_volume"
    target: RuleTarget = Field(default_factory=lambda: RuleTarget(scope="document"))
    expected: DocumentVolumeExpectation


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
        BibliographyLinkRule,
        BibliographyCitedRule,
        BibliographyFormatRule,
        CaptionNumberingRule,
        CaptionFormatRule,
        CaptionReferenceRule,
        AlignmentRule,
        WidowControlRule,
        KeepNextRule,
        HeadingPageBreakRule,
        DocumentVolumeRule,
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
    "bibliography_link": BIBLIOGRAPHY_SCOPES,
    "bibliography_cited": BIBLIOGRAPHY_SCOPES,
    "bibliography_format": BIBLIOGRAPHY_SCOPES,
    "caption_numbering": CAPTION_SCOPES,
    "caption_format": CAPTION_SCOPES,
    "caption_reference": CAPTION_SCOPES,
    "alignment": ALIGNMENT_SCOPES,
    "widow_control": frozenset({"body_text", "body_paragraphs"}),
    "keep_next": HEADING_SCOPES,
    "heading_page_break": HEADING_SCOPES,
    "document_volume": frozenset({"document"}),
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
