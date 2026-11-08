"""Восстановление структуры документа по геометрии PDF.

В режиме ``pdf_only`` нет ни ``styles.xml``, ни ``numbering.xml``, ни ``w:sectPr``:
абзацы, заголовки, списки, оглавление и таблицы восстанавливаются по координатам
строк, шрифтам и разрывам между строками. Поэтому все измеренные свойства
помечаются ``PropertySource="pdf_estimate"``, а номера страниц берутся из
физической пагинации PDF и остаются точными.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from parser.core.schema import (
    BlockUnion,
    ColorValue,
    DocumentTreeNode,
    HeadingMetadata,
    ListItemMetadata,
    ParagraphBlock,
    ParagraphPropertiesEffective,
    RasterImageDetails,
    RunFontPropertiesEffective,
    TableBlock,
    TableCell,
    TableRow,
    TextRun,
    TocContainerBlock,
    TocEntryItem,
    VisualObject,
)
from parser.docx.pdf_text_utils import PT_TO_MM, normalize_compact

from .extractor import PdfDocument, PdfImage, PdfLine, PdfPage, PdfSpan, PdfTable
from .layout import PdfLayout

#: Минимальный разрыв между строками (в долях медианного), при котором
#: начинается новый абзац.
PARAGRAPH_GAP_FACTOR = 1.6

#: Добавка к медианному разрыву, ниже которой смена абзаца не фиксируется, мм.
PARAGRAPH_GAP_EXTRA_MM = 1.6

#: Абзацный отступ, при котором строка считается первой строкой абзаца, мм.
FIRST_LINE_INDENT_MM = 2.5

#: Сдвиг строки по горизонтали, при котором меняется оформление абзаца, мм.
INDENT_SHIFT_MM = 3.0

#: Отношение кегля к базовому, выше которого строка считается заголовком.
HEADING_SIZE_RATIO = 1.15

#: Отношение кегля, при котором заголовок считается заголовком верхнего уровня.
HEADING_TOP_LEVEL_RATIO = 1.3

#: Максимальная длина заголовка в символах.
MAX_HEADING_LENGTH = 160

#: Максимальная длина строки заголовка: длинные строки — обычный текст.
MAX_HEADING_LINE_LENGTH = 100

#: Максимальное число строк в заголовке.
MAX_HEADING_LINES = 3

#: Максимальная длина заголовка без нумерации, распознаваемого по полужирному.
MAX_BOLD_HEADING_LENGTH = 90

#: Минимальное число записей с отточием, чтобы страница считалась оглавлением.
TOC_MIN_ENTRIES = 3

#: Шаг отступа, соответствующий следующему уровню вложенности оглавления, мм.
TOC_INDENT_STEP_MM = 6.0

#: Допустимый отступ записи оглавления от левой границы текста, мм.
TOC_ENTRY_INDENT_TOLERANCE_MM = 20.0

#: Ширина последней строки абзаца, при которой он считается законченным, мм.
SHORT_LAST_LINE_GAP_MM = 8.0

#: Номер раздела/подраздела в начале строки: "1", "1.2", "1.2.3".
RE_SECTION_NUMBER = re.compile(r"^(?P<number>\d{1,2}(?:\.\d{1,2}){0,5})(?P<dot>[.)]?)(?:\s+|$)")

#: Заголовок вида "Глава 1 ..." или "Раздел 2 ...".
RE_CHAPTER_PREFIX = re.compile(
    r"^(?P<word>глава|раздел)\s+(?P<number>\d{1,2})[.)]?\s*", re.IGNORECASE
)

#: Строка оглавления с отточием и номером страницы в конце.
RE_TOC_DOT_LEADER = re.compile(r"\.{3,}\s*(?P<page>\d{1,4})\s*$")

#: Отточие в любом месте строки: признак строки оглавления.
RE_DOT_LEADER_ANY = re.compile(r"\.{3,}")

#: Хвостовые точки и пробелы, остающиеся от отточия в заголовке записи.
RE_TRAILING_DOTS = re.compile(r"[\s.\u2026]+$")

#: Маркер маркированного списка в начале строки.
RE_BULLET_MARKER = re.compile(r"^(?P<marker>[\u2022\u00b7\u25aa\u25e6\u2013\u2014-])\s+(?P<body>\S.*)$")

#: Маркер нумерованного списка: "1)", "а)", "IV.".
RE_ENUM_MARKER = re.compile(
    r"^(?P<marker>\d{1,2}[.)]|[а-яё][.)]|[А-ЯЁ][.)]|[IVXLC]{1,5}[.)])\s+(?P<body>\S.*)$"
)

#: Подпись рисунка или таблицы: не заголовок, хотя и короткая строка.
RE_CAPTION_PREFIX = re.compile(r"^(рисунок|рис\.|таблица|табл\.)\s*\d", re.IGNORECASE)

#: Символы переноса, которые PDF оставляет в конце строки.
HYPHEN_CHARS = "-\u2010\u00ad"

#: Символы конца предложения. В список включен U+037E: часть PDF-конвертеров
#: отдает точку с запятой как греческий вопросительный знак.
SENTENCE_END_CHARS = (".", "!", "?", ":", ";", ",", "\u037e")

#: Структурные заголовки ВКР: слова заголовка -> семантическая роль.
#: Сравниваются первые слова заголовка, поэтому "Задание на ВКР" распознается,
#: а "заданием на проектирование..." в тексте — нет.
STRUCTURAL_ROLES: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("введение",), "introduction"),
    (("заключение",), "conclusion"),
    (("содержание",), "toc"),
    (("оглавление",), "toc"),
    (("реферат",), "abstract_referat"),
    (("аннотация",), "abstract_referat"),
    (("список", "литературы"), "bibliography"),
    (("список", "использованной", "литературы"), "bibliography"),
    (("список", "использованных", "источников"), "bibliography"),
    (("список", "использованных", "источников", "литературы"), "bibliography"),
    (("библиография",), "bibliography"),
    (("библиографический", "список"), "bibliography"),
    (("перечень", "сокращений"), "abbreviations"),
    (("список", "сокращений"), "abbreviations"),
    (("обозначения", "и", "сокращения"), "abbreviations"),
    (("задание",), "task_sheet"),
    (("приложение",), "appendix"),
    (("приложения",), "appendix"),
)

#: Слово заголовка для сопоставления со структурными ролями.
RE_WORD_LIKE = re.compile(r"[а-яёa-z]+")

#: Цвет текста, который не удалось измерить: черный по умолчанию.
DEFAULT_TEXT_COLOR = "000000"


@dataclass
class _TextPiece:
    """Фрагмент текста абзаца: спан и его смещения в тексте блока."""

    span: PdfSpan
    text: str
    start_char: int
    end_char: int
    line_index: int


@dataclass
class TocRegion:
    """Область оглавления: страницы и строки с отточием."""

    page_nums: List[int]
    lines: List[PdfLine]


@dataclass
class PdfStructure:
    """Результат восстановления структуры: блоки, визуалы и дерево документа."""

    blocks: List[BlockUnion]
    visuals: Dict[str, VisualObject]
    document_tree: List[DocumentTreeNode]
    full_text: str


class PdfStructureBuilder:
    """Строит блоки документа из извлеченных строк и оценки вёрстки."""

    def __init__(self, document: PdfDocument, layout: PdfLayout) -> None:
        """Запоминает документ и вёрстку, для которых строится структура."""
        self.document = document
        self.layout = layout
        self.base_font_size_pt = 0.0
        self.base_font_family = ""
        self.median_line_gap_mm = max(0.5, layout.median_line_gap_mm)
        self.is_justified = False
        self._paragraph_seq = 0
        self._table_seq = 0
        self._toc_seq = 0
        self._visual_seq = 0

    # -----------------------------------------------------------------
    # Публичная точка входа
    # -----------------------------------------------------------------

    def build(self) -> PdfStructure:
        """Собирает абзацы, таблицы, оглавление, визуалы и дерево документа."""
        body_lines = self._collect_body_lines()
        self._apply_base_metrics(body_lines)

        toc_regions = self._detect_toc_regions(body_lines)
        toc_line_ids = {id(line) for region in toc_regions for line in region.lines}
        text_lines = [line for line in body_lines if id(line) not in toc_line_ids]

        paragraph_groups = self._group_paragraphs(text_lines)
        paragraphs = [self._build_paragraph(group) for group in paragraph_groups]
        tables = self._build_tables()
        toc_blocks = self._build_toc_blocks(toc_regions, paragraphs)
        visual_blocks, visuals = self._build_visuals()

        items: List[Tuple[float, float, float, BlockUnion]] = []
        for block in [*paragraphs, *tables, *toc_blocks, *visual_blocks]:
            items.append(
                (
                    float(block.page_start_est),
                    float(getattr(block, "pdf_y_top_mm", None) or 0.0),
                    float(getattr(block, "pdf_x_left_mm", None) or 0.0),
                    block,
                )
            )
        items.sort(key=lambda item: (item[0], item[1], item[2]))
        blocks: List[BlockUnion] = [item[3] for item in items]

        self._apply_paragraph_spacing(paragraphs)
        self._link_toc_entries(toc_blocks, paragraphs)
        document_tree = self._build_document_tree(blocks)

        full_text = "\n".join(
            block.text_visible
            for block in blocks
            if isinstance(block, ParagraphBlock) and block.text_visible
        )
        for table in tables:
            for row in table.rows:
                full_text += "\n" + " ".join(
                    cell.text_combined for cell in row.cells if cell.text_combined
                )

        return PdfStructure(
            blocks=blocks,
            visuals=visuals,
            document_tree=document_tree,
            full_text=full_text.strip(),
        )

    # -----------------------------------------------------------------
    # Подготовка: строки тела, базовые метрики, признак выключки
    # -----------------------------------------------------------------

    def _collect_body_lines(self) -> List[PdfLine]:
        """Возвращает строки тела документа без строк таблиц и колонтитулов."""
        lines: List[PdfLine] = []
        for page in self.document.pages:
            zone = self.layout.zones.get(page.page_num)
            page_lines = zone.body_lines if zone else [line for line in page.lines]
            for line in page_lines:
                if line.in_table or not line.text:
                    continue
                lines.append(line)
        lines.sort(key=lambda line: (line.page_num, line.y0_mm, line.x0_mm))
        return lines

    def _apply_base_metrics(self, lines: Sequence[PdfLine]) -> None:
        """Определяет базовый кегль, гарнитуру и признак выключки по ширине."""
        weighted_sizes: List[float] = []
        families: List[Tuple[str, int]] = []
        justified = 0
        measured = 0
        for line in lines:
            size = line.font_size_pt
            if size > 0:
                weighted_sizes.extend([size] * max(1, len(line.text)))
            family = line.font_name
            if family:
                families.append((family, len(line.text)))
            text_right = self._text_right_mm(line.page_num)
            if text_right is not None:
                measured += 1
                if line.x1_mm >= text_right - 3.0:
                    justified += 1

        self.base_font_size_pt = round(_weighted_median(weighted_sizes) or 12.0, 2)
        self.base_font_family = _most_common(families) or "Times New Roman"
        self.is_justified = measured > 0 and justified / measured >= 0.5

    def _text_right_mm(self, page_num: int) -> Optional[float]:
        """Правая граница текстовой области для страницы."""
        page = self.document.pages[page_num - 1]
        margins = self.layout.margin_for_page(page_num)
        return round(page.width_mm - margins.right_mm, 2)

    def _text_left_mm(self, page_num: int) -> float:
        """Левая граница текстовой области для страницы."""
        return self.layout.margin_for_page(page_num).left_mm

    def _text_area_width_mm(self, page_num: int) -> float:
        """Ширина текстовой области для страницы."""
        page = self.document.pages[page_num - 1]
        margins = self.layout.margin_for_page(page_num)
        return max(20.0, page.width_mm - margins.left_mm - margins.right_mm)

    # -----------------------------------------------------------------
    # Абзацы
    # -----------------------------------------------------------------

    def _group_paragraphs(self, lines: Sequence[PdfLine]) -> List[List[PdfLine]]:
        """Склеивает строки в абзацы по разрывам, отступам и смене оформления."""
        groups: List[List[PdfLine]] = []
        current: List[PdfLine] = []
        for line in lines:
            if not current:
                current = [line]
                continue
            if self._starts_new_paragraph(current, line):
                groups.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            groups.append(current)
        return groups

    def _starts_new_paragraph(self, group: Sequence[PdfLine], line: PdfLine) -> bool:
        """True, если строка начинает новый абзац, а не продолжает текущий."""
        previous = group[-1]
        if line.page_num != previous.page_num:
            return not self._continues_on_next_page(previous, line)

        gap = line.y0_mm - previous.y1_mm
        if gap > self.max_paragraph_gap_mm:
            return True

        previous_text = previous.text.strip()
        text_right = self._text_right_mm(line.page_num)
        if (
            self.is_justified
            and text_right is not None
            and previous_text.endswith(SENTENCE_END_CHARS)
            and previous.x1_mm < text_right - SHORT_LAST_LINE_GAP_MM
        ):
            return True

        body_left = self._text_left_mm(line.page_num)
        if (
            line.x0_mm - previous.x0_mm > FIRST_LINE_INDENT_MM
            and previous.x0_mm <= body_left + 1.5
        ):
            return True

        if abs(line.font_size_pt - previous.font_size_pt) >= 1.0:
            return True
        if line.is_bold != previous.is_bold:
            return True
        if self._looks_like_heading_line(line) and not self._looks_like_heading_line(previous):
            return True
        return False

    def _continues_on_next_page(self, previous: PdfLine, line: PdfLine) -> bool:
        """True, если абзац переносится на следующую страницу без разрыва."""
        previous_text = previous.text.rstrip()
        if not previous_text:
            return False
        if previous_text.endswith(SENTENCE_END_CHARS):
            return False
        if previous_text.endswith(tuple(HYPHEN_CHARS)):
            return True
        return line.text[:1].islower()

    @property
    def max_paragraph_gap_mm(self) -> float:
        """Порог вертикального разрыва, после которого начинается новый абзац."""
        return round(
            max(self.median_line_gap_mm * PARAGRAPH_GAP_FACTOR,
                self.median_line_gap_mm + PARAGRAPH_GAP_EXTRA_MM),
            2,
        )

    def _compose_text(
        self, lines: Sequence[PdfLine]
    ) -> Tuple[str, List[_TextPiece], bool]:
        """Собирает текст абзаца, склеивая переносы, и раскладывает спаны по смещениям.

        Текст абзаца и текст ранов совпадают посимвольно: пробел-разделитель
        строк входит в первый ран новой строки, а снятый дефис переноса
        вычитается из последнего рана предыдущей строки.
        """
        buffer = ""
        pieces: List[_TextPiece] = []
        had_hyphen_break = False
        for line_index, line in enumerate(lines):
            separator = ""
            if line_index > 0:
                if self._is_hyphen_break(lines[line_index - 1], line) and buffer.endswith(
                    tuple(HYPHEN_CHARS)
                ):
                    buffer = buffer[:-1]
                    if pieces:
                        pieces[-1].text = pieces[-1].text[:-1]
                        pieces[-1].end_char -= 1
                    had_hyphen_break = True
                else:
                    separator = " "
            first_in_line = True
            for span in line.spans:
                text = span.text
                if not text:
                    continue
                if first_in_line and separator:
                    text = separator + text
                start = len(buffer)
                pieces.append(
                    _TextPiece(
                        span=span,
                        text=text,
                        start_char=start,
                        end_char=start + len(text),
                        line_index=line_index,
                    )
                )
                buffer += text
                first_in_line = False
        return buffer, pieces, had_hyphen_break

    @staticmethod
    def _is_hyphen_break(previous: PdfLine, line: PdfLine) -> bool:
        """True, если строка переносит слово: дефис в конце и строчная буква дальше."""
        previous_text = previous.text.rstrip()
        if not previous_text.endswith(tuple(HYPHEN_CHARS)):
            return False
        next_text = line.text.lstrip()
        return bool(next_text) and next_text[:1].islower()

    def _build_paragraph(self, group: Sequence[PdfLine]) -> ParagraphBlock:
        """Собирает ParagraphBlock из группы строк одного абзаца."""
        self._paragraph_seq += 1
        block_id = f"p_{self._paragraph_seq:05d}"
        text, pieces, had_hyphen_break = self._compose_text(group)
        page_start = group[0].page_num
        page_end = group[-1].page_num
        section_idx = self.layout.section_idx_for_page(page_start)
        start_page_lines = [line for line in group if line.page_num == page_start]
        end_page_lines = [line for line in group if line.page_num == page_end]

        runs = self._build_runs(block_id, pieces)
        heading = self._heading_info(group, text)
        list_info = None if heading else self._list_info(group, text, page_start)
        effective_props = self._effective_props(group, heading)

        block = ParagraphBlock(
            block_id=block_id,
            section_idx=section_idx,
            page_start_est=page_start,
            page_end_est=page_end,
            pdf_y_top_mm=min(line.y0_mm for line in start_page_lines),
            pdf_y_bottom_mm=max(line.y1_mm for line in start_page_lines),
            pdf_x_left_mm=min(line.x0_mm for line in start_page_lines),
            pdf_x_right_mm=max(line.x1_mm for line in start_page_lines),
            pdf_lines_count_on_start_page=len(start_page_lines),
            pdf_lines_count_on_end_page=(
                len(end_page_lines) if page_end != page_start else None
            ),
            pdf_total_lines_count=len(group),
            pdf_has_hyphenated_break=had_hyphen_break,
            text_raw=text,
            text_visible=text,
            is_empty_or_whitespace=not text.strip(),
            contains_only_visuals_or_math=False,
            effective_props=effective_props,
            para_mark_font_effective=runs[-1].effective_props if runs else None,
            runs=runs,
            list_info=list_info,
            heading_info=heading,
        )
        return block

    def _build_runs(self, block_id: str, pieces: Sequence[_TextPiece]) -> List[TextRun]:
        """Собирает TextRun по спанам PDF с эффективными свойствами шрифта."""
        runs: List[TextRun] = []
        for run_index, piece in enumerate(pieces):
            runs.append(
                TextRun(
                    run_id=f"{block_id}_r{run_index}",
                    text=piece.text,
                    visible_text=piece.text,
                    start_char=piece.start_char,
                    end_char=piece.end_char,
                    effective_props=self._run_font(piece.span),
                )
            )
        return runs

    @staticmethod
    def _run_font(span: PdfSpan) -> RunFontPropertiesEffective:
        """Свойства символа, измеренные по спану PDF."""
        family = span.font_family or "Times New Roman"
        color_hex = span.color_hex if re.fullmatch(r"[0-9A-F]{6}", span.color_hex) else DEFAULT_TEXT_COLOR
        return RunFontPropertiesEffective(
            font_ascii=family,
            font_hansi=family,
            font_cs=family,
            font_source="pdf_estimate",
            size_pt=span.font_size_pt or 12.0,
            size_source="pdf_estimate",
            bold=span.bold,
            italic=span.italic,
            color=ColorValue(rgb_hex=color_hex, source="pdf_estimate"),
        )

    def _effective_props(
        self, group: Sequence[PdfLine], heading: Optional[HeadingMetadata]
    ) -> ParagraphPropertiesEffective:
        """Оценивает свойства абзаца: выравнивание, отступы, интервалы."""
        page_num = group[0].page_num
        text_left = self._text_left_mm(page_num)
        text_right = self._text_right_mm(page_num) or text_left
        left = min(line.x0_mm for line in group)
        right = max(line.x1_mm for line in group)
        alignment = self._alignment(group, left, right, text_left, text_right)

        indent_first_line = round(max(0.0, left - text_left), 1) if len(group) > 1 else 0.0
        indent_left = round(max(0.0, min(line.x0_mm for line in group) - text_left), 1)
        if alignment in ("center", "right"):
            # У выключенного по центру или справа абзаца измеренный сдвиг — не отступ.
            indent_first_line = 0.0
            indent_left = 0.0
        if indent_first_line <= 1.0:
            indent_first_line = 0.0

        return ParagraphPropertiesEffective(
            alignment=alignment,
            alignment_source="pdf_estimate",
            indent_left_mm=indent_left,
            indent_first_line_mm=indent_first_line,
            indent_first_line_source="pdf_estimate",
            line_spacing_value=self._line_spacing(group),
            line_spacing_rule="auto",
            line_spacing_source="pdf_estimate",
            outline_lvl=(heading.level - 1) if heading is not None else None,
        )

    def _alignment(
        self,
        group: Sequence[PdfLine],
        left: float,
        right: float,
        text_left: float,
        text_right: float,
    ) -> Literal["left", "center", "right", "both"]:
        """Определяет выравнивание абзаца по его краям относительно текстовой области."""
        if len(group) > 1 and abs(left - text_left) <= 3.0 and abs(right - text_right) <= 3.0:
            return "both"
        center = (text_left + text_right) / 2.0
        block_center = (left + right) / 2.0
        if (
            abs(block_center - center) <= 4.0
            and left - text_left > INDENT_SHIFT_MM
            and text_right - right > INDENT_SHIFT_MM
        ):
            return "center"
        if abs(right - text_right) <= 3.0 and left - text_left > INDENT_SHIFT_MM:
            return "right"
        return "left"

    def _line_spacing(self, group: Sequence[PdfLine]) -> float:
        """Оценивает межстрочный интервал по шагу строк абзаца."""
        pitches = [
            round(current.y0_mm - previous.y0_mm, 2)
            for previous, current in zip(group, group[1:])
            if current.page_num == previous.page_num
        ]
        if not pitches:
            return 1.0
        pitch = _median(pitches) or 0.0
        base_size = max(line.font_size_pt for line in group) or self.base_font_size_pt
        single_line_mm = base_size * PT_TO_MM * 1.2
        if single_line_mm <= 0:
            return 1.0
        return round(min(3.0, max(0.8, pitch / single_line_mm)), 2)

    def _apply_paragraph_spacing(self, paragraphs: Sequence[ParagraphBlock]) -> None:
        """Оценивает отбивки абзацев по вертикальным разрывам между ними."""
        for previous, current in zip(paragraphs, paragraphs[1:]):
            if previous.page_end_est != current.page_start_est:
                continue
            gap = (
                current.pdf_y_top_mm or 0.0
            ) - (previous.pdf_y_bottom_mm or 0.0)
            extra_mm = max(0.0, gap - self.median_line_gap_mm)
            space_pt = round(extra_mm / PT_TO_MM / 2.0, 1)
            if space_pt <= 0.0:
                continue
            previous.effective_props.space_after_pt = space_pt
            current.effective_props.space_before_pt = space_pt

    # -----------------------------------------------------------------
    # Заголовки
    # -----------------------------------------------------------------

    def _looks_like_heading_line(self, line: PdfLine) -> bool:
        """Быстрая проверка строки на признаки заголовка."""
        text = line.text.strip()
        if not text or len(text) > MAX_HEADING_LINE_LENGTH:
            return False
        if line.font_size_pt >= self.base_font_size_pt * HEADING_SIZE_RATIO:
            return True
        if RE_CAPTION_PREFIX.match(text):
            return False
        if self._structural_role(text) is not None:
            return True
        if RE_CHAPTER_PREFIX.match(text) or RE_SECTION_NUMBER.match(text):
            return True
        return line.is_bold and len(text) <= MAX_BOLD_HEADING_LENGTH

    def _heading_info(
        self, group: Sequence[PdfLine], text: str
    ) -> Optional[HeadingMetadata]:
        """Распознает заголовок по кеглю, начертанию, нумерации и ключевым словам."""
        stripped = text.strip()
        if not stripped or len(stripped) > MAX_HEADING_LENGTH:
            return None
        if len(group) > MAX_HEADING_LINES:
            return None
        if RE_CAPTION_PREFIX.match(stripped):
            return None
        if RE_DOT_LEADER_ANY.search(stripped):
            return None
        if stripped.endswith((",", ";")):
            return None
        if stripped.endswith(".") and not (RE_SECTION_NUMBER.match(stripped) or RE_CHAPTER_PREFIX.match(stripped)):
            return None

        max_size = max(line.font_size_pt for line in group)
        size_ratio = max_size / self.base_font_size_pt if self.base_font_size_pt else 1.0
        bold_share = sum(1 for line in group if line.is_bold) / len(group)
        structural = self._structural_role(stripped)
        chapter = RE_CHAPTER_PREFIX.match(stripped)
        numbered = RE_SECTION_NUMBER.match(stripped)
        numbered_rest = stripped[numbered.end() :].strip() if numbered else ""

        # Нумерация сама по себе не делает строку заголовком: элементы списков,
        # сноски и записи литературы тоже начинаются с числа. Заголовком пункт
        # становится по кеглю, полужирному начертанию или по "Глава N".
        is_heading = bool(
            structural
            or chapter
            or size_ratio >= HEADING_SIZE_RATIO
            or (bold_share >= 0.5 and len(stripped) <= MAX_BOLD_HEADING_LENGTH)
        )
        if not is_heading:
            return None
        if not structural and not (stripped[:1].isupper() or numbered_rest[:1].isupper()):
            # Обычный текст с полужирным выделением начинается со строчной буквы.
            return None
        if numbered and not numbered_rest:
            return None
        if chapter and not stripped[chapter.end() :].strip():
            return None

        if structural:
            level = 1
        elif chapter:
            level = 1
        elif numbered:
            level = min(9, numbered.group("number").count(".") + 1)
        elif size_ratio >= HEADING_TOP_LEVEL_RATIO:
            level = 1
        else:
            level = 2

        number_raw: Optional[str] = None
        has_trailing_dot = False
        clean_title = stripped
        if numbered:
            number_raw = numbered.group("number")
            has_trailing_dot = bool(numbered.group("dot"))
            clean_title = stripped[numbered.end() :].strip()
        elif chapter:
            number_raw = chapter.group("number")
            clean_title = stripped[chapter.end() :].strip()

        kind: Literal[
            "structural_unnumbered", "numbered_section", "appendix_heading", "other"
        ] = "structural_unnumbered"
        if structural == "appendix":
            kind = "appendix_heading"
        elif numbered or chapter:
            kind = "numbered_section"

        confidence: Literal[
            "explicit_outline_or_style", "structural_keyword_match", "heuristic_visual"
        ] = "structural_keyword_match" if structural else "heuristic_visual"

        return HeadingMetadata(
            detection_confidence=confidence,
            heading_kind=kind,
            level=level,
            numbering_method="plain_text" if (numbered or chapter) else "none",
            section_number_raw=number_raw,
            has_trailing_dot_in_number=has_trailing_dot,
            clean_title_text=clean_title or stripped,
            has_trailing_dot_in_title=clean_title.endswith("."),
            has_word_glava_or_razdel=bool(chapter),
        )

    @staticmethod
    def _structural_role(text: str) -> Optional[str]:
        """Семантическая роль заголовка по первым словам ВКР или None."""
        words = RE_WORD_LIKE.findall(text.lower())
        if not words:
            return None
        for key_words, role in STRUCTURAL_ROLES:
            if tuple(words[: len(key_words)]) == key_words:
                return role
        return None

    # -----------------------------------------------------------------
    # Списки
    # -----------------------------------------------------------------

    def _list_info(
        self, group: Sequence[PdfLine], text: str, page_num: int
    ) -> Optional[ListItemMetadata]:
        """Распознает маркер списка в начале абзаца."""
        stripped = text.lstrip()
        bullet = RE_BULLET_MARKER.match(stripped)
        enum = RE_ENUM_MARKER.match(stripped)
        match = enum or bullet
        if match is None:
            return None
        marker = match.group("marker")
        body = match.group("body")
        body_left = self._text_left_mm(page_num)
        indent_mm = max(0.0, group[0].x0_mm - body_left)
        level = min(8, int(indent_mm // TOC_INDENT_STEP_MM))
        num_fmt: Literal[
            "bullet",
            "decimal",
            "lowerLetter",
            "upperLetter",
            "lowerRoman",
            "upperRoman",
            "russianLower",
            "russianUpper",
            "none",
            "other",
        ]
        if enum is None:
            num_fmt = "bullet"
        else:
            number = marker.rstrip(").")
            if number.isdigit():
                num_fmt = "decimal"
                level = max(level, min(8, number.count(".")))
            elif re.fullmatch(r"[IVXLC]+", number):
                num_fmt = "upperRoman"
            elif number.isupper():
                num_fmt = "upperLetter"
            else:
                num_fmt = "lowerLetter"

        first_char = body[:1]
        if first_char.isdigit():
            first_letter_case: Literal["lowercase", "uppercase", "digit", "symbol", "empty"] = "digit"
        elif first_char.isupper():
            first_letter_case = "uppercase"
        elif first_char.islower():
            first_letter_case = "lowercase"
        elif first_char:
            first_letter_case = "symbol"
        else:
            first_letter_case = "empty"

        ending = body.rstrip()[-1:] if body.strip() else ""
        ending_map = {
            ";": "semicolon",
            ".": "period",
            ",": "comma",
            ":": "colon",
            "!": "exclamation",
            "?": "question",
        }
        return ListItemMetadata(
            mechanism="manual_text_prefix",
            level=level,
            num_fmt=num_fmt,
            rendered_marker=marker,
            suffix_type="space",
            body_text_without_marker=body,
            first_letter_case=first_letter_case,
            ending_punctuation=ending_map.get(ending, "none"),  # type: ignore[arg-type]
        )

    # -----------------------------------------------------------------
    # Таблицы
    # -----------------------------------------------------------------

    def _build_tables(self) -> List[TableBlock]:
        """Собирает TableBlock по найденным PyMuPDF таблицам."""
        blocks: List[TableBlock] = []
        for page in self.document.pages:
            for table in page.tables:
                blocks.append(self._build_table(page, table))
        return blocks

    def _build_table(self, page: PdfPage, table: PdfTable) -> TableBlock:
        """Собирает TableBlock одной таблицы с координатами и ширинами столбцов."""
        self._table_seq += 1
        block_id = f"tbl_{self._table_seq:04d}"
        section_idx = self.layout.section_idx_for_page(page.page_num)
        widths = table.column_widths_mm()
        rows: List[TableRow] = []
        for row_idx, cells in enumerate(table.rows):
            height = None
            if row_idx < len(table.row_bboxes_pt):
                bbox = table.row_bboxes_pt[row_idx]
                height = round((bbox[3] - bbox[1]) * PT_TO_MM, 2)
            row_cells: List[TableCell] = []
            for col_idx, text in enumerate(cells):
                row_cells.append(
                    TableCell(
                        cell_id=f"{block_id}_r{row_idx}c{col_idx}",
                        row_idx=row_idx,
                        col_idx=col_idx,
                        width_mm=widths[col_idx] if col_idx < len(widths) else None,
                        text_combined=text,
                        is_visually_empty=not text,
                        content_type_heuristic=_classify_cell(text),
                    )
                )
            is_header = row_idx == 0 and bool(row_cells) and all(
                cell.text_combined for cell in row_cells
            )
            rows.append(
                TableRow(
                    row_idx=row_idx,
                    height_mm=height,
                    is_tbl_header=is_header,
                    page_start_est=page.page_num,
                    page_end_est=page.page_num,
                    cells=row_cells,
                )
            )

        text_area_width = self._text_area_width_mm(page.page_num)
        return TableBlock(
            block_id=block_id,
            section_idx=section_idx,
            page_start_est=page.page_num,
            page_end_est=page.page_num,
            pdf_y_top_mm=table.y0_mm,
            pdf_y_bottom_mm=table.y1_mm,
            pdf_x_left_mm=table.x0_mm,
            pdf_x_right_mm=table.x1_mm,
            total_width_mm=table.width_mm,
            grid_column_widths_mm=widths,
            exceeds_page_text_width=table.width_mm > text_area_width + 1.0,
            rows=rows,
            has_column_numbers_row=_has_column_numbers_row(table.rows),
        )

    # -----------------------------------------------------------------
    # Визуальные объекты
    # -----------------------------------------------------------------

    def _build_visuals(self) -> Tuple[List[ParagraphBlock], Dict[str, VisualObject]]:
        """Собирает визуалы PDF и абзацы-контейнеры для них."""
        blocks: List[ParagraphBlock] = []
        visuals: Dict[str, VisualObject] = {}
        for page in self.document.pages:
            for image in page.images:
                self._paragraph_seq += 1
                self._visual_seq += 1
                block_id = f"p_{self._paragraph_seq:05d}"
                visual_id = f"vis_{self._visual_seq:04d}"
                section_idx = self.layout.section_idx_for_page(page.page_num)
                visual = self._build_visual(page, image, block_id, visual_id, section_idx)
                visuals[visual_id] = visual
                blocks.append(
                    ParagraphBlock(
                        block_id=block_id,
                        section_idx=section_idx,
                        page_start_est=page.page_num,
                        page_end_est=page.page_num,
                        pdf_y_top_mm=image.y0_mm,
                        pdf_y_bottom_mm=image.y1_mm,
                        pdf_x_left_mm=image.x0_mm,
                        pdf_x_right_mm=image.x1_mm,
                        pdf_lines_count_on_start_page=0,
                        pdf_total_lines_count=0,
                        text_raw="",
                        text_visible="",
                        is_empty_or_whitespace=True,
                        contains_only_visuals_or_math=True,
                        embedded_visual_ids=[visual_id],
                    )
                )
        return blocks, visuals

    def _build_visual(
        self,
        page: PdfPage,
        image: PdfImage,
        block_id: str,
        visual_id: str,
        section_idx: int,
    ) -> VisualObject:
        """Собирает VisualObject с координатами и параметрами растрового изображения."""
        text_left = self._text_left_mm(page.page_num)
        text_right = self._text_right_mm(page.page_num) or page.width_mm
        width_mm = max(0.1, image.width_mm)
        height_mm = max(0.1, image.height_mm)
        dpi_x = round(image.width_px / (width_mm / 25.4), 1) if image.width_px and width_mm else None
        dpi_y = round(image.height_px / (height_mm / 25.4), 1) if image.height_px and height_mm else None
        return VisualObject(
            visual_id=visual_id,
            parent_block_id=block_id,
            section_idx=section_idx,
            page_est=page.page_num,
            pdf_x_left_mm=image.x0_mm,
            pdf_y_top_mm=image.y0_mm,
            pdf_x_right_mm=image.x1_mm,
            pdf_y_bottom_mm=image.y1_mm,
            visual_kind="raster_image",
            placement="inline",
            wrap_type="inline",
            width_mm=width_mm,
            height_mm=height_mm,
            exceeds_page_margins=(
                image.x0_mm < text_left - 2.0 or image.x1_mm > text_right + 2.0
            ),
            image_details=RasterImageDetails(
                media_target=(
                    f"pdf:image#{image.xref}" if image.xref is not None else "pdf:image"
                ),
                content_type=f"image/{(image.ext or 'png').lower()}",
                pixel_width=image.width_px,
                pixel_height=image.height_px,
                effective_dpi_x=dpi_x,
                effective_dpi_y=dpi_y,
            ),
        )

    # -----------------------------------------------------------------
    # Оглавление
    # -----------------------------------------------------------------

    def _detect_toc_regions(self, lines: Sequence[PdfLine]) -> List[TocRegion]:
        """Находит страницы оглавления и границы записей на них.

        Страница считается страницей оглавления, если на ней не меньше
        TOC_MIN_ENTRIES строк с отточием. Границы области расширяются вверх на
        первую строку перенесенной записи (она осталась без отточия) и вниз на
        продолжение последней записи: иначе куски оглавления попадают в блоки
        как обычные абзацы и заголовки.
        """
        by_page: Dict[int, List[PdfLine]] = {}
        for line in lines:
            by_page.setdefault(line.page_num, []).append(line)

        regions: List[TocRegion] = []
        for page_num in sorted(by_page):
            page_lines = by_page[page_num]
            dot_indices = [
                index
                for index, line in enumerate(page_lines)
                if RE_DOT_LEADER_ANY.search(line.text)
            ]
            if len(dot_indices) < TOC_MIN_ENTRIES:
                continue
            start = self._toc_region_start(page_lines, dot_indices[0], page_num)
            end = self._toc_region_end(page_lines, dot_indices[-1])
            region_lines = page_lines[start : end + 1]
            if regions and page_num == regions[-1].page_nums[-1] + 1:
                regions[-1].page_nums.append(page_num)
                regions[-1].lines.extend(region_lines)
            else:
                regions.append(TocRegion(page_nums=[page_num], lines=list(region_lines)))
        return regions

    def _toc_region_start(
        self, page_lines: Sequence[PdfLine], first_dot_index: int, page_num: int
    ) -> int:
        """Поднимает начало области оглавления на первую строку перенесенной записи."""
        body_left = self._text_left_mm(page_num)
        start = first_dot_index
        while start > 0:
            previous = page_lines[start - 1]
            gap = page_lines[start].y0_mm - previous.y1_mm
            if gap > self.max_paragraph_gap_mm:
                break
            if previous.x0_mm > body_left + INDENT_SHIFT_MM:
                break
            start -= 1
        return start

    def _toc_region_end(self, page_lines: Sequence[PdfLine], last_dot_index: int) -> int:
        """Продлевает область оглавления на продолжение последней записи."""
        end = last_dot_index
        while end + 1 < len(page_lines):
            current = page_lines[end]
            if RE_TOC_DOT_LEADER.search(current.text):
                break
            following = page_lines[end + 1]
            if following.y0_mm - current.y1_mm > self.max_paragraph_gap_mm:
                break
            end += 1
        return end

    def _build_toc_blocks(
        self, regions: Sequence[TocRegion], paragraphs: Sequence[ParagraphBlock]
    ) -> List[TocContainerBlock]:
        """Собирает TocContainerBlock с записями и ссылкой на заголовок оглавления."""
        blocks: List[TocContainerBlock] = []
        for region in regions:
            self._toc_seq += 1
            block_id = f"toc_{self._toc_seq:04d}"
            entry_groups = self._group_toc_entries(region)
            entries = [
                self._build_toc_entry(block_id, entry_index, group)
                for entry_index, group in enumerate(entry_groups, start=1)
            ]
            heading = self._toc_heading_block(paragraphs, region.page_nums[0])
            blocks.append(
                TocContainerBlock(
                    block_id=block_id,
                    section_idx=self.layout.section_idx_for_page(region.page_nums[0]),
                    page_start_est=region.page_nums[0],
                    page_end_est=region.page_nums[-1],
                    is_automated_field_or_sdt=False,
                    toc_heading_block_id=heading.block_id if heading else None,
                    entries=entries,
                )
            )
        return blocks

    def _group_toc_entries(self, region: TocRegion) -> List[List[PdfLine]]:
        """Делит строки области оглавления на записи с учетом переносов."""
        body_left = self._text_left_mm(region.page_nums[0])
        groups: List[List[PdfLine]] = []
        buffer: List[PdfLine] = []
        for line in region.lines:
            if buffer and self._starts_toc_entry(line, body_left):
                groups.append(buffer)
                buffer = [line]
            else:
                buffer.append(line)
        if buffer:
            groups.append(buffer)
        return groups

    @staticmethod
    def _starts_toc_entry(line: PdfLine, body_left: float) -> bool:
        """True, если строка начинает новую запись оглавления, а не продолжает ее.

        Запись может быть выдвинута вправо на несколько уровней вложенности, но
        продолжение перенесенной записи почти всегда начинается со строчной
        буквы, поэтому прописная буква и номер раздела считаются признаками
        новой записи.
        """
        text = line.text.strip()
        if not text or line.x0_mm > body_left + TOC_ENTRY_INDENT_TOLERANCE_MM:
            return False
        if RE_SECTION_NUMBER.match(text) or RE_CHAPTER_PREFIX.match(text):
            return True
        return text[:1].isupper()

    def _build_toc_entry(
        self, block_id: str, entry_index: int, group: Sequence[PdfLine]
    ) -> TocEntryItem:
        """Разбирает одну запись оглавления: номер, заголовок и страницу."""
        raw = re.sub(r"\s+", " ", " ".join(line.text.strip() for line in group)).strip()
        match = RE_TOC_DOT_LEADER.search(raw)
        title_part = raw[: match.start()] if match else raw
        title_part = RE_TRAILING_DOTS.sub("", title_part).strip()
        page_str = match.group("page") if match else None

        number: Optional[str] = None
        title = title_part
        numbered = RE_SECTION_NUMBER.match(title_part)
        chapter = RE_CHAPTER_PREFIX.match(title_part)
        if numbered:
            number = numbered.group("number")
            title = title_part[numbered.end() :].strip()
        elif chapter:
            number = chapter.group("number")
            title = title_part[chapter.end() :].strip()

        first_line = group[0]
        body_left = self._text_left_mm(first_line.page_num)
        indent_mm = max(0.0, first_line.x0_mm - body_left)
        level = 1 + int(indent_mm // TOC_INDENT_STEP_MM)
        if number and "." in number:
            level = max(level, number.count(".") + 1)

        return TocEntryItem(
            entry_index=entry_index,
            block_id=f"{block_id}_e{entry_index:03d}",
            raw_text=raw,
            extracted_number=number,
            extracted_title=RE_TRAILING_DOTS.sub("", title).strip(),
            extracted_page_str=page_str,
            indent_level=min(9, level),
            has_dot_leader=bool(match),
        )

    @staticmethod
    def _toc_heading_block(
        paragraphs: Sequence[ParagraphBlock], first_page: int
    ) -> Optional[ParagraphBlock]:
        """Ищет заголовок "Содержание"/"Оглавление" перед оглавлением."""
        for block in paragraphs:
            if block.page_start_est > first_page:
                break
            if block.heading_info is None:
                continue
            if _is_toc_title(block.text_visible):
                return block
        return None

    def _link_toc_entries(
        self,
        toc_blocks: Sequence[TocContainerBlock],
        paragraphs: Sequence[ParagraphBlock],
    ) -> None:
        """Сопоставляет записи оглавления с найденными заголовками документа."""
        if not toc_blocks:
            return
        by_title: Dict[str, ParagraphBlock] = {}
        for block in paragraphs:
            if block.heading_info is None:
                continue
            key = normalize_compact(block.heading_info.clean_title_text)
            if key:
                by_title.setdefault(key, block)

        for toc_block in toc_blocks:
            for entry in toc_block.entries:
                key = normalize_compact(entry.extracted_title)
                heading = by_title.get(key)
                if heading is None:
                    continue
                entry.matched_heading_block_id = heading.block_id
                entry.actual_heading_page_est = heading.page_start_est
                entry.is_title_exact_match = True
                if entry.extracted_page_str and entry.extracted_page_str.isdigit():
                    entry.page_number_delta = int(entry.extracted_page_str) - heading.page_start_est
                    entry.is_page_number_match = entry.page_number_delta == 0

    # -----------------------------------------------------------------
    # Дерево документа
    # -----------------------------------------------------------------

    def _build_document_tree(self, blocks: Sequence[BlockUnion]) -> List[DocumentTreeNode]:
        """Строит иерархию заголовков с ролями, страницами и прямыми блоками."""
        heading_positions = [
            (index, block)
            for index, block in enumerate(blocks)
            if isinstance(block, ParagraphBlock) and block.heading_info is not None
        ]
        if not heading_positions:
            return []

        last_page = self.document.page_count
        node_by_index: Dict[int, DocumentTreeNode] = {}
        roots: List[DocumentTreeNode] = []
        stack: List[DocumentTreeNode] = []
        for node_index, (block_index, block) in enumerate(heading_positions, start=1):
            info = block.heading_info
            role = self._semantic_role(block)
            node = DocumentTreeNode(
                node_id=f"node_{node_index:03d}",
                heading_block_id=block.block_id,
                title=info.clean_title_text or block.text_visible.strip(),
                section_number=info.section_number_raw,
                level=info.level,
                semantic_role=role,  # type: ignore[arg-type]
                page_start_est=block.page_start_est,
                page_end_est=self._node_end_page(heading_positions, node_index - 1, last_page),
            )
            while stack and stack[-1].level >= node.level:
                stack.pop()
            if stack:
                stack[-1].children.append(node)
            else:
                roots.append(node)
            stack.append(node)
            node_by_index[block_index] = node

        pointer = 0
        open_nodes: List[DocumentTreeNode] = []
        for index, block in enumerate(blocks):
            if pointer < len(heading_positions) and heading_positions[pointer][0] == index:
                node = node_by_index[index]
                while open_nodes and open_nodes[-1].level >= node.level:
                    open_nodes.pop()
                open_nodes.append(node)
                pointer += 1
                continue
            if open_nodes and not isinstance(block, TocContainerBlock):
                open_nodes[-1].direct_block_ids.append(block.block_id)

        return roots

    def _node_end_page(
        self,
        heading_positions: Sequence[Tuple[int, ParagraphBlock]],
        position: int,
        last_page: int,
    ) -> int:
        """Последняя страница узла: до следующего заголовка того же или высшего уровня."""
        block = heading_positions[position][1]
        level = block.heading_info.level
        for _, other in heading_positions[position + 1 :]:
            if other.heading_info.level <= level:
                return max(block.page_start_est, other.page_start_est - 1)
        return max(block.page_start_est, last_page)

    def _semantic_role(self, block: ParagraphBlock) -> str:
        """Семантическая роль заголовка для дерева документа."""
        role = self._structural_role(block.text_visible)
        if role is not None:
            return role
        info = block.heading_info
        if info is not None and info.heading_kind == "appendix_heading":
            return "appendix"
        if info is not None and info.level == 1:
            return "main_chapter"
        return "sub_section"


def _weighted_median(values: Sequence[float]) -> float:
    """Медиана набора значений (кегли текста уже размножены по числу символов)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _median(values: Sequence[float]) -> Optional[float]:
    """Медиана набора значений или None для пустого набора."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _most_common(values: Sequence[Tuple[str, int]]) -> Optional[str]:
    """Наиболее частая строка с учетом веса (числа символов)."""
    if not values:
        return None
    weights: Dict[str, int] = {}
    for value, weight in values:
        weights[value] = weights.get(value, 0) + max(1, weight)
    return max(weights.items(), key=lambda item: item[1])[0]


def _is_toc_title(text: str) -> bool:
    """True, если текст — заголовок оглавления ("Содержание", "Оглавление")."""
    compact = normalize_compact(text)
    return compact.startswith("содержание") or compact.startswith("оглавление")


def _classify_cell(text: str) -> Literal["empty", "dash_placeholder", "numeric", "text", "mixed"]:
    """Классифицирует содержимое ячейки таблицы."""
    stripped = text.strip()
    if not stripped:
        return "empty"
    if stripped in {"-", "\u2013", "\u2014", "\u2212"}:
        return "dash_placeholder"
    digits = sum(1 for char in stripped if char.isdigit())
    letters = sum(1 for char in stripped if char.isalpha())
    if digits and not letters:
        return "numeric"
    if digits and letters:
        return "mixed"
    return "text"


def _has_column_numbers_row(rows: Sequence[Sequence[str]]) -> bool:
    """True, если вторая строка таблицы — нумерация столбцов "1", "2", "3"..."""
    if len(rows) < 2:
        return False
    second = [cell.strip() for cell in rows[1]]
    if not second or any(not cell.isdigit() for cell in second):
        return False
    numbers = [int(cell) for cell in second]
    return numbers == list(range(numbers[0], numbers[0] + len(numbers)))
