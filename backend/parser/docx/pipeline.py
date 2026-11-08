"""Оркестратор конвейера .docx (+ PDF) -> ParsedDocument.

Пакет разбирает уже нормализованный OOXML-пакет. Legacy-форматы (.doc, .rtf,
.odt и другие, читаемые LibreOffice) сначала конвертируются в .docx модулем
parser.docx.converter. Публичных точек входа три:

    DocxParserPipeline.parse()          — с PDF режим docx_pdf, без PDF docx_only;
    DocxParserPipeline.parse_docx_only() — только DOCX, переданный PDF игнорируется;
    parse_docx_to_json_file             — нативный .docx и запись JSON на диск;
    parse_any_to_json_file              — любой поддерживаемый формат: при
                                          необходимости конвертация LibreOffice,
                                          затем разбор и запись JSON.

Документ проходит семь фаз:

1. Каскад стилей, нумерация, создание парсеров.
2. Обнаружение секций по w:sectPr.
3. Потоковый разбор блоков w:body (абзацы, таблицы).
4. Сноски и связи «подпись — таблица — продолжение».
5. Выравнивание блоков по физическим страницам PDF.
6. Оглавление, дерево документа, подписи и перекрестные ссылки.
7. Метаданные, настройки и аномалии оформления.

Порядок фаз фиксирован: структура и ссылки анализируются после PDF-выравнивания,
поэтому страницы в отчете физические, а не оценочные. Без PDF пятая фаза
пропускается, и отчет честно помечает пагинацию как оценочную: page_count.method
равен docx_native_breaks при наличии w:lastRenderedPageBreak, иначе
geometric_estimation. Режим и его возможности попадают в parse_mode, source и
capabilities (контракт описан в mode.py и ARCHITECTURE.md).

PDF-сверка: эталонный PDF должен быть снят с ИСХОДНОГО файла. LibreOffice
пересчитывает вёрстку при конвертации .doc -> .docx, поэтому PDF, отрендеренный
из уже сконвертированного docx, даст другую геометрию и другие номера страниц.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple
from lxml import etree

from .metadata_anomalies_parser import MetaAndAnomaliesParser
from parser.core.mode import ParseMode, detect_mode
from .paragraph_parser import NS_EXT, NumberingStateTracker, ParagraphParser
from .pdf_page_aligner import PdfPageAligner
from parser.core.schema import (
    BlockUnion,
    DocumentCapabilities,
    DocumentMeta,
    PageCountInfo,
    ParagraphBlock,
    ParsedDocument,
    SectionProperties,
    SourceInfo,
    TableBlock,
    TocContainerBlock,
)
from .section_header_footer_parser import HybridPageTracker, SectionAndHeaderFooterParser
from .structure_crossref_parser import StructureCrossRefParser
from .style_resolver import NS, StyleCascadeResolver, TableCascadeContext, safe_fromstring
from .table_parser import TableParser, link_table_captions_and_continuations
from .visual_and_math_parser import VisualAndMathParser


class DocxParserPipeline:
    """
    Главный оркестратор: нормализованный .docx (+ эталонный PDF) -> ParsedDocument.

    parse() выбирает режим по фактическим файлам: с переданным PDF это docx_pdf,
    без PDF — docx_only. parse_docx_only() принудительно разбирает только DOCX
    и игнорирует PDF из конструктора. Обе точки входа вызывают единый
    _parse_core, поэтому семь фаз разбора не дублируются. PDF-выравнивание
    выполняется до анализа структуры, чтобы оглавление и перекрестные ссылки
    сверялись по физическим страницам листа.
    """

    def __init__(
        self,
        docx_path: str | Path,
        pdf_path: Optional[str | Path] = None,
        source_format: Optional[str] = None,
        converted_via_lo: bool = False,
        conversion_tool: Optional[str] = None,
        conversion_duration_ms: Optional[int] = None,
        lo_version: Optional[str] = None,
        lo_image: Optional[str] = None,
    ) -> None:
        """
        Запоминает пути к DOCX и PDF и параметры источника.

        source_format, converted_via_lo и сведения о конвертере попадают в meta
        и позволяют отличить нативный .docx от результата LibreOffice.
        """
        self.docx_path = str(docx_path)
        self.pdf_path = Path(pdf_path) if pdf_path is not None else None
        self.source_format = source_format
        self.converted_via_lo = converted_via_lo
        self.conversion_tool = conversion_tool
        self.conversion_duration_ms = conversion_duration_ms
        self.lo_version = lo_version
        self.lo_image = lo_image

    def parse(self) -> ParsedDocument:
        """
        Выбирает режим по фактическим файлам и собирает отчет.

        С переданным в конструктор PDF разбор идет в режиме docx_pdf (PDF
        уточняет физические страницы), без PDF — в режиме docx_only.

        Raises:
            ValueError: в пакете нет word/document.xml или w:body.
        """
        return self._parse_core(self.docx_path, self.pdf_path)

    def parse_docx_only(self) -> ParsedDocument:
        """
        Разбирает только DOCX: PDF, переданный в конструктор, игнорируется.

        Raises:
            ValueError: в пакете нет word/document.xml или w:body.
        """
        return self._parse_core(self.docx_path, None, requested_mode="docx_only")

    def _parse_core(
        self,
        docx_path: str | Path,
        pdf_path: Optional[str | Path] = None,
        requested_mode: Optional[str] = None,
    ) -> ParsedDocument:
        """Единый конвейер из семи фаз и сборка контракта режима разбора."""
        parse_mode = detect_mode(docx_path, pdf_path, requested_mode)
        if parse_mode == "pdf_only":
            raise ValueError(
                "Режим pdf_only разбирает только PDF: "
                "DocxParserPipeline его не выполняет"
            )
        # PDF участвует в разборе только в комбинированном режиме docx_pdf.
        has_pdf = parse_mode == "docx_pdf" and pdf_path is not None

        alignment_report = None
        with zipfile.ZipFile(docx_path, "r") as zf:
            namelist = set(zf.namelist())
            if "word/document.xml" not in namelist:
                raise ValueError(f"Файл '{docx_path}' не содержит word/document.xml")

            styles_bytes = zf.read("word/styles.xml") if "word/styles.xml" in namelist else None
            theme_bytes = zf.read("word/theme/theme1.xml") if "word/theme/theme1.xml" in namelist else None
            num_bytes = zf.read("word/numbering.xml") if "word/numbering.xml" in namelist else None
            doc_bytes = zf.read("word/document.xml")

            doc_rels_map = self._parse_relationships(zf, "word/_rels/document.xml.rels")

            # ---------------------------------------------------------
            # Фаза 1: инициализация каскада стилей, нумерации и парсеров
            # ---------------------------------------------------------
            style_resolver = StyleCascadeResolver(
                styles_xml_bytes=styles_bytes,
                theme_xml_bytes=theme_bytes,
                numbering_xml_bytes=num_bytes,
            )
            numbering_tracker = NumberingStateTracker(numbering_xml_bytes=num_bytes)

            paragraph_parser = ParagraphParser(
                style_resolver=style_resolver,
                numbering_tracker=numbering_tracker,
                rels_map=doc_rels_map,
            )
            visual_math_parser = VisualAndMathParser(
                zf=zf,
                doc_rels_map=doc_rels_map,
                paragraph_parser=paragraph_parser,
            )
            # Подключаем колбэки регистрации картинок и формул к ParagraphParser
            paragraph_parser.visual_extractor_cb = visual_math_parser.register_visual_from_callback
            paragraph_parser.equation_extractor_cb = visual_math_parser.register_equation_from_callback
            paragraph_parser.ole_prog_id_resolver = visual_math_parser.resolve_ole_prog_id

            table_parser = TableParser(
                style_resolver=style_resolver,
                styles_xml_bytes=styles_bytes,
            )
            section_parser = SectionAndHeaderFooterParser(
                zf=zf,
                doc_rels_map=doc_rels_map,
                style_resolver=style_resolver,
                paragraph_parser=paragraph_parser,
                table_parser=table_parser,
                visual_math_parser=visual_math_parser,
                rels_loader=lambda rel_path: self._parse_relationships(zf, rel_path),
            )

            # ---------------------------------------------------------
            # Фаза 2: предварительное обнаружение разделов (w:sectPr)
            # ---------------------------------------------------------
            doc_root = safe_fromstring(doc_bytes)
            body_elem = doc_root.find("w:body", namespaces=NS)
            if body_elem is None:
                raise ValueError("В word/document.xml отсутствует тег <w:body>")

            sections, section_bounds = section_parser.discover_sections(body_elem)
            has_rendered_breaks = bool(
                body_elem.xpath(".//w:lastRenderedPageBreak", namespaces=NS)
            )
            page_tracker = HybridPageTracker(has_rendered_page_breaks_in_doc=has_rendered_breaks)

            # ---------------------------------------------------------
            # Фаза 3: потоковый разбор блоков w:body по разделам
            # ---------------------------------------------------------
            body_children = list(body_elem)
            blocks: List[BlockUnion] = []
            xml_para_to_block_map: Dict[etree._Element, ParagraphBlock] = {}

            p_seq = 0
            tbl_seq = 0

            for sec_idx, bound in enumerate(section_bounds):
                sec_props = sections[sec_idx]
                page_tracker.on_section_enter(sec_props, is_first_section=(sec_idx == 0))
                visual_math_parser.active_text_area_width_mm = sec_props.text_area_width_mm

                slice_nodes = body_children[bound.start_body_child_idx : bound.end_body_child_idx + 1]
                for node in self._iter_unwrapped_body_nodes(slice_nodes):
                    local_name = etree.QName(node).localname

                    if local_name == "p":
                        p_seq += 1
                        b_id = f"p_{p_seq:05d}"
                        p_block, page_after = paragraph_parser.parse_paragraph(
                            p_elem=node,
                            block_id=b_id,
                            section_idx=sec_idx,
                            start_page=page_tracker.current_page,
                            container_type="body",
                        )
                        visual_math_parser.attach_parent_paragraph_context(p_block)
                        vis_heights = [
                            visual_math_parser.visuals_registry[v_id].height_mm
                            for v_id in p_block.embedded_visual_ids
                            if v_id in visual_math_parser.visuals_registry
                        ]
                        page_tracker.advance_after_paragraph(p_block, page_after, vis_heights)
                        blocks.append(p_block)
                        xml_para_to_block_map[node] = p_block

                    elif local_name == "tbl":
                        tbl_seq += 1
                        b_id = f"tbl_{tbl_seq:04d}"

                        def _cell_child_cb(
                            child_el: etree._Element,
                            child_id: str,
                            s_idx: int,
                            cur_pg: int,
                            t_ctx: TableCascadeContext,
                        ) -> Tuple[BlockUnion, int]:
                            c_tag = etree.QName(child_el).localname
                            if c_tag == "tbl":
                                return table_parser.parse_table(
                                    child_el,
                                    child_id,
                                    s_idx,
                                    cur_pg,
                                    sec_props.text_area_width_mm,
                                    _cell_child_cb,
                                )
                            p_cell, new_pg = paragraph_parser.parse_paragraph(
                                child_el,
                                child_id,
                                s_idx,
                                cur_pg,
                                container_type="table_cell",
                                tbl_ctx=t_ctx,
                            )
                            visual_math_parser.attach_parent_paragraph_context(p_cell)
                            xml_para_to_block_map[child_el] = p_cell
                            return p_cell, new_pg

                        t_block, page_after = table_parser.parse_table(
                            tbl_elem=node,
                            block_id=b_id,
                            section_idx=sec_idx,
                            start_page=page_tracker.current_page,
                            page_text_area_width_mm=sec_props.text_area_width_mm,
                            child_parser=_cell_child_cb,
                        )
                        page_tracker.advance_after_table(t_block, page_after)
                        blocks.append(t_block)

                sec_props.page_end_est = page_tracker.current_page

            # ---------------------------------------------------------
            # Фаза 4: сноски и связывание переносов таблиц
            # ---------------------------------------------------------
            link_table_captions_and_continuations(blocks)
            footnotes = section_parser.parse_footnotes_and_endnotes(blocks)

            # ---------------------------------------------------------
            # Фаза 5: выравнивание по PDF (до анализа структуры)
            # ---------------------------------------------------------
            # Выравнивание выполняется до разбора структуры: страницы заголовков, подписей,
            # упоминаний и записей оглавления должны быть физическими. На эвристической
            # пагинации DOCX сверка оглавления дает ложные расхождения.
            alignment_report = None
            if has_pdf:
                alignment_report = PdfPageAligner(pdf_path).align_and_enrich(
                    blocks=blocks,
                    sections=sections,
                    visuals=visual_math_parser.visuals_registry,
                    equations=visual_math_parser.equations_registry,
                )

            # Источник страниц фиксируется до анализа структуры: сверять номера
            # оглавления с заголовками можно только при точной пагинации PDF.
            if alignment_report is not None and alignment_report.alignment_applied:
                pagination_source: Literal[
                    "pdf_exact", "docx_native_breaks", "geometric_fallback"
                ] = "pdf_exact"
            elif has_rendered_breaks:
                pagination_source = "docx_native_breaks"
            else:
                pagination_source = "geometric_fallback"

            # ---------------------------------------------------------
            # Фаза 6: структура, оглавление, библиография и перекрестные ссылки
            # ---------------------------------------------------------
            struct_parser = StructureCrossRefParser()
            blocks, doc_tree, cross_ref_index = struct_parser.process_document_structure(
                blocks=blocks,
                visuals=visual_math_parser.visuals_registry,
                equations=visual_math_parser.equations_registry,
                verify_page_numbers=(pagination_source == "pdf_exact"),
            )

            # Сноски создаются до выравнивания, поэтому после PDF-пагинации переносим их
            # страницы на страницы реально ссылающихся на них абзацев
            if alignment_report is not None and footnotes:
                block_pages = {b.block_id: b.page_start_est for b in blocks}
                for paragraph in struct_parser._iter_all_paragraphs_with_tables(blocks):
                    block_pages[paragraph.block_id] = paragraph.page_start_est
                for footnote in footnotes:
                    if footnote.referenced_in_block_id is not None:
                        footnote.page_est = block_pages.get(
                            footnote.referenced_in_block_id, footnote.page_est
                        )

            # ---------------------------------------------------------
            # Фаза 7: метаданные, настройки и детектор аномалий
            # ---------------------------------------------------------
            meta_anomalies_parser = MetaAndAnomaliesParser(
                zf=zf,
                docx_path=str(docx_path),
                source_format=self.source_format,
                converted_via_lo=self.converted_via_lo,
                conversion_tool=self.conversion_tool,
                conversion_duration_ms=self.conversion_duration_ms,
                lo_version=self.lo_version,
                lo_image=self.lo_image,
            )
            doc_meta = meta_anomalies_parser.parse_document_meta()
            global_settings = meta_anomalies_parser.parse_global_settings()
            doc_meta.pagination_source = pagination_source
            if alignment_report is not None:
                doc_meta.pdf_filename = alignment_report.pdf_filename
                doc_meta.pdf_sha256 = alignment_report.pdf_sha256
                doc_meta.pdf_docx_sync_score = alignment_report.sync_score
                # PDF — источник точной пагинации: значение Pages из docProps/app.xml
                # может быть устаревшим (например, 21 при фактических 231 страницах).
                doc_meta.word_stat_pages = alignment_report.total_pages
                doc_meta.rendered_page_numbers_present = any(
                    page.has_rendered_page_number
                    for page in alignment_report.pages_audit
                )
            anomalies = meta_anomalies_parser.collect_anomalies(
                body_elem=body_elem,
                blocks=blocks,
                xml_para_to_block_map=xml_para_to_block_map,
                pdf_pages_audit=(
                    alignment_report.pages_audit if alignment_report is not None else None
                ),
                visuals=visual_math_parser.visuals_registry,
                cross_ref_index=cross_ref_index,
            )
            if alignment_report is not None:
                anomalies.pdf_docx_mismatch_warning = alignment_report.mismatch_warning
                pdf_pages_audit = alignment_report.pages_audit
            else:
                pdf_pages_audit = []

            # ---------------------------------------------------------
            # Сборка контракта режима: страницы, источник, возможности
            # ---------------------------------------------------------
            if alignment_report is not None:
                # PDF измерен постранично: число страниц точное.
                page_count = PageCountInfo(
                    total_pages=max(alignment_report.total_pages, 1),
                    method="pdf_exact",
                    confidence="high",
                )
            else:
                page_count = _estimate_docx_only_page_count(
                    sections=sections,
                    blocks=blocks,
                    has_native_breaks=has_rendered_breaks,
                )
                # Последняя секция обязана заканчиваться на последней странице отчета.
                if sections:
                    sections[-1].page_end_est = max(
                        sections[-1].page_end_est, page_count.total_pages
                    )

            return ParsedDocument(
                parse_mode=parse_mode,
                source=_build_source(parse_mode=parse_mode, meta=doc_meta, has_pdf=has_pdf),
                capabilities=_build_capabilities(
                    has_pdf=has_pdf,
                    pagination_source=pagination_source,
                    page_count=page_count,
                ),
                page_count=page_count,
                meta=doc_meta,
                settings=global_settings,
                theme=style_resolver.theme_resolver.theme_data,
                styles=style_resolver.styles,
                sections=sections,
                pdf_pages_audit=pdf_pages_audit,
                blocks=blocks,
                visuals=visual_math_parser.visuals_registry,
                footnotes=footnotes,
                document_tree=doc_tree,
                cross_ref_index=cross_ref_index,
                anomalies=anomalies,
            )

    @staticmethod
    def _parse_relationships(zf: zipfile.ZipFile, rels_path: str) -> Dict[str, str]:
        """Читает .rels-часть пакета в словарь rId -> Target."""
        if rels_path not in zf.namelist():
            return {}
        root = safe_fromstring(zf.read(rels_path))
        rels: Dict[str, str] = {}
        for rel in root:
            r_id = rel.get("Id")
            target = rel.get("Target")
            if r_id and target:
                rels[r_id] = target
        return rels

    @classmethod
    def _iter_unwrapped_body_nodes(cls, nodes: List[etree._Element]):
        """Прозрачно раскрывает контейнеры <w:sdt>/<w:sdtContent> (например, вокруг автооглавления) в w:body."""
        for node in nodes:
            if not isinstance(node.tag, str):
                continue
            local = etree.QName(node).localname
            if local in ("p", "tbl"):
                yield node
            elif local == "sdt":
                sdt_content = node.find("w:sdtContent", namespaces=NS)
                if sdt_content is not None:
                    yield from cls._iter_unwrapped_body_nodes(list(sdt_content))
            elif local == "AlternateContent":
                choice = node.find("mc:Choice", namespaces=NS_EXT)
                branch = choice if choice is not None else node.find("mc:Fallback", namespaces=NS_EXT)
                if branch is not None:
                    yield from cls._iter_unwrapped_body_nodes(list(branch))


def _max_page_of_sections(sections: List[SectionProperties]) -> int:
    """Максимальная page_end_est по секциям (0, если секций нет)."""
    ends = [
        section.page_end_est
        for section in sections
        if isinstance(section.page_end_est, int) and section.page_end_est >= 1
    ]
    return max(ends) if ends else 0


def _max_page_of_blocks(blocks: List[BlockUnion]) -> int:
    """Максимальная page_end_est по блокам с учетом вложенных ячеек и оглавления."""
    max_page = 0
    for block in blocks:
        max_page = max(max_page, block.page_end_est)
        if isinstance(block, TableBlock):
            for row in block.rows:
                max_page = max(max_page, row.page_start_est, row.page_end_est)
                for cell in row.cells:
                    max_page = max(max_page, _max_page_of_blocks(cell.blocks))
        elif isinstance(block, TocContainerBlock):
            max_page = max(max_page, _max_page_of_blocks(block.raw_paragraphs))
    return max_page


def _estimate_docx_only_page_count(
    sections: List[SectionProperties],
    blocks: List[BlockUnion],
    has_native_breaks: bool,
) -> PageCountInfo:
    """Оценивает число страниц DOCX без PDF по секциям, блокам и маркерам разрыва."""
    total_pages = max(
        _max_page_of_sections(sections),
        _max_page_of_blocks(blocks),
        1,
    )
    if has_native_breaks:
        return PageCountInfo(
            total_pages=total_pages,
            method="docx_native_breaks",
            confidence="medium",
            note=(
                "PDF не передан: страницы восстановлены по маркерам "
                "w:lastRenderedPageBreak, оставленным Word при сохранении"
            ),
        )
    return PageCountInfo(
        total_pages=total_pages,
        method="geometric_estimation",
        confidence="low",
        note=(
            "PDF не передан: число страниц оценено по геометрии DOCX "
            "(высоты абзацев, таблиц и разрывы страниц), возможна погрешность"
        ),
    )


def _build_capabilities(
    has_pdf: bool,
    pagination_source: str,
    page_count: PageCountInfo,
) -> DocumentCapabilities:
    """Собирает матрицу возможностей DOCX-конвейера для фактического режима."""
    return DocumentCapabilities(
        styles_parsed=True,
        numbering_parsed=True,
        sections_parsed=True,
        exact_pagination=has_pdf and pagination_source == "pdf_exact",
        pdf_layout_coordinates=has_pdf,
        visual_registry=True,
        footnotes_parsed=True,
        cross_references=True,
        word_metadata=True,
        # Маркеры lastRenderedPageBreak дают измеренные страницы, а не оценку.
        page_count_estimated=page_count.method not in ("pdf_exact", "docx_native_breaks"),
    )


def _build_source(parse_mode: ParseMode, meta: DocumentMeta, has_pdf: bool) -> SourceInfo:
    """Описывает фактический источник разбора и хэши файлов из meta."""
    return SourceInfo(
        mode=parse_mode,
        has_docx=True,
        has_pdf=has_pdf,
        docx_filename=meta.filename,
        docx_sha256=meta.file_sha256,
        pdf_filename=meta.pdf_filename if has_pdf else None,
        pdf_sha256=meta.pdf_sha256 if has_pdf else None,
    )


def parse_docx_to_json_file(
    docx_path: str | Path,
    output_json_path: str | Path,
    indent: int = 2,
    pdf_path: Optional[str | Path] = None,
) -> ParsedDocument:
    """Парсит .docx и сохраняет отформатированный JSON на диск.

    При pdf_path=None результат собирается в режиме docx_only.
    """
    pipeline = DocxParserPipeline(docx_path, pdf_path=pdf_path)
    parsed_doc = pipeline.parse()
    Path(output_json_path).write_text(
        parsed_doc.model_dump_json(indent=indent),
        encoding="utf-8",
    )
    return parsed_doc


def parse_any_to_json_file(
    input_path: str | Path,
    output_json_path: str | Path,
    indent: int = 2,
    pdf_path: Optional[str | Path] = None,
    outdir: Optional[str | Path] = None,
    backend: Optional[str] = None,
    timeout: Optional[int] = None,
) -> ParsedDocument:
    """
    Конвертирует legacy-формат при необходимости, разбирает документ и пишет JSON.

    .docx проходит транзитом без вызова LibreOffice. Для остальных форматов
    результат конвертации создается в outdir (по умолчанию рядом с исходником)
    и заменяет предыдущий `{stem}.docx` только после успешного прогона.
    pdf_path должен указывать на PDF, снятый с исходного файла: после
    конвертации вёрстка пересчитывается.

    backend и timeout по умолчанию берутся из DOCX_CONVERT_BACKEND и
    DOCX_CONVERT_TIMEOUT, чтобы веб-слой мог выбрать docker и лимит времени
    без правки кода.

    Raises:
        FileNotFoundError: исходный файл не существует.
        ValueError: backend имеет недопустимое значение.
        RuntimeError: LibreOffice недоступен или конвертация завершилась ошибкой.
    """
    from .converter import CONVERT_TIMEOUT_SECONDS, DEFAULT_BACKEND, convert_to_docx

    resolved_backend = backend or DEFAULT_BACKEND
    resolved_timeout = timeout if timeout is not None else CONVERT_TIMEOUT_SECONDS
    conversion = convert_to_docx(
        input_path,
        outdir=outdir,
        timeout=resolved_timeout,
        backend=resolved_backend,  # type: ignore[arg-type]
    )
    pipeline = DocxParserPipeline(
        conversion.docx_path,
        pdf_path=pdf_path,
        source_format=conversion.source_format,
        converted_via_lo=conversion.converted_via_lo,
        conversion_tool=conversion.tool,
        conversion_duration_ms=conversion.duration_ms,
        lo_version=conversion.lo_version,
        lo_image=conversion.lo_image,
    )
    parsed_doc = pipeline.parse()
    Path(output_json_path).write_text(
        parsed_doc.model_dump_json(indent=indent),
        encoding="utf-8",
    )
    return parsed_doc