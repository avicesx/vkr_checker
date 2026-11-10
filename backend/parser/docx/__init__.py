"""Разбор DOCX и PDF в единый JSON-отчет.

Пакет поддерживает три режима (см. parser.core.mode и ARCHITECTURE.md):
    docx_only — только DOCX: стили, нумерация, секции, сноски и структура;
    pdf_only  — только PDF: точные страницы и координаты вёрстки (эвристически);
    docx_pdf  — DOCX как источник структуры плюс PDF как эталон пагинации.

Корневая модель ParsedDocument (исторический алиас ParsedDocxDocument) содержит
parse_mode, source, capabilities и page_count, поэтому потребитель всегда видит,
какие данные в этом режиме заполнены, а какие недоступны.

Форматы:
    .docx разбирается напрямую;
    .doc, .rtf, .odt и другие форматы LibreOffice сначала конвертируются в .docx
    (см. converter.py), после чего проходят тот же конвейер.

Эталонный PDF должен быть снят с исходного файла, а не с результата конвертации:
LibreOffice пересчитывает вёрстку, и геометрия страниц может разойтись.

Публичный API:
    DocxParserPipeline  — конвейер разбора, принимает пути .docx и (опционально) .pdf;
    parse_docx_to_json_file — разбор готового .docx с сохранением результата в файл;
    parse_any_to_json_file  — конвертация при необходимости + разбор + сохранение в файл;
    convert_to_docx  — только конвертация legacy-формата в .docx через LibreOffice;
    ParsedDocument / ParsedDocxDocument — корневая модель отчета;
    ParseMode, detect_mode, DEFAULT_PARSE_MODE — контракт режимов разбора;
    SourceInfo, DocumentCapabilities, PageCountInfo — контрактные блоки отчета.

Импорты ленивые (PEP 562): сам пакет не поднимает DOCX-конвейер, converter и
lxml, пока не запрошен соответствующий символ. Поэтому `import parser.pdf`,
который использует только общие примитивы `parser.docx.pdf_text_utils` и
`parser.docx.pdf_page_text`, не загружает `parser.docx.pipeline` и не тянет
зависимости DOCX-разбора.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - только для статических анализаторов
    from .converter import convert_to_docx
    from .pipeline import DocxParserPipeline, parse_any_to_json_file, parse_docx_to_json_file
    from parser.core.mode import (
        DEFAULT_PARSE_MODE,
        PARSE_MODES,
        ParseMode,
        detect_mode,
        normalize_parse_mode,
    )
    from parser.core.schema import (
        DocumentCapabilities,
        PageCountInfo,
        ParsedDocxDocument,
        ParsedDocument,
        SourceInfo,
    )

#: Публичные имена пакета и модули, из которых они берутся.
_LAZY_ATTRIBUTES = {
    "DEFAULT_PARSE_MODE": "parser.core.mode",
    "DocxParserPipeline": ".pipeline",
    "DocumentCapabilities": "parser.core.schema",
    "PARSE_MODES": "parser.core.mode",
    "PageCountInfo": "parser.core.schema",
    "ParseMode": "parser.core.mode",
    "ParsedDocxDocument": "parser.core.schema",
    "ParsedDocument": "parser.core.schema",
    "SourceInfo": "parser.core.schema",
    "convert_to_docx": ".converter",
    "detect_mode": "parser.core.mode",
    "normalize_parse_mode": "parser.core.mode",
    "parse_any_to_json_file": ".pipeline",
    "parse_docx_to_json_file": ".pipeline",
}

__all__ = [
    "DEFAULT_PARSE_MODE",
    "DocxParserPipeline",
    "DocumentCapabilities",
    "PARSE_MODES",
    "PageCountInfo",
    "ParseMode",
    "ParsedDocxDocument",
    "ParsedDocument",
    "SourceInfo",
    "convert_to_docx",
    "detect_mode",
    "normalize_parse_mode",
    "parse_any_to_json_file",
    "parse_docx_to_json_file",
]


def __getattr__(name: str):
    """Лениво импортирует публичный символ пакета при первом обращении.

    Реализует контракт PEP 562: `from parser.docx import DocxParserPipeline`
    работает как прежде, но `import parser.docx` сам по себе не загружает
    `pipeline.py` и `converter.py`.
    """
    module_name = _LAZY_ATTRIBUTES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Возвращает публичные имена пакета без импорта тяжелых модулей."""
    return sorted(__all__)
