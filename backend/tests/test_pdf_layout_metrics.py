"""Тесты оценки межстрочного интервала PDF-слоя (``parser.pdf.layout``).

Проверяется эвристика без реальных PDF: синтетические ``PdfDocument``,
``PdfPage``, ``PdfLine`` и ``PdfSpan`` с известным кеглем и шагом строк.
Единицы: кегль и координаты bbox — пункты (pt), шаг строк и поля — миллиметры
(мм). Ожидаемое отношение шага к кеглю для полуторного интервала — 1.5.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

import pytest

from parser.pdf.extractor import PdfDocument, PdfLine, PdfPage, PdfSpan
from parser.pdf.layout import (
    MAX_LEADING_RATIO,
    MIN_LEADING_RATIO,
    PageZones,
    PdfLayoutEstimator,
)

PT_TO_MM = 25.4 / 72.0

PAGE_WIDTH_PT = 595.28
PAGE_HEIGHT_PT = 841.89
BODY_SIZE_PT = 14.0
BODY_PITCH_PT = 21.0  # 1.5 × 14 pt — полуторный интервал
FIRST_BODY_TOP_PT = 120.0
TEXT_WIDTH_PT = 400.0
PAGE_NUMBER_TOP_PT = 810.0


def make_line(
    text: str,
    *,
    y0_pt: float,
    page_num: int = 1,
    size_pt: float = BODY_SIZE_PT,
    x0_pt: float = 85.0,
    in_table: bool = False,
) -> PdfLine:
    """Синтетическая строка PDF: один спан, bbox от верхнего края ``y0_pt``."""
    bbox = (x0_pt, y0_pt, x0_pt + TEXT_WIDTH_PT, y0_pt + size_pt * 1.15)
    span = PdfSpan(
        bbox_pt=bbox,
        text=text,
        font_name="Times New Roman",
        font_size_pt=size_pt,
    )
    return PdfLine(bbox_pt=bbox, page_num=page_num, spans=[span], in_table=in_table)


def make_page(page_num: int, lines: Sequence[PdfLine]) -> PdfPage:
    """Синтетический лист A4 с заданными строками."""
    return PdfPage(
        page_num=page_num,
        width_pt=PAGE_WIDTH_PT,
        height_pt=PAGE_HEIGHT_PT,
        lines=list(lines),
    )


def body_page(
    page_num: int,
    *,
    body_count: int = 12,
    size_pt: float = BODY_SIZE_PT,
    pitch_pt: float = BODY_PITCH_PT,
    first_y0_pt: float = FIRST_BODY_TOP_PT,
    with_number: bool = True,
) -> PdfPage:
    """Лист с блоком строк тела и напечатанным номером внизу."""
    lines = [
        make_line(
            f"Строка тела {idx + 1}",
            y0_pt=first_y0_pt + idx * pitch_pt,
            page_num=page_num,
            size_pt=size_pt,
        )
        for idx in range(body_count)
    ]
    if with_number:
        lines.append(
            make_line(str(page_num), y0_pt=PAGE_NUMBER_TOP_PT, page_num=page_num, x0_pt=290.0)
        )
    return make_page(page_num, lines)


def synthetic_document(
    *,
    page_count: int = 3,
    body_count: int = 12,
    pitch_pt: float = BODY_PITCH_PT,
    with_number: bool = True,
) -> PdfDocument:
    """Документ из одинаковых листов: известный кегль и известный шаг строк."""
    pages = [
        body_page(
            page_num,
            body_count=body_count,
            pitch_pt=pitch_pt,
            with_number=with_number,
        )
        for page_num in range(1, page_count + 1)
    ]
    return PdfDocument(path=Path("synthetic.pdf"), pages=pages)


def make_estimator(document: Optional[PdfDocument] = None) -> PdfLayoutEstimator:
    """Оценщик вёрстки: документ по умолчанию пустой."""
    return PdfLayoutEstimator(document if document is not None else PdfDocument(path=Path("synthetic.pdf")))


def zone_with(lines: Sequence[PdfLine], page_num: int = 1) -> PageZones:
    """Зоны листа, где все строки уже отнесены к телу документа."""
    return PageZones(
        page_num=page_num,
        width_mm=PAGE_WIDTH_PT * PT_TO_MM,
        height_mm=PAGE_HEIGHT_PT * PT_TO_MM,
        body_lines=list(lines),
    )


# ---------------------------------------------------------------------
# Сводная оценка документа
# ---------------------------------------------------------------------


def test_known_pitch_and_size_estimates_document_ratio() -> None:
    """Шаг 21 pt при кегле 14 pt даёт интервал 1.5 и шаг 7.41 мм."""
    document = synthetic_document()
    layout = PdfLayoutEstimator(document).estimate()

    assert layout.dominant_font_size_pt == pytest.approx(BODY_SIZE_PT)
    assert layout.median_leading_ratio == pytest.approx(1.5, abs=0.01)
    assert layout.median_line_pitch_mm == pytest.approx(BODY_PITCH_PT * PT_TO_MM, abs=0.01)
    # Три листа по 12 строк дают по 11 пар соседних строк на каждом.
    assert layout.leading_sample_count == 3 * (12 - 1)
    assert sorted(layout.page_leading) == [1, 2, 3]
    assert layout.page_leading[2].sample_count == 11
    assert layout.page_leading[2].leading_ratio == pytest.approx(1.5, abs=0.01)


def test_audit_of_page_carries_leading_fields() -> None:
    """Постраничный аудит получает кегль, шаг и интервал листа."""
    layout = PdfLayoutEstimator(synthetic_document(page_count=2)).estimate()

    audit = layout.pages_audit[0]
    assert audit.page_num == 1
    assert audit.dominant_font_size_pt == pytest.approx(BODY_SIZE_PT)
    assert audit.line_pitch_mm == pytest.approx(BODY_PITCH_PT * PT_TO_MM, abs=0.01)
    assert audit.leading_ratio == pytest.approx(1.5, abs=0.01)
    assert audit.leading_sample_count == 11


def test_document_median_ignores_short_page_weight() -> None:
    """Медиана считается по пулу пар: лист с двойным интервалом не перевешивает."""
    pages = [
        body_page(1),
        body_page(2),
        body_page(3, pitch_pt=2.0 * BODY_SIZE_PT),
        make_page(4, [make_line("Всего одна строка", y0_pt=FIRST_BODY_TOP_PT, page_num=4)]),
    ]
    layout = PdfLayoutEstimator(PdfDocument(path=Path("synthetic.pdf"), pages=pages)).estimate()

    assert layout.page_leading[3].leading_ratio == pytest.approx(2.0, abs=0.01)
    assert layout.page_leading[4].sample_count == 0
    assert layout.page_leading[4].leading_ratio is None
    # 22 пары с интервалом 1.5 и 11 пар с интервалом 2.0: медиана остаётся 1.5.
    assert layout.median_leading_ratio == pytest.approx(1.5, abs=0.01)
    assert layout.leading_sample_count == 22 + 11


def test_empty_document_has_no_leading_estimate() -> None:
    """Без текста интервал не оценивается, а не подменяется нулём."""
    layout = PdfLayoutEstimator(PdfDocument(path=Path("empty.pdf"))).estimate()

    assert layout.dominant_font_size_pt is None
    assert layout.median_leading_ratio is None
    assert layout.median_line_pitch_mm is None
    assert layout.leading_sample_count == 0


# ---------------------------------------------------------------------
# Отбор пар строк
# ---------------------------------------------------------------------


def test_heading_and_caption_sizes_are_skipped() -> None:
    """Строки другого кегля не образуют пар и не смещают оценку."""
    heading = make_line("Заголовок раздела", y0_pt=FIRST_BODY_TOP_PT - 40.0, size_pt=20.0)
    body = [
        make_line("Строка тела", y0_pt=FIRST_BODY_TOP_PT + idx * BODY_PITCH_PT)
        for idx in range(6)
    ]
    caption = make_line(
        "Подпись к рисунку",
        y0_pt=FIRST_BODY_TOP_PT + 6 * BODY_PITCH_PT,
        size_pt=10.0,
    )
    estimator = make_estimator()

    pitches, ratios = estimator._leading_samples(zone_with([heading, *body, caption]), BODY_SIZE_PT)

    assert len(ratios) == 5  # 6 строк тела → 5 пар
    # Допуск 0.01: координаты в мм округлены до сотых, шаг «плывёт» на 0.01 мм.
    assert all(ratio == pytest.approx(1.5, abs=0.01) for ratio in ratios)
    assert all(pitch == pytest.approx(BODY_PITCH_PT * PT_TO_MM, abs=0.01) for pitch in pitches)


def test_page_numbers_are_skipped() -> None:
    """Номера страниц вида 1…9999 не участвуют в измерении шага."""
    body = [
        make_line(f"Строка тела {idx + 1}", y0_pt=FIRST_BODY_TOP_PT + idx * BODY_PITCH_PT)
        for idx in range(8)
    ]
    number = make_line("5", y0_pt=PAGE_NUMBER_TOP_PT, x0_pt=290.0)
    long_number = make_line("1234", y0_pt=PAGE_NUMBER_TOP_PT + 30.0, x0_pt=290.0)
    estimator = make_estimator()

    _pitches, ratios = estimator._leading_samples(zone_with([*body, number, long_number]), BODY_SIZE_PT)

    assert len(ratios) == 7  # 8 строк тела → 7 пар, номера отброшены
    assert ratios[0] == pytest.approx(1.5, abs=0.01)


def test_table_lines_are_skipped() -> None:
    """Строка таблицы внутри абзаца не разрывает пару соседних строк тела."""
    body = [
        make_line(f"Строка тела {idx + 1}", y0_pt=FIRST_BODY_TOP_PT + idx * BODY_PITCH_PT)
        for idx in range(6)
    ]
    table_line = make_line(
        "Ячейка таблицы",
        y0_pt=FIRST_BODY_TOP_PT + 2 * BODY_PITCH_PT + 5.0,
        in_table=True,
    )
    estimator = make_estimator()

    _pitches, ratios = estimator._leading_samples(zone_with([*body, table_line]), BODY_SIZE_PT)

    assert len(ratios) == 5
    assert ratios[0] == pytest.approx(1.5, abs=0.01)


def test_values_outside_window_are_skipped() -> None:
    """Отбивка между блоками и слишком плотные строки не считаются интервалом."""
    first_block = [
        make_line(f"Блок 1, строка {idx + 1}", y0_pt=FIRST_BODY_TOP_PT + idx * BODY_PITCH_PT)
        for idx in range(5)
    ]
    gap_pt = 60.0  # ≈ 4.3 кегля — отбивка, а не межстрочный интервал
    second_top = FIRST_BODY_TOP_PT + 5 * BODY_PITCH_PT + gap_pt
    second_block = [
        make_line(f"Блок 2, строка {idx + 1}", y0_pt=second_top + idx * BODY_PITCH_PT)
        for idx in range(5)
    ]
    tight = [
        make_line("Плотная строка 1", y0_pt=20.0),
        make_line("Плотная строка 2", y0_pt=27.0),  # 7 pt ≈ 0.5 кегля
    ]
    estimator = make_estimator()

    _pitches, ratios = estimator._leading_samples(
        zone_with([*tight, *first_block, *second_block]), BODY_SIZE_PT
    )

    assert len(ratios) == 4 + 4  # только внутренние пары двух блоков
    assert all(ratio == pytest.approx(1.5, abs=0.01) for ratio in ratios)
    assert all(MIN_LEADING_RATIO <= ratio <= MAX_LEADING_RATIO for ratio in ratios)


def test_no_pairs_when_dominant_size_unknown() -> None:
    """Без доминирующего кегля оценивать нечего."""
    pitches, ratios = make_estimator()._leading_samples(zone_with([]), None)

    assert pitches == [] and ratios == []


# ---------------------------------------------------------------------
# Доминирующий кегль
# ---------------------------------------------------------------------


def test_dominant_font_size_counts_characters() -> None:
    """Доминирующий кегль определяется по числу символов, а не числу строк."""
    heading = make_line("Заголовок", y0_pt=100.0, size_pt=20.0)
    body = [
        make_line("Длинная строка основного текста", y0_pt=140.0 + idx * BODY_PITCH_PT)
        for idx in range(10)
    ]
    long_table_line = make_line(
        "Очень длинная строка таблицы " * 8,
        y0_pt=400.0,
        size_pt=30.0,
        in_table=True,
    )
    long_page_number = make_line("7", y0_pt=PAGE_NUMBER_TOP_PT, size_pt=30.0)
    document = PdfDocument(
        path=Path("synthetic.pdf"),
        pages=[make_page(1, [heading, *body, long_table_line, long_page_number])],
    )

    assert make_estimator(document)._dominant_font_size() == pytest.approx(BODY_SIZE_PT)


# ---------------------------------------------------------------------
# Прокидка в отчёт
# ---------------------------------------------------------------------


def test_report_carries_leading_metrics() -> None:
    """Метаданные и аудит отчёта содержат оценку интервала без смены версии схемы."""
    from parser.pdf.parser import PdfDocumentParser
    from parser.pdf.structure import PdfStructure

    document = synthetic_document(page_count=2)
    layout = PdfLayoutEstimator(document).estimate()
    structure = PdfStructure(
        blocks=[],
        visuals={},
        document_tree=[],
        full_text=document.text(),
    )

    parsed = PdfDocumentParser(Path("synthetic.pdf"))._assemble(document, layout, structure)

    assert parsed.schema_version == "3.0.0"
    assert parsed.meta.pdf_median_leading_ratio == pytest.approx(1.5, abs=0.01)
    assert parsed.meta.pdf_median_line_pitch_mm == pytest.approx(
        BODY_PITCH_PT * PT_TO_MM, abs=0.01
    )
    assert parsed.meta.pdf_dominant_font_size_pt == pytest.approx(BODY_SIZE_PT)
    assert parsed.meta.pdf_leading_sample_count == 2 * 11
    assert parsed.pdf_pages_audit[0].leading_ratio == pytest.approx(1.5, abs=0.01)

    payload = parsed.model_dump(mode="json")
    assert payload["meta"]["pdf_median_leading_ratio"] == pytest.approx(1.5, abs=0.01)
