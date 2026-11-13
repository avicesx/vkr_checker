"""Оркестрация разбора документов в трёх режимах: сервис — единственная точка входа.

Модуль связывает два независимых парсера и единую модель результата:

    docx_only — DOCX/DOC/RTF/ODT, при необходимости через LibreOffice;
                PDF не читается, страницы оцениваются по DOCX;
    pdf_only  — только PDF: страницы точные, структура DOCX недоступна;
    docx_pdf  — DOCX задает структуру, PDF уточняет физические страницы;
    auto      — режим выбирается по фактически переданным файлам
                (правила — `parser.core.mode.detect_mode`).

Сервис не дублирует фазы парсеров: DOCX-ветку выполняет `DocxParserPipeline`,
PDF-ветку — пакет `parser.pdf`. Здесь же результат приводится к контракту
`ParsedDocument`: явно заполняются `parse_mode`, `source`, `capabilities` и
`page_count`, чтобы потребитель (rule_engine, скрипты) не зависел от того, какие
поля успел заполнить конкретный парсер.

Импорты парсеров ленивые: сервис импортируется и работает в режиме `docx_only`
без PyMuPDF, а `pdf_only` не поднимает DOCX-конвейер и lxml.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from parser.core.mode import (
    DEFAULT_PARSE_MODE,
    PARSE_MODES,
    ParseMode,
    detect_mode,
)
from parser.core.schema import (
    DocumentCapabilities,
    PageCountInfo,
    ParsedDocument,
    SourceInfo,
)

logger = logging.getLogger(__name__)

#: Путь к файлу: строка или pathlib.Path.
PathLike = Union[str, Path]

#: Расширения документов, которые разбирает DOCX-конвейер (legacy — через конвертацию).
DOCX_SUFFIXES: frozenset[str] = frozenset({".docx", ".doc", ".rtf", ".odt"})

#: Расширения, допустимые для PDF-источника.
PDF_SUFFIXES: frozenset[str] = frozenset({".pdf"})

#: Какие файлы обязательны для каждого режима (ключи набора входных файлов).
MODE_REQUIRED_FILES: Dict[str, Tuple[str, ...]] = {
    "docx_only": ("document",),
    "pdf_only": ("pdf",),
    "docx_pdf": ("document", "pdf"),
}

#: Человекочитаемое описание режимов для документации и CLI.
MODE_DESCRIPTIONS: Dict[str, str] = {
    "docx_only": (
        "Только DOCX/DOC/RTF/ODT: стили, нумерация, секции, сноски и перекрестные "
        "ссылки; число страниц оценочное (PDF не читается)"
    ),
    "pdf_only": (
        "Только PDF: точное число страниц, координаты вёрстки и аудит колонтитулов; "
        "структура DOCX (стили, нумерация, сноски) недоступна"
    ),
    "docx_pdf": (
        "DOCX + эталонный PDF: структура и форматирование из DOCX, физические "
        "страницы и координаты из PDF"
    ),
}

#: Матрица «возможности × режим» из ARCHITECTURE.md (раздел 4.2).
#: Единый источник: используется сервисом и describe_parse_modes().
MODE_CAPABILITIES: Dict[ParseMode, DocumentCapabilities] = {
    "docx_only": DocumentCapabilities(
        styles_parsed=True,
        numbering_parsed=True,
        sections_parsed=True,
        exact_pagination=False,
        pdf_layout_coordinates=False,
        visual_registry=True,
        footnotes_parsed=True,
        cross_references=True,
        word_metadata=True,
        page_count_estimated=True,
    ),
    "pdf_only": DocumentCapabilities(
        styles_parsed=False,
        numbering_parsed=False,
        sections_parsed=False,
        exact_pagination=True,
        pdf_layout_coordinates=True,
        visual_registry=False,
        footnotes_parsed=False,
        cross_references=True,
        word_metadata=False,
        page_count_estimated=False,
    ),
    "docx_pdf": DocumentCapabilities(
        styles_parsed=True,
        numbering_parsed=True,
        sections_parsed=True,
        exact_pagination=True,
        pdf_layout_coordinates=True,
        visual_registry=True,
        footnotes_parsed=True,
        cross_references=True,
        word_metadata=True,
        page_count_estimated=False,
    ),
}


class DocumentParsingError(Exception):
    """Документ получен, но разобрать его не удалось.

    Используется для повреждённых файлов, ошибок конвертации и сбоев парсеров:
    вызывающий код отличает его от ошибок конфигурации (ImportError), поэтому
    тип исключения значим. Исходное исключение сохраняется в `__cause__`.
    """


# =====================================================================
# Разбор входного запроса
# =====================================================================

def _coerce_path(value: Optional[PathLike]) -> Optional[Path]:
    """Приводит путь к Path; None и пустая строка означают «файл не передан»."""
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    return Path(text)


def _require_file(path: Path, label: str) -> None:
    """Проверяет, что путь указывает на существующий файл."""
    if not path.is_file():
        raise FileNotFoundError(f"{label} не найден: {path}")


def _resolve_request(
    input_path: Optional[PathLike],
    pdf_path: Optional[PathLike],
    requested: Optional[str],
) -> Tuple[Optional[Path], Optional[Path], ParseMode]:
    """Проверяет пару файлов и определяет фактический режим разбора.

    PDF можно передать как основной документ (`input_path`): это удобно для
    CLI-разбора одного PDF. Тогда `pdf_path` считается тем же файлом, а DOCX
    отсутствует. Явный режим без обязательного файла — ошибка запроса.

    Raises:
        ValueError: неизвестный режим, неподдерживаемый формат или
            несовместимая пара файлов (оба PDF).
        FileNotFoundError: переданный файл не существует.
    """
    document = _coerce_path(input_path)
    reference_pdf = _coerce_path(pdf_path)

    # Признак «это PDF, а не документ» важнее позиции аргумента.
    if document is not None and document.suffix.lower() in PDF_SUFFIXES:
        if reference_pdf is not None and reference_pdf.resolve() != document.resolve():
            raise ValueError(
                "Несовместимая пара файлов: и document, и pdf имеют расширение .pdf. "
                "Передайте DOCX в document и PDF в pdf либо только один PDF"
            )
        reference_pdf = document
        document = None
    elif document is not None and document.suffix.lower() not in DOCX_SUFFIXES:
        allowed = ", ".join(sorted(DOCX_SUFFIXES))
        raise ValueError(
            f"Неподдерживаемый формат документа: {document.suffix or '<без расширения>'}; "
            f"ожидается один из {allowed}"
        )

    if reference_pdf is not None and reference_pdf.suffix.lower() not in PDF_SUFFIXES:
        raise ValueError(
            f"Файл PDF должен иметь расширение .pdf, получено "
            f"{reference_pdf.suffix or '<без расширения>'} ({reference_pdf.name})"
        )

    if document is not None:
        _require_file(document, "Файл документа")
    if reference_pdf is not None:
        _require_file(reference_pdf, "PDF-файл")

    # detect_mode поднимает ValueError для неизвестного режима и для явного
    # режима без обязательного файла; пустая пара файлов — тоже ValueError.
    resolved_mode = detect_mode(document, reference_pdf, requested)
    return document, reference_pdf, resolved_mode


# =====================================================================
# Парсеры
# =====================================================================

def _parse_docx_modes(
    document_path: Path,
    pdf_path: Optional[Path],
    mode: ParseMode,
    outdir: Optional[PathLike],
    backend: Optional[str],
    timeout: Optional[int],
) -> ParsedDocument:
    """Разбирает DOCX в режиме docx_only или docx_pdf через DocxParserPipeline."""
    from parser.docx.converter import (
        CONVERT_TIMEOUT_SECONDS,
        DEFAULT_BACKEND,
        convert_to_docx,
    )
    from parser.docx.pipeline import DocxParserPipeline

    conversion = convert_to_docx(
        document_path,
        outdir=outdir,
        timeout=timeout if timeout is not None else CONVERT_TIMEOUT_SECONDS,
        backend=backend or DEFAULT_BACKEND,  # type: ignore[arg-type]
    )
    pipeline = DocxParserPipeline(
        conversion.docx_path,
        # В docx_only PDF не читается даже при наличии файла.
        pdf_path=pdf_path if mode == "docx_pdf" else None,
        source_format=conversion.source_format,
        converted_via_lo=conversion.converted_via_lo,
        conversion_tool=conversion.tool,
        conversion_duration_ms=conversion.duration_ms,
        lo_version=conversion.lo_version,
        lo_image=conversion.lo_image,
    )
    if mode == "docx_only":
        parse_docx_only = getattr(pipeline, "parse_docx_only", None)
        if callable(parse_docx_only):
            return parse_docx_only()
    return pipeline.parse()


def _load_pdf_entrypoint():
    """Возвращает функцию разбора PDF из пакета parser.pdf (ленивый импорт).

    Импорт выполняется только для режима pdf_only, поэтому сервис не поднимает
    PyMuPDF при разборе DOCX. Поддерживаются оба публичных варианта API:
    parse_pdf(path) и PdfDocumentParser().parse(path).
    """
    try:
        import parser.pdf as pdf_package
    except ImportError as exc:  # pragma: no cover - зависит от установки PyMuPDF
        raise ImportError(
            "Режим pdf_only требует пакет parser.pdf, который не установлен в backend/"
        ) from exc

    parse_pdf = getattr(pdf_package, "parse_pdf", None)
    if callable(parse_pdf):
        return parse_pdf

    parser_cls = getattr(pdf_package, "PdfDocumentParser", None) or getattr(
        pdf_package, "PdfParser", None
    )
    if parser_cls is not None:
        parse_method = getattr(parser_cls(), "parse", None)
        if callable(parse_method):
            return parse_method

    raise ImportError(
        "Пакет parser.pdf не предоставляет parse_pdf() или PdfDocumentParser().parse()"
    )


def _parse_pdf_only(pdf_path: Optional[Path]) -> ParsedDocument:
    """Разбирает единственный PDF в режиме pdf_only через parser.pdf."""
    if pdf_path is None:  # защита от вызова в обход detect_mode
        raise ValueError("Режим pdf_only требует PDF-файл")
    parsed = _load_pdf_entrypoint()(pdf_path)
    if isinstance(parsed, ParsedDocument):
        return parsed
    if isinstance(parsed, dict):
        return ParsedDocument.model_validate(parsed)
    raise DocumentParsingError(
        f"PDF-парсер вернул неожиданный тип результата: {type(parsed).__name__}"
    )


# =====================================================================
# Приведение результата к контракту режима
# =====================================================================

def _sha256_file(path: Optional[Path]) -> Optional[str]:
    """Считает SHA-256 файла потоково, не читая его целиком в память."""
    if path is None:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _apply_mode_contract(
    document: ParsedDocument,
    mode: ParseMode,
    document_path: Optional[Path],
    pdf_path: Optional[Path],
) -> ParsedDocument:
    """Явно заполняет контрактные поля под фактический режим разбора.

    Парсеры заполняют эти поля сами (T2/T3), но сервис — последняя инстанция:
    именно здесь гарантируется, что `source.has_pdf` соответствует режиму,
    `capabilities` не обещают недоступных данных, а `page_count` измерен по PDF,
    когда PDF участвовал.

    `source.docx_filename`/`docx_sha256` описывают именно тот файл, который
    загрузил пользователь (`document_path`), а не результат LibreOffice-конвертации:
    для legacy-форматов это `.doc`/`.rtf`/`.odt`, а `meta.filename`/`meta.file_sha256`
    продолжают описывать уже разобранный `.docx`.
    """
    has_docx = mode != "pdf_only"
    has_pdf = mode != "docx_only"

    # Хэш считается от исходного файла: пара имя/хэш всегда согласована, даже если
    # конвейер разбирал результат конвертации (у него свой hash в meta.file_sha256).
    docx_sha256 = _sha256_file(document_path) if has_docx else None
    pdf_sha256 = document.source.pdf_sha256 if has_pdf else None
    if has_pdf and not pdf_sha256:
        pdf_sha256 = _sha256_file(pdf_path)

    source = SourceInfo(
        mode=mode,
        has_docx=has_docx,
        has_pdf=has_pdf,
        docx_filename=document_path.name if has_docx and document_path else None,
        docx_sha256=docx_sha256,
        pdf_filename=pdf_path.name if has_pdf and pdf_path else None,
        pdf_sha256=pdf_sha256,
    )

    page_count = _normalize_page_count(document, mode)

    # При расхождении текста DOCX/PDF блоки остаются на оценочных страницах DOCX
    # (могут превышать page_count.total_pages), а координат вёрстки у них нет:
    # pdf_layout_coordinates=False — машинный признак неприменённого выравнивания.
    alignment_applied = document.meta.pagination_source == "pdf_exact"
    if mode == "docx_pdf" and not alignment_applied and document.sections:
        pdf_pages = len(document.pdf_pages_audit)
        if pdf_pages:
            document.sections[-1].page_end_est = max(
                document.sections[-1].page_start_est, pdf_pages
            )

    capabilities = MODE_CAPABILITIES[mode].model_copy(deep=True)
    # Флаги описывают наличие DOCX-производных данных, а не непустоту коллекций:
    # в pdf_only sections могут содержать оценку по геометрии PDF, но
    # sections_parsed остаётся False по матрице ARCHITECTURE 4.2. Исключения —
    # cross_references (индекс заполняет общий слой enrichment) и visual_registry
    # (True, только если подпись рисунка связалась с визуальным объектом).
    capabilities.page_count_estimated = page_count.method not in (
        "pdf_exact",
        "docx_native_breaks",
    )
    if mode == "docx_pdf":
        # Точная пагинация и координаты вёрстки подтверждаются только сверкой DOCX/PDF.
        capabilities.exact_pagination = alignment_applied
        capabilities.pdf_layout_coordinates = alignment_applied
    elif mode == "pdf_only":
        capabilities.exact_pagination = page_count.method == "pdf_exact"
        capabilities.visual_registry = document.capabilities.visual_registry
    else:
        capabilities.exact_pagination = False
        capabilities.pdf_layout_coordinates = False

    document.parse_mode = mode
    document.source = source
    document.page_count = page_count
    document.capabilities = capabilities
    return document


def _normalize_page_count(document: ParsedDocument, mode: ParseMode) -> PageCountInfo:
    """Согласует page_count со страницами, реально измеренными по PDF.

    Если PDF прочитан, число его страниц точное при любом sync_score: даже при
    расхождении текста DOCX/PDF блоки не привязываются, но количество листов
    измерено. Поэтому method/confidence не понижаются, а расхождение
    объясняется в note; capabilities.exact_pagination при этом остается False.
    """
    page_count = document.page_count
    pdf_pages = len(document.pdf_pages_audit)

    if mode in ("pdf_only", "docx_pdf") and pdf_pages:
        if mode == "docx_pdf" and document.meta.pagination_source != "pdf_exact":
            sync_score = document.meta.pdf_docx_sync_score
            score_text = (
                f"{sync_score:.1%}" if isinstance(sync_score, (int, float)) else "ниже порога"
            )
            return PageCountInfo(
                total_pages=pdf_pages,
                method="pdf_exact",
                confidence="high",
                note=(
                    f"Число страниц измерено по эталонному PDF ({pdf_pages}) и точное, "
                    "но блоки не привязаны к страницам из-за расхождения текста "
                    f"DOCX и PDF (sync_score {score_text})"
                ),
            )
        if page_count.method != "pdf_exact" or page_count.total_pages != pdf_pages:
            source_label = "PDF" if mode == "pdf_only" else "эталонному PDF"
            return PageCountInfo(
                total_pages=pdf_pages,
                method="pdf_exact",
                confidence="high",
                note=f"Число страниц измерено по {source_label}",
            )
        return page_count

    if mode == "docx_only" and page_count.method == "pdf_exact":
        # PDF в этом режиме не читается: значение не может быть точным.
        estimated_pages = max(
            (section.page_end_est for section in document.sections),
            default=1,
        )
        return PageCountInfo(
            total_pages=max(1, int(estimated_pages)),
            method="geometric_estimation",
            confidence="low",
            note="PDF не передан: число страниц оценено по геометрии DOCX",
        )

    return page_count


# =====================================================================
# Публичный API
# =====================================================================

def parse_document(
    input_path: Optional[PathLike],
    pdf_path: Optional[PathLike] = None,
    mode: Optional[str] = DEFAULT_PARSE_MODE,
    outdir: Optional[PathLike] = None,
    backend: Optional[str] = None,
    timeout: Optional[int] = None,
) -> ParsedDocument:
    """Разбирает документ в выбранном режиме и возвращает ParsedDocument.

    Args:
        input_path: DOCX/DOC/RTF/ODT **или** PDF (тогда `pdf_path` не нужен).
            None допустим только вместе с `pdf_path`.
        pdf_path: отдельный эталонный PDF (.pdf). В режиме docx_only игнорируется.
        mode: "auto" (по умолчанию), "docx_only", "pdf_only" или "docx_pdf";
            регистр и пробелы нормализуются.
        outdir: каталог для результата LibreOffice-конвертации legacy-форматов
            (по умолчанию рядом с исходником).
        backend: backend конвертации: auto | docker | local (по умолчанию из
            DOCX_CONVERT_BACKEND).
        timeout: таймаут конвертации в секундах (по умолчанию DOCX_CONVERT_TIMEOUT).

    Raises:
        ValueError: неизвестный режим, неподдерживаемый формат, несовместимая
            пара файлов, явный режим без обязательного файла.
        FileNotFoundError: переданный файл не существует.
        DocumentParsingError: документ получен, но разобрать его не удалось
            (повреждённый файл, ошибка конвертации, сбой парсера).
        ImportError: пакет parser.pdf недоступен, а запрошен режим pdf_only.
    """
    document_path, reference_pdf, resolved_mode = _resolve_request(
        input_path, pdf_path, mode
    )
    logger.info(
        "Разбор документа: mode=%s, has_docx=%s, has_pdf=%s",
        resolved_mode,
        document_path is not None,
        reference_pdf is not None,
    )

    try:
        if resolved_mode == "pdf_only":
            parsed = _parse_pdf_only(reference_pdf)
        else:
            parsed = _parse_docx_modes(
                document_path,  # type: ignore[arg-type]  # гарантировано detect_mode
                reference_pdf,
                resolved_mode,
                outdir,
                backend,
                timeout,
            )
    except (DocumentParsingError, ImportError):
        # ImportError означает недоступный парсер (конфигурация окружения), а не
        # битый документ: вызывающий код отличает его от DocumentParsingError.
        raise
    except Exception as exc:  # noqa: BLE001 - граница сервиса: любые сбои парсеров
        raise DocumentParsingError(
            f"Не удалось разобрать документ в режиме {resolved_mode}: {exc}"
        ) from exc

    return _apply_mode_contract(parsed, resolved_mode, document_path, reference_pdf)


def parse_to_json_file(
    input_path: Optional[PathLike],
    output_json_path: PathLike,
    pdf_path: Optional[PathLike] = None,
    mode: Optional[str] = DEFAULT_PARSE_MODE,
    outdir: Optional[PathLike] = None,
    backend: Optional[str] = None,
    timeout: Optional[int] = None,
    indent: int = 2,
) -> ParsedDocument:
    """Разбирает документ и сохраняет результат в JSON (UTF-8, indent=2).

    Возвращает тот же ParsedDocument, что и `parse_document`. Каталог вывода
    создается при необходимости; содержимое документа в логи не попадает.
    """
    document = parse_document(
        input_path,
        pdf_path=pdf_path,
        mode=mode,
        outdir=outdir,
        backend=backend,
        timeout=timeout,
    )
    target = Path(output_json_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document.model_dump_json(indent=indent), encoding="utf-8")
    return document


def describe_parse_modes() -> List[dict]:
    """Возвращает описание трёх режимов и их возможностей для документации."""
    return [
        {
            "mode": mode,
            "required_files": list(MODE_REQUIRED_FILES[mode]),
            "description": MODE_DESCRIPTIONS[mode],
            "capabilities": MODE_CAPABILITIES[mode].model_dump(mode="json"),
        }
        for mode in PARSE_MODES
    ]
