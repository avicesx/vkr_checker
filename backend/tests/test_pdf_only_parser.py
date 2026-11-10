"""Тесты автономного PDF-парсера: режим pdf_only, вёрстка, структура и таблицы.

PDF рисуется на лету через PyMuPDF (A4, Times New Roman, заголовки разных
кеглей, абзацы, маркированный и нумерованный списки, простая таблица с
рамками, номера страниц в нижнем колонтитуле и оглавление с отточием), поэтому
тесты не зависят от бинарных образцов. Отдельно проверяется разбор реальных
файлов manual_test и round-trip JSON-контракта.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

import pytest

pymupdf = pytest.importorskip(
    "pymupdf", reason="Для тестов PDF-парсера требуется PyMuPDF (pip install pymupdf)"
)

from parser.core.schema import (
    ParagraphBlock,
    ParsedDocument,
    TableBlock,
    TocContainerBlock,
)
from parser.pdf import PdfDocumentParser, parse_pdf

MM_TO_PT = 72.0 / 25.4

PAGE_WIDTH_MM = 210.0
PAGE_HEIGHT_MM = 297.0
LEFT_MARGIN_MM = 30.0
TEXT_RIGHT_MM = 195.0
BODY_SIZE_PT = 14.0
BODY_PITCH_MM = 8.5
PAGE_NUMBER_BASELINE_MM = 288.0

#: Шрифты Times New Roman с поддержкой кириллицы для синтетических PDF.
FONT_DIRS = (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/msttcorefonts"))
REGULAR_FONT_NAMES = ("times.ttf", "Times New Roman.ttf", "LiberationSerif-Regular.ttf")
BOLD_FONT_NAMES = ("timesbd.ttf", "Times New Roman Bold.ttf", "LiberationSerif-Bold.ttf")


def _find_font(names: Tuple[str, ...]) -> Path:
    """Ищет файл шрифта в известных каталогах и пропускает тест, если его нет."""
    for directory in FONT_DIRS:
        for name in names:
            candidate = directory / name
            if candidate.exists():
                return candidate
    pytest.skip("Не найден шрифт Times New Roman с кириллицей для синтетического PDF")


def _measure(text: str, size_pt: float, bold: bool = False) -> float:
    """Ширина строки в миллиметрах по метрикам реального шрифта."""
    font = pymupdf.Font(fontfile=str(_find_font(BOLD_FONT_NAMES if bold else REGULAR_FONT_NAMES)))
    return font.text_length(text, fontsize=size_pt) / MM_TO_PT


def _plain(text: str) -> str:
    """Нормализует текст для сравнений: неразрывные пробелы и знак ';' из PDF."""
    return text.replace("\xa0", " ").replace("\u037e", ";")


def _pt(value_mm: float) -> float:
    """Переводит миллиметры в пункты PDF."""
    return value_mm * MM_TO_PT


def _new_page(document: "pymupdf.Document") -> "pymupdf.Page":
    """Добавляет страницу A4 и регистрирует на ней шрифты с кириллицей."""
    page = document.new_page(width=_pt(PAGE_WIDTH_MM), height=_pt(PAGE_HEIGHT_MM))
    page.insert_font(fontname="tnr", fontfile=str(_find_font(REGULAR_FONT_NAMES)))
    page.insert_font(fontname="tnrb", fontfile=str(_find_font(BOLD_FONT_NAMES)))
    return page


def _draw_text(
    page: "pymupdf.Page",
    x_mm: float,
    baseline_mm: float,
    text: str,
    size_pt: float = BODY_SIZE_PT,
    bold: bool = False,
) -> None:
    """Рисует строку текста с указанной базовой линией."""
    page.insert_text(
        pymupdf.Point(_pt(x_mm), _pt(baseline_mm)),
        text,
        fontname="tnrb" if bold else "tnr",
        fontsize=size_pt,
        color=(0, 0, 0),
    )


def _draw_page_number(page: "pymupdf.Page", number: int) -> None:
    """Рисует номер страницы по центру нижнего колонтитула."""
    text = str(number)
    center_mm = PAGE_WIDTH_MM / 2.0
    _draw_text(page, center_mm - len(text) * 1.5, PAGE_NUMBER_BASELINE_MM, text, size_pt=12.0)


def _draw_dot_leader(page: "pymupdf.Page", y_mm: float, title: str, page_number: int) -> None:
    """Рисует запись оглавления: заголовок, отточие и номер страницы.

    Число точек рассчитывается по метрикам шрифта, чтобы строка заканчивалась
    внутри текстовой области: оценка полей опирается на правый край текста.
    """
    number_str = str(page_number)
    head = f"{title} "
    tail = f" {number_str}"
    available_mm = TEXT_RIGHT_MM - LEFT_MARGIN_MM - _measure(head, BODY_SIZE_PT) - _measure(
        tail, BODY_SIZE_PT
    )
    dot_width_mm = max(0.1, _measure(".", BODY_SIZE_PT))
    dots = "." * max(5, int(available_mm / dot_width_mm) - 1)
    _draw_text(page, LEFT_MARGIN_MM, y_mm, f"{head}{dots}{tail}", size_pt=BODY_SIZE_PT)


def _draw_paragraph(
    page: "pymupdf.Page",
    baseline_mm: float,
    lines: List[str],
    first_line_indent_mm: float = 12.5,
) -> float:
    """Рисует абзац с отступом первой строки и возвращает базовую линию следующей."""
    baseline = baseline_mm
    for index, line in enumerate(lines):
        x = LEFT_MARGIN_MM + (first_line_indent_mm if index == 0 else 0.0)
        _draw_text(page, x, baseline, line)
        baseline += BODY_PITCH_MM
    return baseline


FILLER_PARAGRAPHS = (
    (
        "Для проверки оценки полей страница заполняется текстом до нижнего",
        "поля, как в реальном документе: короткие абзацы чередуются с",
        "длинными, а интервал между строками остается полуторным.",
    ),
    (
        "Второй абзац продолжает раскрытие темы и содержит уточняющие",
        "формулировки, которые встречаются в пояснительной записке.",
    ),
    (
        "Третий абзац описывает порядок подготовки исходных данных и",
        "требования к составу разделов проектной документации, а также",
        "перечень согласований, необходимых для начала работ.",
    ),
    (
        "Четвертый абзац посвящен контролю качества и приемке результата;",
        "здесь перечислены критерии, по которым заказчик оценивает работу.",
    ),
)


def _fill_page(page: "pymupdf.Page", baseline_mm: float, target_baseline_mm: float) -> float:
    """Заполняет страницу абзацами до заданной базовой линии."""
    index = 0
    baseline = baseline_mm
    while baseline < target_baseline_mm:
        lines = FILLER_PARAGRAPHS[index % len(FILLER_PARAGRAPHS)]
        baseline = _draw_paragraph(page, baseline, list(lines))
        index += 1
    return baseline


def _draw_table(
    page: "pymupdf.Page",
    x0_mm: float,
    y0_mm: float,
    column_widths_mm: List[float],
    rows: List[List[str]],
    row_height_mm: float = 9.0,
) -> None:
    """Рисует таблицу с рамками: линии сетки и текст в ячейках."""
    total_width = sum(column_widths_mm)
    y1_mm = y0_mm + row_height_mm * len(rows)
    for index in range(len(rows) + 1):
        line_y = y0_mm + row_height_mm * index
        page.draw_line(
            pymupdf.Point(_pt(x0_mm), _pt(line_y)),
            pymupdf.Point(_pt(x0_mm + total_width), _pt(line_y)),
        )
    x = x0_mm
    for width in [0.0, *column_widths_mm]:
        x += width if width else 0.0
        page.draw_line(pymupdf.Point(_pt(x), _pt(y0_mm)), pymupdf.Point(_pt(x), _pt(y1_mm)))
    for row_index, row in enumerate(rows):
        baseline = y0_mm + row_height_mm * row_index + 6.0
        x = x0_mm + 2.0
        for cell_index, cell in enumerate(row):
            if cell:
                _draw_text(page, x, baseline, cell, size_pt=12.0)
            x += column_widths_mm[cell_index]


def _build_synthetic_pdf(path: Path) -> Path:
    """Собирает синтетический PDF: титул, оглавление, три страницы текста и таблица."""
    document = pymupdf.open()

    # Страница 1: титул и оглавление с отточием.
    page = _new_page(document)
    _draw_text(page, 65.0, 100.0, "Дипломная работа", size_pt=18.0, bold=True)
    _draw_text(page, 90.0, 140.0, "Содержание", size_pt=16.0, bold=True)
    _draw_dot_leader(page, 152.0, "Введение", 2)
    _draw_dot_leader(page, 161.0, "1. Основная часть", 2)
    _draw_dot_leader(page, 170.0, "2. Результаты", 3)
    _draw_page_number(page, 1)

    # Страница 2: введение, абзацы, подраздел, перечисление и текст до низа листа.
    page = _new_page(document)
    _draw_text(page, 60.0, 25.0, "Введение", size_pt=18.0, bold=True)
    baseline = _draw_paragraph(
        page,
        40.0,
        [
            "Договор подряда на проектные работы является одним из",
            "наиболее востребованных видов договоров в строительстве.",
            "Его предметом выступает разработка технической документации.",
        ],
        first_line_indent_mm=0.0,
    )
    baseline = _draw_paragraph(
        page,
        baseline + 3.0,
        [
            "Второй абзац начинается с отступа первой строки и содержит",
            "продолжение мысли о качестве проектных решений и сроках.",
        ],
    )
    baseline += 3.0
    _draw_text(page, LEFT_MARGIN_MM, baseline, "1. Основная часть", size_pt=16.0, bold=True)
    baseline = _draw_paragraph(
        page,
        baseline + BODY_PITCH_MM,
        [
            "Основная часть раскрывает содержание методики оценки и",
            "порядок расчета показателей эффективности проекта.",
        ],
        first_line_indent_mm=0.0,
    )
    baseline = _draw_paragraph(
        page,
        baseline + 3.0,
        ["1) первый пункт перечисления;"],
        first_line_indent_mm=0.0,
    )
    baseline = _fill_page(page, baseline + 3.0, 266.0)
    _draw_text(page, LEFT_MARGIN_MM, baseline, "изложенные ниже")
    _draw_page_number(page, 2)

    # Страница 3: продолжение абзаца, подраздел, таблица и заполнение листа.
    page = _new_page(document)
    _draw_text(page, LEFT_MARGIN_MM, 25.0, "требования к оформлению результатов.")
    _draw_text(page, LEFT_MARGIN_MM, 38.0, "2. Результаты", size_pt=16.0, bold=True)
    _draw_text(
        page,
        LEFT_MARGIN_MM,
        53.0,
        "В таблице приведены итоговые значения показателей проекта.",
    )
    _draw_table(
        page,
        LEFT_MARGIN_MM,
        65.0,
        [70.0, 30.0],
        [
            ["Показатель", "Значение"],
            ["Выручка", "1200"],
            ["Затраты", "800"],
        ],
    )
    _draw_text(page, LEFT_MARGIN_MM, 130.0, "1.1. Подраздел", size_pt=15.0, bold=True)
    baseline = _draw_paragraph(
        page,
        145.0,
        ["Текст подраздела с пояснениями к таблице."],
        first_line_indent_mm=0.0,
    )
    _fill_page(page, baseline + 3.0, 268.0)
    _draw_page_number(page, 3)

    document.save(str(path))
    document.close()
    return path


@pytest.fixture(scope="module")
def synthetic_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Путь к синтетическому PDF, собранному один раз на модуль."""
    target = tmp_path_factory.mktemp("pdf_only") / "synthetic.pdf"
    return _build_synthetic_pdf(target)


