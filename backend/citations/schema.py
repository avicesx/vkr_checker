"""Контракт отчёта о внутритекстовых ссылках на источники.

Отчёт ``citations.json`` — самостоятельный артефакт, не часть
``ParsedDocument``: парсер остаётся единственным источником фактов о документе,
а ссылки считаются производным слоем поверх его блоков. Поэтому здесь нет ни
одного поля о вёрстке — только то, что нужно потребителю: что за ссылка, куда
она указывает, где стоит и с чем сопоставилась.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

#: Версия контракта отчёта. Меняется при несовместимом изменении полей.
CITATION_SCHEMA_VERSION = "1.0.0"

#: Стили ссылок, которые различает сканер.
CitationStyle = Literal[
    "numeric",           # [3]
    "numeric_with_page", # [3, с. 15]
    "numeric_range",     # [1–4], [3, 5-7]
    "author_year",       # (Иванов, 2020)
]

#: Итог сопоставления ссылки со списком литературы.
Resolution = Literal["resolved", "unresolved"]


class CitationSource(BaseModel):
    """Откуда взят отчёт: файл, его хэш и режим разбора."""

    filename: Optional[str] = Field(default=None, description="Имя исходного файла документа")
    sha256: Optional[str] = Field(default=None, description="SHA-256 исходного файла")
    parse_mode: str = Field(description="Режим разбора ParsedDocument: docx_only, pdf_only, docx_pdf")
    page_count: int = Field(ge=1, description="Число страниц по данным парсера")


class Citation(BaseModel):
    """Одно вхождение ссылки на источник в тексте документа."""

    citation_id: str = Field(description="Идентификатор вхождения в порядке следования, cit_0001")
    raw: str = Field(description="Исходный фрагмент ссылки вместе со скобками")
    style: CitationStyle = Field(description="Форма записи ссылки")

    targets: List[str] = Field(
        default_factory=list,
        description="Номера источников для числовых ссылок; для авторско-годовых — «Фамилия, год»",
    )
    page_in_source: Optional[str] = Field(
        default=None,
        description="Страница источника из самой ссылки: «с. 15» -> 15, диапазон сохраняется как 15-17",
    )
    authors: List[str] = Field(
        default_factory=list,
        description="Фамилии авторов для авторско-годовых ссылок (без «и др.»)",
    )
    year: Optional[int] = Field(default=None, description="Год издания для авторско-годовых ссылок")

    resolved_bib_ids: List[str] = Field(
        default_factory=list,
        description="Записи списка литературы, к которым удалось сопоставить ссылку",
    )
    resolution: Resolution = Field(
        default="unresolved",
        description="resolved — есть хотя бы одно совпадение со списком литературы",
    )

    block_id: str = Field(description="Абзац, в котором стоит ссылка")
    page_est: int = Field(ge=1, description="Оценка страницы абзаца")
    start_char: int = Field(ge=0, description="Начало ссылки в text_visible абзаца")
    end_char: int = Field(ge=0, description="Конец ссылки в text_visible абзаца (не включительно)")
    section: Optional[str] = Field(
        default=None,
        description="Ближайший заголовок раздела по document_tree на момент ссылки",
    )
    context: str = Field(description="Окно текста вокруг ссылки для ручной проверки")


class CitationSummary(BaseModel):
    """Сводка по документу: сколько ссылок и насколько они сопоставлены."""

    total: int = Field(ge=0)
    by_style: Dict[str, int] = Field(default_factory=dict)
    resolved: int = Field(ge=0)
    unresolved: int = Field(ge=0)
    unique_sources: int = Field(
        ge=0,
        description="Число уникальных записей списка литературы, на которые сослались",
    )


class CitationReport(BaseModel):
    """Корневой объект citations.json."""

    schema_version: str = Field(default=CITATION_SCHEMA_VERSION)
    source: CitationSource
    citations: List[Citation] = Field(default_factory=list)
    summary: CitationSummary
    not_extracted: List[str] = Field(
        default_factory=list,
        description="Что и почему не удалось извлечь или сопоставить",
    )
