"""Единый модуль разбора документов проверки ВКР.

Структура пакета:
    parser.core — общий контракт: JSON-схема отчета (schema.py) и режимы
                  разбора (mode.py); не зависит от DOCX/PDF-конвейеров;
    parser.docx — DOCX-конвейер: стили, нумерация, секции, таблицы, формулы,
                  структура, а также выравнивание DOCX с эталонным PDF;
    parser.pdf  — автономный PDF-парсер: извлечение страниц, оценка вёрстки,
                  структура документа.

Пакет намеренно не импортирует подмодули на уровне `__init__`: `import parser`
не поднимает ни lxml, ни pydantic, ни pymupdf. Импортируйте нужный слой явно:

    from parser.core.mode import ParseMode, detect_mode
    from parser.docx import DocxParserPipeline
    from parser.pdf import parse_pdf

Документация: `parser/core/ARCHITECTURE.md` и `parser/core/PIPELINE.md`.
"""
from __future__ import annotations

__all__ = ["core", "docx", "pdf"]
