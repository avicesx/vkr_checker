"""Контракт извлечения упоминаний объектов в основном тексте.

Тесты проверяют три формы упоминаний: числовые ссылки в квадратных скобках
(``[3]``), ссылки на рисунки/таблицы/приложения («см. рисунок 2», «табл. 3»,
«приложение Б») и авторско-годовые ссылки ``(Иванов, 2020)``. Отдельно
проверяются место упоминания в документе (абзац, страница, смещения в
``text_visible``), разрешение целей по переданным записям литературы и
подписям, а также исключение раздела литературы из поиска.

Документы собираются синтетически, поэтому тесты не зависят от бинарных
образцов и конвейеров DOCX/PDF.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from parser.core.schema import (
    BibliographyEntry,
    CrossReferenceIndex,
    DocumentMeta,
    DocumentTreeNode,
    GlobalSettings,
    HeadingMetadata,
    NumberedCaptionEntity,
    PageCountInfo,
    ParagraphBlock,
    ParsedDocument,
    ThemeData,
)
from parser.enrichment.mentions import extract_mentions


# ---------------------------------------------------------------------
# Сборка документов
# ---------------------------------------------------------------------


def _paragraph(
    block_id: str,
    text: str,
    page: int = 1,
    container_type: str = "body",
) -> ParagraphBlock:
    """Создает абзац с заданным текстом, страницей и типом контейнера."""
    return ParagraphBlock(
        block_id=block_id,
        section_idx=0,
        page_start_est=page,
        page_end_est=page,
        text_raw=text,
        text_visible=text,
        container_type=container_type,  # type: ignore[arg-type]
    )


def _bib_entry(
    index: int, raw_text: str, block_id: Optional[str] = None
) -> BibliographyEntry:
    """Создает запись списка литературы с порядковым номером."""
    return BibliographyEntry(
        bib_id=f"bib_{index:03d}",
        block_id=block_id or f"bib_{index:03d}",
        order_index=index,
        raw_text=raw_text,
    )


def _caption(
    entity_id: str,
    entity_type: str,
    number_str: str,
    page: int = 1,
) -> NumberedCaptionEntity:
    """Создает пронумерованный объект с подписью."""
    prefix = {"figure": "Рисунок", "table": "Таблица", "appendix": "Приложение"}[
        entity_type
    ]
    return NumberedCaptionEntity(
        entity_id=entity_id,
        entity_type=entity_type,  # type: ignore[arg-type]
        caption_block_id=f"cap_{entity_id}",
        page_est=page,
        prefix_word=prefix,
        number_str=number_str,
        numbering_scheme="continuous_arabic",
        title_text="Схема",
    )


def _tree_node(
    node_id: str,
    title: str,
    heading_block_id: Optional[str],
    *,
    level: int = 0,
    role: str = "main_chapter",
    page: int = 1,
    page_end: Optional[int] = None,
    children: Sequence[DocumentTreeNode] = (),
) -> DocumentTreeNode:
    """Создает узел дерева документа, привязанный к абзацу-заголовку."""
    return DocumentTreeNode(
        node_id=node_id,
        heading_block_id=heading_block_id,
        title=title,
        level=level,
        semantic_role=role,  # type: ignore[arg-type]
        page_start_est=page,
        page_end_est=page_end if page_end is not None else page,
        children=list(children),
    )


def _document(
    paragraphs: Iterable[ParagraphBlock],
    *,
    tree: Sequence[DocumentTreeNode] = (),
    bibliography: Sequence[BibliographyEntry] = (),
    captions: Sequence[NumberedCaptionEntity] = (),
) -> ParsedDocument:
    """Собирает минимальный ParsedDocument для сканера упоминаний."""
    blocks: List[ParagraphBlock] = list(paragraphs)
    pages = max((block.page_end_est for block in blocks), default=1)
    figures = [item for item in captions if item.entity_type == "figure"]
    tables = [item for item in captions if item.entity_type == "table"]
    appendices = [item for item in captions if item.entity_type == "appendix"]
    return ParsedDocument(
        parse_mode="docx_only",
        meta=DocumentMeta(filename="vkr.docx", file_sha256="0" * 64),
        settings=GlobalSettings(),
        theme=ThemeData(),
        page_count=PageCountInfo(
            total_pages=pages, method="docx_native_breaks", confidence="high"
        ),
        blocks=blocks,
        document_tree=list(tree),
        cross_ref_index=CrossReferenceIndex(
            figures=figures,
            tables=tables,
            Apps=appendices,
            bibliography=list(bibliography),
        ),
    )


# ---------------------------------------------------------------------
# Квадратные скобки
# ---------------------------------------------------------------------


def test_bracket_citation_resolves_only_when_entry_exists() -> None:
    """«[3]» сопоставляется с записью №3, а «[7]» без записи остается без цели."""
    text = "В работе [3] отмечено, а в [7] — нет."
    document = _document(
        [_paragraph("p_0001", text, page=4)],
        bibliography=[_bib_entry(3, "Иванов И.И. Теория. — 2020.")],
    )

    mentions = extract_mentions(document)

    assert [item.mention_id for item in mentions] == ["men_001", "men_002"]
    assert [item.ref_type for item in mentions] == ["bibliography", "bibliography"]

    resolved, unresolved = mentions
    assert resolved.extracted_targets == ["3"]
    assert resolved.resolved_entity_ids == ["bib_003"]
    assert unresolved.extracted_targets == ["7"]
    assert unresolved.resolved_entity_ids == []


def test_bracket_citation_reports_place_in_text() -> None:
    """Смещения указывают в ``text_visible``, страница берется из абзаца."""
    text = "Как показано в [3, с. 15], вывод верен."
    document = _document(
        [_paragraph("p_0001", text, page=6)],
        bibliography=[_bib_entry(3, "Иванов И.И. Теория. — 2020.")],
    )

    mention = extract_mentions(document)[0]

    assert (mention.block_id, mention.page_est) == ("p_0001", 6)
    assert mention.raw_snippet == "[3, с. 15]"
    assert text[mention.start_char : mention.end_char] == mention.raw_snippet
    # Страница источника — не номер записи: цель одна.
    assert mention.extracted_targets == ["3"]
    assert mention.resolved_entity_ids == ["bib_003"]


# ---------------------------------------------------------------------
# Рисунки, таблицы, приложения
# ---------------------------------------------------------------------


def test_see_figure_reference_resolves_to_caption_entity() -> None:
    """«см. рисунок 2» разрешается в fig_002 по номеру подписи."""
    text = "Как показано на см. рисунок 2, схема работает."
    captions = [_caption("fig_002", "figure", "2")]
    document = _document([_paragraph("p_0001", text, page=2)])

    mentions = extract_mentions(document, captions=captions)

    assert len(mentions) == 1
    mention = mentions[0]
    assert mention.ref_type == "figure"
    assert mention.extracted_targets == ["2"]
    assert mention.resolved_entity_ids == ["fig_002"]
    assert mention.raw_snippet == "см. рисунок 2"
    assert mention.start_char == text.index("см.")
    assert text[mention.start_char : mention.end_char] == mention.raw_snippet
    assert mention.page_est == 2
    assert mention.uses_short_abbrev is False


def test_table_and_appendix_references_resolve_by_number() -> None:
    """«табл. 3» и «приложение Б» разрешаются по своим типам объектов."""
    text = "См. табл. 3 и приложение Б."
    captions = [
        _caption("tbl_003", "table", "3"),
        _caption("app_b", "appendix", "Б"),
    ]
    document = _document([_paragraph("p_0001", text, page=3)])

    mentions = extract_mentions(document, captions=captions)

    assert [item.ref_type for item in mentions] == ["table", "appendix"]
    assert [item.extracted_targets for item in mentions] == [["3"], ["Б"]]
    assert [item.resolved_entity_ids for item in mentions] == [["tbl_003"], ["app_b"]]
    # Таблица записана сокращением, приложение — полным словом.
    assert mentions[0].uses_short_abbrev is True
    assert mentions[1].uses_short_abbrev is False


def test_section_based_object_number_is_resolved() -> None:
    """Составной номер «2.1» сопоставляется с number_str подписи как есть."""
    text = "На рисунке 2.1 видно зависимость."
    captions = [_caption("fig_021", "figure", "2.1")]
    document = _document([_paragraph("p_0001", text, page=5)])

    mention = extract_mentions(document, captions=captions)[0]

    assert mention.ref_type == "figure"
    assert mention.extracted_targets == ["2.1"]
    assert mention.resolved_entity_ids == ["fig_021"]


def test_unknown_object_number_stays_unresolved() -> None:
    """Упоминание без соответствующей подписи сохраняется без цели."""
    document = _document([_paragraph("p_0001", "См. рисунок 9.")])

    mention = extract_mentions(document, captions=[_caption("fig_002", "figure", "2")])[0]

    assert mention.extracted_targets == ["9"]
    assert mention.resolved_entity_ids == []


# ---------------------------------------------------------------------
# Автор-год
# ---------------------------------------------------------------------


def test_author_year_reference_resolves_by_surname_and_year() -> None:
    """«(Иванов, 2020)» сопоставляется по фамилии и году в raw_text записи."""
    text = "Ранее (Иванов, 2020) и (Петров, 2019)."
    document = _document(
        [_paragraph("p_0001", text, page=7)],
        bibliography=[_bib_entry(1, "Иванов И.И. Теория надежности. — 2020.")],
    )

    mentions = extract_mentions(document)

    assert [item.ref_type for item in mentions] == ["bibliography", "bibliography"]
    assert [item.extracted_targets for item in mentions] == [
        ["Иванов, 2020"],
        ["Петров, 2019"],
    ]
    assert [item.resolved_entity_ids for item in mentions] == [["bib_001"], []]
    assert mentions[0].raw_snippet == "(Иванов, 2020)"
    assert text[mentions[0].start_char : mentions[0].end_char] == "(Иванов, 2020)"
    assert mentions[0].page_est == 7


# ---------------------------------------------------------------------
# Границы поиска
# ---------------------------------------------------------------------


def test_bibliography_section_blocks_are_ignored() -> None:
    """Записи раздела литературы не считаются упоминаниями в тексте."""
    paragraphs = [
        _paragraph("p_0001", "В тексте есть ссылка [3].", page=1),
        _paragraph("p_0002", "СПИСОК ЛИТЕРАТУРЫ", page=2),
        _paragraph("p_0003", "[3] Иванов И.И. Теория. — 2020.", page=2),
        _paragraph("p_0004", "[4] Петров П.П. Практика. — 2019.", page=2),
    ]
    tree = [
        _tree_node(
            "n_bib",
            "СПИСОК ЛИТЕРАТУРЫ",
            "p_0002",
            level=1,
            role="bibliography",
            page=2,
        )
    ]
    document = _document(
        paragraphs,
        tree=tree,
        bibliography=[
            _bib_entry(3, "[3] Иванов И.И. Теория. — 2020.", "p_0003"),
            _bib_entry(4, "[4] Петров П.П. Практика. — 2019.", "p_0004"),
        ],
    )

    mentions = extract_mentions(document)

    assert len(mentions) == 1
    assert mentions[0].block_id == "p_0001"
    assert mentions[0].raw_snippet == "[3]"


def test_non_body_paragraphs_are_not_scanned() -> None:
    """Сноски и колонтитулы в основной проход не попадают."""
    document = _document(
        [
            _paragraph("p_0001", "Ссылка [3] в тексте."),
            _paragraph("p_0002", "Ссылка [4] в сноске.", container_type="footnote"),
        ],
        bibliography=[
            _bib_entry(3, "Иванов И.И. Теория. — 2020."),
            _bib_entry(4, "Петров П.П. Практика. — 2019."),
        ],
    )

    mentions = extract_mentions(document)

    assert [item.block_id for item in mentions] == ["p_0001"]


def test_empty_text_is_skipped() -> None:
    """Пустые абзацы не создают упоминаний."""
    document = _document(
        [_paragraph("p_0001", "   "), _paragraph("p_0002", "[3]")],
        bibliography=[_bib_entry(3, "Иванов И.И. Теория. — 2020.")],
    )

    mentions = extract_mentions(document)

    assert [item.block_id for item in mentions] == ["p_0002"]
    assert mentions[0].resolved_entity_ids == ["bib_003"]


def test_heading_block_is_not_a_mention() -> None:
    """«1.Структура» — заголовок раздела, а не упоминание объекта."""
    heading = _paragraph("p_0001", "1.Структура")
    heading.heading_info = HeadingMetadata(
        is_heading=True,
        detection_confidence="heuristic_visual",
        heading_kind="numbered_section",
        level=1,
        clean_title_text="Структура",
    )
    document = _document([heading], captions=[_caption("fig_001", "figure", "1")])

    assert extract_mentions(document) == []


def test_tree_heading_block_is_not_a_mention() -> None:
    """Абзац, привязанный к заголовку дерева, тоже пропускается."""
    paragraphs = [_paragraph("p_0001", "2.Организация")]
    tree = [_tree_node("n_2", "Организация", "p_0001", level=1)]
    document = _document(paragraphs, tree=tree)

    assert extract_mentions(document) == []


def test_dot_after_number_does_not_swallow_heading_title() -> None:
    """«Рис 1.Структура» — упоминание рисунка 1, а не цели «1.Структура»."""
    text = "Рис 1.Структура"
    document = _document(
        [_paragraph("p_0001", text)], captions=[_caption("fig_001", "figure", "1")]
    )

    mentions = extract_mentions(document)

    assert len(mentions) == 1
    assert mentions[0].extracted_targets == ["1"]
    assert mentions[0].resolved_entity_ids == ["fig_001"]
    assert mentions[0].raw_snippet == "Рис 1"


def test_caption_block_is_not_a_mention() -> None:
    """Абзац-подпись не считается упоминанием, ссылка из тела — считается."""
    caption_text = "Рисунок 1 — Схема процесса"
    body_text = "На рис. 1 показана схема процесса."
    captions = [_caption("fig_001", "figure", "1")]
    document = _document(
        [
            _paragraph("cap_fig_001", caption_text),
            _paragraph("p_0002", body_text),
        ],
        captions=captions,
    )

    mentions = extract_mentions(document, captions=captions)

    assert len(mentions) == 1
    assert mentions[0].block_id == "p_0002"
    assert mentions[0].raw_snippet == "рис. 1"
    assert mentions[0].resolved_entity_ids == ["fig_001"]


def test_enumeration_in_prepositional_case_is_one_mention() -> None:
    """«в таблицах 5,6» — одно упоминание с двумя целями и двумя резолвами."""
    text = "Данные отражены в таблицах 5,6."
    captions = [_caption("tbl_005", "table", "5"), _caption("tbl_006", "table", "6")]
    document = _document([_paragraph("p_0001", text)], captions=captions)

    mentions = extract_mentions(document, captions=captions)

    assert len(mentions) == 1
    mention = mentions[0]
    assert mention.ref_type == "table"
    assert mention.extracted_targets == ["5", "6"]
    assert mention.resolved_entity_ids == ["tbl_005", "tbl_006"]
    assert mention.raw_snippet == "таблицах 5,6"
    assert text[mention.start_char : mention.end_char] == mention.raw_snippet


def test_figure_case_forms_are_resolved() -> None:
    """Падежные формы «рисунок/рисунка/…/рисунках» разрешаются по номеру."""
    cases = (
        ("рисунок 1", "1"),
        ("рисунка 2", "2"),
        ("рисунку 3", "3"),
        ("рисунком 4", "4"),
        ("рисунке 5", "5"),
        ("рисунков 6", "6"),
        ("рисункам 7", "7"),
        ("рисунками 8", "8"),
        ("рисунках 9", "9"),
    )
    captions = [
        _caption(f"fig_{number:0>3}", "figure", number)
        for number in (case[1] for case in cases)
    ]
    for text, number in cases:
        document = _document([_paragraph("p_0001", text)])
        mentions = extract_mentions(document, captions=captions)
        assert len(mentions) == 1, text
        assert mentions[0].extracted_targets == [number], text
        assert mentions[0].resolved_entity_ids == [f"fig_{number:0>3}"], text


def test_table_case_forms_are_resolved() -> None:
    """Падежные формы «таблица/таблицы/…/таблицах» разрешаются по номеру."""
    cases = (
        ("таблица 1", "1"),
        ("таблицы 2", "2"),
        ("таблице 3", "3"),
        ("таблицу 4", "4"),
        ("таблицей 5", "5"),
        ("таблицами 6", "6"),
        ("таблицам 7", "7"),
        ("таблицах 8", "8"),
    )
    captions = [
        _caption(f"tbl_{number:0>3}", "table", number)
        for number in (case[1] for case in cases)
    ]
    for text, number in cases:
        document = _document([_paragraph("p_0001", text)])
        mentions = extract_mentions(document, captions=captions)
        assert len(mentions) == 1, text
        assert mentions[0].resolved_entity_ids == [f"tbl_{number:0>3}"], text


def test_figure_enumeration_with_comma_is_split_into_targets() -> None:
    """«на рисунках 1, 2» — одно упоминание с целями 1 и 2."""
    text = "На рисунках 1, 2 показаны схемы."
    captions = [_caption("fig_001", "figure", "1"), _caption("fig_002", "figure", "2")]
    document = _document([_paragraph("p_0001", text)], captions=captions)

    mentions = extract_mentions(document, captions=captions)

    assert len(mentions) == 1
    assert mentions[0].extracted_targets == ["1", "2"]
    assert mentions[0].resolved_entity_ids == ["fig_001", "fig_002"]
    assert mentions[0].raw_snippet == "рисунках 1, 2"


def test_sentence_dot_is_not_part_of_number() -> None:
    """«таблица 5.» — точка завершает предложение и в номер не входит."""
    text = "Смотрите таблица 5."
    captions = [_caption("tbl_005", "table", "5")]
    document = _document([_paragraph("p_0001", text)], captions=captions)

    mentions = extract_mentions(document, captions=captions)

    assert len(mentions) == 1
    assert mentions[0].extracted_targets == ["5"]
    assert mentions[0].raw_snippet == "таблица 5"
    assert text[mentions[0].end_char] == "."


def test_figure_range_is_expanded_into_targets() -> None:
    """«на рисунках 1–4» — одно упоминание с целями 1, 2, 3, 4."""
    text = "На рисунках 1–4 показаны схемы."
    captions = [
        _caption(f"fig_{number:0>3}", "figure", number)
        for number in ("1", "2", "3", "4")
    ]
    document = _document([_paragraph("p_0001", text)], captions=captions)

    mentions = extract_mentions(document, captions=captions)

    assert len(mentions) == 1
    mention = mentions[0]
    assert mention.extracted_targets == ["1", "2", "3", "4"]
    assert mention.resolved_entity_ids == ["fig_001", "fig_002", "fig_003", "fig_004"]
    assert mention.raw_snippet == "рисунках 1–4"
    assert text[mention.start_char : mention.end_char] == mention.raw_snippet


def test_section_range_is_expanded_by_second_level() -> None:
    """«таблицы 2.1–2.3» раскрывается по второму уровню при общем префиксе."""
    text = "В таблицах 2.1–2.3 приведены данные."
    captions = [
        _caption("tbl_021", "table", "2.1"),
        _caption("tbl_022", "table", "2.2"),
        _caption("tbl_023", "table", "2.3"),
    ]
    document = _document([_paragraph("p_0001", text)], captions=captions)

    mentions = extract_mentions(document, captions=captions)

    assert len(mentions) == 1
    assert mentions[0].extracted_targets == ["2.1", "2.2", "2.3"]
    assert mentions[0].resolved_entity_ids == ["tbl_021", "tbl_022", "tbl_023"]


def test_long_range_is_not_expanded() -> None:
    """Диапазон длиннее 20 не раскрывается: остаются только два конца."""
    text = "На рисунках 1–30 показаны схемы."
    document = _document([_paragraph("p_0001", text)])

    mentions = extract_mentions(document)

    assert len(mentions) == 1
    assert mentions[0].extracted_targets == ["1", "30"]


def test_dash_forms_of_range_are_equivalent() -> None:
    """Дефис, en dash и em dash в диапазоне дают одинаковые цели."""
    captions = [
        _caption(f"fig_{number:0>3}", "figure", number)
        for number in ("1", "2", "3", "4")
    ]
    for dash in ("-", "–", "—"):
        text = f"На рисунках 1{dash}4 показаны схемы."
        document = _document([_paragraph("p_0001", text)], captions=captions)
        mentions = extract_mentions(document, captions=captions)
        assert len(mentions) == 1, text
        assert mentions[0].extracted_targets == ["1", "2", "3", "4"], text
        assert mentions[0].resolved_entity_ids == [
            "fig_001",
            "fig_002",
            "fig_003",
            "fig_004",
        ], text


def test_appendix_letter_range_is_not_expanded() -> None:
    """«приложение А–В» не раскрывается: как в DOCX, цель — только «А»."""
    text = "Смотрите приложение А–В."
    captions = [_caption("app_001", "appendix", "А")]
    document = _document([_paragraph("p_0001", text)], captions=captions)

    mentions = extract_mentions(document, captions=captions)

    assert len(mentions) == 1
    assert mentions[0].ref_type == "appendix"
    assert mentions[0].extracted_targets == ["А"]
    assert mentions[0].resolved_entity_ids == ["app_001"]
