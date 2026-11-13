"""Сервис извлечения ссылок: готовый JSON разбора -> citations.json.

Сервис не разбирает документ заново: он читает ``ParsedDocument`` из JSON,
который уже отдал ``services.document_parsing``, и строит по нему отдельный
отчёт о ссылках. Так сохраняется принцип «парсим один раз, производные артефакты
считаем по модели»: отчёт можно пересобрать без повторного разбора и без
LibreOffice/PyMuPDF.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

from citations import CitationReport, extract_citations
from parser.core.schema import ParsedDocument

PathLike = Union[str, Path]


def extract_citations_from_json(parse_json_path: PathLike) -> CitationReport:
    """Читает JSON разбора и возвращает отчёт о ссылках на источники."""
    source = Path(parse_json_path)
    document = ParsedDocument.model_validate_json(source.read_text(encoding="utf-8"))
    return extract_citations(document)


def extract_citations_to_json_file(
    parse_json_path: PathLike,
    output_json_path: PathLike,
    indent: int = 2,
) -> CitationReport:
    """Строит отчёт о ссылках и сохраняет его в JSON (UTF-8, indent=2).

    Каталог вывода создается при необходимости; содержимое документа в логи не
    попадает. Возвращает тот же отчёт, что записан в файл.
    """
    report = extract_citations_from_json(parse_json_path)
    target = Path(output_json_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.model_dump_json(indent=indent), encoding="utf-8")
    return report
