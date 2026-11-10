"""Сервисный слой разбора документов (единственная точка входа).

Пакет связывает DOCX-конвейер (`parser.docx`) и автономный PDF-парсер
(`parser.pdf`) в один сценарий: по переданным файлам выбирается режим
(`docx_only`, `pdf_only`, `docx_pdf`) и возвращается единая модель
`ParsedDocument`. Пакет не знает о способе доставки файлов: его вызывают
скрипты и тесты, а какой парсер работал, решает сам сервис.

Публичный API:
    parse_document        — разбор в выбранном режиме -> ParsedDocument;
    parse_to_json_file    — то же плюс запись JSON (UTF-8, indent=2) на диск;
    describe_parse_modes  — описание режимов и их возможностей для документации;
    DocumentParsingError  — документ получен, но разобрать его не удалось;
    MODE_CAPABILITIES     — матрица «возможности × режим» (единый источник).
"""
from .document_parsing import (
    DOCX_SUFFIXES,
    MODE_CAPABILITIES,
    MODE_DESCRIPTIONS,
    MODE_REQUIRED_FILES,
    PDF_SUFFIXES,
    DocumentParsingError,
    describe_parse_modes,
    parse_document,
    parse_to_json_file,
)

__all__ = [
    "DOCX_SUFFIXES",
    "MODE_CAPABILITIES",
    "MODE_DESCRIPTIONS",
    "MODE_REQUIRED_FILES",
    "PDF_SUFFIXES",
    "DocumentParsingError",
    "describe_parse_modes",
    "parse_document",
    "parse_to_json_file",
]
