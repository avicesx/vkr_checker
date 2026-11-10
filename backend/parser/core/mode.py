"""Режимы разбора документа и автоматический выбор режима.

Модуль — единственный источник допустимых значений режима. На него опираются
схема отчета (schema.ParseMode), PDF-парсер (parser.pdf) и сервис:
строковые значения нельзя переименовывать, они попадают в JSON (`parse_mode`,
`source.mode`) и в параметры `parse_document()`.

Режимы:
    docx_only — разбирается только DOCX; PDF не участвует, пагинация оценочная;
    pdf_only  — разбирается только PDF; DOCX-структура (стили, нумерация,
                сноски, секции) недоступна, страницы точные;
    docx_pdf  — DOCX задает структуру и форматирование, PDF уточняет физические
                страницы и координаты вёрстки.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Union, get_args

#: Единственные допустимые значения режима разбора (порядок фиксирован).
ParseMode = Literal["docx_only", "pdf_only", "docx_pdf"]

#: Значение режима по умолчанию: режим выбирается по фактически переданным файлам.
DEFAULT_PARSE_MODE = "auto"

#: Кортеж допустимых значений для валидации входных параметров сервиса.
PARSE_MODES: tuple[ParseMode, ...] = get_args(ParseMode)

#: Нормализованное значение режима, включая "auto" (выбор по фактическим файлам).
NormalizedParseMode = Literal["auto", "docx_only", "pdf_only", "docx_pdf"]

#: Путь к файлу: строка или pathlib.Path.
PathLike = Union[str, Path]


def _path_present(path: Optional[PathLike]) -> bool:
    """True, если путь задан (None и пустая строка означают «файл не передан»)."""
    if path is None:
        return False
    return str(path).strip() != ""


def normalize_parse_mode(requested: Optional[str]) -> NormalizedParseMode:
    """Приводит запрошенный режим к нижнему регистру и проверяет допустимость.

    None, пустая строка и "auto" возвращают DEFAULT_PARSE_MODE. Любое другое
    значение обязано совпадать с одним из ParseMode, иначе ValueError.
    """
    if requested is None:
        return DEFAULT_PARSE_MODE
    mode = str(requested).strip().lower()
    if mode == "":
        return DEFAULT_PARSE_MODE
    if mode == DEFAULT_PARSE_MODE:
        return DEFAULT_PARSE_MODE
    if mode not in PARSE_MODES:
        allowed = ", ".join((*PARSE_MODES, DEFAULT_PARSE_MODE))
        raise ValueError(
            f"Неизвестный режим разбора: {requested!r}. Допустимые значения: {allowed}"
        )
    return mode


def detect_mode(
    docx_path: Optional[PathLike] = None,
    pdf_path: Optional[PathLike] = None,
    requested: Optional[str] = None,
) -> ParseMode:
    """Определяет режим разбора по переданным файлам и явному запросу.

    Правила auto (requested is None / "auto"):
        есть и DOCX, и PDF -> docx_pdf;
        только DOCX         -> docx_only;
        только PDF          -> pdf_only;
        ничего не передано  -> ValueError.

    Явно запрошенный режим не расширяется автоматически: если для него не хватает
    обязательного файла, поднимается ValueError. Лишний файл не ошибка: например,
    requested="docx_only" при переданном PDF означает «PDF игнорировать».
    """
    has_docx = _path_present(docx_path)
    has_pdf = _path_present(pdf_path)
    mode = normalize_parse_mode(requested)

    if mode == DEFAULT_PARSE_MODE:
        if has_docx and has_pdf:
            return "docx_pdf"
        if has_docx:
            return "docx_only"
        if has_pdf:
            return "pdf_only"
        raise ValueError(
            "Не выбран режим разбора: не передан ни DOCX, ни PDF. "
            f"Передайте хотя бы один файл или укажите requested из {PARSE_MODES}."
        )

    if mode == "docx_only" and not has_docx:
        raise ValueError("Режим docx_only требует docx_path: DOCX-файл не передан")
    if mode == "pdf_only" and not has_pdf:
        raise ValueError("Режим pdf_only требует pdf_path: PDF-файл не передан")
    if mode == "docx_pdf" and not (has_docx and has_pdf):
        missing = "DOCX" if not has_docx else "PDF"
        raise ValueError(f"Режим docx_pdf требует оба файла: не передан {missing}")

    return mode
