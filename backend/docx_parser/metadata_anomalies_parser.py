"""Метаданные пакета и аномалии оформления.

Работает в фазе 7: читает docProps/core.xml, docProps/app.xml и word/settings.xml,
собирает сведения о документе и глобальные настройки, затем ищет типовые дефекты
верстки — пустые абзацы подряд, артефакты правок и комментариев, невидимый текст,
номера страниц в оглавлении без отрисованного номера в колонтитуле, битые поля
ссылок.
"""
from __future__ import annotations

import hashlib
import os
import re
import zipfile
from typing import Dict, List, Optional, Tuple
from lxml import etree

from .schema import (
    BlockUnion,
    ColorValue,
    CommentArtifact,
    ConsecutiveEmptyParagraphs,
    CrossReferenceIndex,
    DocumentAnomalies,
    DocumentMeta,
    FigureNumberingIssue,
    GlobalSettings,
    NumberedCaptionEntity,
    ParagraphBlock,
    PdfPageAudit,
    TableBlock,
    TocContainerBlock,
    TocPageNumberWithoutRenderedNumber,
    TrackChangeArtifact,
    VisualObject,
)
from .style_resolver import NS, dxa_to_mm, parse_oxml_bool, safe_fromstring, safe_int, w_attr

NS_META = {
    **NS,
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
}

RE_BROKEN_REF_TEXT = re.compile(
    r"Ошибка!\s*Источник\s+ссылки\s+не\s+найден|Error!\s*Reference\s+source\s+not\s+found",
    re.IGNORECASE,
)


