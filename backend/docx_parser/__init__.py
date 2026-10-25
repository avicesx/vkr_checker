"""Разбор DOCX и legacy-форматов (опционально сверенных с PDF) в JSON-отчет.

Пакет собирает единый ParsedDocxDocument: блоки текста и таблиц с форматированием,
визуальные объекты и формулы, секции и колонтитулы, оглавление и дерево документа,
перекрестные ссылки, метаданные и аномалии оформления.

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
    ParsedDocxDocument  — корневая модель отчета.
"""

from .converter import convert_to_docx
from .pipeline import DocxParserPipeline, parse_any_to_json_file, parse_docx_to_json_file
from .schema import ParsedDocxDocument

__all__ = [
    "DocxParserPipeline",
    "ParsedDocxDocument",
    "convert_to_docx",
    "parse_any_to_json_file",
    "parse_docx_to_json_file",
]
