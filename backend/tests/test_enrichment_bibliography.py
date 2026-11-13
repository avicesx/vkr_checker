"""Извлечение библиографии общим слоем обогащения ParsedDocument.

Документы собираются синтетически (блоки + дерево документа), поэтому тесты
не зависят от бинарных образцов и от DOCX/PDF-конвейеров. Проверяются
нумерованные записи, производные поля (год, URL, страницы, ISBN/ISSN),
категория источника, склейка многострочной записи, обход раздела по
заголовкам (когда дерево не размечено прямыми блоками) и пустой раздел.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

import pytest

from parser.enrichment import extract_bibliography
from parser.core.schema import (
    DocumentMeta,
    DocumentTreeNode,
    GlobalSettings,
    HeadingMetadata,
    ListItemMetadata,
    PageCountInfo,
    ParagraphBlock,
    ParsedDocument,
    ThemeData,
)


# ---------------------------------------------------------------------
# Сборка документов
# ---------------------------------------------------------------------


def _paragraph(
    block_id: str,
    text: str,
    *,
    heading: bool = False,
    level: int = 1,
    list_info: Optional[ListItemMetadata] = None,
) -> ParagraphBlock:
    """Создает абзац основного текста (при необходимости — заголовок)."""
    heading_info = None
    if heading:
        heading_info = HeadingMetadata(
            detection_confidence="structural_keyword_match",
            heading_kind="structural_unnumbered",
            level=level,
            clean_title_text=text,
        )

    return ParagraphBlock(
        block_id=block_id,
        section_idx=0,
        page_start_est=1,
        page_end_est=1,
        text_raw=text,
        text_visible=text,
        heading_info=heading_info,
        list_info=list_info,
    )


def _tree_node(
    node_id: str,
    title: str,
    *,
    heading_block_id: Optional[str] = None,
    role: str = "bibliography",
    level: int = 1,
    direct_block_ids: Sequence[str] = (),
    children: Sequence[DocumentTreeNode] = (),
) -> DocumentTreeNode:
    """Создает узел дерева документа с ролью и прямыми блоками."""
    return DocumentTreeNode(
        node_id=node_id,
        heading_block_id=heading_block_id,
        title=title,
        level=level,
        semantic_role=role,  # type: ignore[arg-type]
        page_start_est=1,
        page_end_est=1,
        direct_block_ids=list(direct_block_ids),
        children=list(children),
    )


def _document(
    blocks: Iterable[ParagraphBlock],
    tree: Sequence[DocumentTreeNode] = (),
) -> ParsedDocument:
    """Собирает минимальный ParsedDocument для проверки библиографии."""
    block_list: List[ParagraphBlock] = list(blocks)
    return ParsedDocument(
        meta=DocumentMeta(filename="vkr.docx", file_sha256="0" * 64),
        settings=GlobalSettings(),
        theme=ThemeData(),
        page_count=PageCountInfo(
            total_pages=1, method="docx_native_breaks", confidence="high"
        ),
        blocks=block_list,
        document_tree=list(tree),
    )


# ---------------------------------------------------------------------
# Нумерованные записи и производные поля
# ---------------------------------------------------------------------


def test_numbered_entries_and_derived_fields() -> None:
    """Нумерованные записи дают order_index, префикс и разобранные поля."""
    blocks = [
        _paragraph("p_0001", "СПИСОК ЛИТЕРАТУРЫ", heading=True),
        _paragraph(
            "p_0002",
            "1. Иванов И. И. Теория алгоритмов : учебник. — М. : Наука, 2018. — 320 с. "
            "— ISBN 978-5-02-040123-4.",
        ),
        _paragraph(
            "p_0003",
            "2) Петров П. П. Обзор методов // Вестник науки. — 2020. — С. 12-15.",
        ),
        _paragraph(
            "p_0004",
            "3. ГОСТ Р 7.0.5-2008. Библиографическая ссылка. — М. : Стандартинформ, 2008.",
        ),
        _paragraph(
            "p_0005",
            "4. Электронный ресурс. — URL: https://example.org/article?id=7. "
            "— Дата обращения: 01.02.2021.",
        ),
    ]
    tree = [
        _tree_node(
            "node_001",
            "СПИСОК ЛИТЕРАТУРЫ",
            heading_block_id="p_0001",
            direct_block_ids=["p_0002", "p_0003", "p_0004", "p_0005"],
        )
    ]

    entries = extract_bibliography(_document(blocks, tree))

    assert [entry.order_index for entry in entries] == [1, 2, 3, 4]
    assert [entry.bib_id for entry in entries] == [
        "bib_001",
        "bib_002",
        "bib_003",
        "bib_004",
    ]
    assert [entry.raw_number_prefix for entry in entries] == ["1.", "2)", "3.", "4."]
    assert [entry.block_id for entry in entries] == ["p_0002", "p_0003", "p_0004", "p_0005"]

    # Префикс не попадает в raw_text.
    assert entries[0].raw_text.startswith("Иванов И. И.")
    assert not entries[1].raw_text.startswith("2)")

    assert [entry.year_extracted for entry in entries] == [2018, 2020, 2008, 2021]
    assert entries[0].pages_extracted == "320 с."
    assert entries[1].pages_extracted == "С. 12-15"
    assert entries[0].isbn_or_issn is not None
    assert "978" in entries[0].isbn_or_issn

    assert entries[3].urls == ["https://example.org/article?id=7"]
    assert entries[0].urls == []

    # Категории: ISBN — книга, // без URL — unknown, ГОСТ, URL.
    assert entries[0].detected_category == "book_monograph"
    assert entries[1].detected_category == "unknown"
    assert entries[2].detected_category == "standard_gost"
    assert entries[3].detected_category == "electronic_resource"


def test_url_forms_and_trailing_punctuation() -> None:
    """Распознаются http(s)- и www-URL, хвостовая пунктуация отбрасывается."""
    blocks = [
        _paragraph("p_0001", "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ", heading=True),
        _paragraph(
            "p_0002",
            "1. Сайт вуза. — URL: www.example.ru/page, https://example.org/a?b=1.",
        ),
    ]
    tree = [
        _tree_node(
            "node_001",
            "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ",
            heading_block_id="p_0001",
            direct_block_ids=["p_0002"],
        )
    ]

    entries = extract_bibliography(_document(blocks, tree))

    assert len(entries) == 1
    assert entries[0].urls == ["www.example.ru/page", "https://example.org/a?b=1"]
    assert entries[0].detected_category == "electronic_resource"


def test_prefix_forms_set_order_index() -> None:
    """Квадратные скобки и круглая скобка дают номер из префикса."""
    blocks = [
        _paragraph("p_0001", "ЛИТЕРАТУРА", heading=True),
        _paragraph("p_0002", "[3] Иванов И. И. Книга. — 2017. — 100 с."),
        _paragraph("p_0003", "5) Петров П. П. Статья. — 2019. — 90 с."),
    ]
    tree = [
        _tree_node(
            "node_001",
            "ЛИТЕРАТУРА",
            heading_block_id="p_0001",
            direct_block_ids=["p_0002", "p_0003"],
        )
    ]

    entries = extract_bibliography(_document(blocks, tree))

    assert [entry.order_index for entry in entries] == [3, 5]
    assert [entry.bib_id for entry in entries] == ["bib_003", "bib_005"]
    assert [entry.raw_number_prefix for entry in entries] == ["[3]", "5)"]


def test_openxml_list_marker_is_used_as_prefix() -> None:
    """Автонумерация Word: маркер списка становится raw_number_prefix."""
    list_info = ListItemMetadata(
        mechanism="openxml_numPr",
        rendered_marker="1.",
        body_text_without_marker="Иванов И. И. Книга. — М. : Наука, 2018. — 120 с.",
        first_letter_case="digit",
        ending_punctuation="period",
    )
    blocks = [
        _paragraph("p_0001", "СПИСОК ЛИТЕРАТУРЫ", heading=True),
        _paragraph(
            "p_0002",
            "1. Иванов И. И. Книга. — М. : Наука, 2018. — 120 с.",
            list_info=list_info,
        ),
    ]
    tree = [
        _tree_node(
            "node_001",
            "СПИСОК ЛИТЕРАТУРЫ",
            heading_block_id="p_0001",
            direct_block_ids=["p_0002"],
        )
    ]

    entries = extract_bibliography(_document(blocks, tree))

    assert len(entries) == 1
    assert entries[0].raw_number_prefix == "1."
    assert entries[0].order_index == 1
    assert entries[0].raw_text.startswith("Иванов И. И.")
    assert entries[0].year_extracted == 2018
    assert entries[0].pages_extracted == "120 с."


# ---------------------------------------------------------------------
# Многострочные записи и пустой раздел
# ---------------------------------------------------------------------


def test_multiline_entry_is_glued_to_previous() -> None:
    """Абзац без номера после записи склеивается с ней, а не создает новую."""
    blocks = [
        _paragraph("p_0001", "СПИСОК ЛИТЕРАТУРЫ", heading=True),
        _paragraph("p_0002", "1. Иванов И. И. Теория алгоритмов."),
        _paragraph("p_0003", "М. : Наука, 2018. — 320 с."),
        _paragraph("p_0004", "2. Петров П. П. Методы оптимизации."),
        _paragraph("p_0005", "СПб. : Питер, 2019. — 210 с."),
    ]
    tree = [
        _tree_node(
            "node_001",
            "СПИСОК ЛИТЕРАТУРЫ",
            heading_block_id="p_0001",
            direct_block_ids=["p_0002", "p_0003", "p_0004", "p_0005"],
        )
    ]

    entries = extract_bibliography(_document(blocks, tree))

    assert len(entries) == 2
    assert entries[0].block_id == "p_0002"
    assert "Теория алгоритмов." in entries[0].raw_text
    assert "М. : Наука, 2018. — 320 с." in entries[0].raw_text
    assert entries[0].year_extracted == 2018
    assert entries[0].pages_extracted == "320 с."

    assert entries[1].block_id == "p_0004"
    assert entries[1].order_index == 2
    assert "Методы оптимизации." in entries[1].raw_text
    assert "СПб. : Питер, 2019. — 210 с." in entries[1].raw_text
    assert entries[1].year_extracted == 2019


def test_empty_bibliography_returns_empty_list() -> None:
    """Раздел без записей дает пустой список."""
    blocks = [_paragraph("p_0001", "СПИСОК ЛИТЕРАТУРЫ", heading=True)]
    tree = [
        _tree_node(
            "node_001",
            "СПИСОК ЛИТЕРАТУРЫ",
            heading_block_id="p_0001",
        )
    ]

    assert extract_bibliography(_document(blocks, tree)) == []


def test_document_without_bibliography_returns_empty_list() -> None:
    """Без раздела литературы извлечение возвращает пустой список."""
    blocks = [
        _paragraph("p_0001", "ВВЕДЕНИЕ", heading=True),
        _paragraph("p_0002", "Основной текст без списка литературы."),
    ]
    tree = [
        _tree_node(
            "node_001",
            "ВВЕДЕНИЕ",
            heading_block_id="p_0001",
            role="introduction",
            direct_block_ids=["p_0002"],
        )
    ]

    assert extract_bibliography(_document(blocks, tree)) == []


# ---------------------------------------------------------------------
# Раздел, размеченный только заголовками (без direct_block_ids)
# ---------------------------------------------------------------------


def test_section_range_is_used_when_direct_block_ids_are_empty() -> None:
    """Диапазон раздела ограничен следующим заголовком того же уровня."""
    blocks = [
        _paragraph("p_0001", "СПИСОК ЛИТЕРАТУРЫ", heading=True),
        _paragraph("p_0002", "1. Иванов И. И. Теория алгоритмов."),
        _paragraph("p_0003", "М. : Наука, 2018. — 320 с."),
        _paragraph("p_0004", "ПРИЛОЖЕНИЕ А", heading=True),
        _paragraph("p_0005", "Текст приложения в список литературы не входит."),
    ]
    tree = [
        _tree_node(
            "node_001",
            "СПИСОК ЛИТЕРАТУРЫ",
            heading_block_id="p_0001",
            direct_block_ids=[],
        ),
        _tree_node(
            "node_002",
            "ПРИЛОЖЕНИЕ А",
            heading_block_id="p_0004",
            role="appendix",
            direct_block_ids=["p_0005"],
        ),
    ]

    entries = extract_bibliography(_document(blocks, tree))

    assert len(entries) == 1
    assert entries[0].order_index == 1
    assert entries[0].block_id == "p_0002"
    assert "Теория алгоритмов." in entries[0].raw_text
    assert "М. : Наука, 2018. — 320 с." in entries[0].raw_text
    assert "Текст приложения" not in entries[0].raw_text


# ---------------------------------------------------------------------
# Несколько записей внутри одного абзаца (склейка PDF-структурой)
# ---------------------------------------------------------------------


def test_inline_numbers_split_entries_merged_into_one_block() -> None:
    """Внутренние номера делят абзац, но не разрезают годы и «С. 12»."""
    blocks = [
        _paragraph("p_0001", "СПИСОК ЛИТЕРАТУРЫ", heading=True),
        _paragraph(
            "p_0002",
            "33. Бэнгз Д. Руководство по составлению бизнес-плана. — М.: Финпресс, "
            "2003.  34. Власова В. М. Основы предпринимательской деятельности. — "
            "М.: Финансы и статистика, 1997. — С. 12-15.",
        ),
        _paragraph(
            "p_0003",
            "35. Волкова И. В. Ресторанный бизнес в России. — М.: Флинта, 2002.",
        ),
    ]
    tree = [
        _tree_node(
            "node_001",
            "СПИСОК ЛИТЕРАТУРЫ",
            heading_block_id="p_0001",
            direct_block_ids=["p_0002", "p_0003"],
        )
    ]

    entries = extract_bibliography(_document(blocks, tree))

    assert [entry.order_index for entry in entries] == [33, 34, 35]
    assert [entry.bib_id for entry in entries] == ["bib_033", "bib_034", "bib_035"]
    assert entries[1].block_id == "p_0002"
    assert entries[1].raw_text.startswith("Власова В. М.")
    # Год «2003.» и страницы «С. 12-15» не стали отдельными записями.
    assert entries[0].year_extracted == 2003
    assert entries[1].year_extracted == 1997
    assert entries[1].pages_extracted == "С. 12-15"


def test_prose_after_list_is_not_glued_to_last_entry() -> None:
    """Текст после списка (в том числе «2000.») не становится записью."""
    blocks = [
        _paragraph("p_0001", "СПИСОК ЛИТЕРАТУРЫ", heading=True),
        _paragraph("p_0002", "1. Иванов И. И. Книга. — М. : Наука, 2018. — 120 с."),
        _paragraph("p_0003", "2. Петров П. П. Статья. — СПб. : Питер, 2019. — 90 с."),
        _paragraph("p_0004", "В заключении подведены итоги работы."),
        _paragraph("p_0005", "2000. Это год, упомянутый в тексте."),
        _paragraph("p_0006", "Продолжение заключения без номера."),
    ]
    tree = [
        _tree_node(
            "node_001",
            "СПИСОК ЛИТЕРАТУРЫ",
            heading_block_id="p_0001",
            direct_block_ids=["p_0002", "p_0003", "p_0004", "p_0005", "p_0006"],
        )
    ]

    entries = extract_bibliography(_document(blocks, tree))

    assert [entry.order_index for entry in entries] == [1, 2]
    assert "заключении" not in entries[-1].raw_text
    assert entries[-1].block_id == "p_0003"


def test_heading_after_entries_closes_the_list() -> None:
    """Заголовок следующего раздела завершает список литературы."""
    blocks = [
        _paragraph("p_0001", "СПИСОК ЛИТЕРАТУРЫ", heading=True),
        _paragraph("p_0002", "1. Иванов И. И. Книга. — М. : Наука, 2018. — 120 с."),
        _paragraph("p_0003", "ЗАКЛЮЧЕНИЕ", heading=True),
        _paragraph("p_0004", "Текст заключения в список не входит."),
    ]
    tree = [
        _tree_node(
            "node_001",
            "СПИСОК ЛИТЕРАТУРЫ",
            heading_block_id="p_0001",
            direct_block_ids=["p_0002", "p_0003", "p_0004"],
        )
    ]

    entries = extract_bibliography(_document(blocks, tree))

    assert len(entries) == 1
    assert "заключения" not in entries[0].raw_text


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
