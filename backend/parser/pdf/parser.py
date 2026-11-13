"""Автономный разбор PDF в ParsedDocument режима pdf_only.

Парсер собирает результат из трех слоев: extractor.py дает факты листов
(строки, таблицы, изображения), layout.py оценивает вёрстку и точные номера
страниц, structure.py восстанавливает блоки, оглавление и дерево документа.
DOCX и LibreOffice не используются: единственный источник — PDF.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from parser.core.schema import (
    BibliographyEntry,
    CrossReferenceIndex,
    DocumentAnomalies,
    DocumentCapabilities,
    DocumentMeta,
    GlobalSettings,
    NumberedCaptionEntity,
    PageCountInfo,
    ParsedDocument,
    SourceInfo,
    TextCrossReferenceMention,
    ThemeData,
)
from parser.enrichment import extract_bibliography, extract_captions, extract_mentions

from .extractor import PdfDocument, PdfExtractor
from .layout import PdfLayout, PdfLayoutEstimator
from .structure import PdfStructure, PdfStructureBuilder

#: Режим разбора, который реализует этот пакет.
PARSE_MODE = "pdf_only"

#: Формат исходного файла для meta.source_format.
SOURCE_FORMAT = "pdf"

#: Минимальное число страниц в документе: контракт PageCountInfo требует >= 1.
MIN_PAGE_COUNT = 1

#: Регулярное выражение слова для статистики (как в Word: цепочки непробельных).
RE_WORD = re.compile(r"\S+")


class PdfDocumentParser:
    """Разбирает один PDF в ParsedDocument с parse_mode="pdf_only"."""

    def __init__(self, pdf_path: Optional[str | Path] = None, detect_tables: bool = True) -> None:
        """Запоминает путь к PDF (его можно передать и в parse) и режим поиска таблиц."""
        self.pdf_path = Path(pdf_path) if pdf_path is not None else None
        self.detect_tables = detect_tables

    def extract(self, pdf_path: Optional[str | Path] = None) -> PdfDocument:
        """Извлекает постраничные факты PDF без сборки модели отчета."""
        return PdfExtractor(self._resolve_path(pdf_path), self.detect_tables).extract()

    def parse(self, pdf_path: Optional[str | Path] = None) -> ParsedDocument:
        """Разбирает PDF и возвращает ParsedDocument режима pdf_only."""
        document = self.extract(pdf_path)
        layout = PdfLayoutEstimator(document).estimate()
        structure = PdfStructureBuilder(document, layout).build()
        return self._assemble(document, layout, structure)

    def _resolve_path(self, pdf_path: Optional[str | Path]) -> Path:
        """Выбирает путь из аргумента метода или из конструктора."""
        path = Path(pdf_path) if pdf_path is not None else self.pdf_path
        if path is None:
            raise ValueError("Не передан путь к PDF-файлу")
        return path

    # -----------------------------------------------------------------
    # Сборка модели отчета
    # -----------------------------------------------------------------

    def _assemble(
        self,
        document: PdfDocument,
        layout: PdfLayout,
        structure: PdfStructure,
    ) -> ParsedDocument:
        """Собирает ParsedDocument из извлеченных данных, вёрстки и структуры."""
        total_pages = max(MIN_PAGE_COUNT, document.page_count)
        page_count = PageCountInfo(
            total_pages=total_pages,
            method="pdf_exact",
            confidence="high",
            note=None,
        )
        meta = self._build_meta(document, layout, structure, total_pages)
        capabilities = self._build_capabilities()
        result = ParsedDocument(
            parse_mode=PARSE_MODE,
            source=SourceInfo(
                mode=PARSE_MODE,
                has_docx=False,
                has_pdf=True,
                pdf_filename=document.filename,
                pdf_sha256=document.sha256,
            ),
            capabilities=capabilities,
            page_count=page_count,
            meta=meta,
            settings=GlobalSettings(),
            theme=ThemeData(),
            styles={},
            sections=layout.sections,
            pdf_pages_audit=layout.pages_audit,
            blocks=structure.blocks,
            visuals=structure.visuals,
            footnotes=[],
            document_tree=structure.document_tree,
            cross_ref_index=CrossReferenceIndex(),
            anomalies=DocumentAnomalies(),
        )
        self._enrich(result)
        return result

    # -----------------------------------------------------------------
    # Обогащение: общий слой parser.enrichment
    # -----------------------------------------------------------------

    @staticmethod
    def _enrich(result: ParsedDocument) -> None:
        """Заполняет cross_ref_index общим слоем enrichment и правит capabilities.

        Слой формат-независим: библиография, подписи и упоминания извлекаются
        эвристически по блокам PDF и document_tree, поэтому в pdf_only индекс
        перекрестных ссылок больше не пустой (см. ARCHITECTURE.md, 4.2.1).
        """
        bibliography = extract_bibliography(result)
        captions = extract_captions(result)
        mentions = extract_mentions(result, bibliography=bibliography, captions=captions)
        result.cross_ref_index = CrossReferenceIndex(
            figures=[caption for caption in captions if caption.entity_type == "figure"],
            tables=[caption for caption in captions if caption.entity_type == "table"],
            Apps=[caption for caption in captions if caption.entity_type == "appendix"],
            bibliography=bibliography,
            mentions=mentions,
        )
        _mark_cited_bibliography(bibliography, mentions)
        _mark_mentioned_captions(captions, mentions, _block_order(result))
        # Флаг описывает наличие слоя, а не непустоту коллекций: обогащение
        # отработало, поэтому перекрестные ссылки считаются разобранными.
        result.capabilities.cross_references = True
        # Визуальный реестр появляется только там, где подпись рисунка связалась
        # с растровым изображением PDF (у остальных фигур target_object_id пуст).
        result.capabilities.visual_registry = any(
            caption.target_object_id
            for caption in captions
            if caption.entity_type == "figure"
        )

    @staticmethod
    def _build_capabilities() -> DocumentCapabilities:
        """Базовая матрица pdf_only: DOCX-производных данных в режиме нет.

        Флаги cross_references/visual_registry поднимает слой обогащения
        (``_enrich``), когда он действительно заполнил индекс.
        """
        return DocumentCapabilities(
            styles_parsed=False,
            numbering_parsed=False,
            sections_parsed=False,
            exact_pagination=True,
            pdf_layout_coordinates=True,
            visual_registry=False,
            footnotes_parsed=False,
            cross_references=False,
            word_metadata=False,
            page_count_estimated=False,
        )

    @staticmethod
    def _build_meta(
        document: PdfDocument,
        layout: PdfLayout,
        structure: PdfStructure,
        total_pages: int,
    ) -> DocumentMeta:
        """Собирает метаданные: файл, пагинацию, статистику по тексту PDF."""
        words = len(RE_WORD.findall(structure.full_text))
        chars_with_spaces = len(structure.full_text)
        chars_no_spaces = len(re.sub(r"\s+", "", structure.full_text))
        return DocumentMeta(
            filename=document.filename,
            file_sha256=document.sha256,
            source_format=SOURCE_FORMAT,
            converted_via_lo=False,
            conversion_tool=None,
            pdf_filename=document.filename,
            pdf_sha256=document.sha256,
            rendered_page_numbers_present=layout.rendered_page_numbers_present,
            pagination_source="pdf_exact",
            pdf_docx_sync_score=None,
            word_stat_pages=total_pages,
            word_stat_words=words,
            word_stat_chars_with_spaces=chars_with_spaces,
            word_stat_chars_no_spaces=chars_no_spaces,
            pdf_dominant_font_size_pt=layout.dominant_font_size_pt,
            pdf_median_leading_ratio=layout.median_leading_ratio,
            pdf_median_line_pitch_mm=layout.median_line_pitch_mm,
            pdf_leading_sample_count=layout.leading_sample_count,
        )


def _block_order(document: ParsedDocument) -> Dict[str, int]:
    """Порядковый номер блока в потоке документа: нужен для сравнения «до/после»."""
    return {block.block_id: index for index, block in enumerate(document.blocks)}


def _mark_cited_bibliography(
    bibliography: Sequence[BibliographyEntry],
    mentions: Sequence[TextCrossReferenceMention],
) -> None:
    """Проставляет is_cited_in_text записям, на которые ссылается текст."""
    cited: Set[str] = set()
    for mention in mentions:
        if mention.ref_type == "bibliography":
            cited.update(mention.resolved_entity_ids)
    for entry in bibliography:
        if entry.bib_id in cited:
            entry.is_cited_in_text = True


def _mark_mentioned_captions(
    captions: Sequence[NumberedCaptionEntity],
    mentions: Sequence[TextCrossReferenceMention],
    block_order: Dict[str, int],
) -> None:
    """Проставляет подписям признаки упоминания в тексте, как в DOCX-конвейере."""
    by_entity: Dict[str, List[TextCrossReferenceMention]] = {}
    for mention in mentions:
        for entity_id in mention.resolved_entity_ids:
            by_entity.setdefault(entity_id, []).append(mention)
    for caption in captions:
        related = by_entity.get(caption.entity_id)
        if not related:
            continue
        first = related[0]
        caption.is_mentioned_in_text = True
        caption.first_mention_block_id = first.block_id
        caption.first_mention_page_est = first.page_est
        caption.all_mention_ids = [mention.mention_id for mention in related]
        caption_order = block_order.get(caption.caption_block_id or "")
        mention_order = block_order.get(first.block_id)
        if caption_order is not None and mention_order is not None:
            caption.is_mentioned_before_appearance = mention_order < caption_order


def parse_pdf(
    pdf_path: str | Path, detect_tables: bool = True
) -> ParsedDocument:
    """Удобная функция: разбирает PDF по пути и возвращает ParsedDocument."""
    return PdfDocumentParser(pdf_path, detect_tables=detect_tables).parse()