@pytest.fixture(scope="module")
def document(synthetic_pdf: Path) -> ParsedDocument:
    """Разобранный синтетический PDF."""
    return parse_pdf(synthetic_pdf)


def _headings(document: ParsedDocument) -> List[ParagraphBlock]:
    """Абзацы-заголовки в порядке документа."""
    return [
        block
        for block in document.blocks
        if isinstance(block, ParagraphBlock) and block.heading_info is not None
    ]


# ---------------------------------------------------------------------
# Контракт режима и источника
# ---------------------------------------------------------------------


def test_parse_mode_and_source(document: ParsedDocument) -> None:
    """Режим pdf_only: DOCX не участвовал, PDF — единственный источник."""
    assert document.parse_mode == "pdf_only"
    assert document.schema_version == "3.0.0"
    assert document.source.mode == "pdf_only"
    assert document.source.has_pdf is True
    assert document.source.has_docx is False
    assert document.source.docx_filename is None
    assert document.source.docx_sha256 is None
    assert document.source.pdf_filename == "synthetic.pdf"
    assert len(document.source.pdf_sha256 or "") == 64


def test_capabilities_matrix_for_pdf_only(document: ParsedDocument) -> None:
    """Матрица возможностей: точные страницы есть, структуры DOCX нет."""
    capabilities = document.capabilities
    assert capabilities.styles_parsed is False
    assert capabilities.numbering_parsed is False
    assert capabilities.sections_parsed is False
    assert capabilities.visual_registry is False
    assert capabilities.footnotes_parsed is False
    assert capabilities.cross_references is False
    assert capabilities.word_metadata is False
    assert capabilities.exact_pagination is True
    assert capabilities.pdf_layout_coordinates is True
    assert capabilities.page_count_estimated is False


