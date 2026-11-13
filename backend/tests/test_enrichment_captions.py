"""Синтетические тесты подписей рисунков, таблиц и приложений.

Проверяется распознавание форм «Рисунок 1 — Схема», «Рис. 1. Схема»,
«Таблица 2 — Данные», «Приложение А. Текст», защита от ложных срабатываний
(строка без номера) и привязка рисунка к иллюстрации на той же странице.
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from parser.core.schema import (
    DocumentMeta,
    GlobalSettings,
    PageCountInfo,
    ParagraphBlock,
    ParsedDocument,
    ThemeData,
    VisualObject,
)
from parser.enrichment.captions import extract_captions


def _paragraph(
    block_id: str,
    text: str,
    page: int = 1,
    *,
    pdf_y_top_mm: Optional[float] = None,
    pdf_y_bottom_mm: Optional[float] = None,
) -> ParagraphBlock:
    """Создает абзац основного текста с заданной страницей и координатами."""
    return ParagraphBlock(
        block_id=block_id,
        section_idx=0,
        page_start_est=page,
        page_end_est=page,
        text_raw=text,
        text_visible=text,
        pdf_y_top_mm=pdf_y_top_mm,
        pdf_y_bottom_mm=pdf_y_bottom_mm,
    )


def _visual(
    visual_id: str,
    page: int = 1,
    *,
    caption_block_id: Optional[str] = None,
    pdf_y_top_mm: Optional[float] = None,
    pdf_y_bottom_mm: Optional[float] = None,
) -> VisualObject:
    """Создает иллюстрацию с минимальным набором обязательных полей."""
    return VisualObject(
        visual_id=visual_id,
        parent_block_id="p_0001",
        section_idx=0,
        page_est=page,
        visual_kind="raster_image",
        placement="inline",
        width_mm=100.0,
        height_mm=50.0,
        caption_block_id=caption_block_id,
        pdf_y_top_mm=pdf_y_top_mm,
        pdf_y_bottom_mm=pdf_y_bottom_mm,
    )


def _document(
    paragraphs: Iterable[ParagraphBlock],
    visuals: Iterable[VisualObject] = (),
) -> ParsedDocument:
    """Собирает минимальный ParsedDocument для проверки слоя подписей."""
    blocks: List[ParagraphBlock] = list(paragraphs)
    visual_map = {visual.visual_id: visual for visual in visuals}
    pages = max((block.page_end_est for block in blocks), default=1)
    return ParsedDocument(
        parse_mode="pdf_only",
        meta=DocumentMeta(filename="vkr.pdf", file_sha256="0" * 64),
        settings=GlobalSettings(),
        theme=ThemeData(),
        page_count=PageCountInfo(
            total_pages=pages, method="geometric_estimation", confidence="low"
        ),
        blocks=blocks,
        visuals=visual_map,
    )


def test_figure_caption_fields_are_extracted() -> None:
    """«Рисунок 1 — Схема» дает сущность рисунка со всеми полями подписи."""
    document = _document([_paragraph("p_0001", "Рисунок 1 — Схема", page=2)])

    captions = extract_captions(document)

    assert len(captions) == 1
    caption = captions[0]
    assert caption.entity_id == "fig_001"
    assert caption.entity_type == "figure"
    assert caption.caption_block_id == "p_0001"
    assert caption.page_est == 2
    assert caption.prefix_word.lower() == "рисунок"
    assert caption.number_str == "1"
    assert caption.separator == "—"
    assert caption.title_text == "Схема"
    assert caption.has_trailing_dot is False
    assert caption.numbering_scheme == "continuous_arabic"
    # Упоминания в тексте — ответственность отдельного этапа обогащения.
    assert caption.is_mentioned_in_text is False


def test_short_figure_prefix_and_trailing_dot_are_recognized() -> None:
    """«Рис. 3. График.» распознается, завершающая точка фиксируется отдельно."""
    document = _document([_paragraph("p_0001", "Рис. 3. График.", page=1)])

    captions = extract_captions(document)

    assert len(captions) == 1
    caption = captions[0]
    assert caption.entity_id == "fig_001"
    assert caption.prefix_word.lower() == "рис."
    assert caption.number_str == "3"
    assert caption.separator == "."
    assert caption.title_text == "График"
    assert caption.has_trailing_dot is True


def test_table_caption_is_extracted() -> None:
    """«Таблица 2 — Данные» дает сущность таблицы, а не рисунка."""
    document = _document([_paragraph("p_0001", "Таблица 2 — Данные")])

    captions = extract_captions(document)

    assert len(captions) == 1
    caption = captions[0]
    assert caption.entity_id == "tbl_001"
    assert caption.entity_type == "table"
    assert caption.prefix_word.lower() == "таблица"
    assert caption.number_str == "2"
    assert caption.separator == "—"
    assert caption.title_text == "Данные"
    assert caption.numbering_scheme == "continuous_arabic"


def test_appendix_caption_is_extracted() -> None:
    """«Приложение А. Текст» дает приложение со схемой appendix_based."""
    document = _document([_paragraph("p_0001", "Приложение А. Текст", page=7)])

    captions = extract_captions(document)

    assert len(captions) == 1
    caption = captions[0]
    assert caption.entity_id == "app_001"
    assert caption.entity_type == "appendix"
    assert caption.caption_block_id == "p_0001"
    assert caption.page_est == 7
    assert caption.prefix_word.lower() == "приложение"
    assert caption.number_str == "А"
    assert caption.separator == "."
    assert caption.title_text == "Текст"
    assert caption.has_trailing_dot is False
    assert caption.numbering_scheme == "appendix_based"


def test_strings_without_number_are_not_captions() -> None:
    """Строки без номера и предложение основного текста подписями не считаются."""
    document = _document(
        [
            _paragraph("p_0001", "Рисунок не найден"),
            _paragraph("p_0002", "Таблица не заполнена"),
            _paragraph("p_0003", "Приложение"),
            _paragraph("p_0004", "Приложение А содержит описание стенда."),
        ]
    )

    assert extract_captions(document) == []


def test_entity_ids_are_sequential_within_type() -> None:
    """Идентификаторы сквозные внутри типа и не зависят от таблиц."""
    document = _document(
        [
            _paragraph("p_0001", "Рисунок 1 — Первый"),
            _paragraph("p_0002", "Таблица 1 — Данные"),
            _paragraph("p_0003", "Рисунок 2 — Второй"),
        ]
    )

    captions = extract_captions(document)

    assert [caption.entity_id for caption in captions] == [
        "fig_001",
        "tbl_001",
        "fig_002",
    ]


def test_figure_is_linked_to_nearest_visual_on_same_page() -> None:
    """Из двух иллюстраций одной страницы выбирается ближайшая по pdf_y_*."""
    caption = _paragraph(
        "p_0002", "Рисунок 1 — Схема", page=2, pdf_y_top_mm=120.0, pdf_y_bottom_mm=130.0
    )
    far_visual = _visual("vis_0001", page=2, pdf_y_top_mm=20.0, pdf_y_bottom_mm=60.0)
    near_visual = _visual("vis_0002", page=2, pdf_y_top_mm=140.0, pdf_y_bottom_mm=180.0)
    document = _document([_paragraph("p_0001", "Текст", page=2), caption], [far_visual, near_visual])

    captions = extract_captions(document)

    assert len(captions) == 1
    assert captions[0].target_object_id == "vis_0002"
    # Связь двусторонняя: иллюстрация знает свою подпись.
    assert near_visual.caption_block_id == "p_0002"
    assert far_visual.caption_block_id is None


def test_figure_skips_other_pages_and_already_captioned_visuals() -> None:
    """Иллюстрация с другой страницы или уже занятая подписью не переиспользуется."""
    caption = _paragraph("p_0002", "Рисунок 1 — Схема", page=2)
    other_page = _visual("vis_0001", page=1)
    captioned = _visual("vis_0002", page=2, caption_block_id="p_0099")
    document = _document([caption], [other_page, captioned])

    captions = extract_captions(document)

    assert len(captions) == 1
    assert captions[0].target_object_id is None
    assert other_page.caption_block_id is None
    assert captioned.caption_block_id == "p_0099"


def test_figure_keeps_visual_already_linked_to_same_caption() -> None:
    """Повторный вызов не теряет связь, уже проставленную этой же подписью."""
    caption = _paragraph("p_0001", "Рисунок 1 — Схема", page=1)
    visual = _visual("vis_0001", page=1, caption_block_id="p_0001")
    document = _document([caption], [visual])

    first = extract_captions(document)
    second = extract_captions(document)

    assert first[0].target_object_id == "vis_0001"
    assert second[0].target_object_id == "vis_0001"


def test_two_figures_on_same_page_get_different_visuals() -> None:
    """Каждая подпись забирает свою иллюстрацию: одна и та же не достается двум."""
    first = _paragraph(
        "p_0001", "Рисунок 1 — Первый", page=1, pdf_y_top_mm=110.0, pdf_y_bottom_mm=120.0
    )
    second = _paragraph(
        "p_0002", "Рисунок 2 — Второй", page=1, pdf_y_top_mm=210.0, pdf_y_bottom_mm=220.0
    )
    upper = _visual("vis_0001", page=1, pdf_y_top_mm=20.0, pdf_y_bottom_mm=100.0)
    lower = _visual("vis_0002", page=1, pdf_y_top_mm=120.0, pdf_y_bottom_mm=200.0)
    document = _document([first, second], [upper, lower])

    captions = extract_captions(document)

    assert [caption.target_object_id for caption in captions] == ["vis_0002", "vis_0001"]


def test_whitespace_separator_is_recognized() -> None:
    """Подпись без тире («Таблица 1  Анализ», «Рис 1.Схема») тоже распознаётся."""
    document = _document(
        [
            _paragraph("p_0001", "Таблица 1  Анализ рентабельности"),
            _paragraph("p_0002", "Рис 1.Схема управления"),
            _paragraph("p_0003", "Таблица 2Данные без разделителя"),
        ]
    )

    captions = extract_captions(document)

    assert [(caption.entity_type, caption.number_str) for caption in captions] == [
        ("table", "1"),
        ("figure", "1"),
    ]
    assert captions[0].title_text == "Анализ рентабельности"
    assert captions[1].title_text == "Схема управления"


def test_sentence_with_caption_prefix_is_not_a_caption() -> None:
    """«Таблица 1 показывает…» — предложение основного текста, а не подпись."""
    document = _document(
        [
            _paragraph("p_0001", "Таблица 1 показывает динамику показателей."),
            _paragraph("p_0002", "Рисунок 1 демонстрирует рост выручки."),
        ]
    )

    assert extract_captions(document) == []


def test_duplicate_number_keeps_caption_with_separator() -> None:
    """Из двух кандидатов с одним номером остаётся подпись с разделителем."""
    document = _document(
        [
            _paragraph("p_0001", "Таблица 1 Данные за год"),
            _paragraph("p_0002", "Таблица 1 — Данные за год"),
        ]
    )

    captions = extract_captions(document)

    assert len(captions) == 1
    assert captions[0].entity_id == "tbl_001"
    assert captions[0].number_str == "1"
    assert captions[0].separator == "—"
    assert captions[0].title_text == "Данные за год"