class MetaAndAnomaliesParser:
    """Метаданные пакета, глобальные настройки и аномалии оформления."""

    def __init__(
        self,
        zf: zipfile.ZipFile,
        docx_path: str,
        source_format: Optional[str] = None,
        converted_via_lo: bool = False,
        conversion_tool: Optional[str] = None,
        conversion_duration_ms: Optional[int] = None,
        lo_version: Optional[str] = None,
        lo_image: Optional[str] = None,
    ) -> None:
        """Запоминает zip-пакет, путь к файлу и параметры его конвертации."""
        self.zf = zf
        self.docx_path = docx_path
        self.namelist = set(zf.namelist())
        self.source_format = source_format
        self.converted_via_lo = converted_via_lo
        self.conversion_tool = conversion_tool
        self.conversion_duration_ms = conversion_duration_ms
        self.lo_version = lo_version
        self.lo_image = lo_image

    def parse_document_meta(self) -> DocumentMeta:
        """Собирает DocumentMeta: автора, даты, приложение, пагинацию и SHA-256 файла."""
        filename = os.path.basename(self.docx_path)
        sha256_hash = self._compute_file_sha256(self.docx_path)

        author: Optional[str] = None
        last_mod_by: Optional[str] = None
        created_iso: Optional[str] = None
        modified_iso: Optional[str] = None

        if "docProps/core.xml" in self.namelist:
            root = safe_fromstring(self.zf.read("docProps/core.xml"))
            author = self._find_text(root, "dc:creator")
            last_mod_by = self._find_text(root, "cp:lastModifiedBy")
            created_iso = self._find_text(root, "dcterms:created")
            modified_iso = self._find_text(root, "dcterms:modified")

        template_name: Optional[str] = None
        application: Optional[str] = None
        pages: Optional[int] = None
        words: Optional[int] = None
        chars_no_sp: Optional[int] = None
        chars_with_sp: Optional[int] = None

        if "docProps/app.xml" in self.namelist:
            app_root = safe_fromstring(self.zf.read("docProps/app.xml"))
            template_name = self._find_text(app_root, "ep:Template")
            application = self._find_text(app_root, "ep:Application")
            pages = self._find_int(app_root, "ep:Pages")
            words = self._find_int(app_root, "ep:Words")
            chars_no_sp = self._find_int(app_root, "ep:Characters")
            chars_with_sp = self._find_int(app_root, "ep:CharactersWithSpaces")

        return DocumentMeta(
            filename=filename,
            file_sha256=sha256_hash,
            source_format=self.source_format,
            converted_via_lo=self.converted_via_lo,
            conversion_tool=self.conversion_tool,
            conversion_duration_ms=self.conversion_duration_ms,
            lo_version=self.lo_version,
            lo_image=self.lo_image,
            author=author,
            last_modified_by=last_mod_by,
            created_iso=created_iso,
            modified_iso=modified_iso,
            template_name=template_name,
            application=application,
            word_stat_pages=pages,
            word_stat_words=words,
            word_stat_chars_no_spaces=chars_no_sp,
            word_stat_chars_with_spaces=chars_with_sp,
        )

    def parse_global_settings(self) -> GlobalSettings:
        """Читает word/settings.xml и возвращает GlobalSettings с флагами верстки."""
        if "word/settings.xml" not in self.namelist:
            return GlobalSettings()

        root = safe_fromstring(self.zf.read("word/settings.xml"))

        auto_hyph = parse_oxml_bool(root.find("w:autoHyphenation", namespaces=NS)) is True
        mirror_m = parse_oxml_bool(root.find("w:mirrorMargins", namespaces=NS)) is True
        even_odd = parse_oxml_bool(root.find("w:evenAndOddHeaders", namespaces=NS)) is True
        track_rev = parse_oxml_bool(root.find("w:trackRevisions", namespaces=NS)) is True

        hyph_zone_mm: Optional[float] = None
        hz_el = root.find("w:hyphenationZone", namespaces=NS)
        if hz_el is not None and w_attr(hz_el, "val") is not None:
            hyph_zone_mm = dxa_to_mm(w_attr(hz_el, "val") or 0)

        consec_hyph: Optional[int] = None
        ch_el = root.find("w:consecutiveHyphenLimit", namespaces=NS)
        if ch_el is not None:
            consec_hyph = safe_int(w_attr(ch_el, "val"))

        def_tab_mm = 12.5
        dt_el = root.find("w:defaultTabStop", namespaces=NS)
        if dt_el is not None and w_attr(dt_el, "val") is not None:
            def_tab_mm = dxa_to_mm(w_attr(dt_el, "val") or 708)

        prot_el = root.find("w:documentProtection", namespaces=NS)
        has_prot = False
        if prot_el is not None:
            enf = w_attr(prot_el, "enforcement")
            has_prot = enf in ("1", "true", "on")

        dec_sym: Optional[str] = None
        ds_el = root.find("w:decimalSymbol", namespaces=NS)
        if ds_el is not None:
            dec_sym = w_attr(ds_el, "val")

        return GlobalSettings(
            auto_hyphenation=auto_hyph,
            hyphenation_zone_mm=hyph_zone_mm,
            consecutive_hyphens_limit=consec_hyph,
            mirror_margins=mirror_m,
            even_and_odd_headers=even_odd,
            default_tab_stop_mm=def_tab_mm,
            track_revisions_enabled=track_rev,
            has_document_protection=has_prot,
            decimal_symbol=dec_sym,
        )

    def collect_anomalies(
        self,
        body_elem: etree._Element,
        blocks: List[BlockUnion],
        xml_para_to_block_map: Dict[etree._Element, ParagraphBlock],
        pdf_pages_audit: Optional[List[PdfPageAudit]] = None,
        visuals: Optional[Dict[str, VisualObject]] = None,
        cross_ref_index: Optional[CrossReferenceIndex] = None,
    ) -> DocumentAnomalies:
        """
        Ищет дефекты оформления по готовым блокам документа.

        Проверяются пустые абзацы подряд, артефакты правок и комментариев,
        невидимый текст, внедрённые OLE-объекты, номера страниц без отрисовки,
        битые поля ссылок, а также подписи рисунков: пропущенная подпись,
        иллюстрация без подписи, отсутствие ссылки в тексте и сбои нумерации.
        """
        track_changes = self._extract_track_changes(body_elem, xml_para_to_block_map)
        comments = self._extract_comments(body_elem, xml_para_to_block_map)
        empty_clusters = self._detect_empty_paragraph_clusters(blocks)

        hidden_ids: List[str] = []
        broken_ids: List[str] = []
        shaded_ids: List[str] = []
        non_black_ids: List[str] = []
        ole_ids: List[str] = []

        for p in self._iter_all_paragraphs(blocks):
            if any(r.effective_props.vanish_hidden for r in p.runs):
                hidden_ids.append(p.block_id)

            # OLE, не распознанный как формула (вложения Excel, PDF и т.п.),
            # выносится отдельной аномалией: содержимое нужно проверять вручную.
            if visuals and any(
                visuals.get(vis_id) is not None
                and visuals[vis_id].visual_kind == "ole_object"
                for vis_id in p.embedded_visual_ids
            ):
                ole_ids.append(p.block_id)

            if RE_BROKEN_REF_TEXT.search(p.text_visible):
                broken_ids.append(p.block_id)

            has_shading = p.effective_props.shading_fill is not None or any(
                r.effective_props.shading_fill is not None for r in p.runs
            )
            if has_shading:
                shaded_ids.append(p.block_id)

            has_non_black = any(
                bool(r.visible_text.strip())
                and not self._is_black_text_color(r.effective_props.color)
                for r in p.runs
            )
            if has_non_black:
                non_black_ids.append(p.block_id)

        toc_pages_without_rendered_numbers: List[
            TocPageNumberWithoutRenderedNumber
        ] = []
        if pdf_pages_audit and not any(
            page.has_rendered_page_number for page in pdf_pages_audit
        ):
            for block in blocks:
                if not isinstance(block, TocContainerBlock):
                    continue
                for entry in block.entries:
                    if entry.extracted_page_str:
                        toc_pages_without_rendered_numbers.append(
                            TocPageNumberWithoutRenderedNumber(
                                entry_block_id=entry.block_id,
                                extracted_page_str=entry.extracted_page_str,
                                matched_heading_block_id=entry.matched_heading_block_id,
                            )
                        )

        (
            uncaptioned_visual_ids,
            captions_without_visual_ids,
            unreferenced_figure_ids,
            figure_numbering_issues,
        ) = self._detect_figure_anomalies(visuals, cross_ref_index)

        return DocumentAnomalies(
            unaccepted_track_changes=track_changes,
            leftover_comments=comments,
            empty_paragraph_clusters=empty_clusters,
            hidden_text_block_ids=hidden_ids,
            embedded_ole_block_ids=ole_ids,
            broken_field_block_ids=broken_ids,
            shaded_background_block_ids=shaded_ids,
            non_black_text_block_ids=non_black_ids,
            toc_page_numbers_without_rendered_numbers=toc_pages_without_rendered_numbers,
            uncaptioned_visual_ids=uncaptioned_visual_ids,
            figure_captions_without_visual_ids=captions_without_visual_ids,
            unreferenced_figure_ids=unreferenced_figure_ids,
            figure_numbering_issues=figure_numbering_issues,
        )

    def _detect_figure_anomalies(
        self,
        visuals: Optional[Dict[str, VisualObject]],
        cross_ref_index: Optional[CrossReferenceIndex],
    ) -> Tuple[List[str], List[str], List[str], List[FigureNumberingIssue]]:
        """Ищет иллюстрации без подписи, подписи без объекта и дефекты нумерации рисунков."""
        uncaptioned_visual_ids: List[str] = []
        if visuals:
            for vis_id, vis in visuals.items():
                # OLE-объекты (вложения Excel и т.п.) разбираются отдельной аномалией.
                if vis.caption_block_id is None and vis.visual_kind != "ole_object":
                    uncaptioned_visual_ids.append(vis_id)

        if cross_ref_index is None:
            return uncaptioned_visual_ids, [], [], []

        captions_without_visual_ids: List[str] = []
        unreferenced_figure_ids: List[str] = []
        for fig in cross_ref_index.figures:
            if fig.target_object_id is None or (
                visuals is not None and fig.target_object_id not in visuals
            ):
                captions_without_visual_ids.append(fig.caption_block_id)
            if not fig.is_mentioned_in_text:
                unreferenced_figure_ids.append(fig.entity_id)

        return (
            uncaptioned_visual_ids,
            captions_without_visual_ids,
            unreferenced_figure_ids,
            self._detect_figure_numbering_issues(cross_ref_index.figures),
        )

    @staticmethod
    def _normalize_figure_prefix(prefix_word: str) -> str:
        """Сводит «Рис.», «Рисунок», «Скриншот» и «Снимок экрана» к семейству подписей."""
        prefix = prefix_word.strip().lower().replace(".", "")
        if prefix.startswith("рисунк") or prefix == "рис":
            return "рисунок"
        if prefix.startswith("скрин") or prefix.startswith("снимок"):
            return "скриншот"
        return prefix

    @classmethod
    def _detect_figure_numbering_issues(
        cls, figures: List[NumberedCaptionEntity]
    ) -> List[FigureNumberingIssue]:
        """Проверяет сквозную арабскую нумерацию рисунков: дубли, пропуски, порядок."""
        issues: List[FigureNumberingIssue] = []
        seen_numbers: Dict[Tuple[str, str], str] = {}
        last_by_family: Dict[str, Tuple[int, str, str]] = {}

        for fig in figures:
            if fig.numbering_scheme != "continuous_arabic":
                continue
            try:
                number = int(fig.number_str)
            except ValueError:
                continue

            family = cls._normalize_figure_prefix(fig.prefix_word)
            number_key = (family, fig.number_str)
            if number_key in seen_numbers:
                issues.append(
                    FigureNumberingIssue(
                        issue_kind="duplicate",
                        number_str=fig.number_str,
                        caption_block_id=fig.caption_block_id,
                        previous_number_str=seen_numbers[number_key],
                    )
                )
                continue
            seen_numbers[number_key] = fig.caption_block_id

            previous = last_by_family.get(family)
            if previous is not None:
                prev_number, prev_number_str, _ = previous
                if number == prev_number + 1:
                    pass
                elif number > prev_number + 1:
                    # Ограничиваем перечень пропусков, чтобы разрыв 1..1000 не раздувал отчет.
                    for missing in range(prev_number + 1, min(number, prev_number + 11)):
                        issues.append(
                            FigureNumberingIssue(
                                issue_kind="gap",
                                number_str=str(missing),
                                caption_block_id=fig.caption_block_id,
                                previous_number_str=prev_number_str,
                            )
                        )
                elif number != prev_number:
                    issues.append(
                        FigureNumberingIssue(
                            issue_kind="out_of_order",
                            number_str=fig.number_str,
                            caption_block_id=fig.caption_block_id,
                            previous_number_str=prev_number_str,
                        )
                    )

            last_by_family[family] = (number, fig.number_str, fig.caption_block_id)

        return issues

    @staticmethod
    def _is_black_text_color(color: ColorValue) -> bool:
        """True, если цвет текста черный или auto."""
        if color.is_auto or color.rgb_hex == "000000":
            return True
        return (color.theme_color or "").lower() == "text1"

    def _extract_track_changes(
        self,
        body_elem: etree._Element,
        xml_para_to_block_map: Dict[etree._Element, ParagraphBlock],
    ) -> List[TrackChangeArtifact]:
        """Собирает артефакты w:ins, w:del, w:moveFrom и w:moveTo."""
        artifacts: List[TrackChangeArtifact] = []
        tag_map = {
            "ins": "insertion",
            "del": "deletion",
            "moveFrom": "move_from",
            "moveTo": "move_to",
            "rPrChange": "format_change",
            "pPrChange": "format_change",
        }

        # Вложенные правки (например w:ins внутри w:moveTo) дают дубли одного и того же
        # фрагмента — дедуплицируем по (абзац, автор, дата, текст).
        seen_keys: set[Tuple[str, Optional[str], Optional[str], str]] = set()

        for p_el, p_block in xml_para_to_block_map.items():
            for tag_xml, art_type in tag_map.items():
                for node in p_el.findall(f".//w:{tag_xml}", namespaces=NS):
                    author = w_attr(node, "author")
                    date_iso = w_attr(node, "date")
                    texts = node.xpath(".//w:t/text() | .//w:delText/text()", namespaces=NS)
                    snippet = "".join(texts).strip() or f"[{art_type}]"
                    dedup_key = (p_block.block_id, author, date_iso, snippet[:120])
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)
                    artifacts.append(
                        TrackChangeArtifact(
                            artifact_type=art_type,  # type: ignore[arg-type]
                            author=author,
                            date_iso=date_iso,
                            block_id=p_block.block_id,
                            page_est=p_block.page_start_est,
                            text_snippet=snippet[:120],
                        )
                    )
        return artifacts

    def _extract_comments(
        self,
        body_elem: etree._Element,
        xml_para_to_block_map: Dict[etree._Element, ParagraphBlock],
    ) -> List[CommentArtifact]:
        """Собирает комментарии из word/comments.xml, на которые ссылается текст."""
        if "word/comments.xml" not in self.namelist:
            return []

        comments_root = safe_fromstring(self.zf.read("word/comments.xml"))
        comment_locations: Dict[str, Tuple[ParagraphBlock, str]] = {}

        for p_el, p_block in xml_para_to_block_map.items():
            for c_start in p_el.findall(".//w:commentRangeStart", namespaces=NS):
                c_id = w_attr(c_start, "id")
                if c_id is not None and c_id not in comment_locations:
                    comment_locations[c_id] = (p_block, p_block.text_visible[:100])
            for c_ref in p_el.findall(".//w:commentReference", namespaces=NS):
                c_id = w_attr(c_ref, "id")
                if c_id is not None and c_id not in comment_locations:
                    comment_locations[c_id] = (p_block, p_block.text_visible[:100])

        results: List[CommentArtifact] = []
        for c_el in comments_root.findall("w:comment", namespaces=NS):
            c_id = w_attr(c_el, "id")
            if c_id is None:
                continue
            author = w_attr(c_el, "author")
            initials = w_attr(c_el, "initials")
            date_iso = w_attr(c_el, "date")
            body_texts = c_el.xpath(".//w:t/text()", namespaces=NS)
            comment_body = " ".join("".join(body_texts).split())

            loc = comment_locations.get(c_id)
            block_id = loc[0].block_id if loc else "unknown_block"
            page_est = loc[0].page_start_est if loc else 1
            commented_txt = loc[1] if loc else ""

            results.append(
                CommentArtifact(
                    comment_id=c_id,
                    author=author,
                    initials=initials,
                    date_iso=date_iso,
                    block_id=block_id,
                    page_est=page_est,
                    commented_text=commented_txt,
                    comment_body=comment_body,
                )
            )

        return results

    @staticmethod
    def _detect_empty_paragraph_clusters(
        blocks: List[BlockUnion],
    ) -> List[ConsecutiveEmptyParagraphs]:
        """Находит серии пустых абзацев длиннее заданного порога."""
        clusters: List[ConsecutiveEmptyParagraphs] = []
        run_buffer: List[ParagraphBlock] = []

        def _flush() -> None:
            if run_buffer:
                clusters.append(
                    ConsecutiveEmptyParagraphs(
                        start_block_id=run_buffer[0].block_id,
                        end_block_id=run_buffer[-1].block_id,
                        section_idx=run_buffer[0].section_idx,
                        page_est=run_buffer[0].page_start_est,
                        count=len(run_buffer),
                    )
                )
                run_buffer.clear()

        for b in blocks:
            if isinstance(b, ParagraphBlock) and b.is_empty_or_whitespace:
                run_buffer.append(b)
            else:
                _flush()
        _flush()
        return clusters

    @classmethod
    def _iter_all_paragraphs(cls, blocks: List[BlockUnion]):
        """Обходит абзацы верхнего уровня и абзацы внутри таблиц."""
        for b in blocks:
            if isinstance(b, ParagraphBlock):
                yield b
            elif isinstance(b, TableBlock):
                for row in b.rows:
                    for cell in row.cells:
                        if not cell.is_merged_shadow:
                            yield from cls._iter_all_paragraphs(cell.blocks)

    @staticmethod
    def _find_text(root: etree._Element, xpath_expr: str) -> Optional[str]:
        """Возвращает текст первого совпавшего элемента или пустую строку."""
        el = root.find(xpath_expr, namespaces=NS_META)
        return el.text.strip() if (el is not None and el.text) else None

    @classmethod
    def _find_int(cls, root: etree._Element, xpath_expr: str) -> Optional[int]:
        """Возвращает целое из первого совпавшего элемента, пробелы игнорируются."""
        txt = cls._find_text(root, xpath_expr)
        if not txt:
            return None
        # Word пишет счетчики с пробелами-разделителями ("1 234"), а иногда "0"
        return safe_int(txt.replace(" ", "").replace("\u00a0", ""))

    @staticmethod
    def _compute_file_sha256(path: str) -> str:
        """Считает SHA-256 файла блоками по 1 МиБ."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()