def test_page_count_is_exact(document: ParsedDocument) -> None:
    """Число страниц взято из PDF и помечено как точное."""
    assert document.page_count.total_pages == 3
    assert document.page_count.method == "pdf_exact"
    assert document.page_count.confidence == "high"
    assert document.meta.word_stat_pages == 3
    assert document.meta.pagination_source == "pdf_exact"
    assert document.meta.pdf_docx_sync_score is None
    assert document.meta.source_format == "pdf"
    assert document.meta.rendered_page_numbers_present is True
    assert document.meta.word_stat_words and document.meta.word_stat_words > 40
    assert (document.meta.word_stat_chars_with_spaces or 0) > (
        document.meta.word_stat_chars_no_spaces or 0
    )


def test_empty_collections_for_docx_only_data(document: ParsedDocument) -> None:
    """Данные, доступные только из DOCX, в pdf_only пусты и помечены флагами."""
    assert document.styles == {}
    assert document.footnotes == []
    assert document.cross_ref_index.figures == []
    assert document.cross_ref_index.bibliography == []
    assert document.anomalies.pdf_docx_mismatch_warning is None
    assert document.capabilities.styles_parsed is False


# ---------------------------------------------------------------------
# Точные страницы и вёрстка
# ---------------------------------------------------------------------


