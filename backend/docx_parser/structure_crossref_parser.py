"""Оглавление, дерево документа, подписи, ссылки и библиография.

Работает в фазе 6, после PDF-выравнивания, поэтому номера страниц в оглавлении,
подписях и упоминаниях сверяются с физическими страницами листа.

Модуль сворачивает абзацы оглавления в TocContainerBlock, строит дерево
заголовков, сопоставляет записи оглавления с заголовками, извлекает подписи
рисунков, таблиц и приложений с номерами формул, разбирает список литературы
и разрешает упоминания вида «рисунок 2.1», «таблица 3», «[4]».
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional, Set, Tuple

from .schema import (
    BibliographyEntry,
    BlockUnion,
    CrossReferenceIndex,
    DocumentTreeNode,
    EquationObject,
    NumberedCaptionEntity,
    ParagraphBlock,
    TableBlock,
    TextCrossReferenceMention,
    TocContainerBlock,
    TocEntryItem,
    VisualObject,
)

# Префикс «Раздел N» / «Глава N» / «Параграф N» относится к sec_word, а не к заголовку:
# иначе служебное слово попадает в title и строка оглавления не сопоставляется с текстом.
RE_TOC_LINE = re.compile(
    r"^\s*"
    r"(?:(?P<app_prefix>приложение\s+[А-ЯЁA-Z0-9]+)|"
    r"(?:(?P<sec_word>глава|раздел|параграф)\s+)?(?P<sec_num>[1-9]\d*(?:\.[1-9]\d*)*\.?))?"
    r"(?:\s*[\—\–\-:]\s*|\s+)"
    r"(?P<title>.+?)"
    r"(?:[\s\.\u2026\t]+(?P<page>\d{1,4}))?\s*$",
    re.IGNORECASE,
)

# «Скриншот N.» — подпись рисунка в методичках и отчетах наравне с «Рисунок N».
RE_FIGURE_CAPTION = re.compile(
    r"^\s*(?P<prefix>рисунок|рис\.?|скриншот|скрин\.?|снимок\s+экрана)\s+"
    r"(?P<num>[А-ЯЁA-Z]?\s*\d+(?:\.\d+)*)"
    r"(?:\s*(?P<sep>[\—\–\-\.:])\s*(?P<title>.*))?$",
    re.IGNORECASE,
)

RE_TABLE_CAPTION = re.compile(
    r"^\s*(?P<prefix>таблица|табл\.?)\s+"
    r"(?P<num>[А-ЯЁA-Z]?\s*\d+(?:\.\d+)*)"
    r"(?:\s*(?P<sep>[\—\–\-\.:])\s*(?P<title>.*))?$",
    re.IGNORECASE,
)

RE_APPENDIX_TITLE = re.compile(
    r"^\s*(?P<prefix>приложение)\s+(?P<num>[А-ЯЁA-Z0-9]+)"
    r"(?:\s*(?:\([^\)]+\))?\s*(?P<sep>[\—\–\-\.:])?\s*(?P<title>.*))?$",
    re.IGNORECASE,
)

RE_EQ_NUM_TAIL = re.compile(
    r"(?P<punct>[,\.;])?\s*(?P<lead_space>\t|[ \u00A0]{2,})?\(\s*(?P<num>[А-ЯЁA-Z]?\s*\d+(?:\.\d+)*)\s*\)\s*$"
)
RE_EQ_NUM_STANDALONE = re.compile(r"^\s*\(\s*(?P<num>[А-ЯЁA-Z]?\s*\d+(?:\.\d+)*)\s*\)\s*$")
RE_WHERE_START = re.compile(r"^\s*(?P<word>где)(?P<colon>\s*:)?\s+\S+", re.IGNORECASE)

RE_BIB_MANUAL_NUM = re.compile(r"^\s*(?P<raw_pref>\[\s*\d+\s*\]|\d+\.|\d+\))\s*(?P<body>\S.*)$")
RE_BIB_YEAR = re.compile(r"\b(19\d{2}|20[0-3]\d)\b")
RE_BIB_PAGES = re.compile(r"\b(?:[СсPp]\.\s*\d+(?:\s*[\—\–\-]\s*\d+)?|\d+\s*[сp]\.)")
RE_BIB_ISBN_ISSN = re.compile(r"\b(?:ISBN|ISSN)\s*([0-9Xx\-\–\s]{8,20})\b", re.IGNORECASE)
RE_BIB_URL = re.compile(r"https?://[^\s\)\]\>\"«»]+", re.IGNORECASE)
RE_BIB_ACCESS_DATE = re.compile(r"дата\s+обращения\s*:\s*\d{1,2}\.\d{1,2}\.\d{2,4}", re.IGNORECASE)
RE_BIB_DOT_DASH = re.compile(r"\.\s*[\—\–\-]\s+")

RE_BROKEN_FIELD = re.compile(
    r"Ошибка!\s*Источник\s+ссылки\s+не\s+найден\.?|Error!\s*Reference\s+source\s+not\s+found\.?",
    re.IGNORECASE,
)

RE_CITATION_BRACKETS = re.compile(
    r"\[\s*\d+(?:\s*(?:[,;]|[\—\–\-])\s*(?:[сpстр]+\.?\s*)?\d+)*\s*\]",
    re.IGNORECASE,
)

RE_ENTITY_MENTION = re.compile(
    r"\b(?P<word>"
    r"рисунк(?:ами|ах|ам|ом|ов|е|у|и|а)?|рисунок|рис\.|"
    r"скриншот(?:ами|ах|ам|ом|ов|е|у|ы|а)?|скрин\.?|снимок\s+экрана|"
    r"таблиц(?:ами|ах|ам|ей|ею|ы|е|у|а)?|табл\.|"
    r"формул(?:ами|ах|ам|ой|ою|ы|е|у|а)?|уравнени(?:ям|ях|ем|ю|я|е)|"
    r"раздел(?:ами|ах|ам|ом|ов|е|у|ы)?|глав(?:ами|ах|ам|ой|ы|е|у|а)"
    r")"
    r"(?P<ws>[\s\u00A0]+)"
    r"\(?(?P<target>[А-ЯЁA-Z]?\s*\d+(?:\.\d+)*(?:\s*[\—\–\-]\s*[А-ЯЁA-Z]?\s*\d+(?:\.\d+)*)?|[А-ЯЁA-Z])\)?",
    re.IGNORECASE,
)
RE_APPENDIX_MENTION = re.compile(
    r"(?<!\w)(?P<word>приложение|приложения|прил\.)"
    r"(?P<ws>[ \u00A0]+)(?P<target>[А-ЯЁA-Z]|\d+)(?!\w)",
    re.IGNORECASE,
)


class StructureCrossRefParser:
    """Оглавление, дерево документа, подписи, библиография и перекрестные ссылки."""

    def process_document_structure(
        self,
        blocks: List[BlockUnion],
        visuals: Dict[str, VisualObject],
        equations: List[EquationObject],
        verify_page_numbers: bool = True,
    ) -> Tuple[List[BlockUnion], List[DocumentTreeNode], CrossReferenceIndex]:
        """
        Прогоняет все шаги фазы 6 и возвращает блоки, дерево и индекс ссылок.

        verify_page_numbers=False оставляет is_page_number_match неопределенным:
        без точной пагинации PDF сравнение номеров оглавления с эвристическими
        страницами DOCX давало бы ложные расхождения.
        """
        blocks_with_toc, toc_container = self.extract_and_fold_toc(blocks)
        doc_tree = self.build_document_tree(blocks_with_toc)

        if toc_container is not None:
            self.reconcile_toc_with_headings(
                toc_container,
                blocks_with_toc,
                verify_page_numbers=verify_page_numbers,
            )

        self.enrich_equations_and_explications(blocks_with_toc, equations)
        figures_list, tables_list, apps_list = self.extract_and_link_captions(
            blocks_with_toc, visuals, doc_tree
        )
        bibliography_list, bib_block_ids = self.parse_bibliography_section(
            blocks_with_toc, doc_tree
        )
        mentions_list = self.extract_and_resolve_mentions(
            blocks=blocks_with_toc,
            figures=figures_list,
            tables=tables_list,
            apps=apps_list,
            equations=equations,
            bibliography=bibliography_list,
            excluded_block_ids=bib_block_ids,
            document_tree=doc_tree,
        )

        cross_ref_index = CrossReferenceIndex(
            figures=figures_list,
            tables=tables_list,
            Apps=apps_list,
            equations=equations,
            bibliography=bibliography_list,
            mentions=mentions_list,
        )

        return blocks_with_toc, doc_tree, cross_ref_index

    def extract_and_fold_toc(
        self, blocks: List[BlockUnion]
    ) -> Tuple[List[BlockUnion], Optional[TocContainerBlock]]:
        """Сворачивает абзацы оглавления в TocContainerBlock."""
        toc_heading_idx: Optional[int] = None
        toc_heading_id: Optional[str] = None

        for idx, b in enumerate(blocks):
            if isinstance(b, ParagraphBlock) and b.heading_info and b.heading_info.is_heading:
                norm_title = b.heading_info.clean_title_text.strip().upper()
                if norm_title in ("СОДЕРЖАНИЕ", "ОГЛАВЛЕНИЕ"):
                    toc_heading_idx = idx
                    toc_heading_id = b.block_id
                    break

        toc_start_idx: Optional[int] = None
        toc_end_idx: Optional[int] = None

        if toc_heading_idx is not None:
            toc_start_idx = toc_heading_idx + 1
            toc_end_idx = toc_start_idx
            for idx in range(toc_start_idx, len(blocks)):
                b = blocks[idx]
                if not isinstance(b, ParagraphBlock):
                    break
                if (
                    b.heading_info
                    and b.heading_info.is_heading
                    and not self._is_toc_paragraph(b)
                ):
                    break
                toc_end_idx = idx + 1
        else:
            for idx in range(min(len(blocks), 60)):
                b = blocks[idx]
                if isinstance(b, ParagraphBlock) and self._is_toc_paragraph(b):
                    if toc_start_idx is None:
                        toc_start_idx = idx
                    toc_end_idx = idx + 1
                elif toc_start_idx is not None:
                    if isinstance(b, ParagraphBlock) and b.is_empty_or_whitespace:
                        continue
                    break

        if toc_start_idx is None or toc_end_idx is None or toc_start_idx >= toc_end_idx:
            return blocks, None

        toc_slice = [
            b for b in blocks[toc_start_idx:toc_end_idx]
            if isinstance(b, ParagraphBlock) and not b.is_empty_or_whitespace
        ]
        if not toc_slice:
            return blocks, None

        is_automated = any(self._has_automated_toc_markers(p) for p in toc_slice)
        entries: List[TocEntryItem] = []
        min_indent = min((p.effective_props.indent_left_mm for p in toc_slice), default=0.0)

        for entry_idx, p in enumerate(toc_slice):
            entry = self._parse_single_toc_entry(p, entry_idx, min_indent)
            entries.append(entry)

        toc_block = TocContainerBlock(
            block_id=f"toc_{toc_slice[0].block_id}",
            section_idx=toc_slice[0].section_idx,
            page_start_est=toc_slice[0].page_start_est,
            page_end_est=toc_slice[-1].page_end_est,
            is_automated_field_or_sdt=is_automated,
            toc_heading_block_id=toc_heading_id,
            entries=entries,
            # Исходные абзацы сохраняются в JSON: правила нормоконтроля проверяют
            # отточия, табуляции, гиперссылки и шрифтовое оформление строк оглавления.
            raw_paragraphs=toc_slice,
        )

        new_blocks = blocks[:toc_start_idx] + [toc_block] + blocks[toc_end_idx:]
        return new_blocks, toc_block

    @staticmethod
    def _is_toc_paragraph(p: ParagraphBlock) -> bool:
        """True, если абзац относится к оглавлению."""
        style_str = f"{p.style_id or ''} {p.style_name_ui or ''}".lower()
        if any(k in style_str for k in ("toc", "оглавление")):
            return True
        for r in p.runs:
            if r.field_instr and any(k in r.field_instr.upper() for k in ("TOC", "PAGEREF")):
                return True
            if r.hyperlink_anchor and r.hyperlink_anchor.startswith("_Toc"):
                return True
        txt = p.text_visible.strip()
        if re.search(r"(?:\t|\.{3,}|\u2026+)\s*\d{1,4}\s*$", txt):
            return True
        return False

    @staticmethod
    def _has_automated_toc_markers(p: ParagraphBlock) -> bool:
        """True, если в абзаце есть поля TOC/PAGEREF или стиль оглавления."""
        style_str = f"{p.style_id or ''} {p.style_name_ui or ''}".lower()
        if any(k in style_str for k in ("toc", "оглавление")):
            return True
        for r in p.runs:
            if r.in_field and r.field_instr and any(
                k in r.field_instr.upper() for k in ("TOC", "PAGEREF", "HYPERLINK")
            ):
                return True
            if r.hyperlink_anchor and r.hyperlink_anchor.startswith("_Toc"):
                return True
        return False

    def _parse_single_toc_entry(
        self, p: ParagraphBlock, entry_index: int, base_indent_mm: float
    ) -> TocEntryItem:
        """Разбирает строку оглавления в TocEntryItem."""
        raw_txt = p.text_visible.strip()

        style_str = f"{p.style_id or ''} {p.style_name_ui or ''}".lower()
        m_style_lvl = re.search(r"(?:toc|оглавление)\s*([1-9])", style_str)
        indent_lvl = int(m_style_lvl.group(1)) if m_style_lvl else 1

        extracted_num: Optional[str] = None
        extracted_title = raw_txt
        extracted_page: Optional[str] = None

        title_source = raw_txt
        tab_index = raw_txt.rfind("\t")
        if tab_index >= 0:
            page_part = raw_txt[tab_index + 1 :].strip()
            title_source = raw_txt[:tab_index].strip()
            if re.fullmatch(r"\d{1,4}", page_part):
                extracted_page = page_part
        else:
            # Retain support for legacy TOC lines whose page number uses dot leaders.
            m_legacy = RE_TOC_LINE.match(raw_txt)
            if m_legacy and m_legacy.group("page"):
                extracted_page = m_legacy.group("page").strip()
                title_source = re.sub(
                    r"(?:[\s\.\u2026]+)\d{1,4}\s*$", "", raw_txt
                ).strip()

        title_source = re.sub(r"[\.\u2026]+$", "", title_source).strip()
        m_app = re.match(r"^приложение\s+([А-ЯЁA-Z0-9]+)(?:\s+(.+))?$", title_source, re.I)
        m_section = re.match(
            r"^(?:глава|раздел|параграф)\s+(\d+(?:\.\d+)*\.?)\s+(.+)$",
            title_source,
            re.I,
        )
        m_number = re.match(r"^(\d+(?:\.\d+)*\.?)\s+(.+)$", title_source)
        if m_app:
            extracted_num = m_app.group(1)
            extracted_title = title_source
        elif m_section:
            extracted_num = m_section.group(1)
            extracted_title = m_section.group(2).strip()
            if not m_style_lvl:
                parts = [pt for pt in extracted_num.strip(".").split(".") if pt.isdigit()]
                if parts:
                    indent_lvl = max(1, min(9, len(parts)))
        elif m_number:
            extracted_num = m_number.group(1)
            extracted_title = m_number.group(2).strip()
            if not m_style_lvl:
                parts = [pt for pt in extracted_num.strip(".").split(".") if pt.isdigit()]
                if parts:
                    indent_lvl = max(1, min(9, len(parts)))
        else:
            extracted_title = title_source

        if not m_style_lvl and extracted_num is None:
            diff_mm = max(0.0, p.effective_props.indent_left_mm - base_indent_mm)
            if diff_mm >= 3.0:
                indent_lvl = min(9, 1 + int(round(diff_mm / 5.0)))

        has_dot_leader = any(
            t.leader in ("dot", "middleDot") for t in p.effective_props.tab_stops
        ) or bool(re.search(r"\.{3,}|\u2026", raw_txt))

        has_blue_or_u = False
        for r in p.runs:
            if not r.visible_text.strip():
                continue
            eff = r.effective_props
            if eff.underline not in ("none", "nil"):
                has_blue_or_u = True
                break
            if eff.color.theme_color in ("hyperlink", "followedHyperlink"):
                has_blue_or_u = True
                break
            if eff.color.rgb_hex in ("0000FF", "0563C1", "4F81BD", "2F5496", "467886"):
                has_blue_or_u = True
                break

        return TocEntryItem(
            entry_index=entry_index,
            block_id=p.block_id,
            raw_text=raw_txt,
            extracted_number=extracted_num,
            extracted_title=extracted_title,
            extracted_page_str=extracted_page,
            indent_level=indent_lvl,
            has_dot_leader=has_dot_leader,
            has_hyperlink_blue_or_underline=has_blue_or_u,
        )

    def build_document_tree(self, blocks: List[BlockUnion]) -> List[DocumentTreeNode]:
        """Строит иерархию заголовков с диапазонами страниц."""
        roots: List[DocumentTreeNode] = []
        stack: List[DocumentTreeNode] = []
        node_seq = 0

        first_page = blocks[0].page_start_est if blocks else 1
        preamble = DocumentTreeNode(
            node_id="node_000_preamble",
            heading_block_id=None,
            title="Преамбула / Титульный лист",
            section_number=None,
            level=0,
            semantic_role="title_page",
            page_start_est=first_page,
            page_end_est=first_page,
        )
        roots.append(preamble)
        stack.append(preamble)

        for b in blocks:
            b_start = b.page_start_est
            b_end = b.page_end_est

            if isinstance(b, ParagraphBlock) and b.heading_info and b.heading_info.is_heading:
                h = b.heading_info
                node_seq += 1
                role = self._classify_heading_semantic_role(h.clean_title_text, h.heading_kind, h.level)

                new_node = DocumentTreeNode(
                    node_id=f"node_{node_seq:03d}",
                    heading_block_id=b.block_id,
                    title=h.clean_title_text,
                    section_number=h.section_number_raw.rstrip(".") if h.section_number_raw else None,
                    level=h.level,
                    semantic_role=role,  # type: ignore[arg-type]
                    page_start_est=b_start,
                    page_end_est=b_end,
                )

                while stack and (stack[-1].level >= h.level or stack[-1].level == 0):
                    stack.pop()

                if not stack:
                    roots.append(new_node)
                else:
                    stack[-1].children.append(new_node)

                stack.append(new_node)
            else:
                active_node = stack[-1]
                active_node.direct_block_ids.append(b.block_id)
                for ancestor in stack:
                    if b_end > ancestor.page_end_est:
                        ancestor.page_end_est = b_end

        if len(roots) > 1 and not roots[0].direct_block_ids and not roots[0].children:
            roots.pop(0)

        return roots

    @staticmethod
    def _classify_heading_semantic_role(
        clean_title: str, heading_kind: str, level: int
    ) -> str:
        """Определяет роль заголовка: введение, раздел, заключение, приложение."""
        up = re.sub(r"\s+", " ", clean_title).strip().upper()
        if heading_kind == "appendix_heading" or up.startswith("ПРИЛОЖЕНИЕ") or up == "ПРИЛОЖЕНИЯ":
            return "appendix"
        if " ЗАДАНИЕ" in f" {up}" or up.startswith("ЗАДАНИЕ"):
            return "task_sheet"
        if up in ("РЕФЕРАТ", "ABSTRACT", "АННОТАЦИЯ"):
            return "abstract_referat"
        if up in ("СОДЕРЖАНИЕ", "ОГЛАВЛЕНИЕ"):
            return "toc"
        if any(k in up for k in ("СОКРАЩЕНИЙ", "ОБОЗНАЧЕНИЙ", "ТЕРМИНЫ", "ОПРЕДЕЛЕНИЯ", "ГЛОССАРИЙ")):
            return "abbreviations"
        if up == "ВВЕДЕНИЕ":
            return "introduction"
        if up in ("ЗАКЛЮЧЕНИЕ", "ВЫВОДЫ"):
            return "conclusion"
        if any(k in up for k in ("СПИСОК", "ЛИТЕРАТУРЫ", "ИСТОЧНИКОВ", "БИБЛИОГРАФ")):
            return "bibliography"
        if level == 1:
            return "main_chapter"
        if level > 1:
            return "sub_section"
        return "unknown"

    def reconcile_toc_with_headings(
        self,
        toc_block: TocContainerBlock,
        blocks: List[BlockUnion],
        verify_page_numbers: bool = True,
    ) -> None:
        """
        Сверяет записи оглавления с заголовками и их страницами.

        При verify_page_numbers=False вычисляется только фактический сдвиг
        page_number_delta, а is_page_number_match остается None.
        """
        headings: List[ParagraphBlock] = [
            b
            for b in blocks
            if isinstance(b, ParagraphBlock)
            and b.heading_info is not None
            and b.heading_info.is_heading
            and b.block_id != toc_block.toc_heading_block_id
        ]

        used_heading_ids: Set[str] = set()
        # Пары (запись оглавления, найденный заголовок) — для расчёта систематического сдвига страниц
        matched_pairs: List[Tuple[TocEntryItem, ParagraphBlock]] = []

        for entry in toc_block.entries:
            entry_num_norm = (
                entry.extracted_number.strip().rstrip(".").upper()
                if entry.extracted_number
                else None
            )
            entry_title_norm = self._normalize_for_comparison(entry.extracted_title)

            matched_h: Optional[ParagraphBlock] = None

            # 1. Точное совпадение по номеру раздела или по паре (номер + заголовок)
            for h_block in headings:
                if h_block.block_id in used_heading_ids:
                    continue
                h_info = h_block.heading_info
                assert h_info is not None
                h_num_norm = (
                    h_info.section_number_raw.strip().rstrip(".").upper()
                    if h_info.section_number_raw
                    else None
                )
                h_title_norm = self._normalize_for_comparison(h_info.clean_title_text)

                if entry_num_norm and h_num_norm and entry_num_norm == h_num_norm:
                    matched_h = h_block
                    break
                if entry_title_norm and entry_title_norm == h_title_norm and entry_num_norm == h_num_norm:
                    matched_h = h_block
                    break

            # 2. Симметричный поиск по тексту (если в оглавлении написан префикс "Раздел 1", а в тексте без него)
            if matched_h is None and entry_title_norm:
                for h_block in headings:
                    if h_block.block_id in used_heading_ids:
                        continue
                    h_info = h_block.heading_info
                    assert h_info is not None
                    h_title_norm = self._normalize_for_comparison(h_info.clean_title_text)
                    if entry_title_norm == h_title_norm or (
                        min(len(entry_title_norm), len(h_title_norm)) >= 8
                        and (entry_title_norm in h_title_norm or h_title_norm in entry_title_norm)
                    ):
                        matched_h = h_block
                        break

            if matched_h is not None and matched_h.heading_info is not None:
                used_heading_ids.add(matched_h.block_id)
                entry.matched_heading_block_id = matched_h.block_id
                entry.actual_heading_page_est = matched_h.page_start_est

                h_clean = self._normalize_for_comparison(matched_h.heading_info.clean_title_text)
                entry.is_title_exact_match = (entry_title_norm == h_clean)
                matched_pairs.append((entry, matched_h))
            else:
                entry.is_title_exact_match = False
                entry.is_page_number_match = None

        # 3. Номера страниц: в ВКР титульный лист и задание часто не нумеруются, поэтому
        # напечатанный в оглавлении номер систематически отстает от физического page_start_est.
        # Вычисляем типичный сдвиг (мода разностей) и проверяем совпадение с его учетом.
        deltas: List[int] = []
        for entry, h_block in matched_pairs:
            if entry.extracted_page_str and entry.extracted_page_str.isdigit():
                toc_page_int = int(entry.extracted_page_str)
                entry.page_number_delta = toc_page_int - h_block.page_start_est
                deltas.append(entry.page_number_delta)

        if not verify_page_numbers:
            for entry in toc_block.entries:
                entry.is_page_number_match = None
            return

        page_offset = 0
        if deltas:
            counts = Counter(deltas)
            page_offset = max(counts.items(), key=lambda kv: (kv[1], -abs(kv[0])))[0]

        for entry, h_block in matched_pairs:
            if entry.extracted_page_str and entry.extracted_page_str.isdigit():
                toc_page_int = int(entry.extracted_page_str)
                entry.is_page_number_match = (
                    toc_page_int == h_block.page_start_est
                    or toc_page_int + page_offset == h_block.page_start_est
                )
            else:
                entry.is_page_number_match = None

    @staticmethod
    def _normalize_for_comparison(text: str) -> str:
        """Приводит заголовок к виду, пригодному для сравнения."""
        t = re.sub(r"^приложение\s+[А-ЯЁA-Z0-9]+\s*[\—\–\-\.:]?\s*", "", text.strip(), flags=re.I)
        t = re.sub(r"^(?:глава|раздел|параграф)\s+\d+(?:\.\d+)*\.?\s*[\—\–\-\.:]?\s*", "", t, flags=re.I)
        t = re.sub(r"\s+", " ", t).strip().rstrip(".").upper()
        return t.replace("Ё", "Е")

    def enrich_equations_and_explications(
        self, blocks: List[BlockUnion], equations: List[EquationObject]
    ) -> None:
        """Дополняет формулы номерами и текстом пояснений."""
        if not equations:
            return

        eq_by_id: Dict[str, EquationObject] = {eq.equation_id: eq for eq in equations}

        for idx, b in enumerate(blocks):
            if isinstance(b, ParagraphBlock) and b.embedded_equation_ids:
                for eq_id in b.embedded_equation_ids:
                    eq = eq_by_id.get(eq_id)
                    if eq is None:
                        continue
                    self._inspect_paragraph_for_eq_number(b, eq)
                    self._find_where_explication_after(blocks, idx + 1, eq)

            elif isinstance(b, TableBlock) and len(b.rows) == 1:
                row = b.rows[0]
                row_eqs: List[EquationObject] = []
                num_cell_text: Optional[str] = None

                for cell in row.cells:
                    for cb in cell.blocks:
                        if isinstance(cb, ParagraphBlock) and cb.embedded_equation_ids:
                            for eq_id in cb.embedded_equation_ids:
                                if eq_id in eq_by_id:
                                    row_eqs.append(eq_by_id[eq_id])
                    if cell.col_idx == len(row.cells) - 1 and cell.text_combined:
                        m_num = RE_EQ_NUM_STANDALONE.match(cell.text_combined.strip())
                        if m_num:
                            num_cell_text = m_num.group("num")

                if row_eqs:
                    target_eq = row_eqs[-1]
                    target_eq.is_standalone_line = True
                    if num_cell_text:
                        target_eq.has_number = True
                        target_eq.number_clean = re.sub(r"\s+", "", num_cell_text)
                        target_eq.number_raw = f"({target_eq.number_clean})"
                        target_eq.number_positioning_method = "invisible_table"
                        target_eq.numbering_scheme = (  # type: ignore[assignment]
                            self._classify_numbering_scheme(target_eq.number_clean)
                        )
                    self._find_where_explication_after(blocks, idx + 1, target_eq)

    @staticmethod
    def _inspect_paragraph_for_eq_number(p: ParagraphBlock, eq: EquationObject) -> None:
        """Ищет номер формулы в абзаце справа от нее."""
        txt = p.text_visible.strip()
        if not txt:
            return

        m_tail = RE_EQ_NUM_TAIL.search(p.text_visible)
        if m_tail:
            eq.has_number = True
            clean_n = re.sub(r"\s+", "", m_tail.group("num"))
            eq.number_clean = clean_n
            eq.number_raw = f"({clean_n})"
            eq.numbering_scheme = (  # type: ignore[assignment]
                StructureCrossRefParser._classify_numbering_scheme(clean_n)
            )
            if m_tail.group("punct"):
                eq.punctuation_after_equation = m_tail.group("punct")

            has_right_tab = any(t.alignment == "right" for t in p.effective_props.tab_stops) or ("\t" in p.text_raw)
            lead_sp = m_tail.group("lead_space") or ""
            if "\t" in lead_sp or has_right_tab:
                eq.number_positioning_method = "right_tab_stop"
            elif len(lead_sp) >= 3:
                eq.number_positioning_method = "spaces_hack"
            else:
                eq.number_positioning_method = "inline_next_to_eq"
        else:
            if txt[-1] in (",", ".", ";"):
                eq.punctuation_after_equation = txt[-1]

    @staticmethod
    def _find_where_explication_after(
        blocks: List[BlockUnion], start_idx: int, eq: EquationObject
    ) -> None:
        """Ищет пояснение «где ...» после формулы."""
        cursor = start_idx
        while cursor < len(blocks) and cursor < start_idx + 2:
            b = blocks[cursor]
            if isinstance(b, ParagraphBlock) and b.is_empty_or_whitespace:
                cursor += 1
                continue
            break

        if cursor >= len(blocks):
            return
        first_b = blocks[cursor]
        if not isinstance(first_b, ParagraphBlock):
            return

        txt = first_b.text_visible.strip()
        m_where = RE_WHERE_START.match(txt)
        if not m_where:
            return

        word_gde = m_where.group("word")
        has_colon = m_where.group("colon") is not None

        eq.has_where_explication = True
        eq.where_starts_lowercase_no_colon = (word_gde.islower() and not has_colon)
        eq.where_block_ids.append(first_b.block_id)

        if not txt.endswith("."):
            for follow_idx in range(cursor + 1, min(len(blocks), cursor + 15)):
                fb = blocks[follow_idx]
                if not isinstance(fb, ParagraphBlock) or fb.is_empty_or_whitespace:
                    break
                if fb.heading_info or fb.embedded_visual_ids or fb.embedded_equation_ids:
                    break
                f_txt = fb.text_visible.strip()
                if re.match(r"^[А-ЯЁA-Zа-яёa-z0-9\s,\(\)]+\s*[\—\–\-]", f_txt):
                    eq.where_block_ids.append(fb.block_id)
                    if f_txt.endswith("."):
                        break
                else:
                    break

    def extract_and_link_captions(
        self,
        blocks: List[BlockUnion],
        visuals: Dict[str, VisualObject],
        doc_tree: Optional[List[DocumentTreeNode]] = None,
    ) -> Tuple[List[NumberedCaptionEntity], List[NumberedCaptionEntity], List[NumberedCaptionEntity]]:
        """Извлекает подписи рисунков, таблиц и приложений и связывает их с объектами."""
        figures: List[NumberedCaptionEntity] = []
        tables: List[NumberedCaptionEntity] = []
        apps: List[NumberedCaptionEntity] = []

        node_by_heading_block: Dict[str, str] = {
            node.heading_block_id: node.node_id
            for node in self._iter_tree_nodes(doc_tree or [])
            if node.heading_block_id is not None
        }

        # В одних документах подпись стоит над рисунком («Скриншот 1.»), в других —
        # под ним («Рисунок 1 — ...»). Направление поиска объекта определяется один
        # раз по первым подписям, иначе подпись может забрать чужую иллюстрацию.
        caption_side = self._detect_figure_caption_side(blocks)

        fig_seq = 0
        tbl_seq = 0
        app_seq = 0

        for idx, b in enumerate(blocks):
            if not isinstance(b, ParagraphBlock) or b.is_empty_or_whitespace:
                continue

            txt = b.text_visible.strip()

            m_fig = RE_FIGURE_CAPTION.match(txt)
            if m_fig and not (b.heading_info and b.heading_info.is_heading):
                fig_seq += 1
                prefix_w = m_fig.group("prefix").strip()
                num_s = re.sub(r"\s+", "", m_fig.group("num")).upper()
                sep_s = m_fig.group("sep")
                title_raw = (m_fig.group("title") or "").strip()
                has_dot = title_raw.endswith(".")
                clean_title = title_raw.rstrip(".")

                target_vis_id = self._find_caption_visual(
                    blocks, idx, b, visuals, caption_side
                )

                if target_vis_id and target_vis_id in visuals:
                    visuals[target_vis_id].caption_block_id = b.block_id

                figures.append(
                    NumberedCaptionEntity(
                        entity_id=f"fig_ent_{fig_seq:03d}",
                        entity_type="figure",
                        target_object_id=target_vis_id,
                        caption_block_id=b.block_id,
                        page_est=b.page_start_est,
                        prefix_word=prefix_w,
                        number_str=num_s,
                        numbering_scheme=self._classify_numbering_scheme(num_s),  # type: ignore[arg-type]
                        separator=sep_s,
                        title_text=clean_title,
                        has_trailing_dot=has_dot,
                    )
                )
                continue

            m_tbl = RE_TABLE_CAPTION.match(txt)
            if m_tbl and not (b.heading_info and b.heading_info.is_heading):
                tbl_seq += 1
                prefix_w = m_tbl.group("prefix").strip()
                num_s = re.sub(r"\s+", "", m_tbl.group("num")).upper()
                sep_s = m_tbl.group("sep")
                title_raw = (m_tbl.group("title") or "").strip()
                has_dot = title_raw.endswith(".")
                clean_title = title_raw.rstrip(".")

                target_tbl_id: Optional[str] = None
                for fwd_i in range(idx + 1, min(len(blocks), idx + 4)):
                    nxt_b = blocks[fwd_i]
                    if isinstance(nxt_b, TableBlock):
                        target_tbl_id = nxt_b.block_id
                        if nxt_b.caption_block_id is None:
                            nxt_b.caption_block_id = b.block_id
                        break
                    if isinstance(nxt_b, ParagraphBlock) and not nxt_b.is_empty_or_whitespace:
                        break

                tables.append(
                    NumberedCaptionEntity(
                        entity_id=f"tbl_ent_{tbl_seq:03d}",
                        entity_type="table",
                        target_object_id=target_tbl_id,
                        caption_block_id=b.block_id,
                        page_est=b.page_start_est,
                        prefix_word=prefix_w,
                        number_str=num_s,
                        numbering_scheme=self._classify_numbering_scheme(num_s),  # type: ignore[arg-type]
                        separator=sep_s,
                        title_text=clean_title,
                        has_trailing_dot=has_dot,
                    )
                )
                continue

            if b.heading_info and b.heading_info.heading_kind == "appendix_heading":
                m_app = RE_APPENDIX_TITLE.match(txt)
                app_seq += 1
                num_s = (
                    m_app.group("num").upper()
                    if m_app
                    else (b.heading_info.section_number_raw or str(app_seq)).upper()
                )
                sep_s = m_app.group("sep") if m_app else None
                title_raw = (m_app.group("title") if m_app and m_app.group("title") else b.heading_info.clean_title_text).strip()

                if not title_raw and idx + 1 < len(blocks):
                    nxt_p = blocks[idx + 1]
                    if (
                        isinstance(nxt_p, ParagraphBlock)
                        and nxt_p.effective_props.alignment == "center"
                        and not nxt_p.is_empty_or_whitespace
                    ):
                        title_raw = nxt_p.text_visible.strip()

                apps.append(
                    NumberedCaptionEntity(
                        entity_id=f"app_ent_{app_seq:02d}",
                        entity_type="appendix",
                        target_object_id=b.block_id,
                        caption_block_id=b.block_id,
                        structure_node_id=node_by_heading_block.get(b.block_id),
                        page_est=b.page_start_est,
                        prefix_word="ПРИЛОЖЕНИЕ",
                        number_str=num_s,
                        numbering_scheme="appendix_based",
                        separator=sep_s,
                        title_text=title_raw.rstrip("."),
                        has_trailing_dot=title_raw.endswith("."),
                    )
                )

        return figures, tables, apps

    @staticmethod
    def _adjacent_visual_paragraph(
        blocks: List[BlockUnion], idx: int, step: int
    ) -> Optional[ParagraphBlock]:
        """Ищет ближайший абзац с иллюстрацией в направлении step, пропуская пустые."""
        for distance in range(1, 7):
            j = idx + step * distance
            if not 0 <= j < len(blocks):
                return None
            neighbour = blocks[j]
            if not isinstance(neighbour, ParagraphBlock):
                return None
            if neighbour.embedded_visual_ids:
                return neighbour
            if not neighbour.is_empty_or_whitespace:
                return None
        return None

    def _detect_figure_caption_side(self, blocks: List[BlockUnion]) -> str:
        """Определяет типовое положение подписи рисунка: над иллюстрацией или под ней."""
        above = 0  # подпись стоит перед рисунком
        below = 0  # подпись стоит после рисунка

        for idx, b in enumerate(blocks):
            if not isinstance(b, ParagraphBlock) or b.is_empty_or_whitespace:
                continue
            if b.embedded_visual_ids or (b.heading_info and b.heading_info.is_heading):
                continue
            if not RE_FIGURE_CAPTION.match(b.text_visible.strip()):
                continue

            prev_para = self._adjacent_visual_paragraph(blocks, idx, -1)
            next_para = self._adjacent_visual_paragraph(blocks, idx, 1)
            if next_para is not None and prev_para is None:
                above += 1
            elif prev_para is not None and next_para is None:
                below += 1

        return "below" if below > above else "above"

    @staticmethod
    def _find_caption_visual(
        blocks: List[BlockUnion],
        idx: int,
        caption_block: ParagraphBlock,
        visuals: Dict[str, VisualObject],
        caption_side: str,
    ) -> Optional[str]:
        """
        Находит иллюстрацию для подписи рисунка.

        Сначала проверяется тот же абзац, затем соседние абзацы в типовом для
        документа направлении (подпись над рисунком или под ним). Иллюстрация,
        уже занятая другой подписью, не переиспользуется.
        """
        if caption_block.embedded_visual_ids:
            for vis_id in caption_block.embedded_visual_ids:
                if vis_id in visuals and visuals[vis_id].caption_block_id is None:
                    return vis_id
            for vis_id in caption_block.embedded_visual_ids:
                if vis_id in visuals:
                    return vis_id

        preferred_steps = (1, -1) if caption_side == "above" else (-1, 1)
        for step in preferred_steps:
            for distance in range(1, 7):
                j = idx + step * distance
                if not 0 <= j < len(blocks):
                    break
                neighbour = blocks[j]
                if not isinstance(neighbour, ParagraphBlock):
                    break
                if neighbour.embedded_visual_ids:
                    for vis_id in neighbour.embedded_visual_ids:
                        if vis_id in visuals and visuals[vis_id].caption_block_id is None:
                            return vis_id
                    # Иллюстрация уже подписана другим абзацем — дальше искать не нужно.
                    break
                if not neighbour.is_empty_or_whitespace:
                    break
        return None

    @staticmethod
    def _classify_numbering_scheme(num_str: str) -> str:
        """Определяет схему нумерации подписей: сквозная или по разделам."""
        clean = num_str.strip(".")
        if re.match(r"^\d+$", clean):
            return "continuous_arabic"
        if re.match(r"^\d+\.\d+(?:\.\d+)*$", clean):
            return "section_based"
        if re.match(r"^[А-ЯЁA-Z]\.\d+$", clean, re.I):
            return "appendix_based"
        return "invalid"

    def parse_bibliography_section(
        self,
        blocks: List[BlockUnion],
        doc_tree: List[DocumentTreeNode],
    ) -> Tuple[List[BibliographyEntry], Set[str]]:
        """Разбирает список литературы и возвращает записи и их block_id."""
        bib_block_ids: Set[str] = set()
        for node in self._iter_tree_nodes(doc_tree):
            if node.semantic_role == "bibliography":
                bib_block_ids.update(node.direct_block_ids)
                for child in self._iter_tree_nodes(node.children):
                    bib_block_ids.update(child.direct_block_ids)

        if not bib_block_ids:
            return [], set()

        block_map: Dict[str, BlockUnion] = {b.block_id: b for b in blocks}
        entries: List[BibliographyEntry] = []
        order_idx = 0

        for b_id in [b.block_id for b in blocks if b.block_id in bib_block_ids]:
            b = block_map.get(b_id)
            if not isinstance(b, ParagraphBlock) or b.is_empty_or_whitespace:
                continue
            if b.heading_info and b.heading_info.is_heading:
                continue

            full_txt = b.text_visible.strip()
            raw_prefix: Optional[str] = None
            body_txt = full_txt

            if b.list_info and b.list_info.mechanism == "openxml_numPr":
                raw_prefix = b.list_info.rendered_marker
                body_txt = b.list_info.body_text_without_marker
            else:
                m_num = RE_BIB_MANUAL_NUM.match(full_txt)
                if m_num:
                    raw_prefix = m_num.group("raw_pref").strip()
                    body_txt = m_num.group("body").strip()

            order_idx += 1
            entries.append(self._analyze_single_bib_entry(b.block_id, order_idx, raw_prefix, body_txt))

        return entries, bib_block_ids

    def _analyze_single_bib_entry(
        self,
        block_id: str,
        order_index: int,
        raw_prefix: Optional[str],
        raw_text: str,
    ) -> BibliographyEntry:
        """Разбирает одну запись списка литературы по полям."""
        has_double_slash = "//" in raw_text
        text_no_urls = RE_BIB_URL.sub("", raw_text).replace("//", "")
        has_single_slash = "/" in text_no_urls
        dot_dash_count = len(RE_BIB_DOT_DASH.findall(raw_text))

        text_no_access = RE_BIB_ACCESS_DATE.sub("", raw_text)
        years_found = [int(y) for y in RE_BIB_YEAR.findall(text_no_access)]
        year_val = years_found[-1] if years_found else None

        m_pages = RE_BIB_PAGES.search(raw_text)
        pages_val = m_pages.group(0).strip() if m_pages else None

        m_isbn = RE_BIB_ISBN_ISSN.search(raw_text)
        isbn_val = m_isbn.group(0).strip() if m_isbn else None

        urls_found = [u.rstrip(".,;:") for u in RE_BIB_URL.findall(raw_text)]
        m_access = RE_BIB_ACCESS_DATE.search(raw_text)
        access_val = m_access.group(0).strip() if m_access else None

        has_type_desig = bool(
            re.search(
                r"\[\s*Электронный\s+ресурс\s*\]|Текст\s*:\s*(?:непосредственный|электронный)",
                raw_text,
                re.IGNORECASE,
            )
        )

        category = self._classify_bib_category(raw_text, bool(urls_found), has_double_slash)

        return BibliographyEntry(
            bib_id=f"bib_{order_index:03d}",
            block_id=block_id,
            order_index=order_index,
            raw_number_prefix=raw_prefix,
            raw_text=raw_text,
            detected_category=category,  # type: ignore[arg-type]
            has_slash_responsibility=has_single_slash,
            has_double_slash_source=has_double_slash,
            dot_dash_count=dot_dash_count,
            year_extracted=year_val,
            pages_extracted=pages_val,
            isbn_or_issn=isbn_val,
            urls=urls_found,
            access_date_raw=access_val,
            has_text_type_designation=has_type_desig,
        )

    @staticmethod
    def _classify_bib_category(
        text: str, has_url: bool, has_double_slash: bool
    ) -> str:
        """Определяет категорию источника: книга, статья, стандарт, ссылка."""
        low = text.lower()
        if re.search(r"\b(?:гост|снип|сп|санпин|исо|iso|iec|мэк)\b", low):
            return "standard_gost"
        if re.search(r"\b(?:федеральный\s+закон|кодекс|постановление\s+правительства|указ\s+президента|приказ\s+мин)\b", low):
            return "normative_act_law"
        if re.search(r"\b(?:патент|пат\.\s*\d+|а\.с\.\s*\d+|свидетельство\s+о\s+гос)\b", low):
            return "patent"
        if re.search(r"\b(?:дис\.\s*…|диссертац|автореф\.\s*дис)\b", low):
            return "dissertation_autoref"
        if has_url or "режим доступа" in low or "url:" in low or "[электронный ресурс]" in low:
            return "electronic_resource"
        if has_double_slash:
            if any(k in low for k in ("конф.", "конференц", "симпозиум", "форум", "чтени", "сб. науч. тр", "материалы")):
                return "conference_paper"
            return "journal_article"
        if any(k in low for k in ("учебник", "учеб. пособие", "монография")) or re.search(r"\b\d+\s*с\.", low):
            return "book_monograph"
        return "unknown"

    def extract_and_resolve_mentions(
        self,
        blocks: List[BlockUnion],
        figures: List[NumberedCaptionEntity],
        tables: List[NumberedCaptionEntity],
        apps: List[NumberedCaptionEntity],
        equations: List[EquationObject],
        bibliography: List[BibliographyEntry],
        excluded_block_ids: Set[str],
        document_tree: Optional[List[DocumentTreeNode]] = None,
    ) -> List[TextCrossReferenceMention]:
        """Находит упоминания объектов в тексте и разрешает их цели."""
        fig_by_num: Dict[str, NumberedCaptionEntity] = {f.number_str.upper(): f for f in figures}
        tbl_by_num: Dict[str, NumberedCaptionEntity] = {t.number_str.upper(): t for t in tables}
        app_by_num: Dict[str, NumberedCaptionEntity] = {a.number_str.upper(): a for a in apps}
        eq_by_num: Dict[str, EquationObject] = {
            eq.number_clean.upper(): eq for eq in equations if eq.number_clean
        }
        bib_by_idx: Dict[str, BibliographyEntry] = {str(b.order_index): b for b in bibliography}
        section_node_by_number: Dict[str, str] = {
            node.section_number.strip().rstrip(".").upper(): node.node_id
            for node in self._iter_tree_nodes(document_tree or [])
            if node.section_number and node.section_number.strip().rstrip(".")
        }

        block_stream_order: Dict[str, int] = {}
        stream_counter = 0
        for p in self._iter_all_paragraphs_with_tables(blocks):
            stream_counter += 1
            block_stream_order[p.block_id] = stream_counter

        caption_block_ids: Set[str] = (
            {f.caption_block_id for f in figures}
            | {t.caption_block_id for t in tables}
            | {a.caption_block_id for a in apps}
        )

        mentions: List[TextCrossReferenceMention] = []
        mention_seq = 0
        citation_appearance_rank_counter = 0

        for p in self._iter_all_paragraphs_with_tables(blocks):
            if p.block_id in excluded_block_ids or p.block_id in caption_block_ids:
                continue
            if p.heading_info and p.heading_info.is_heading:
                continue
            txt = p.text_visible
            if not txt.strip():
                continue

            p_order = block_stream_order.get(p.block_id, 0)

            for m_brk in RE_BROKEN_FIELD.finditer(txt):
                mention_seq += 1
                mentions.append(
                    TextCrossReferenceMention(
                        mention_id=f"mnt_{mention_seq:04d}",
                        block_id=p.block_id,
                        page_est=p.page_start_est,
                        start_char=m_brk.start(),
                        end_char=m_brk.end(),
                        raw_snippet=m_brk.group(0),
                        ref_type="broken_word_field",
                    )
                )

            for m_cit in RE_CITATION_BRACKETS.finditer(txt):
                snippet = m_cit.group(0)
                targets = self._expand_citation_brackets(snippet)
                if not targets:
                    continue

                mention_seq += 1
                m_id = f"mnt_{mention_seq:04d}"
                resolved_ids: List[str] = []

                for t_num in targets:
                    bib_ent = bib_by_idx.get(t_num)
                    if bib_ent is not None:
                        resolved_ids.append(bib_ent.bib_id)
                        if not bib_ent.is_cited_in_text:
                            bib_ent.is_cited_in_text = True
                            citation_appearance_rank_counter += 1
                            bib_ent.first_citation_appearance_rank = citation_appearance_rank_counter
                        bib_ent.citation_mention_ids.append(m_id)

                mentions.append(
                    TextCrossReferenceMention(
                        mention_id=m_id,
                        block_id=p.block_id,
                        page_est=p.page_start_est,
                        start_char=m_cit.start(),
                        end_char=m_cit.end(),
                        raw_snippet=snippet,
                        ref_type="bibliography",
                        extracted_targets=targets,
                        resolved_entity_ids=resolved_ids,
                    )
                )

            for m_ent in RE_ENTITY_MENTION.finditer(txt):
                word_raw = m_ent.group("word")
                ws_between = m_ent.group("ws")
                target_raw = m_ent.group("target")
                word_low = word_raw.lower()

                if len(target_raw.strip()) == 1 and target_raw.isalpha() and not word_low.startswith("прил"):
                    continue

                ref_type = self._classify_mention_type(word_low)
                uses_abbrev = word_low.endswith(".")
                has_nbsp = "\u00A0" in ws_between
                targets = self._expand_entity_target_range(target_raw)

                mention_seq += 1
                m_id = f"mnt_{mention_seq:04d}"
                resolved_ids = []
                resolved_node_ids: List[str] = []

                for t_key in targets:
                    norm_key = t_key.upper()
                    if ref_type == "figure" and norm_key in fig_by_num:
                        ent = fig_by_num[norm_key]
                        resolved_ids.append(ent.entity_id)
                        self._register_caption_mention(ent, m_id, p.block_id, p.page_start_est, p_order, block_stream_order)
                    elif ref_type == "table" and norm_key in tbl_by_num:
                        ent = tbl_by_num[norm_key]
                        resolved_ids.append(ent.entity_id)
                        self._register_caption_mention(ent, m_id, p.block_id, p.page_start_est, p_order, block_stream_order)
                    elif ref_type == "appendix" and norm_key in app_by_num:
                        ent = app_by_num[norm_key]
                        resolved_ids.append(ent.entity_id)
                        self._register_caption_mention(ent, m_id, p.block_id, p.page_start_est, p_order, block_stream_order)
                    elif ref_type == "equation" and norm_key in eq_by_num:
                        equation = eq_by_num[norm_key]
                        resolved_ids.append(equation.equation_id)
                        self._register_equation_mention(
                            equation,
                            m_id,
                            p.block_id,
                            p.page_start_est,
                            p_order,
                            block_stream_order,
                        )
                    elif ref_type == "section":
                        section_key = norm_key.strip().rstrip(".")
                        node_id = section_node_by_number.get(section_key)
                        if node_id is not None and node_id not in resolved_node_ids:
                            resolved_node_ids.append(node_id)

                mentions.append(
                    TextCrossReferenceMention(
                        mention_id=m_id,
                        block_id=p.block_id,
                        page_est=p.page_start_est,
                        start_char=m_ent.start(),
                        end_char=m_ent.end(),
                        raw_snippet=m_ent.group(0),
                        ref_type=ref_type,  # type: ignore[arg-type]
                        extracted_targets=targets,
                        resolved_entity_ids=resolved_ids,
                        resolved_structure_node_ids=resolved_node_ids,
                        uses_short_abbrev=uses_abbrev,
                        has_nbsp_before_number=has_nbsp,
                    )
                )

            for m_app in RE_APPENDIX_MENTION.finditer(txt):
                mention_seq += 1
                m_id = f"mnt_{mention_seq:04d}"
                target = m_app.group("target")
                resolved_ids = []
                app_ent = app_by_num.get(target.upper())
                if app_ent is not None:
                    resolved_ids.append(app_ent.entity_id)
                    self._register_caption_mention(
                        app_ent,
                        m_id,
                        p.block_id,
                        p.page_start_est,
                        p_order,
                        block_stream_order,
                    )
                mentions.append(
                    TextCrossReferenceMention(
                        mention_id=m_id,
                        block_id=p.block_id,
                        page_est=p.page_start_est,
                        start_char=m_app.start(),
                        end_char=m_app.end(),
                        raw_snippet=m_app.group(0),
                        ref_type="appendix",
                        extracted_targets=[target],
                        resolved_entity_ids=resolved_ids,
                        uses_short_abbrev=m_app.group("word").lower().endswith("."),
                        has_nbsp_before_number="\u00A0" in m_app.group("ws"),
                    )
                )

        return mentions

    @staticmethod
    def _register_caption_mention(
        entity: NumberedCaptionEntity,
        mention_id: str,
        mention_block_id: str,
        mention_page: int,
        mention_stream_order: int,
        block_stream_order: Dict[str, int],
    ) -> None:
        """Регистрирует упоминание внутри подписи."""
        entity.all_mention_ids.append(mention_id)
        if not entity.is_mentioned_in_text:
            entity.is_mentioned_in_text = True
            entity.first_mention_block_id = mention_block_id
            entity.first_mention_page_est = mention_page
            caption_order = block_stream_order.get(entity.caption_block_id, 999999)
            entity.is_mentioned_before_appearance = (mention_stream_order < caption_order)

    @staticmethod
    def _register_equation_mention(
        equation: EquationObject,
        mention_id: str,
        mention_block_id: str,
        mention_page: int,
        mention_stream_order: int,
        block_stream_order: Dict[str, int],
    ) -> None:
        """Регистрирует упоминание формулы и ее положение относительно самой формулы."""
        equation.all_mention_ids.append(mention_id)
        if not equation.is_mentioned_in_text:
            equation.is_mentioned_in_text = True
            equation.first_mention_block_id = mention_block_id
            equation.first_mention_page_est = mention_page
            equation_order = block_stream_order.get(equation.parent_block_id, 999999)
            equation.is_mentioned_before_appearance = mention_stream_order < equation_order

    @staticmethod
    def _classify_mention_type(word_low: str) -> str:
        """Определяет тип упоминания: рисунок, таблица, формула, раздел, источник."""
        if word_low.startswith("рис") or word_low.startswith("скрин") or word_low.startswith("снимок"):
            return "figure"
        if word_low.startswith("табл"):
            return "table"
        if word_low.startswith("форм") or word_low.startswith("уравн"):
            return "equation"
        if word_low.startswith("прил"):
            return "appendix"
        return "section"

    @staticmethod
    def _expand_citation_brackets(bracket_str: str) -> List[str]:
        """Раскрывает групповые ссылки вида «[3, 5-7]» в отдельные упоминания."""
        inner = bracket_str.strip("[] \t")
        inner = re.sub(r"[,;]\s*(?:с|стр|p|pp)\.?\s*\d+(?:\s*[\—\–\-]\s*\d+)?", "", inner, flags=re.I)

        results: List[str] = []
        for part in re.split(r"[,;]", inner):
            part = part.strip()
            if not part:
                continue
            m_range = re.match(r"^(\d+)\s*[\—\–\-]\s*(\d+)$", part)
            if m_range:
                start_n, end_n = int(m_range.group(1)), int(m_range.group(2))
                if start_n <= end_n and (end_n - start_n) <= 50:
                    results.extend(str(i) for i in range(start_n, end_n + 1))
                else:
                    results.extend([str(start_n), str(end_n)])
            elif part.isdigit():
                results.append(part)
        return results

    @staticmethod
    def _expand_entity_target_range(target_raw: str) -> List[str]:
        """Раскрывает диапазоны целей вида «рисунки 2.1-2.3»."""
        clean = re.sub(r"\s+", "", target_raw)
        if not any(d in clean for d in ("—", "–", "-")):
            return [clean]

        parts = re.split(r"[\—\–\-]", clean)
        if len(parts) != 2:
            return [clean]

        left, right = parts[0], parts[1]
        if left.isdigit() and right.isdigit():
            s, e = int(left), int(right)
            if s <= e and (e - s) <= 20:
                return [str(i) for i in range(s, e + 1)]
        m_l = re.match(r"^(\d+\.)(\d+)$", left)
        m_r = re.match(r"^(\d+\.)(\d+)$", right)
        if m_l and m_r and m_l.group(1) == m_r.group(1):
            pref = m_l.group(1)
            s, e = int(m_l.group(2)), int(m_r.group(2))
            if s <= e and (e - s) <= 20:
                return [f"{pref}{i}" for i in range(s, e + 1)]

        return [left, right]

    @classmethod
    def _iter_tree_nodes(cls, nodes: List[DocumentTreeNode]):
        """Обходит узлы дерева документа в порядке следования."""
        for n in nodes:
            yield n
            yield from cls._iter_tree_nodes(n.children)

    @classmethod
    def _iter_all_paragraphs_with_tables(cls, blocks: List[BlockUnion]):
        """Обходит абзацы верхнего уровня и абзацы внутри таблиц."""
        for b in blocks:
            if isinstance(b, ParagraphBlock):
                yield b
            elif isinstance(b, TableBlock):
                for row in b.rows:
                    for cell in row.cells:
                        if not cell.is_merged_shadow:
                            yield from cls._iter_all_paragraphs_with_tables(cell.blocks)