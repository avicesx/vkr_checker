"""Паритет DOCX и PDF: один и тот же документ дает одинаковую семантику ссылок.

Синтетический документ собирается дважды: как `.docx` через
``DocxPackageBuilder`` + ``DocxParserPipeline`` и как `.pdf` через PyMuPDF +
``parse_pdf``. Идентификаторы блоков, страницы и координаты в режимах разные,
поэтому сравнивается семантика: записи библиографии (порядковые номера),
подписи рисунка и таблицы, упоминание ``[1]`` с разрешенной целью, а также
флаг ``capabilities.cross_references`` у PDF-результата.

PDF рисуется реальным Times New Roman (как в ``test_pdf_only_rules.py``):
эвристики `parser.pdf` чувствительны к метрикам шрифта, поэтому подмена
встроенной гарнитурой дала бы другую геометрию строк.
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pytest

pymupdf = pytest.importorskip(
    "pymupdf", reason="Для тестов PDF-парсера требуется PyMuPDF (pip install pymupdf)"
)

from conftest import DocxPackageBuilder
from parser.core.schema import ParsedDocument
from parser.docx.pipeline import DocxParserPipeline
from parser.pdf import parse_pdf

MM_TO_PT = 72.0 / 25.4

PAGE_WIDTH_MM = 210.0
PAGE_HEIGHT_MM = 297.0
LEFT_MARGIN_MM = 31.5
BODY_SIZE_PT = 12.0
HEADING_SIZE_PT = 16.0
BODY_PITCH_MM = 8.5
#: Отбивка между абзацами. `parser.pdf.structure` считает медиану шага строк по
#: парам внутри абзацев и режет абзац при разрыве больше max(1.6 × медиана,
#: медиана + 1.6 мм) ≈ 6 мм, поэтому между абзацами нужна отбивка заметно
#: больше шага строк.
BLOCK_GAP_MM = 20.0

#: Шрифты Times New Roman с поддержкой кириллицы для синтетических PDF.
FONT_DIRS = (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/msttcorefonts"))
REGULAR_FONT_NAMES = ("times.ttf", "Times New Roman.ttf", "LiberationSerif-Regular.ttf")
BOLD_FONT_NAMES = ("timesbd.ttf", "Times New Roman Bold.ttf", "LiberationSerif-Bold.ttf")


# ---------------------------------------------------------------------
# Общее содержимое документа
# ---------------------------------------------------------------------

BODY_LINES = (
    "В работе рассматриваются основные подходы к оценке",
    "эффективности проекта.",
)
CITATION_LINES = (
    "Как показано в работе [1], методика применима",
    "к проектам разного масштаба.",
)
FIGURE_CAPTION = "Рисунок 1 — Схема"
TABLE_CAPTION = "Таблица 1 — Данные"
#: Перечисление целей: в PDF слой разбирает его в одну ссылку с двумя целями.
ENUMERATION_LINE = "Данные отражены в таблицах 5,6."
#: Диапазон целей: одно упоминание, раскрываемое в цели 1, 2, 3 и 4.
RANGE_LINE = "На рисунках 1–4 показаны схемы."
#: Дополнительные подписи рисунков, чтобы диапазон 1–4 разрешался полностью.
EXTRA_FIGURE_CAPTIONS = (
    "Рисунок 2 — Вторая схема",
    "Рисунок 3 — Третья схема",
    "Рисунок 4 — Четвёртая схема",
)
#: Заголовок раздела с номером: в PDF такой абзац не должен приниматься за
#: упоминание объекта с целью «1.Структура» (см. регрессию F1).
SECTION_HEADING = "1.Структура"
BIBLIOGRAPHY_HEADING = "СПИСОК ЛИТЕРАТУРЫ"
BIBLIOGRAPHY_ENTRIES = (
    "1. Иванов И.И. Теория управления. — М.: Наука, 2020. — 200 с.",
    "2. Петров П.П. Практика управления. — СПб.: Питер, 2021. — 150 с.",
)


# ---------------------------------------------------------------------
# DOCX-половина пары
# ---------------------------------------------------------------------


def _paragraph(text: str) -> str:
    """Абзац основного текста с выключкой по ширине."""
    return (
        '<w:p><w:pPr><w:jc w:val="both"/></w:pPr>'
        f"<w:r><w:t>{text}</w:t></w:r></w:p>"
    )


def _heading(text: str) -> str:
    """Абзац заголовка первого уровня."""
    return (
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:jc w:val="center"/></w:pPr>'
        f"<w:r><w:t>{text}</w:t></w:r></w:p>"
    )


def _parse_docx(builder: DocxPackageBuilder) -> ParsedDocument:
    """Собирает DOCX с тем же содержимым и возвращает результат разбора."""
    body = "".join(
        [
            _heading("ОСНОВНАЯ ЧАСТЬ"),
            _heading(SECTION_HEADING),
            _paragraph(" ".join(BODY_LINES)),
            _paragraph(FIGURE_CAPTION),
            _paragraph(TABLE_CAPTION),
            _paragraph(ENUMERATION_LINE),
            _paragraph(" ".join(CITATION_LINES)),
            _paragraph(RANGE_LINE),
            *(_paragraph(caption) for caption in EXTRA_FIGURE_CAPTIONS),
            _heading(BIBLIOGRAPHY_HEADING),
            *(_paragraph(entry) for entry in BIBLIOGRAPHY_ENTRIES),
        ]
    )
    builder.set_body(body)
    return DocxParserPipeline(builder.build("parity.docx")).parse()


# ---------------------------------------------------------------------
# PDF-половина пары
# ---------------------------------------------------------------------


def _find_font(names: Tuple[str, ...]) -> Path:
    """Ищет файл шрифта в известных каталогах и пропускает тест, если его нет."""
    for directory in FONT_DIRS:
        for name in names:
            candidate = directory / name
            if candidate.exists():
                return candidate
    pytest.skip("Не найден шрифт Times New Roman с кириллицей для синтетического PDF")


def _pt(value_mm: float) -> float:
    """Переводит миллиметры в пункты PDF."""
    return value_mm * MM_TO_PT


def _draw_text(
    page: "pymupdf.Page",
    x_mm: float,
    baseline_mm: float,
    text: str,
    size_pt: float = BODY_SIZE_PT,
    bold: bool = False,
) -> None:
    """Рисует строку текста с указанными координатами базовой линии."""
    page.insert_text(
        pymupdf.Point(_pt(x_mm), _pt(baseline_mm)),
        text,
        fontname="tnrb" if bold else "tnr",
        fontsize=size_pt,
        color=(0, 0, 0),
    )


def _draw_paragraph(
    page: "pymupdf.Page",
    baseline_mm: float,
    lines: Tuple[str, ...],
    *,
    size_pt: float = BODY_SIZE_PT,
    bold: bool = False,
) -> float:
    """Рисует абзац построчно и возвращает позицию следующей строки."""
    for line in lines:
        _draw_text(page, LEFT_MARGIN_MM, baseline_mm, line, size_pt=size_pt, bold=bold)
        baseline_mm += BODY_PITCH_MM
    return baseline_mm


def _new_text_page(document: "pymupdf.Document") -> "pymupdf.Page":
    """Создаёт страницу A4 и регистрирует на ней шрифты Times New Roman."""
    page = document.new_page(width=_pt(PAGE_WIDTH_MM), height=_pt(PAGE_HEIGHT_MM))
    page.insert_font(fontname="tnr", fontfile=str(_find_font(REGULAR_FONT_NAMES)))
    page.insert_font(fontname="tnrb", fontfile=str(_find_font(BOLD_FONT_NAMES)))
    return page


def _build_pdf(path: Path) -> Path:
    """Рисует PDF с тем же содержимым, что и DOCX-половина пары."""
    document = pymupdf.open()
    page = _new_text_page(document)

    baseline = 30.0
    baseline = _draw_paragraph(
        page, baseline, ("ОСНОВНАЯ ЧАСТЬ",),
        size_pt=HEADING_SIZE_PT, bold=True,
    )
    baseline += BLOCK_GAP_MM
    baseline = _draw_paragraph(
        page, baseline, (SECTION_HEADING,),
        size_pt=HEADING_SIZE_PT, bold=True,
    )
    baseline += BLOCK_GAP_MM
    baseline = _draw_paragraph(page, baseline, BODY_LINES)
    baseline += BLOCK_GAP_MM
    baseline = _draw_paragraph(page, baseline, (FIGURE_CAPTION,))
    baseline += BLOCK_GAP_MM
    baseline = _draw_paragraph(page, baseline, (TABLE_CAPTION,))
    baseline += BLOCK_GAP_MM
    baseline = _draw_paragraph(page, baseline, (ENUMERATION_LINE,))
    baseline += BLOCK_GAP_MM
    baseline = _draw_paragraph(page, baseline, CITATION_LINES)

    # Диапазон и дополнительные подписи рисунков — на второй странице: так
    # строки не попадают в нижнюю зону листа, которую PDF-парсер считает
    # колонтитулом.
    page = _new_text_page(document)
    baseline = 30.0
    baseline = _draw_paragraph(page, baseline, (RANGE_LINE,))
    for caption in EXTRA_FIGURE_CAPTIONS:
        baseline += BLOCK_GAP_MM
        baseline = _draw_paragraph(page, baseline, (caption,))

    # Список литературы — на третьей странице: так строки не уходят за нижнее
    # поле, а PDF-парсер видит отдельный раздел с заголовком.
    page = _new_text_page(document)
    baseline = 30.0
    baseline = _draw_paragraph(
        page, baseline, (BIBLIOGRAPHY_HEADING,),
        size_pt=HEADING_SIZE_PT, bold=True,
    )
    for entry in BIBLIOGRAPHY_ENTRIES:
        baseline += BLOCK_GAP_MM
        baseline = _draw_paragraph(page, baseline, (entry,))

    document.save(str(path))
    document.close()
    return path


# ---------------------------------------------------------------------
# Паритет
# ---------------------------------------------------------------------


def test_pdf_only_and_docx_share_cross_reference_semantics(
    tmp_path: Path,
    docx_builder: DocxPackageBuilder,
) -> None:
    """Общий слой enrichment дает в DOCX и PDF одинаковую семантику ссылок."""
    docx_document = _parse_docx(docx_builder)
    pdf_document = parse_pdf(_build_pdf(tmp_path / "parity.pdf"))

    docx_index = docx_document.cross_ref_index
    pdf_index = pdf_document.cross_ref_index

    # Записи списка литературы: те же порядковые номера, тот же год.
    docx_bibliography = docx_index.bibliography
    pdf_bibliography = pdf_index.bibliography
    assert len(docx_bibliography) >= 2
    assert len(pdf_bibliography) >= 2
    assert [entry.order_index for entry in docx_bibliography] == [
        entry.order_index for entry in pdf_bibliography
    ]
    assert [entry.year_extracted for entry in docx_bibliography] == [
        entry.year_extracted for entry in pdf_bibliography
    ]

    # Подписи рисунков и таблицы найдены в обоих режимах.
    assert [caption.number_str for caption in docx_index.figures] == [
        "1",
        "2",
        "3",
        "4",
    ]
    assert [caption.number_str for caption in pdf_index.figures] == [
        "1",
        "2",
        "3",
        "4",
    ]
    assert [caption.number_str for caption in docx_index.tables] == ["1"]
    assert [caption.number_str for caption in pdf_index.tables] == ["1"]

    # Ссылка [1] разрешена в обоих режимах: цель — запись списка литературы.
    def bibliography_mentions(document: ParsedDocument) -> list:
        return [
            mention
            for mention in document.cross_ref_index.mentions
            if mention.ref_type == "bibliography"
        ]

    docx_mentions = bibliography_mentions(docx_document)
    pdf_mentions = bibliography_mentions(pdf_document)
    assert docx_mentions
    assert pdf_mentions
    assert all(mention.resolved_entity_ids for mention in docx_mentions)
    assert all(mention.resolved_entity_ids for mention in pdf_mentions)
    assert docx_mentions[0].extracted_targets == pdf_mentions[0].extracted_targets

    # Признак цитируемости записи проставлен в обоих режимах.
    assert docx_bibliography[0].is_cited_in_text is True
    assert pdf_bibliography[0].is_cited_in_text is True

    # Заголовок «1.Структура» не превращается в упоминание объекта.
    for document in (docx_document, pdf_document):
        assert not [
            mention
            for mention in document.cross_ref_index.mentions
            if "Структура" in mention.raw_snippet
            or "1.Структура" in mention.extracted_targets
        ]

    # Абзацы-подписи не считаются упоминаниями: ни одно упоминание не стоит на
    # блоке подписи, поэтому подпись не помечает свой объект упомянутым.
    for document in (docx_document, pdf_document):
        index = document.cross_ref_index
        caption_blocks = {
            caption.caption_block_id
            for caption in (*index.figures, *index.tables, *index.Apps)
        }
        assert not [
            mention for mention in index.mentions if mention.block_id in caption_blocks
        ]

    def caption_flags(document: ParsedDocument) -> list:
        index = document.cross_ref_index
        return [
            (
                caption.entity_type,
                caption.number_str,
                caption.is_mentioned_in_text,
                caption.first_mention_block_id is None,
            )
            for caption in (*index.figures, *index.tables)
        ]

    # Флаги упомянутости совпадают по семантике, а не по идентификаторам.
    assert caption_flags(docx_document) == caption_flags(pdf_document)

    # Перечисление «в таблицах 5,6» распознано в обоих режимах: падежная форма
    # и список целей не ломают паритет флагов таблиц и рисунков.
    for document in (docx_document, pdf_document):
        assert [
            mention
            for mention in document.cross_ref_index.mentions
            if mention.ref_type == "table"
            and mention.raw_snippet.replace("\u00a0", " ").startswith("таблицах 5")
        ]

    # Диапазон «на рисунках 1–4» раскрыт в четыре цели и разрешён целиком в
    # обоих режимах; флаги упомянутости рисунков при этом совпадают.
    for document in (docx_document, pdf_document):
        range_mentions = [
            mention
            for mention in document.cross_ref_index.mentions
            if mention.ref_type == "figure"
            and mention.raw_snippet.replace("\u00a0", " ").startswith("рисунках 1")
        ]
        assert len(range_mentions) == 1
        assert range_mentions[0].extracted_targets == ["1", "2", "3", "4"]
        assert len(range_mentions[0].resolved_entity_ids) == 4

    # Индекс PDF заполнен общим слоем, поэтому флаг поднят.
    assert pdf_document.capabilities.cross_references is True


def _build_pdf_with_whitespace_captions(path: Path) -> Path:
    """Рисует PDF с подписями без тире и заголовком раздела с номером."""
    document = pymupdf.open()
    page = document.new_page(width=_pt(PAGE_WIDTH_MM), height=_pt(PAGE_HEIGHT_MM))
    page.insert_font(fontname="tnr", fontfile=str(_find_font(REGULAR_FONT_NAMES)))
    page.insert_font(fontname="tnrb", fontfile=str(_find_font(BOLD_FONT_NAMES)))

    baseline = 30.0
    baseline = _draw_paragraph(
        page, baseline, (SECTION_HEADING,), size_pt=HEADING_SIZE_PT, bold=True
    )
    baseline += BLOCK_GAP_MM
    baseline = _draw_paragraph(page, baseline, ("Таблица 1  Анализ рентабельности",))
    baseline += BLOCK_GAP_MM
    baseline = _draw_paragraph(page, baseline, ("Рис 1.Схема управления",))

    document.save(str(path))
    document.close()
    return path


def test_pdf_whitespace_captions_are_recognized(tmp_path: Path) -> None:
    """В PDF подписи с пробельным разделителем и точка после номера допустимы."""
    document = parse_pdf(_build_pdf_with_whitespace_captions(tmp_path / "captions.pdf"))
    index = document.cross_ref_index

    assert [caption.number_str for caption in index.tables] == ["1"]
    assert [caption.number_str for caption in index.figures] == ["1"]
    assert index.tables[0].title_text == "Анализ рентабельности"
    assert index.figures[0].title_text == "Схема управления"

    # Заголовок раздела «1.Структура» упоминанием объекта не считается.
    assert not [
        mention
        for mention in index.mentions
        if "Структура" in mention.raw_snippet
        or "1.Структура" in mention.extracted_targets
    ]