def test_page_numbers_are_exact(document: ParsedDocument) -> None:
    """Аудит по каждой странице: номер страницы, зона, выравнивание и совпадение."""
    assert len(document.pdf_pages_audit) == 3
    numbers = [audit.rendered_page_number_int for audit in document.pdf_pages_audit]
    assert numbers == [1, 2, 3]
    for audit in document.pdf_pages_audit:
        assert audit.has_rendered_page_number is True
        assert audit.page_number_location == "footer"
        assert audit.page_number_alignment == "center"
        assert audit.matches_physical_page_num is True
        assert audit.rendered_page_number_raw == str(audit.page_num)


def test_margins_are_estimated_and_marked(document: ParsedDocument) -> None:
    """Поля оценены по тексту и попадают в разумный для A4 диапазон."""
    assert len(document.sections) == 1
    section = document.sections[0]
    assert section.page_start_est == 1
    assert section.page_end_est == 3
    assert abs(section.page_width_mm - 210.0) <= 1.0
    assert abs(section.page_height_mm - 297.0) <= 1.0
    assert section.orientation == "portrait"
    assert section.is_a4_standard is True
    margins = section.margins
    assert 20.0 <= margins.left_mm <= 40.0
    assert 5.0 <= margins.right_mm <= 30.0
    assert 10.0 <= margins.top_mm <= 30.0
    assert 5.0 <= margins.bottom_mm <= 30.0
    assert 120.0 <= section.text_area_width_mm <= 190.0
    # Поля синтезированы по геометрии, а не разобраны из w:sectPr.
    assert document.capabilities.sections_parsed is False
    for audit in document.pdf_pages_audit:
        assert audit.body_top_y_mm is not None
        assert audit.body_bottom_y_mm is not None
        assert audit.body_left_x_mm is not None
        assert audit.body_right_x_mm is not None
        assert audit.bottom_free_space_mm is not None


