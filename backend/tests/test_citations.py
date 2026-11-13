"""Контракт и поведение извлечения ссылок на источники.

Тесты проверяют четыре формы из постановки (``[3]``, ``[3, с. 15]``, ``[1–4]``,
``(Иванов, 2020)``), место ссылки в документе (абзац, страница, смещения,
раздел, контекст), исключение раздела литературы из поиска, сопоставление со
списком литературы и честные пометки, когда сопоставить нечего. Отдельная
группа — сервис: JSON разбора на входе, ``citations.json`` на выходе.

Документы собираются синтетически, поэтому тесты не зависят от бинарных
образцов и PyMuPDF.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from citations import CITATION_SCHEMA_VERSION, CitationReport, extract_citations
from citations.extract import NOTE_NO_BIBLIOGRAPHY, NOTE_PDF_BIBLIOGRAPHY
from citations.patterns import (
    parse_author_year_citation,
    parse_author_year_citations,
    parse_bracket_citation,
)
from parser.core.schema import (
    BibliographyEntry,
    CrossReferenceIndex,
    DocumentMeta,
    DocumentTreeNode,
    GlobalSettings,
    PageCountInfo,
    ParagraphBlock,
    ParsedDocument,
    TableBlock,
    TableCell,
    TableRow,
    ThemeData,
)
from services.references import extract_citations_from_json, extract_citations_to_json_file

EN_DASH = "–"


# ---------------------------------------------------------------------
# Сборка документов
# ---------------------------------------------------------------------


def _paragraph(
    block_id: str, text: str, page: int = 1, *, container_type: str = "body"
) -> ParagraphBlock:
    """Создает абзац с заданным местом на странице и типом контейнера."""
    return ParagraphBlock(
        block_id=block_id,
        section_idx=0,
        container_type=container_type,  # type: ignore[arg-type]
        page_start_est=page,
        page_end_est=page,
        text_raw=text,
        text_visible=text,
    )


def _bib_entry(index: int, raw_text: str, block_id: str = "bib_block") -> BibliographyEntry:
    """Создает запись списка литературы с порядковым номером."""
    return BibliographyEntry(
        bib_id=f"bib_{index:03d}",
        block_id=block_id,
        order_index=index,
        raw_text=raw_text,
    )


def _tree_node(
    node_id: str,
    title: str,
    heading_block_id: Optional[str],
    *,
    level: int = 0,
    role: str = "main_chapter",
    page: int = 1,
    page_end: Optional[int] = None,
    children: Sequence[DocumentTreeNode] = (),
) -> DocumentTreeNode:
    """Создает узел дерева документа, привязанный к абзацу-заголовку."""
    return DocumentTreeNode(
        node_id=node_id,
        heading_block_id=heading_block_id,
        title=title,
        level=level,
        semantic_role=role,  # type: ignore[arg-type]
        page_start_est=page,
        page_end_est=page_end if page_end is not None else page,
        children=list(children),
    )


def _table_block(
    block_id: str,
    rows: Sequence[Sequence[ParagraphBlock]],
    *,
    page: int = 1,
) -> TableBlock:
    """Создает таблицу, в каждой ячейке которой лежит ровно один абзац."""
    table_rows = [
        TableRow(
            row_idx=row_idx,
            page_start_est=page,
            page_end_est=page,
            cells=[
                TableCell(
                    cell_id=f"{block_id}_r{row_idx}c{col_idx}",
                    row_idx=row_idx,
                    col_idx=col_idx,
                    text_combined=paragraph.text_visible,
                    blocks=[paragraph],
                )
                for col_idx, paragraph in enumerate(row)
            ],
        )
        for row_idx, row in enumerate(rows)
    ]
    return TableBlock(
        block_id=block_id,
        section_idx=0,
        page_start_est=page,
        page_end_est=page,
        rows=table_rows,
    )


def _document(
    blocks: Iterable[object],
    *,
    tree: Sequence[DocumentTreeNode] = (),
    bibliography: Sequence[BibliographyEntry] = (),
    parse_mode: str = "docx_only",
) -> ParsedDocument:
    """Собирает минимальный ParsedDocument для сканера ссылок."""
    block_list = list(blocks)
    pages = max((getattr(block, "page_end_est", 1) for block in block_list), default=1)
    return ParsedDocument(
        parse_mode=parse_mode,  # type: ignore[arg-type]
        meta=DocumentMeta(filename="vkr.docx", file_sha256="0" * 64),
        settings=GlobalSettings(),
        theme=ThemeData(),
        page_count=PageCountInfo(
            total_pages=pages, method="docx_native_breaks", confidence="high"
        ),
        blocks=block_list,  # type: ignore[arg-type]
        document_tree=list(tree),
        cross_ref_index=CrossReferenceIndex(bibliography=list(bibliography)),
    )


def _citations_of(text: str, **kwargs) -> List:
    """Извлекает ссылки из одного абзаца и возвращает их список."""
    document = _document([_paragraph("p_0001", text, page=kwargs.pop("page", 1))], **kwargs)
    return extract_citations(document).citations


# ---------------------------------------------------------------------
# Разбор форм ссылок
# ---------------------------------------------------------------------


def test_bracket_citation_with_page_is_parsed_into_target_and_page() -> None:
    """«[3, с. 15]» — цель 3, страница 15, а не цель 15."""
    parsed = parse_bracket_citation("[3, с. 15]")
    assert parsed is not None
    assert parsed.targets == ["3"]
    assert parsed.page_in_source == "15"
    assert parsed.style == "numeric_with_page"


def test_bracket_citation_range_is_expanded() -> None:
    """«[1–4]» раскрывается в четыре отдельные цели."""
    parsed = parse_bracket_citation(f"[1{EN_DASH}4]")
    assert parsed is not None
    assert parsed.targets == ["1", "2", "3", "4"]
    assert parsed.style == "numeric_range"


def test_bracket_citation_mixed_range_and_page_range() -> None:
    """«[3, 5-7]» и «[3, с. 15-17]» разбираются без потери частей."""
    mixed = parse_bracket_citation("[3, 5-7]")
    assert mixed is not None
    assert mixed.targets == ["3", "5", "6", "7"]

    with_page = parse_bracket_citation("[3, с. 15-17]")
    assert with_page is not None
    assert with_page.targets == ["3"]
    assert with_page.page_in_source == "15-17"


def test_author_year_citation_is_parsed() -> None:
    """«(Иванов, 2020)» дает фамилию, год и цель в едином виде."""
    parsed = parse_author_year_citation("(Иванов, 2020)")
    assert parsed is not None
    assert parsed.authors == ["Иванов"]
    assert parsed.year == 2020
    assert parsed.targets == ["Иванов, 2020"]


def test_author_year_with_etal_and_page() -> None:
    """«(Иванов и др., 2020, с. 15)» и латинский «et al.» тоже поддержаны."""
    russian = parse_author_year_citation("(Иванов и др., 2020, с. 15)")
    assert russian is not None
    assert russian.authors == ["Иванов"]
    assert russian.page_in_source == "15"

    latin = parse_author_year_citation("(Smith et al., 2020)")
    assert latin is not None
    assert latin.authors == ["Smith"]
    assert latin.year == 2020


def test_multi_source_group_is_split_into_entries() -> None:
    """«(Иванов, 2020; Петров, 2021)» — две записи с собственными смещениями."""
    raw = "(Иванов, 2020; Петров, 2021)"
    entries = parse_author_year_citations(raw)

    assert [entry.targets for entry in entries] == [["Иванов, 2020"], ["Петров, 2021"]]
    assert [entry.year for entry in entries] == [2020, 2021]
    assert [raw[entry.start : entry.end] for entry in entries] == [
        "Иванов, 2020",
        "Петров, 2021",
    ]
    # одиночный разбор остается совместимым: возвращается первая запись
    first = parse_author_year_citation(raw)
    assert first is not None
    assert first.authors == ["Иванов"]


def test_multi_source_group_keeps_page_of_each_entry() -> None:
    """Страница источника относится к своей записи, а не ко всей скобке."""
    entries = parse_author_year_citations("(Иванов, 2020, с. 15; Петров, 2021, с. 20)")
    assert [entry.page_in_source for entry in entries] == ["15", "20"]


# ---------------------------------------------------------------------
# Поиск в тексте и место в документе
# ---------------------------------------------------------------------


def test_numeric_citation_keeps_position_and_context() -> None:
    """Вхождение хранит абзац, страницу, смещения и окно контекста."""
    text = "Как показано в [3], предложенный метод устойчив к шуму."
    citations = _citations_of(text, page=4)

    assert len(citations) == 1
    citation = citations[0]
    assert citation.raw == "[3]"
    assert citation.style == "numeric"
    assert citation.targets == ["3"]
    assert citation.block_id == "p_0001"
    assert citation.page_est == 4
    assert citation.start_char == text.index("[3]")
    assert citation.end_char == text.index("[3]") + len("[3]")
    assert "[3]" in citation.context


def test_citation_ids_follow_document_order() -> None:
    """Идентификаторы выдаются по порядку следования абзацев и вхождений."""
    document = _document(
        [
            _paragraph("p_0001", "Первый источник [1] и второй [2]."),
            _paragraph("p_0002", "Третий источник [3]."),
        ]
    )
    citations = extract_citations(document).citations

    assert [c.citation_id for c in citations] == ["cit_0001", "cit_0002", "cit_0003"]
    assert [c.block_id for c in citations] == ["p_0001", "p_0001", "p_0002"]


def test_year_in_parentheses_without_author_is_not_a_citation() -> None:
    """«(2020)» без фамилии — не авторско-годовая ссылка."""
    assert _citations_of("Отчет за (2020) год подготовлен.") == []


def test_section_title_is_taken_from_document_tree() -> None:
    """Ссылка получает ближайший предшествующий заголовок раздела."""
    tree = [
        _tree_node("n_0001", "1 Введение", "p_0001"),
        _tree_node("n_0002", "2 Метод", "p_0002", level=0, page=2),
    ]
    document = _document(
        [
            _paragraph("p_0001", "Введение", page=1),
            _paragraph("p_0002", "Метод", page=2),
            _paragraph("p_0003", "Подход описан в [4].", page=2),
        ],
        tree=tree,
    )
    citation = extract_citations(document).citations[0]
    assert citation.section == "2 Метод"


def test_long_context_is_trimmed_with_ellipsis() -> None:
    """Контекст сжимается до окна вокруг ссылки, а не копирует абзац целиком."""
    text = "Начало. " + "очень длинное предложение " * 12 + "[7] " + "хвост " * 12
    citation = _citations_of(text)[0]

    assert len(citation.context) < len(text)
    assert citation.context.startswith("…")
    assert citation.context.endswith("…")


def test_multi_source_citation_produces_two_citations() -> None:
    """В отчёте группа из двух источников — две ссылки со своими целями."""
    text = "Сравнение дано в (Иванов, 2020; Петров, 2021)."
    citations = _citations_of(text)

    assert [c.style for c in citations] == ["author_year", "author_year"]
    assert [c.targets for c in citations] == [["Иванов, 2020"], ["Петров, 2021"]]
    assert [c.authors for c in citations] == [["Иванов"], ["Петров"]]
    assert [c.year for c in citations] == [2020, 2021]
    assert [c.block_id for c in citations] == ["p_0001", "p_0001"]
    for citation in citations:
        assert text[citation.start_char : citation.end_char] == citation.raw
    assert citations[0].end_char <= citations[1].start_char


def test_multi_source_citations_resolve_independently() -> None:
    """Каждая запись сопоставляется со своей записью списка литературы."""
    bibliography = [
        _bib_entry(1, "Иванов И.И. Методы анализа. — М., 2020."),
        _bib_entry(2, "Петров П.П. Другие методы. — СПб., 2021."),
    ]
    report = extract_citations(
        _document(
            [_paragraph("p_0001", "См. (Иванов, 2020; Петров, 2021).")],
            bibliography=bibliography,
        )
    )

    assert [c.resolved_bib_ids for c in report.citations] == [["bib_001"], ["bib_002"]]
    assert report.summary.total == 2
    assert report.summary.resolved == 2
    assert report.summary.unique_sources == 2


def test_table_cell_citations_are_scanned() -> None:
    """Ссылки в ячейках таблиц попадают в отчёт со block_id абзаца ячейки."""
    table = _table_block(
        "t_0001",
        [
            [_paragraph("p_0002", "Показатель [4]", page=5, container_type="table_cell")],
            [_paragraph("p_0003", "Метод (Иванов, 2020)", page=5, container_type="table_cell")],
        ],
        page=5,
    )
    document = _document(
        [
            _paragraph("p_0001", "Основной текст [1].", page=4),
            table,
            _paragraph("p_0004", "Продолжение [2].", page=5),
        ],
        bibliography=[_bib_entry(4, "Сидоров С.С. Работа. — М., 2021.")],
    )
    citations = extract_citations(document).citations

    assert [c.block_id for c in citations] == ["p_0001", "p_0002", "p_0003", "p_0004"]
    assert citations[1].raw == "[4]"
    assert citations[1].page_est == 5
    assert citations[1].resolved_bib_ids == ["bib_004"]
    assert citations[2].style == "author_year"


def test_headers_and_textboxes_are_still_skipped() -> None:
    """Колонтитулы и надписи не сканируются: в отчёт идут только body и table_cell."""
    document = _document(
        [
            _paragraph("p_0001", "Колонтитул [9]", container_type="header"),
            _paragraph("p_0002", "Надпись [8]", container_type="textbox"),
            _paragraph("p_0003", "Текст [1]."),
        ]
    )
    citations = extract_citations(document).citations

    assert [c.block_id for c in citations] == ["p_0003"]


# ---------------------------------------------------------------------
# Раздел литературы и сопоставление
# ---------------------------------------------------------------------


def test_bibliography_section_entries_are_not_citations() -> None:
    """Записи раздела литературы не попадают в отчёт как ссылки в тексте."""
    tree = [_tree_node("n_bib", "СПИСОК ЛИТЕРАТУРЫ", "p_0002", role="bibliography", page=2)]
    document = _document(
        [
            _paragraph("p_0001", "В тексте есть ссылка [1].", page=1),
            _paragraph("p_0002", "СПИСОК ЛИТЕРАТУРЫ", page=2),
            _paragraph("p_0003", "1. Иванов И.И. Теория. — М., 2020.", page=2),
            _paragraph("p_0004", "[5] Смирнов А.А. Практика. — СПб., 2021.", page=2),
        ],
        tree=tree,
    )
    citations = extract_citations(document).citations

    assert len(citations) == 1
    assert citations[0].block_id == "p_0001"


def test_numeric_citation_resolves_against_bibliography() -> None:
    """«[3]» сопоставляется с записью №3 списка литературы."""
    bibliography = [
        _bib_entry(1, "Иванов И.И. Первая работа. — М., 2019."),
        _bib_entry(2, "Петров П.П. Вторая работа. — М., 2020."),
        _bib_entry(3, "Сидоров С.С. Третья работа. — СПб., 2021."),
    ]
    citations = _citations_of("См. [3].", bibliography=bibliography)

    assert citations[0].resolution == "resolved"
    assert citations[0].resolved_bib_ids == ["bib_003"]


def test_author_year_citation_resolves_by_surname_and_year() -> None:
    """Авторско-годовая ссылка ищется по фамилии и году в тексте записи."""
    bibliography = [
        _bib_entry(1, "Иванов И.И. Методы анализа. — М.: Наука, 2020. — 200 с."),
        _bib_entry(2, "Петров П.П. Другие методы. — М., 2021."),
    ]
    citations = _citations_of("Подход описан в (Иванов, 2020).", bibliography=bibliography)

    assert citations[0].style == "author_year"
    assert citations[0].resolved_bib_ids == ["bib_001"]


def test_unresolved_citation_without_bibliography_is_marked_honestly() -> None:
    """Без списка литературы ссылка остается unresolved и отчёт это объясняет."""
    report = extract_citations(_document([_paragraph("p_0001", "См. [3].")]))

    assert report.citations[0].resolution == "unresolved"
    assert report.citations[0].resolved_bib_ids == []
    assert NOTE_NO_BIBLIOGRAPHY in report.not_extracted


def test_pdf_only_finds_citations_and_explains_missing_bibliography() -> None:
    """pdf_only: текст блоков сканируется, но записи списка литературы недоступны."""
    report = extract_citations(
        _document([_paragraph("p_0001", "Ранее показано [34].")], parse_mode="pdf_only")
    )

    assert report.summary.total == 1
    assert report.citations[0].raw == "[34]"
    assert report.citations[0].resolution == "unresolved"
    assert NOTE_PDF_BIBLIOGRAPHY in report.not_extracted


def test_summary_counts_styles_and_unique_sources() -> None:
    """Сводка считает стили, сопоставление и уникальные источники."""
    bibliography = [_bib_entry(1, "Иванов И.И. Работа. — М., 2020.")]
    document = _document(
        [
            _paragraph("p_0001", "См. [1], [1, с. 5] и (Иванов, 2020)."),
            _paragraph("p_0002", f"Диапазон [1{EN_DASH}2]."),
        ],
        bibliography=bibliography,
    )
    summary = extract_citations(document).summary

    assert summary.total == 4
    assert summary.by_style == {
        "numeric": 1,
        "numeric_with_page": 1,
        "author_year": 1,
        "numeric_range": 1,
    }
    assert summary.resolved == 4
    assert summary.unique_sources == 1


# ---------------------------------------------------------------------
# Контракт отчёта и сервис
# ---------------------------------------------------------------------


def test_report_round_trips_through_json() -> None:
    """Отчёт сериализуется и читается обратно без потерь."""
    report = extract_citations(_document([_paragraph("p_0001", "См. [3].")]))
    restored = CitationReport.model_validate_json(report.model_dump_json())

    assert restored.schema_version == CITATION_SCHEMA_VERSION
    assert restored.citations == report.citations


def test_service_reads_parse_json_and_writes_citations_json(tmp_path: Path) -> None:
    """Сервис берет JSON разбора и пишет citations.json, не разбирая документ заново."""
    document = _document(
        [_paragraph("p_0001", "См. [2].")],
        bibliography=[_bib_entry(2, "Петров П.П. Работа. — М., 2020.")],
    )
    parse_json = tmp_path / "parse.json"
    parse_json.write_text(document.model_dump_json(), encoding="utf-8")
    output = tmp_path / "out" / "citations.json"

    report = extract_citations_to_json_file(parse_json, output)

    assert output.exists()
    written = CitationReport.model_validate_json(output.read_text(encoding="utf-8"))
    assert written.summary.total == 1
    assert written.citations[0].resolved_bib_ids == ["bib_002"]
    assert report.citations == written.citations


def test_service_reads_report_without_document_source(tmp_path: Path) -> None:
    """JSON разбора читается моделью ParsedDocument и дает тот же отчёт."""
    document = _document([_paragraph("p_0001", "См. (Иванов, 2020).")])
    parse_json = tmp_path / "parse.json"
    parse_json.write_text(document.model_dump_json(), encoding="utf-8")

    report = extract_citations_from_json(parse_json)

    assert report.source.filename == "vkr.docx"
    assert report.citations[0].style == "author_year"
