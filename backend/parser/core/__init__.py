"""Общий контракт разбора: JSON-схема отчета и режимы.

Содержимое слоя:
    schema.py — pydantic-модели отчета ParsedDocument (алиас ParsedDocxDocument),
                блоки SourceInfo, DocumentCapabilities, PageCountInfo и все
                элементы документа: секции, абзацы, таблицы, рисунки, формулы,
                TOC, сноски, аудит страниц PDF;
    mode.py   — ParseMode (docx_only / pdf_only / docx_pdf), PARSE_MODES,
                DEFAULT_PARSE_MODE, detect_mode() и normalize_parse_mode().

Слой core не импортирует parser.docx и parser.pdf: контракт не знает о
конвейерах. Подмодули подключаются явно, чтобы `import parser.core` не тянул
pydantic без необходимости:

    from parser.core.schema import ParsedDocument
    from parser.core.mode import ParseMode, detect_mode
"""
from __future__ import annotations

__all__ = ["mode", "schema"]