def test_paragraph_pages_are_exact(document: ParsedDocument) -> None:
    """Страницы абзацев точные: абзац, разорванный между листами, виден как 2-3."""
    paragraphs = [block for block in document.blocks if isinstance(block, ParagraphBlock)]
    assert paragraphs
    assert all(1 <= block.page_start_est <= 3 for block in paragraphs)
    split = [
        block
        for block in paragraphs
        if block.text_visible and block.page_start_est != block.page_end_est
    ]
    assert split, "Ожидался абзац, разорванный между страницами 2 и 3"
    cross_page = split[0]
    assert (cross_page.page_start_est, cross_page.page_end_est) == (2, 3)
    assert cross_page.pdf_lines_count_on_start_page
    assert cross_page.pdf_lines_count_on_end_page


def test_runs_and_effective_props_are_pdf_estimates(document: ParsedDocument) -> None:
    """Раны содержат измеренные шрифт и кегль с источником pdf_estimate."""
    paragraphs = [
        block
        for block in document.blocks
        if isinstance(block, ParagraphBlock) and len(block.text_visible) > 40
    ]
    assert paragraphs
    block = paragraphs[0]
    assert block.runs, "У абзаца должны быть раны"
    assert "".join(run.text for run in block.runs) == block.text_raw
    for run in block.runs:
        props = run.effective_props
        assert props.font_source == "pdf_estimate"
        assert props.size_source == "pdf_estimate"
        assert props.size_pt > 0
        assert props.font_ascii
    assert block.effective_props.alignment_source == "pdf_estimate"
    assert block.effective_props.line_spacing_value > 0


# ---------------------------------------------------------------------
# Структура: заголовки, списки, оглавление, таблицы
# ---------------------------------------------------------------------


def test_heading_levels(document: ParsedDocument) -> None:
    """Заголовки распознаны по кеглю и нумерации, уровни назначены."""
    headings = _headings(document)
    titles = [_plain(block.heading_info.clean_title_text).strip() for block in headings]
    assert any(title.startswith("Введение") for title in titles)
    assert any(title.startswith("Основная часть") for title in titles)
    assert any(title.startswith("Результаты") for title in titles)
    assert any(title.startswith("Подраздел") for title in titles)

    intro = next(block for block in headings if _plain(block.text_visible).startswith("Введение"))
    assert intro.heading_info.level == 1
    assert intro.heading_info.heading_kind == "structural_unnumbered"
    assert intro.heading_info.detection_confidence == "structural_keyword_match"

    section = next(block for block in headings if "Основная часть" in _plain(block.text_visible))
    assert section.heading_info.level == 1
    assert section.heading_info.heading_kind == "numbered_section"
    assert section.heading_info.section_number_raw == "1"

    subsection = next(block for block in headings if "Подраздел" in _plain(block.text_visible))
    assert subsection.heading_info.level == 2
    assert subsection.heading_info.section_number_raw == "1.1"

    for block in headings:
        assert block.effective_props.outline_lvl == block.heading_info.level - 1


def test_document_tree_is_built_from_headings(document: ParsedDocument) -> None:
    """Дерево документа строится по заголовкам и содержит роли и страницы."""
    tree = document.document_tree
    assert tree, "Дерево документа не должно быть пустым"
    titles = [_plain(node.title) for node in tree]
    assert any("Введение" in title for title in titles)
    introduction = next(node for node in tree if "Введение" in _plain(node.title))
    assert introduction.semantic_role == "introduction"
    assert introduction.page_start_est == 2
    assert introduction.page_end_est >= introduction.page_start_est
    for node in tree:
        assert node.heading_block_id
        assert node.level >= 1


