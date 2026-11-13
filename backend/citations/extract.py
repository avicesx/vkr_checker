"""Извлечение внутритекстовых ссылок на источники из разобранного документа.

Сканер работает поверх ``ParsedDocument.blocks``, а не поверх XML или PDF, —
поэтому одинаково применим к ``docx_only``, ``pdf_only`` и ``docx_pdf``. Для
DOCX список литературы уже разобран парсером (``cross_ref_index.bibliography``),
и ссылки сопоставляются с записями; в PDF записи списка не извлекаются, поэтому
ссылки находятся и получают место в документе, но остаются ``unresolved``.

Раздел литературы исключается из поиска по ``document_tree``: у обоих
конвейеров он размечен ``semantic_role="bibliography"``, поэтому записи списка
не попадают в отчёт как «ссылки в тексте».
"""
from __future__ import annotations

import re
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

from parser.core.schema import (
    BibliographyEntry,
    DocumentTreeNode,
    ParagraphBlock,
    ParsedDocument,
)

from .patterns import (
    RE_AUTHOR_YEAR_CITATION,
    RE_BRACKET_CITATION,
    parse_author_year_citation,
    parse_bracket_citation,
)
from .schema import Citation, CitationReport, CitationSource, CitationSummary

#: Радиус окна контекста вокруг ссылки, символов.
CONTEXT_RADIUS = 60

#: Причины, по которым часть ссылок могла не попасть в отчёт.
NOTE_NO_BIBLIOGRAPHY = (
    "Список литературы не извлечён: ссылки найдены, но сопоставить их с источниками нельзя"
)
NOTE_NO_CITATIONS = "В тексте документа не найдено ссылок на источники"
NOTE_PDF_BIBLIOGRAPHY = (
    "Режим pdf_only: записи списка литературы не извлекаются, сопоставление ссылок недоступно"
)


def extract_citations(document: ParsedDocument) -> CitationReport:
    """Собирает отчёт о ссылках на источники для разобранного документа."""
    paragraphs = _body_paragraphs(document.blocks)
    skipped = _bibliography_block_ids(paragraphs, document.document_tree)
    section_titles = _section_titles(paragraphs, document.document_tree)
    bibliography = list(document.cross_ref_index.bibliography)

    citations: List[Citation] = []
    for block in paragraphs:
        if block.block_id in skipped:
            continue
        text = block.text_visible
        if not text or not text.strip():
            continue
        for match in _iter_matches(text):
            citations.append(
                _build_citation(
                    sequence=len(citations) + 1,
                    block=block,
                    text=text,
                    start=match.start,
                    end=match.end,
                    raw=match.raw,
                    style=match.style,
                    targets=match.targets,
                    page_in_source=match.page_in_source,
                    authors=match.authors,
                    year=match.year,
                    section=section_titles.get(block.block_id),
                    bibliography=bibliography,
                )
            )

    return _build_report(document, citations, bibliography)


# ---------------------------------------------------------------------
# Поиск вхождений
# ---------------------------------------------------------------------


class _FoundMatch:
    """Найденное вхождение ссылки до сборки модели отчёта."""

    __slots__ = ("start", "end", "raw", "style", "targets", "page_in_source", "authors", "year")

    def __init__(
        self,
        *,
        start: int,
        end: int,
        raw: str,
        style: str,
        targets: Sequence[str],
        page_in_source: Optional[str] = None,
        authors: Sequence[str] = (),
        year: Optional[int] = None,
    ) -> None:
        self.start = start
        self.end = end
        self.raw = raw
        self.style = style
        self.targets = list(targets)
        self.page_in_source = page_in_source
        self.authors = list(authors)
        self.year = year


def _iter_matches(text: str) -> Iterator[_FoundMatch]:
    """Перебирает вхождения всех форм ссылок в порядке следования по тексту."""
    matches: List[_FoundMatch] = []

    for match in RE_BRACKET_CITATION.finditer(text):
        parsed = parse_bracket_citation(match.group(0))
        if parsed is None:
            continue
        matches.append(
            _FoundMatch(
                start=match.start(),
                end=match.end(),
                raw=parsed.raw,
                style=parsed.style,
                targets=parsed.targets,
                page_in_source=parsed.page_in_source,
            )
        )

    for match in RE_AUTHOR_YEAR_CITATION.finditer(text):
        parsed = parse_author_year_citation(match.group(0))
        if parsed is None:
            continue
        matches.append(
            _FoundMatch(
                start=match.start(),
                end=match.end(),
                raw=parsed.raw,
                style=parsed.style,
                targets=parsed.targets,
                page_in_source=parsed.page_in_source,
                authors=parsed.authors,
                year=parsed.year,
            )
        )

    matches.sort(key=lambda item: item.start)
    last_end = -1
    for item in matches:
        if item.start < last_end:
            continue
        last_end = item.end
        yield item


# ---------------------------------------------------------------------
# Сборка модели
# ---------------------------------------------------------------------


def _build_citation(
    *,
    sequence: int,
    block: ParagraphBlock,
    text: str,
    start: int,
    end: int,
    raw: str,
    style: str,
    targets: Sequence[str],
    page_in_source: Optional[str],
    authors: Sequence[str],
    year: Optional[int],
    section: Optional[str],
    bibliography: Sequence[BibliographyEntry],
) -> Citation:
    """Создает модель одного вхождения и сразу сопоставляет ее со списком литературы."""
    resolved = _resolve_targets(
        style=style,
        targets=targets,
        authors=authors,
        year=year,
        bibliography=bibliography,
    )
    return Citation(
        citation_id=f"cit_{sequence:04d}",
        raw=raw,
        style=style,  # type: ignore[arg-type]
        targets=list(targets),
        page_in_source=page_in_source,
        authors=list(authors),
        year=year,
        resolved_bib_ids=resolved,
        resolution="resolved" if resolved else "unresolved",
        block_id=block.block_id,
        page_est=block.page_start_est,
        start_char=start,
        end_char=end,
        section=section,
        context=_context(text, start, end),
    )


