"""Внутритекстовые ссылки на источники: поиск, сопоставление и отчёт.

Пакет считает производный артефакт поверх готового ``ParsedDocument`` и не
меняет сам парсер. Ссылки ищутся по тексту абзацев четырьмя формами (``[3]``,
``[3, с. 15]``, ``[1–4]``, ``(Иванов, 2020)``), получают место в документе
(абзац, страница, смещения, раздел, контекст) и, если список литературы уже
разобран, сопоставляются с его записями.

Публичный API:
    extract_citations      — ParsedDocument -> CitationReport;
    CitationReport         — контракт citations.json (schema_version 1.0.0);
    CITATION_SCHEMA_VERSION — версия контракта отчёта.
"""
from .extract import extract_citations
from .schema import (
    CITATION_SCHEMA_VERSION,
    Citation,
    CitationReport,
    CitationSource,
    CitationSummary,
)

__all__ = [
    "CITATION_SCHEMA_VERSION",
    "Citation",
    "CitationReport",
    "CitationSource",
    "CitationSummary",
    "extract_citations",
]