def test_list_item_metadata(document: ParsedDocument) -> None:
    """Нумерованный пункт перечисления получает ListItemMetadata."""
    lists = [
        block
        for block in document.blocks
        if isinstance(block, ParagraphBlock) and block.list_info is not None
    ]
    assert lists, "Ожидался хотя бы один элемент списка"
    item = lists[0]
    assert item.list_info.mechanism == "manual_text_prefix"
    assert item.list_info.rendered_marker in {"1)", "1."}
    assert _plain(item.list_info.body_text_without_marker).startswith("первый пункт")
    assert item.list_info.num_fmt == "decimal"
    assert item.heading_info is None


def test_toc_entries_are_reconstructed(document: ParsedDocument) -> None:
    """Оглавление собирается в TocContainerBlock с номерами страниц и отточием."""
    toc_blocks = [block for block in document.blocks if isinstance(block, TocContainerBlock)]
    assert len(toc_blocks) == 1
    toc = toc_blocks[0]
    assert toc.page_start_est == 1
    assert len(toc.entries) >= 3
    titles = [entry.extracted_title for entry in toc.entries]
    assert any(title.startswith("Введение") for title in titles)
    pages = {entry.extracted_title: entry.extracted_page_str for entry in toc.entries}
    assert "2" in pages.values()
    assert "3" in pages.values()
    for entry in toc.entries:
        assert entry.has_dot_leader is True
        assert entry.raw_text
    matched = [entry for entry in toc.entries if entry.matched_heading_block_id]
    assert matched, "Записи оглавления должны сопоставляться с заголовками"
    assert toc.toc_heading_block_id is not None


def test_table_block_has_rows_cells_and_coordinates(document: ParsedDocument) -> None:
    """Таблица восстанавливается с координатами, строками и текстом ячеек."""
    tables = [block for block in document.blocks if isinstance(block, TableBlock)]
    assert len(tables) == 1
    table = tables[0]
    assert table.page_start_est == 3
    assert len(table.rows) == 3
    assert all(len(row.cells) == 2 for row in table.rows)
    assert table.rows[0].cells[0].text_combined == "Показатель"
    assert table.rows[1].cells[1].text_combined == "1200"
    assert table.grid_column_widths_mm and len(table.grid_column_widths_mm) == 2
    assert table.total_width_mm and table.total_width_mm > 90.0
    assert table.pdf_y_top_mm is not None and table.pdf_y_bottom_mm is not None
    assert table.pdf_x_left_mm is not None and table.pdf_x_right_mm is not None
    assert table.rows[1].page_start_est == 3
    # Текст ячеек не должен дублироваться обычными абзацами.
    paragraph_texts = [
        _plain(block.text_visible)
        for block in document.blocks
        if isinstance(block, ParagraphBlock)
    ]
    assert not any("1200" in text for text in paragraph_texts)


# ---------------------------------------------------------------------
# Сериализация и совместимость
# ---------------------------------------------------------------------


def test_json_round_trip(document: ParsedDocument) -> None:
    """model_dump_json -> model_validate_json сохраняет режим, страницы и аудит."""
    payload = document.model_dump_json()
    restored = ParsedDocument.model_validate_json(payload)
    assert restored.parse_mode == "pdf_only"
    assert restored.page_count == document.page_count
    assert restored.source == document.source
    assert restored.capabilities == document.capabilities
    assert len(restored.pdf_pages_audit) == len(document.pdf_pages_audit)
    assert len(restored.blocks) == len(document.blocks)
    assert restored.model_dump_json() == payload


def test_pdf_keys_survive_without_meta_pdf_filename(document: ParsedDocument) -> None:
    """pdf_*-ключи остаются в JSON, даже если meta не дублирует имя PDF."""
    payload = json.loads(document.model_dump_json())
    assert "pdf_pages_audit" in payload
    assert payload["meta"]["rendered_page_numbers_present"] is True
    assert payload["source"]["has_pdf"] is True
    assert payload["capabilities"]["pdf_layout_coordinates"] is True

    stripped = document.model_copy(deep=True)
    stripped.meta.pdf_filename = None
    stripped.meta.pdf_sha256 = None
    stripped_payload = json.loads(stripped.model_dump_json())
    assert "pdf_pages_audit" in stripped_payload
    assert stripped_payload["source"]["has_pdf"] is True