def _resolve_targets(
    *,
    style: str,
    targets: Sequence[str],
    authors: Sequence[str],
    year: Optional[int],
    bibliography: Sequence[BibliographyEntry],
) -> List[str]:
    """Сопоставляет цели ссылки с записями списка литературы."""
    if not bibliography:
        return []

    if style == "author_year":
        if year is None or not authors:
            return []
        resolved: List[str] = []
        for entry in bibliography:
            haystack = entry.raw_text.casefold()
            if str(year) not in haystack:
                continue
            if any(author.casefold() in haystack for author in authors):
                resolved.append(entry.bib_id)
        return _dedupe(resolved)

    by_index: Dict[str, str] = {str(entry.order_index): entry.bib_id for entry in bibliography}
    return _dedupe([by_index[target] for target in targets if target in by_index])


def _build_report(
    document: ParsedDocument,
    citations: List[Citation],
    bibliography: Sequence[BibliographyEntry],
) -> CitationReport:
    """Собирает корневой отчёт и сводку, включая честные причины неполноты."""
    by_style: Dict[str, int] = {}
    for citation in citations:
        by_style[citation.style] = by_style.get(citation.style, 0) + 1

    resolved = sum(1 for citation in citations if citation.resolution == "resolved")
    unique_sources = len(
        {bib_id for citation in citations for bib_id in citation.resolved_bib_ids}
    )

    notes: List[str] = []
    if not citations:
        notes.append(NOTE_NO_CITATIONS)
    if not bibliography:
        notes.append(NOTE_PDF_BIBLIOGRAPHY if document.parse_mode == "pdf_only" else NOTE_NO_BIBLIOGRAPHY)

    return CitationReport(
        source=CitationSource(
            filename=document.meta.filename,
            sha256=document.meta.file_sha256,
            parse_mode=document.parse_mode,
            page_count=document.page_count.total_pages,
        ),
        citations=citations,
        summary=CitationSummary(
            total=len(citations),
            by_style=by_style,
            resolved=resolved,
            unresolved=len(citations) - resolved,
            unique_sources=unique_sources,
        ),
        not_extracted=notes,
    )


# ---------------------------------------------------------------------
# Вспомогательные проходы по документу
# ---------------------------------------------------------------------


def _body_paragraphs(blocks: Sequence[object]) -> List[ParagraphBlock]:
    """Оставляет абзацы основного текста: колонтитулы, сноски и таблицы не сканируем."""
    return [
        block
        for block in blocks
        if isinstance(block, ParagraphBlock) and block.container_type == "body"
    ]


def _flatten_tree(nodes: Sequence[DocumentTreeNode]) -> Iterator[DocumentTreeNode]:
    """Обходит дерево документа в порядке следования узлов."""
    for node in nodes:
        yield node
        yield from _flatten_tree(node.children)


def _bibliography_block_ids(
    paragraphs: Sequence[ParagraphBlock],
    document_tree: Sequence[DocumentTreeNode],
) -> Set[str]:
    """Возвращает абзацы раздела литературы, чтобы не считать их записи ссылками."""
    index_by_block = {paragraph.block_id: index for index, paragraph in enumerate(paragraphs)}
    marks: List[Tuple[int, DocumentTreeNode]] = []
    for node in _flatten_tree(document_tree):
        index = index_by_block.get(node.heading_block_id or "")
        if index is not None:
            marks.append((index, node))
    marks.sort(key=lambda pair: pair[0])

    skipped: Set[str] = set()
    for position, (start, node) in enumerate(marks):
        if node.semantic_role != "bibliography":
            continue
        end = len(paragraphs)
        for next_index, next_node in marks[position + 1 :]:
            if next_node.level <= node.level:
                end = next_index
                break
        skipped.update(paragraph.block_id for paragraph in paragraphs[start:end])
    return skipped


def _section_titles(
    paragraphs: Sequence[ParagraphBlock],
    document_tree: Sequence[DocumentTreeNode],
) -> Dict[str, str]:
    """Сопоставляет каждому абзацу ближайший предшествующий заголовок раздела."""
    index_by_block = {paragraph.block_id: index for index, paragraph in enumerate(paragraphs)}
    marks: List[Tuple[int, str]] = []
    for node in _flatten_tree(document_tree):
        index = index_by_block.get(node.heading_block_id or "")
        if index is not None:
            marks.append((index, node.title))
    marks.sort(key=lambda pair: pair[0])

    titles: Dict[str, str] = {}
    pointer = 0
    current: Optional[str] = None
    for index, paragraph in enumerate(paragraphs):
        while pointer < len(marks) and marks[pointer][0] <= index:
            current = marks[pointer][1]
            pointer += 1
        if current is not None:
            titles[paragraph.block_id] = current
    return titles


def _context(text: str, start: int, end: int, radius: int = CONTEXT_RADIUS) -> str:
    """Возвращает сжатое окно текста вокруг ссылки для ручной проверки."""
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = re.sub(r"\s+", " ", text[left:right]).strip()
    if left > 0:
        snippet = "…" + snippet
    if right < len(text):
        snippet = snippet + "…"
    return snippet


def _dedupe(values: Sequence[str]) -> List[str]:
    """Убирает повторы, сохраняя порядок появления."""
    seen: Set[str] = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
