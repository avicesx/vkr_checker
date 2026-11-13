"""Общий слой обогащения ParsedDocument (пост-процессор над готовой моделью).

Пакет занимает место между конвейерами разбора (`parser.docx`, `parser.pdf`) и
потребителями отчета (rule_engine, citations, сервис экспорта). Модули слоя
формат-независимы: получают уже собранный ``ParsedDocument`` и заполняют
производные структуры, которые конвейеры оставляют пустыми в отдельных
режимах (например, в ``pdf_only`` нет ``cross_ref_index``).

Публичный API:
    extract_bibliography — список литературы -> List[BibliographyEntry];
    extract_captions     — подписи рисунков/таблиц/приложений;
    extract_mentions     — упоминания объектов и ссылок в тексте.

Пакет намеренно не импортируется из ``parser/__init__``: тяжелые зависимости
(``pydantic``) поднимаются только при явном импорте слоя.
"""
from __future__ import annotations

from .bibliography import extract_bibliography
from .captions import extract_captions
from .mentions import extract_mentions

__all__ = [
    "extract_bibliography",
    "extract_captions",
    "extract_mentions",
]