# ---------------------------------------------------------------------
# Крайние случаи и реальные файлы
# ---------------------------------------------------------------------


def test_pdf_without_text_layer_is_parsed(tmp_path: Path) -> None:
    """PDF без текстового слоя разбирается без исключений и без блоков."""
    path = tmp_path / "empty.pdf"
    document = pymupdf.open()
    page = document.new_page(width=_pt(PAGE_WIDTH_MM), height=_pt(PAGE_HEIGHT_MM))
    page.draw_line(pymupdf.Point(10, 10), pymupdf.Point(100, 100))
    document.save(str(path))
    document.close()

    parsed = PdfDocumentParser().parse(path)
    assert parsed.page_count.total_pages == 1
    assert parsed.page_count.method == "pdf_exact"
    assert parsed.blocks == []
    assert parsed.meta.word_stat_words == 0
    assert parsed.meta.rendered_page_numbers_present is False
    assert parsed.capabilities.exact_pagination is True


def test_missing_file_raises(tmp_path: Path) -> None:
    """Отсутствующий файл дает FileNotFoundError, а не пустой результат."""
    with pytest.raises(FileNotFoundError):
        parse_pdf(tmp_path / "absent.pdf")


def test_parser_accepts_path_in_constructor_and_method(synthetic_pdf: Path) -> None:
    """Путь PDF можно передать в конструктор или в parse()."""
    from_constructor = PdfDocumentParser(synthetic_pdf).parse()
    from_method = PdfDocumentParser().parse(synthetic_pdf)
    assert from_constructor.page_count.total_pages == from_method.page_count.total_pages
    assert from_constructor.source.pdf_filename == from_method.source.pdf_filename


MANUAL_TEST_DIR = Path(__file__).resolve().parent.parent / "manual_test"


@pytest.mark.parametrize("filename", ["2.pdf", "3.pdf"])
def test_real_manual_pdf_parses(filename: str) -> None:
    """Реальные PDF из manual_test разбираются без исключений и дают точные страницы."""
    path = MANUAL_TEST_DIR / filename
    if not path.exists():
        pytest.skip(f"Нет файла {path}")

    parsed = parse_pdf(path)
    assert parsed.parse_mode == "pdf_only"
    assert parsed.source.has_pdf is True
    assert parsed.source.has_docx is False
    assert parsed.capabilities.exact_pagination is True
    assert parsed.capabilities.pdf_layout_coordinates is True
    assert parsed.capabilities.styles_parsed is False

    with pymupdf.open(str(path)) as source:
        expected_pages = source.page_count
    assert parsed.page_count.total_pages == expected_pages
    assert parsed.page_count.method == "pdf_exact"
    assert len(parsed.pdf_pages_audit) == expected_pages
    assert parsed.meta.word_stat_pages == expected_pages
    assert parsed.meta.rendered_page_numbers_present is True
    assert parsed.sections, "Должна быть хотя бы одна оцененная секция"
    assert parsed.sections[0].page_start_est == 1
    assert parsed.sections[0].page_end_est == expected_pages
    assert parsed.blocks, "Текстовый слой должен дать блоки"
    assert len(parsed.blocks) > expected_pages
    assert parsed.document_tree, "Дерево документа должно строиться по заголовкам"

    headings = [
        block
        for block in parsed.blocks
        if isinstance(block, ParagraphBlock) and block.heading_info is not None
    ]
    assert headings
    assert any(block.heading_info.level == 1 for block in headings)
    for block in parsed.blocks:
        if isinstance(block, ParagraphBlock):
            assert 1 <= block.page_start_est <= expected_pages
            assert block.page_start_est <= block.page_end_est <= expected_pages
    ParsedDocument.model_validate_json(parsed.model_dump_json())
