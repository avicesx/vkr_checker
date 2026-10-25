"""Сопоставление блоков DOCX с физическими страницами PDF.

Работает в фазе 5, до анализа структуры. Из PDF извлекаются текстовые строки
и графические боксы, блоки DOCX выстраиваются в монотонную последовательность,
после чего получают реальные номера страниц, координаты, аудит колонтитулов
и границы текстового блока.

Если текст PDF расходится с DOCX (pdf_docx_sync_score ниже порога), координатная
привязка не применяется: остаются эвристические страницы DOCX и предупреждение
в аномалиях.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .schema import (
    BlockUnion,
    EquationObject,
    ParagraphBlock,
    PdfPageAudit,
    SectionProperties,
    TableBlock,
    TableRow,
    TocContainerBlock,
    VisualObject,
)

PT_TO_MM = 25.4 / 72.0  # 1 пункт PDF = 0.352778 мм

RE_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)
RE_PAGE_NUM_CANDIDATE = re.compile(
    r"^\s*(?:стр\.?|страница|page|p\.?)?\s*[\-\u2012\u2013\u2014]?\s*(?P<num>\d{1,4})\s*[\-\u2012\u2013\u2014]?\s*$",
    re.IGNORECASE,
)


@dataclass
class _PdfTextLine:
    """Строка текста PDF с координатами и признаком зоны."""

    global_idx: int
    page_num: int  # 1-based
    x0_mm: float
    y0_mm: float
    x1_mm: float
    y1_mm: float
    raw_text: str
    compact_text: str  # Нижний регистр, только буквы/цифры, ё -> е (устойчиво к дефисам переноса)
    font_name: str
    font_size_pt: float


@dataclass
class _PdfVisualBox:
    """Графический бокс на странице PDF."""

    page_num: int
    x0_mm: float
    y0_mm: float
    x1_mm: float
    y1_mm: float
    used: bool = False

    @property
    def width_mm(self) -> float:
        """Ширина бокса в миллиметрах."""
        return max(0.1, self.x1_mm - self.x0_mm)

    @property
    def height_mm(self) -> float:
        """Высота бокса в миллиметрах."""
        return max(0.1, self.y1_mm - self.y0_mm)


@dataclass
class PdfAlignmentReport:
    """Итоги выравнивания: страницы, синхронность, предупреждение и факт применения."""

    total_pages: int
    pdf_filename: str
    pdf_sha256: str
    sync_score: float
    mismatch_warning: Optional[str]
    alignment_applied: bool = True
    pages_audit: List[PdfPageAudit] = field(default_factory=list)


class PdfPageAligner:
    """
    Сканирует эталонный PDF-файл студента через PyMuPDF (fitz),
    проверяет колонтитулы/нумерацию страниц в углах листа и привязывает
    все блоки, строки таблиц и рисунки из DOCX к точным страницам и координатам в мм.
    """

    def __init__(self, pdf_path: str | Path) -> None:
        """Проверяет существование файла и запоминает путь к PDF."""
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF-файл не найден: {self.pdf_path}")

    @staticmethod
    def _normalize_compact(text: str) -> str:
        """
        Сжимает строку до буквенно-цифрового потока в нижнем регистре (ё -> е).
        Делает сопоставление нечувствительным к мягким переносам слов и разрывам строк в PDF.
        """
        t = text.lower().replace("ё", "е")
        return RE_NON_ALNUM.sub("", t).replace("_", "")

    def align_and_enrich(
        self,
        blocks: List[BlockUnion],
        sections: List[SectionProperties],
        visuals: Dict[str, VisualObject],
        equations: List[EquationObject],
    ) -> PdfAlignmentReport:
        """Проставляет блокам физические страницы, координаты и аудит колонтитулов."""
        import fitz  # PyMuPDF

        pdf_sha256 = self._compute_sha256(self.pdf_path)
        pdf_doc = fitz.open(str(self.pdf_path))
        total_pages = len(pdf_doc)

        # Базовые поля страницы из первого раздела (для зонирования колонтитулов)
        top_margin_mm = sections[0].margins.top_mm if sections else 20.0
        bottom_margin_mm = sections[0].margins.bottom_mm if sections else 20.0

        # 1. Извлекаем строки PDF, зонируем колонтитулы и собираем рамки иллюстраций
        body_lines, visual_boxes_by_page, pages_audit = self._extract_and_zone_pdf(
            pdf_doc=pdf_doc,
            top_margin_mm=top_margin_mm,
            bottom_margin_mm=bottom_margin_mm,
        )
        pdf_doc.close()

        # 2. Вычисляем индекс синхронности текста между DOCX и PDF
        sync_score = self._compute_docx_pdf_sync_score(blocks, body_lines)
        mismatch_warning: Optional[str] = None
        if sync_score < 0.80:
            mismatch_warning = (
                f"Низкое совпадение текста между DOCX и PDF ({sync_score:.1%}). "
                f"Возможно, загружены разные редакции документа."
            )

        # 3. Монотонное выравнивание абзацев и таблиц по потоку строк PDF.
        # При низкой синхронности текстов (разные редакции документа) координатная
        # привязка дает неверные страницы, поэтому остаются эвристические страницы
        # DOCX плюс предупреждение в аномалиях.
        if sync_score < 0.80:
            if sections:
                sections[-1].page_end_est = max(sections[-1].page_start_est, total_pages)
            return PdfAlignmentReport(
                total_pages=total_pages,
                pdf_filename=os.path.basename(self.pdf_path),
                pdf_sha256=pdf_sha256,
                sync_score=round(sync_score, 4),
                mismatch_warning=mismatch_warning,
                alignment_applied=False,
                pages_audit=pages_audit,
            )

        self._align_blocks_monotonic(
            blocks=blocks,
            sections=sections,
            body_lines=body_lines,
            visuals=visuals,
            equations=equations,
            visual_boxes_by_page=visual_boxes_by_page,
        )

        if sections:
            sections[-1].page_end_est = max(sections[-1].page_start_est, total_pages)

        return PdfAlignmentReport(
            total_pages=total_pages,
            pdf_filename=os.path.basename(self.pdf_path),
            pdf_sha256=pdf_sha256,
            sync_score=round(sync_score, 4),
            mismatch_warning=mismatch_warning,
            alignment_applied=True,
            pages_audit=pages_audit,
        )

    # -----------------------------------------------------------------
    # ШАГ 1: Извлечение текста/графики из PDF и зонирование колонтитулов
    # -----------------------------------------------------------------

    def _extract_and_zone_pdf(
        self,
        pdf_doc,
        top_margin_mm: float,
        bottom_margin_mm: float,
    ) -> Tuple[List[_PdfTextLine], Dict[int, List[_PdfVisualBox]], List[PdfPageAudit]]:
        """Извлекает строки и графические боксы PDF, отделяя зоны колонтитулов."""
        body_lines: List[_PdfTextLine] = []
        visual_boxes_by_page: Dict[int, List[_PdfVisualBox]] = {}
        pages_audit: List[PdfPageAudit] = []
        global_line_idx = 0

        for page_idx in range(len(pdf_doc)):
            page_num = page_idx + 1
            page = pdf_doc[page_idx]
            rect = page.rect
            page_w_mm = round(rect.width * PT_TO_MM, 2)
            page_h_mm = round(rect.height * PT_TO_MM, 2)

            # Границы зон верхнего и нижнего колонтитулов (внутри полей страницы)
            header_limit_y_mm = max(14.0, top_margin_mm - 0.8)
            footer_limit_y_mm = min(page_h_mm - 14.0, page_h_mm - bottom_margin_mm + 0.8)

            raw_dict = page.get_text("dict")
            page_raw_lines: List[Tuple[float, float, float, float, str, str, float]] = []

            for b in raw_dict.get("blocks", []):
                if b.get("type") == 0:  # Текстовый блок
                    for line in b.get("lines", []):
                        spans = line.get("spans", [])
                        if not spans:
                            continue
                        line_txt = "".join(s.get("text", "") for s in spans).strip()
                        if not line_txt:
                            continue
                        bbox = line.get("bbox", (0, 0, 0, 0))
                        x0_mm = round(bbox[0] * PT_TO_MM, 2)
                        y0_mm = round(bbox[1] * PT_TO_MM, 2)
                        x1_mm = round(bbox[2] * PT_TO_MM, 2)
                        y1_mm = round(bbox[3] * PT_TO_MM, 2)

                        main_span = max(spans, key=lambda s: len(s.get("text", "")))
                        f_name = main_span.get("font", "")
                        f_size = round(float(main_span.get("size", 12.0)), 1)
                        page_raw_lines.append((x0_mm, y0_mm, x1_mm, y1_mm, line_txt, f_name, f_size))

            # Сортируем строки страницы сверху вниз, слева направо (с допуском 1.5 мм по вертикали)
            page_raw_lines.sort(key=lambda item: (round(item[1] / 1.5), item[0]))

            header_candidates: List[Tuple[float, float, float, float, str, str, float]] = []
            footer_candidates: List[Tuple[float, float, float, float, str, str, float]] = []
            page_body_lines: List[_PdfTextLine] = []

            for item in page_raw_lines:
                x0_mm, y0_mm, x1_mm, y1_mm, l_txt, f_name, f_size = item

                # Строка в зоне верхнего колонтитула. Проверяем верхнюю границу
                # bbox, чтобы не потерять строку, заходящую в поле верхней частью.
                if y0_mm < header_limit_y_mm and len(l_txt) <= 80:
                    header_candidates.append(item)
                    continue
                # Строка в зоне нижнего колонтитула. Проверяем нижнюю границу
                # bbox: номер страницы может начинаться выше границы поля, но
                # заходить в нижнее поле (типично для шрифта 14 pt).
                if y1_mm > footer_limit_y_mm and len(l_txt) <= 80:
                    footer_candidates.append(item)
                    continue

                compact = self._normalize_compact(l_txt)
                if not compact:
                    continue

                line_obj = _PdfTextLine(
                    global_idx=global_line_idx,
                    page_num=page_num,
                    x0_mm=x0_mm,
                    y0_mm=y0_mm,
                    x1_mm=x1_mm,
                    y1_mm=y1_mm,
                    raw_text=l_txt,
                    compact_text=compact,
                    font_name=f_name,
                    font_size_pt=f_size,
                )
                global_line_idx += 1
                body_lines.append(line_obj)
                page_body_lines.append(line_obj)

            # Аудит колонтитулов и номера страницы на данном физическом листе
            audit = self._build_page_header_footer_audit(
                page_num=page_num,
                page_w_mm=page_w_mm,
                page_h_mm=page_h_mm,
                bottom_margin_mm=bottom_margin_mm,
                header_items=header_candidates,
                footer_items=footer_candidates,
                page_body_lines=page_body_lines,
            )
            pages_audit.append(audit)

            # Извлекаем прямоугольники растровых картинок со страницы PDF
            vis_boxes: List[_PdfVisualBox] = []
            try:
                for img_info in page.get_image_info():
                    ib = img_info.get("bbox")
                    if ib:
                        vis_boxes.append(
                            _PdfVisualBox(
                                page_num=page_num,
                                x0_mm=round(ib[0] * PT_TO_MM, 2),
                                y0_mm=round(ib[1] * PT_TO_MM, 2),
                                x1_mm=round(ib[2] * PT_TO_MM, 2),
                                y1_mm=round(ib[3] * PT_TO_MM, 2),
                            )
                        )
            except Exception:
                pass
            visual_boxes_by_page[page_num] = self.cluster_drawings(vis_boxes)

        # Номера страниц: титульный лист/задание часто не нумеруются, поэтому напечатанное
        # число систематически смещено относительно физического листа. Определяем сдвиг
        # (мода разностей) и проверяем совпадение с его учетом.
        page_number_deltas = [
            a.rendered_page_number_int - a.page_num
            for a in pages_audit
            if a.rendered_page_number_int is not None
        ]
        if page_number_deltas:
            delta_counts = Counter(page_number_deltas)
            page_offset = max(delta_counts.items(), key=lambda kv: (kv[1], -abs(kv[0])))[0]
            if page_offset != 0:
                for audit_item in pages_audit:
                    if audit_item.rendered_page_number_int is not None:
                        audit_item.matches_physical_page_num = (
                            audit_item.rendered_page_number_int
                            == audit_item.page_num + page_offset
                        )

        return body_lines, visual_boxes_by_page, pages_audit

    @staticmethod
    def cluster_drawings(
        boxes: List[_PdfVisualBox], tolerance_mm: float = 2.0
    ) -> List[_PdfVisualBox]:
        """
        Склеивает пересекающиеся/касающиеся прямоугольники картинок в один bbox.

        PDF-движки часто отдают одну иллюстрацию или штамп как несколько фрагментов
        (слои, тайлы, отдельные части EMF). Без кластеризации визуал получал координаты
        случайного фрагмента, и проверка выхода за поля давала ложные срабатывания.
        """
        if not boxes:
            return []

        def _touches(a: _PdfVisualBox, b: _PdfVisualBox) -> bool:
            if a.page_num != b.page_num:
                return False
            return not (
                a.x1_mm + tolerance_mm < b.x0_mm
                or b.x1_mm + tolerance_mm < a.x0_mm
                or a.y1_mm + tolerance_mm < b.y0_mm
                or b.y1_mm + tolerance_mm < a.y0_mm
            )

        clusters: List[_PdfVisualBox] = list(boxes)
        changed = True
        while changed:
            changed = False
            merged_clusters: List[_PdfVisualBox] = []
            for cur in clusters:
                for idx, other in enumerate(merged_clusters):
                    if _touches(cur, other):
                        merged_clusters[idx] = _PdfVisualBox(
                            page_num=other.page_num,
                            x0_mm=min(cur.x0_mm, other.x0_mm),
                            y0_mm=min(cur.y0_mm, other.y0_mm),
                            x1_mm=max(cur.x1_mm, other.x1_mm),
                            y1_mm=max(cur.y1_mm, other.y1_mm),
                            used=cur.used or other.used,
                        )
                        changed = True
                        break
                else:
                    merged_clusters.append(cur)
            clusters = merged_clusters

        clusters.sort(key=lambda b: (b.page_num, b.y0_mm, b.x0_mm))
        return clusters

    @staticmethod
    def _build_page_header_footer_audit(
        page_num: int,
        page_w_mm: float,
        page_h_mm: float,
        bottom_margin_mm: float,
        header_items: List[Tuple[float, float, float, float, str, str, float]],
        footer_items: List[Tuple[float, float, float, float, str, str, float]],
        page_body_lines: List[_PdfTextLine],
    ) -> PdfPageAudit:
        """Формирует аудит колонтитулов по страницам."""
        has_num = False
        loc: str = "none"
        align: str = "none"
        raw_num_str: Optional[str] = None
        parsed_int: Optional[int] = None
        matches_phys: Optional[bool] = None
        f_name: Optional[str] = None
        f_size: Optional[float] = None
        num_y: Optional[float] = None
        num_x: Optional[float] = None

        header_extras: List[str] = []
        footer_extras: List[str] = []

        for zone_label, items, extra_collector in (
            ("footer", footer_items, footer_extras),
            ("header", header_items, header_extras),
        ):
            for x0_mm, y0_mm, x1_mm, y1_mm, txt, font_n, font_s in items:
                m = RE_PAGE_NUM_CANDIDATE.match(txt)
                if m and not has_num:
                    has_num = True
                    loc = zone_label
                    raw_num_str = txt.strip()
                    parsed_int = int(m.group("num"))
                    matches_phys = (parsed_int == page_num)
                    f_name = font_n
                    f_size = font_s
                    num_y = y0_mm
                    num_x = round((x0_mm + x1_mm) / 2.0, 2)

                    # Вычисляем горизонтальное выравнивание цифры относительно центра листа
                    page_center_x = page_w_mm / 2.0
                    if abs(num_x - page_center_x) <= 18.0:
                        align = "center"
                    elif num_x < page_center_x:
                        align = "left"
                    else:
                        align = "right"
                else:
                    extra_collector.append(txt)

        body_top = page_body_lines[0].y0_mm if page_body_lines else None
        body_bottom = max((l.y1_mm for l in page_body_lines), default=None)
        body_left = min((l.x0_mm for l in page_body_lines), default=None)
        body_right = max((l.x1_mm for l in page_body_lines), default=None)
        free_bottom: Optional[float] = None
        if body_bottom is not None:
            printable_bottom = page_h_mm - bottom_margin_mm
            free_bottom = round(max(0.0, printable_bottom - body_bottom), 2)

        return PdfPageAudit(
            page_num=page_num,
            width_mm=page_w_mm,
            height_mm=page_h_mm,
            has_rendered_page_number=has_num,
            page_number_location=loc,  # type: ignore[arg-type]
            page_number_alignment=align,  # type: ignore[arg-type]
            rendered_page_number_raw=raw_num_str,
            rendered_page_number_int=parsed_int,
            matches_physical_page_num=matches_phys,
            page_number_font_name=f_name,
            page_number_font_size_pt=f_size,
            page_number_y_mm=num_y,
            page_number_x_mm=num_x,
            header_extra_text=" | ".join(header_extras) if header_extras else None,
            footer_extra_text=" | ".join(footer_extras) if footer_extras else None,
            body_top_y_mm=body_top,
            body_bottom_y_mm=body_bottom,
            body_left_x_mm=body_left,
            body_right_x_mm=body_right,
            bottom_free_space_mm=free_bottom,
        )

    # -----------------------------------------------------------------
    # ШАГ 2: Оценка синхронности текстов DOCX и PDF
    # -----------------------------------------------------------------

    def _compute_docx_pdf_sync_score(
        self, blocks: List[BlockUnion], body_lines: List[_PdfTextLine]
    ) -> float:
        """Считает долю совпавшего текста между DOCX и PDF."""
        docx_chunks: List[str] = []
        for b in blocks:
            if isinstance(b, ParagraphBlock) and b.text_visible.strip():
                c = self._normalize_compact(b.text_visible)
                if len(c) >= 10:
                    docx_chunks.append(c[:25])
            elif isinstance(b, TableBlock):
                for r in b.rows[:5]:
                    row_txt = "".join(c.text_combined for c in r.cells if not c.is_merged_shadow)
                    c = self._normalize_compact(row_txt)
                    if len(c) >= 10:
                        docx_chunks.append(c[:25])

        if not docx_chunks:
            return 1.0

        pdf_full_stream = "".join(l.compact_text for l in body_lines)
        if not pdf_full_stream:
            return 0.0

        hits = sum(1 for probe in docx_chunks if probe in pdf_full_stream)
        return hits / len(docx_chunks)

    # -----------------------------------------------------------------
    # ШАГ 3: Монотонное потоковое выравнивание блоков DOCX <-> PDF
    # -----------------------------------------------------------------

    def _align_blocks_monotonic(
        self,
        blocks: List[BlockUnion],
        sections: List[SectionProperties],
        body_lines: List[_PdfTextLine],
        visuals: Dict[str, VisualObject],
        equations: List[EquationObject],
        visual_boxes_by_page: Dict[int, List[_PdfVisualBox]],
    ) -> None:
        """Выравнивает блоки по потоку строк PDF без возврата назад."""
        if not body_lines:
            return

        cursor = 0
        total_lines = len(body_lines)
        last_known_page = 1
        last_known_y = 20.0
        toc_region_end: Optional[int] = None

        eq_map = {e.equation_id: e for e in equations}
        text_area_w = sections[0].text_area_width_mm if sections else 165.0
        left_margin = sections[0].margins.left_mm if sections else 30.0
        right_limit_mm = left_margin + text_area_w + 1.5

        for b in blocks:
            if isinstance(b, ParagraphBlock):
                is_toc_line = self._is_toc_line_paragraph(b)
                if toc_region_end is not None and not is_toc_line:
                    # Строки оглавления могли остаться несопоставленными: тело
                    # документа начинается только после области с отточиями.
                    cursor = max(cursor, min(total_lines, toc_region_end + 1))
                    toc_region_end = None

                if is_toc_line:
                    cursor, last_known_page, last_known_y, region_end = self._align_toc_line(
                        para=b,
                        body_lines=body_lines,
                        cursor=cursor,
                        total_lines=total_lines,
                        fallback_page=last_known_page,
                        fallback_y=last_known_y,
                    )
                    if region_end is not None:
                        toc_region_end = region_end
                else:
                    cursor, last_known_page, last_known_y = self._align_single_paragraph(
                        para=b,
                        body_lines=body_lines,
                        cursor=cursor,
                        total_lines=total_lines,
                        fallback_page=last_known_page,
                        fallback_y=last_known_y,
                        visuals=visuals,
                        eq_map=eq_map,
                        visual_boxes_by_page=visual_boxes_by_page,
                        right_limit_mm=right_limit_mm,
                    )

            elif isinstance(b, TableBlock):
                cursor, last_known_page, last_known_y = self._align_single_table(
                    table=b,
                    body_lines=body_lines,
                    cursor=cursor,
                    total_lines=total_lines,
                    fallback_page=last_known_page,
                    fallback_y=last_known_y,
                    visuals=visuals,
                    eq_map=eq_map,
                    visual_boxes_by_page=visual_boxes_by_page,
                    right_limit_mm=right_limit_mm,
                )

            elif isinstance(b, TocContainerBlock):
                cursor, last_known_page, last_known_y = self._align_toc_block(
                    toc=b,
                    body_lines=body_lines,
                    cursor=cursor,
                    total_lines=total_lines,
                    fallback_page=last_known_page,
                    fallback_y=last_known_y,
                )

    def _align_toc_block(
        self,
        toc: TocContainerBlock,
        body_lines: List[_PdfTextLine],
        cursor: int,
        total_lines: int,
        fallback_page: int,
        fallback_y: float,
    ) -> Tuple[int, int, float]:
        """
        Привязывает блок оглавления к страницам PDF по строкам с отточиями.

        Номера страниц в DOCX-оглавлении могут быть устаревшими, поэтому искать
        записи по полному тексту (с номером) нельзя: запись «Введение … 3»
        находится в теле документа и уводит курсор за пределы оглавления.
        Строки оглавления распознаются по отточиям, что не зависит от номеров.
        """
        region = self._find_toc_region(body_lines, cursor, total_lines)
        if region is not None:
            first_idx, last_idx = region
            toc.page_start_est = body_lines[first_idx].page_num
            toc.page_end_est = body_lines[last_idx].page_num
            new_cursor = min(total_lines, last_idx + 1)
            return new_cursor, body_lines[last_idx].page_num, body_lines[last_idx].y1_mm

        # Резервный путь для оглавлений без отточий: сопоставление записей по тексту.
        toc_cursor = cursor
        matched_spans: List[Tuple[int, int]] = []
        for entry in toc.entries:
            compact_target = self._normalize_compact(entry.raw_text)
            if not compact_target:
                continue
            span = self._match_compact_span(
                compact_target, body_lines, toc_cursor, total_lines
            )
            if span is not None:
                matched_spans.append(span)
                toc_cursor = span[1] + 1
        if matched_spans:
            first_lines = body_lines[matched_spans[0][0] : matched_spans[0][1] + 1]
            last_lines = body_lines[matched_spans[-1][0] : matched_spans[-1][1] + 1]
            toc.page_start_est = first_lines[0].page_num
            toc.page_end_est = last_lines[-1].page_num
            return (
                min(total_lines, toc_cursor),
                last_lines[-1].page_num,
                last_lines[-1].y1_mm,
            )

        toc.page_start_est = fallback_page
        toc.page_end_est = fallback_page
        return cursor, fallback_page, fallback_y

    @staticmethod
    def _find_toc_region(
        body_lines: List[_PdfTextLine],
        cursor: int,
        total_lines: int,
    ) -> Optional[Tuple[int, int]]:
        """
        Находит границы области оглавления в потоке строк PDF.

        Строки оглавления распознаются по отточиям (точки или многоточие).
        Заголовок записи может переноситься на 1-2 строки без отточий, поэтому
        область завершается только после трех непустых строк без отточий подряд.
        """
        first_idx: Optional[int] = None
        last_idx: Optional[int] = None
        gap = 0
        scan_limit = min(total_lines, cursor + 400)

        for idx in range(cursor, scan_limit):
            line = body_lines[idx]
            has_leader = line.raw_text.count(".") >= 3 or line.raw_text.count("…") >= 3
            if has_leader:
                if first_idx is None:
                    first_idx = idx
                last_idx = idx
                gap = 0
                continue
            if first_idx is None:
                # До первой строки оглавления допускаем заголовок «Содержание» и пустые строки.
                continue
            if not line.raw_text.strip():
                continue
            gap += 1
            if gap > 3:
                break

        if first_idx is None or last_idx is None:
            return None
        return first_idx, last_idx

    @staticmethod
    def _is_toc_line_paragraph(para: ParagraphBlock) -> bool:
        """
        True, если абзац похож на строку оглавления.

        Достаточно стиля TOC или номера страницы через табуляцию/отточия.
        Поля PAGEREF сами по себе не признак: они встречаются и в перекрестных
        ссылках внутри тела, которые нельзя выравнивать как оглавление.
        """
        style_str = f"{para.style_id or ''} {para.style_name_ui or ''}".lower()
        if not para.text_visible.strip():
            # Пустой абзац между оглавлением и телом не должен запускать новый
            # поиск области с отточиями: иначе курсор перескочит через начало тела.
            return False
        if "toc" in style_str or "оглавление" in style_str:
            return True
        return bool(
            re.search(r"(?:\t|\.{3,}|…+)\s*\d{1,4}\s*$", para.text_visible.strip())
        )

    @staticmethod
    def _toc_entry_title_compact(text_visible: str) -> str:
        """Отбрасывает номер страницы и отточия, оставляя заголовок записи."""
        title = re.sub(r"(?:\t|\.{3,}|…+)\s*\d{1,4}\s*$", "", text_visible.strip())
        title = re.sub(r"[\s.…]+$", "", title)
        return PdfPageAligner._normalize_compact(title)

    def _align_toc_line(
        self,
        para: ParagraphBlock,
        body_lines: List[_PdfTextLine],
        cursor: int,
        total_lines: int,
        fallback_page: int,
        fallback_y: float,
    ) -> Tuple[int, int, float, Optional[int]]:
        """
        Привязывает строку оглавления к строке PDF с отточиями.

        Номер страницы в DOCX-оглавлении может быть устаревшим, поэтому строка
        ищется только по заголовку и только внутри области оглавления. Поиск
        по полному тексту находил бы заголовок в теле документа и уводил курсор.
        """
        region = self._find_toc_region(body_lines, cursor, total_lines)
        if region is None:
            compact_target = self._normalize_compact(para.text_visible)
            span = self._match_compact_span(compact_target, body_lines, cursor, total_lines)
            if span is None:
                para.page_start_est = fallback_page
                para.page_end_est = fallback_page
                return cursor, fallback_page, fallback_y, None
            line = body_lines[span[0]]
            para.page_start_est = para.page_end_est = line.page_num
            para.pdf_y_top_mm = line.y0_mm
            para.pdf_y_bottom_mm = line.y1_mm
            return min(total_lines, span[1] + 1), line.page_num, line.y1_mm, None

        region_start, region_end = region
        target = self._toc_entry_title_compact(para.text_visible)
        probe = target[: min(18, len(target))]
        matched_idx: Optional[int] = None

        if probe:
            for idx in range(region_start, region_end + 1):
                line_compact = body_lines[idx].compact_text
                if not line_compact:
                    continue
                if probe in line_compact or (
                    len(target) >= 4
                    and target.startswith(line_compact[: min(14, len(line_compact))])
                ):
                    matched_idx = idx
                    break

        if matched_idx is None:
            # Заголовок не найден (например, перефразирован): оставляем место в TOC,
            # но не выходим за его область.
            para.page_start_est = fallback_page
            para.page_end_est = fallback_page
            return cursor, fallback_page, fallback_y, region_end

        line = body_lines[matched_idx]
        para.page_start_est = para.page_end_est = line.page_num
        para.pdf_y_top_mm = line.y0_mm
        para.pdf_y_bottom_mm = line.y1_mm
        para.pdf_x_left_mm = line.x0_mm
        para.pdf_x_right_mm = line.x1_mm
        return min(total_lines, matched_idx + 1), line.page_num, line.y1_mm, region_end

    def _align_single_paragraph(
        self,
        para: ParagraphBlock,
        body_lines: List[_PdfTextLine],
        cursor: int,
        total_lines: int,
        fallback_page: int,
        fallback_y: float,
        visuals: Dict[str, VisualObject],
        eq_map: Dict[str, EquationObject],
        visual_boxes_by_page: Dict[int, List[_PdfVisualBox]],
        right_limit_mm: float,
    ) -> Tuple[int, int, float]:
        """Привязывает абзац к странице и строкам PDF."""
        compact_target = self._normalize_compact(para.text_visible)

        if not compact_target:
            para.page_start_est = fallback_page
            para.page_end_est = fallback_page
            para.pdf_y_top_mm = fallback_y
            para.pdf_y_bottom_mm = fallback_y
            self._sync_embedded_objects(
                para, visuals, eq_map, visual_boxes_by_page, right_limit_mm
            )
            return cursor, fallback_page, fallback_y

        matched_span = self._match_compact_span(compact_target, body_lines, cursor, total_lines)
        if matched_span is None:
            para.page_start_est = fallback_page
            para.page_end_est = fallback_page
            self._sync_embedded_objects(
                para, visuals, eq_map, visual_boxes_by_page, right_limit_mm
            )
            return cursor, fallback_page, fallback_y

        start_idx, end_idx = matched_span
        span_lines = body_lines[start_idx : end_idx + 1]

        p_start = span_lines[0].page_num
        p_end = span_lines[-1].page_num
        para.page_start_est = p_start
        para.page_end_est = p_end

        start_page_lines = [l for l in span_lines if l.page_num == p_start]
        end_page_lines = [l for l in span_lines if l.page_num == p_end]

        para.pdf_y_top_mm = start_page_lines[0].y0_mm
        para.pdf_y_bottom_mm = end_page_lines[-1].y1_mm
        para.pdf_x_left_mm = min(l.x0_mm for l in span_lines)
        para.pdf_x_right_mm = max(l.x1_mm for l in span_lines)
        para.pdf_lines_count_on_start_page = len(start_page_lines)
        para.pdf_lines_count_on_end_page = len(end_page_lines) if p_end > p_start else None
        para.pdf_total_lines_count = len(span_lines)
        # Перенос слова на границе строки (кроме последней) — признак автопереноса в PDF
        para.pdf_has_hyphenated_break = any(
            re.search(r"[-\u00ad\u2010]$", l.raw_text.rstrip()) is not None
            for l in span_lines[:-1]
        )

        # Если абзац в PDF разорвался между страницами, вычисляем символьное смещение разрыва
        if p_end > p_start and not para.rendered_page_breaks_offsets:
            chars_on_first = sum(len(l.raw_text) for l in start_page_lines)
            para.rendered_page_breaks_offsets = [min(len(para.text_visible), chars_on_first)]

        self._sync_embedded_objects(
            para, visuals, eq_map, visual_boxes_by_page, right_limit_mm
        )

        # Если в одной физической строке PDF было склеено несколько коротких элементов (например, ячейки),
        # двигаем курсор на end_idx + 1 только тогда, когда мы исчерпали текущую строку PDF
        next_cursor = end_idx + 1 if len(compact_target) >= len(body_lines[end_idx].compact_text) * 0.7 else end_idx
        return min(total_lines, next_cursor), p_end, para.pdf_y_bottom_mm

    def _align_single_table(
        self,
        table: TableBlock,
        body_lines: List[_PdfTextLine],
        cursor: int,
        total_lines: int,
        fallback_page: int,
        fallback_y: float,
        visuals: Dict[str, VisualObject],
        eq_map: Dict[str, EquationObject],
        visual_boxes_by_page: Dict[int, List[_PdfVisualBox]],
        right_limit_mm: float,
    ) -> Tuple[int, int, float]:
        """Привязывает таблицу и ее строки к страницам PDF."""
        if not table.rows:
            table.page_start_est = fallback_page
            table.page_end_est = fallback_page
            return cursor, fallback_page, fallback_y

        # Компактный слепок первой строки (шапки) таблицы для детекции автоматического повтора шапки на новой странице
        header_row_compact = self._normalize_compact(
            "".join(c.text_combined for c in table.rows[0].cells if not c.is_merged_shadow)
        )

        cur_page = fallback_page
        cur_y = fallback_y

        for r_idx, row in enumerate(table.rows):
            prev_row_page = cur_page
            row_compact = self._normalize_compact(
                "".join(c.text_combined for c in row.cells if not c.is_merged_shadow)
            )

            # Если мы не в первой строке таблицы, проверяем: не вставил ли рендерер повтор шапки перед этой строкой на новой странице
            repeated_header_detected = False
            if r_idx > 0 and header_row_compact and cursor < total_lines:
                probe_line = body_lines[cursor]
                if (
                    probe_line.page_num > prev_row_page
                    and header_row_compact[:15] in probe_line.compact_text
                    and row_compact[:15] not in probe_line.compact_text
                ):
                    repeated_header_detected = True
                    hdr_span = self._match_compact_span(header_row_compact, body_lines, cursor, total_lines)
                    if hdr_span is not None:
                        cursor = hdr_span[1] + 1

            # Выравниваем саму строку таблицы
            row_span = (
                self._match_compact_span(row_compact, body_lines, cursor, total_lines)
                if row_compact
                else None
            )

            if row_span is not None:
                s_i, e_i = row_span
                r_lines = body_lines[s_i : e_i + 1]
                row.page_start_est = r_lines[0].page_num
                row.page_end_est = r_lines[-1].page_num
                row.pdf_y_top_mm = r_lines[0].y0_mm
                row.pdf_y_bottom_mm = r_lines[-1].y1_mm
                row.pdf_x_left_mm = min(l.x0_mm for l in r_lines)
                row.pdf_x_right_mm = max(l.x1_mm for l in r_lines)
                cur_page = row.page_end_est
                cur_y = row.pdf_y_bottom_mm
                cursor = min(total_lines, e_i + 1)
            else:
                row.page_start_est = cur_page
                row.page_end_est = cur_page
                row.pdf_y_top_mm = cur_y
                row.pdf_y_bottom_mm = cur_y

            row.pdf_repeated_header_above = repeated_header_detected
            if r_idx > 0 and (row.page_start_est > prev_row_page or row.page_end_est > row.page_start_est):
                row.has_page_break_inside_or_before = True

            # Синхронизируем страницы внутренних абзацев ячеек этой строки
            for cell in row.cells:
                for cb in cell.blocks:
                    if isinstance(cb, ParagraphBlock):
                        cb.page_start_est = row.page_start_est
                        cb.page_end_est = row.page_end_est
                        cb.pdf_y_top_mm = row.pdf_y_top_mm
                        cb.pdf_y_bottom_mm = row.pdf_y_bottom_mm
                        self._sync_embedded_objects(
                            cb, visuals, eq_map, visual_boxes_by_page, right_limit_mm
                        )

        table.page_start_est = table.rows[0].page_start_est
        table.page_end_est = table.rows[-1].page_end_est
        table.spans_multiple_pages = table.page_end_est > table.page_start_est

        tops = [r.pdf_y_top_mm for r in table.rows if r.pdf_y_top_mm is not None]
        bottoms = [r.pdf_y_bottom_mm for r in table.rows if r.pdf_y_bottom_mm is not None]
        lefts = [r.pdf_x_left_mm for r in table.rows if r.pdf_x_left_mm is not None]
        rights = [r.pdf_x_right_mm for r in table.rows if r.pdf_x_right_mm is not None]

        if tops:
            table.pdf_y_top_mm = tops[0]
        if bottoms:
            table.pdf_y_bottom_mm = bottoms[-1]
        if lefts:
            table.pdf_x_left_mm = min(lefts)
        if rights:
            table.pdf_x_right_mm = max(rights)
            if table.pdf_x_right_mm > right_limit_mm:
                table.exceeds_page_text_width = True

        return cursor, cur_page, cur_y

    @staticmethod
    def _match_compact_span(
        target_compact: str,
        body_lines: List[_PdfTextLine],
        cursor: int,
        total_lines: int,
    ) -> Optional[Tuple[int, int]]:
        """
        Находит диапазон индексов строк PDF (start_idx, end_idx), покрывающий текст target_compact,
        начиная с позиции cursor.
        Сначала ищет в узком окне (ближайшие 35 строк), при неудаче — в широком (180 строк).
        """
        if cursor >= total_lines or not target_compact:
            return None

        head_len = min(18, len(target_compact))
        head_probe = target_compact[:head_len]

        start_idx: Optional[int] = None

        for window_size in (35, 180):
            limit = min(total_lines, cursor + window_size)
            for idx in range(cursor, limit):
                line_c = body_lines[idx].compact_text
                if not line_c:
                    continue
                # Либо префикс абзаца входит в строку PDF, либо вся короткая строка PDF является префиксом абзаца
                if head_probe in line_c or (
                    len(line_c) >= min(4, len(target_compact))
                    and target_compact.startswith(line_c[: min(14, len(line_c))])
                ):
                    start_idx = idx
                    break
            if start_idx is not None:
                break

        if start_idx is None:
            return None

        # Жадное продвижение end_idx, пока следующие строки PDF являются продолжением текста абзаца
        end_idx = start_idx
        consumed_approx = len(body_lines[start_idx].compact_text)
        target_len = len(target_compact)
        tail_probe = target_compact[-min(16, target_len) :]

        while end_idx + 1 < total_lines and consumed_approx < target_len * 1.15:
            # Если текущая строка уже содержит самый конец абзаца и мы набрали >= 75% длины — останавливаемся
            if consumed_approx >= target_len * 0.75 and tail_probe in body_lines[end_idx].compact_text:
                break

            nxt_line = body_lines[end_idx + 1]
            nxt_probe = nxt_line.compact_text[: min(14, len(nxt_line.compact_text))]
            if nxt_probe and nxt_probe in target_compact:
                end_idx += 1
                consumed_approx += len(nxt_line.compact_text)
            else:
                break

        return start_idx, end_idx

    @staticmethod
    def _sync_embedded_objects(
        para: ParagraphBlock,
        visuals: Dict[str, VisualObject],
        eq_map: Dict[str, EquationObject],
        visual_boxes_by_page: Dict[int, List[_PdfVisualBox]],
        right_limit_mm: float,
    ) -> None:
        """Переносит страницы визуальных объектов и формул вслед за абзацами."""
        for vis_id in para.embedded_visual_ids:
            vis = visuals.get(vis_id)
            if vis is None:
                continue
            vis.page_est = para.page_start_est
            # Ищем подходящий прямоугольник картинки на этой странице PDF.
            # Картинка может отрендериться на соседнем листе (перенос висячей строки),
            # поэтому при отсутствии свободных боксов смотрим page±1.
            candidates = visual_boxes_by_page.get(vis.page_est, [])
            if not any(not box.used for box in candidates):
                for alt_page in (vis.page_est + 1, vis.page_est - 1):
                    alt_candidates = visual_boxes_by_page.get(alt_page, [])
                    if any(not box.used for box in alt_candidates):
                        candidates = alt_candidates
                        break
            for box in candidates:
                if not box.used:
                    box.used = True
                    vis.pdf_x_left_mm = box.x0_mm
                    vis.pdf_y_top_mm = box.y0_mm
                    vis.pdf_x_right_mm = box.x1_mm
                    vis.pdf_y_bottom_mm = box.y1_mm
                    if box.x1_mm > right_limit_mm:
                        vis.exceeds_page_margins = True
                    break

        for eq_id in para.embedded_equation_ids:
            eq = eq_map.get(eq_id)
            if eq is not None:
                eq.page_est = para.page_start_est

    @staticmethod
    def _compute_sha256(path: Path) -> str:
        """Считает SHA-256 файла блоками по 1 МиБ."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()