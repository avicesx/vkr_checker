"""Новые параметры набора правил: покрытие требований методичек.

Документы собираются синтетически из схемы разбора: абзацы, заголовки,
дерево разделов, подписи, таблицы, формулы, оглавление и список литературы.
Каждый тест проверяет один параметр в двух состояниях: при включённом
требовании документ либо проходит, либо получает нарушение с адресом.

Параметры опциональны: набор по умолчанию их не включает, поэтому поведение
действующих проверок не меняется. Здесь фиксируются только новые ветки.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from parser.core.schema import (
    BibliographyEntry,
    DocumentTreeNode,
    EquationObject,
    NumberedCaptionEntity,
    PageCountInfo,
    ParagraphBlock,
    PdfPageAudit,
    SectionProperties,
    TableBlock,
    TocContainerBlock,
    TocEntryItem,
)
from test_rules_urgpu import (
    _assessment,
    _caption,
    _doc,
    _engine,
    _heading,
    _ids,
    _node,
    _para,
    _rule,
    _run,
    _table,
)


def _bib(
    bib_id: str,
    order_index: int,
    raw_text: str,
    *,
    block_id: Optional[str] = None,
    prefix: Optional[str] = None,
    category: str = "book_monograph",
) -> BibliographyEntry:
    """Создает запись списка литературы с номером и описанием."""
    return BibliographyEntry(
        bib_id=bib_id,
        block_id=block_id or f"p_{bib_id}",
        order_index=order_index,
        raw_text=raw_text,
        raw_number_prefix=prefix,
        detected_category=category,  # type: ignore[arg-type]
    )


def _equation(
    equation_id: str,
    *,
    number: Optional[str] = None,
    alignment: str = "right",
    parent_block_id: str = "p_eq",
) -> EquationObject:
    """Создает вынесенную формулу с номером и выравниванием."""
    return EquationObject(
        equation_id=equation_id,
        parent_block_id=parent_block_id,
        section_idx=0,
        representation_kind="omml_para",  # type: ignore[arg-type]
        is_standalone_line=True,
        text_linearized="E = mc2",
        alignment=alignment,  # type: ignore[arg-type]
        has_number=number is not None,
        number_raw=number,
        number_clean=number,
    )


def _pdf_page(page_num: int, *, has_number: bool = True) -> PdfPageAudit:
    """Создает страничный аудит PDF с номером или без него."""
    return PdfPageAudit(
        page_num=page_num,
        width_mm=210.0,
        height_mm=297.0,
        has_rendered_page_number=has_number,
        page_number_location="footer" if has_number else "none",
        page_number_alignment="center" if has_number else None,
        rendered_page_number_raw=str(page_num) if has_number else None,
        rendered_page_number_int=page_num if has_number else None,
    )


def _toc(
    block_id: str,
    entries: Sequence[TocEntryItem],
    *,
    page: int = 3,
) -> TocContainerBlock:
    """Создает оглавление с записями."""
    return TocContainerBlock(
        block_id=block_id,
        section_idx=0,
        page_start_est=page,
        page_end_est=page,
        is_automated_field_or_sdt=False,
        entries=list(entries),
    )


def _entry(
    index: int,
    raw_text: str,
    *,
    block_id: Optional[str] = None,
    number: Optional[str] = None,
    title: Optional[str] = None,
    page: Optional[str] = "5",
    matched_heading_block_id: Optional[str] = None,
) -> TocEntryItem:
    """Создает запись оглавления с номером, заголовком и страницей."""
    return TocEntryItem(
        entry_index=index,
        block_id=block_id or f"toc_entry{index}",
        raw_text=raw_text,
        extracted_number=number,
        extracted_title=title if title is not None else raw_text,
        extracted_page_str=page,
        matched_heading_block_id=matched_heading_block_id,
    )




def _doc_with_equations(document: Any, equations: Sequence[EquationObject]) -> Any:
    """Добавляет формулы в индекс перекрёстных ссылок документа."""
    index = document.cross_ref_index.model_copy(update={"equations": list(equations)})
    return document.model_copy(update={"cross_ref_index": index})


# ---------------------------------------------------------------------
# Заголовки
# ---------------------------------------------------------------------


def test_heading_number_style_roman_and_arabic() -> None:
    """Римская запись номера принимается при number_style='roman' и не при 'arabic'."""
    document = _doc(
        blocks=[
            _heading("h1", "I. ПЕРВАЯ ГЛАВА"),
            _heading("h2", "II. ВТОРАЯ ГЛАВА"),
        ],
        tree=[
            _node("n1", "ПЕРВАЯ ГЛАВА", role="main_chapter", heading_block_id="h1"),
            _node("n2", "ВТОРАЯ ГЛАВА", role="main_chapter", heading_block_id="h2"),
        ],
    )
    roman = _engine(
        _rule("num", "heading_numbering", params={"number_style": "roman"})
    )
    arabic = _engine(
        _rule("num", "heading_numbering", params={"number_style": "arabic"})
    )

    assert _ids(roman.check(document), "num") == []
    assert len(_ids(arabic.check(document), "num")) >= 1


def test_heading_prefix_word_required() -> None:
    """Слово «Глава» перед номером проверяется параметром prefix_word."""
    document = _doc(
        blocks=[_heading("h1", "ГЛАВА 1 ВВЕДЕНИЕ")],
        tree=[_node("n1", "ВВЕДЕНИЕ", role="main_chapter", heading_block_id="h1")],
    )
    matching = _engine(
        _rule("fmt", "heading_format", params={"prefix_word": "Глава"})
    )
    other = _engine(
        _rule("fmt", "heading_format", params={"prefix_word": "Раздел"})
    )

    assert _ids(matching.check(document), "fmt") == []
    assert len(_ids(other.check(document), "fmt")) == 1


def test_heading_forbidden_prefix_words() -> None:
    """Запрещённое слово перед номером даёт нарушение, разрешённое — нет."""
    document = _doc(
        blocks=[_heading("h1", "Глава 1 ВВЕДЕНИЕ")],
        tree=[_node("n1", "ВВЕДЕНИЕ", role="main_chapter", heading_block_id="h1")],
    )
    forbidden = _engine(
        _rule(
            "num",
            "heading_numbering",
            params={"forbid_prefix_words": ["Глава"]},
        )
    )
    allowed = _engine(
        _rule(
            "num",
            "heading_numbering",
            params={"forbid_prefix_words": ["Раздел"]},
        )
    )

    assert len(_ids(forbidden.check(document), "num")) == 1
    assert _ids(allowed.check(document), "num") == []


def test_heading_quotes_forbidden() -> None:
    """Кавычки в названии заголовка запрещаются параметром forbid_quotes."""
    document = _doc(
        blocks=[
            _heading("h1", "«ВВЕДЕНИЕ»"),
            _heading("h2", "ЗАКЛЮЧЕНИЕ"),
        ],
        tree=[
            _node("n1", "ВВЕДЕНИЕ", role="main_chapter", heading_block_id="h1"),
            _node("n2", "ЗАКЛЮЧЕНИЕ", role="conclusion", heading_block_id="h2"),
        ],
    )
    engine = _engine(
        _rule("fmt", "heading_format", params={"forbid_quotes": True})
    )

    assert len(_ids(engine.check(document), "fmt")) == 1


# ---------------------------------------------------------------------
# Подписи
# ---------------------------------------------------------------------


def _caption_document(
    text: str,
    *,
    title: str = "Схема",
) -> Any:
    """Документ с одной подписью рисунка и её абзацем."""
    block = _para("p_cap", text)
    entity = _caption("fig1", "figure", "1", caption_block_id="p_cap", title=title)
    return _doc(blocks=[block], figures=[entity])


def test_caption_hyphenation_forbidden() -> None:
    """Мягкий перенос в подписи запрещается, чистый текст проходит."""
    broken = _caption_document("Рисунок 1. Схе\u00adма процесса")
    clean = _caption_document("Рисунок 1. Схема процесса")
    engine = _engine(
        _rule(
            "fmt",
            "caption_format",
            params={"entity_type": "figure", "forbid_hyphenation": True},
        )
    )

    assert len(_ids(engine.check(broken), "fmt")) == 1
    assert _ids(engine.check(clean), "fmt") == []


def test_caption_break_after_prefix_forbidden() -> None:
    """Перенос строки сразу после префикса и номера запрещается."""
    broken = _caption_document("Рисунок 1.\nСхема процесса")
    clean = _caption_document("Рисунок 1. Схема процесса")
    engine = _engine(
        _rule(
            "fmt",
            "caption_format",
            params={"entity_type": "figure", "forbid_break_after_prefix": True},
        )
    )

    assert len(_ids(engine.check(broken), "fmt")) == 1
    assert _ids(engine.check(clean), "fmt") == []


def test_caption_number_sign_forbidden() -> None:
    """Знак номера в подписи запрещается, обычная запись проходит."""
    broken = _caption_document("Рисунок №1. Схема процесса")
    clean = _caption_document("Рисунок 1. Схема процесса")
    engine = _engine(
        _rule(
            "fmt",
            "caption_format",
            params={"entity_type": "figure", "forbid_number_sign": True},
        )
    )

    assert len(_ids(engine.check(broken), "fmt")) == 1
    assert _ids(engine.check(clean), "fmt") == []


def test_caption_sentence_case() -> None:
    """Название подписи со строчной буквы проходит, с заглавных слов — нет."""
    good = _caption_document("Рисунок 1. Схема процесса", title="Схема процесса")
    bad = _caption_document("Рисунок 1. Схема Процесса", title="Схема Процесса")
    engine = _engine(
        _rule(
            "fmt",
            "caption_format",
            params={"entity_type": "figure", "sentence_case": True},
        )
    )

    assert _ids(engine.check(good), "fmt") == []
    assert len(_ids(engine.check(bad), "fmt")) == 1


# ---------------------------------------------------------------------
# Оглавление
# ---------------------------------------------------------------------


def test_toc_start_page() -> None:
    """Страница начала оглавления проверяется параметром start_page."""
    document = _doc(
        blocks=[
            _para("toc_p", "Содержание", page=3),
            _toc("toc1", [_entry(0, "Введение 5", title="Введение")], page=3),
        ],
    )
    matching = _engine(
        _rule(
            "toc",
            "toc",
            params={
                "start_page": 3,
                "require_page_numbers": False,
                "check_titles": False,
            },
        )
    )
    other = _engine(
        _rule(
            "toc",
            "toc",
            params={
                "start_page": 5,
                "require_page_numbers": False,
                "check_titles": False,
            },
        )
    )

    assert _ids(matching.check(document), "toc") == []
    assert len(_ids(other.check(document), "toc")) == 1


def test_toc_requires_entry_number() -> None:
    """Номер записи обязателен, если у заголовка есть номер."""
    numbered = _doc(
        blocks=[
            _para("toc_p", "Содержание"),
            _heading("h1", "1 ВВЕДЕНИЕ"),
            _toc(
                "toc1",
                [
                    _entry(
                        0,
                        "1 Введение 5",
                        number="1",
                        title="Введение",
                        matched_heading_block_id="h1",
                    ),
                    _entry(1, "Заключение 9", number="2", title="Заключение"),
                ],
            ),
        ],
        tree=[
            _node("n1", "ВВЕДЕНИЕ", role="introduction", heading_block_id="h1"),
            _node("n2", "ЗАКЛЮЧЕНИЕ", role="conclusion", heading_block_id="h2"),
        ],
    )
    unnumbered = _doc(
        blocks=[
            _para("toc_p", "Содержание"),
            _heading("h1", "1 ВВЕДЕНИЕ"),
            _toc(
                "toc1",
                [
                    _entry(
                        0,
                        "Введение 5",
                        title="Введение",
                        matched_heading_block_id="h1",
                    )
                ],
            ),
        ],
        tree=[_node("n1", "ВВЕДЕНИЕ", role="introduction", heading_block_id="h1")],
    )
    engine = _engine(
        _rule(
            "toc",
            "toc",
            params={
                "require_toc": True,
                "require_entry_number": True,
                "require_page_numbers": False,
                "check_titles": False,
            },
        )
    )

    assert _ids(engine.check(numbered), "toc") == []
    assert len(_ids(engine.check(unnumbered), "toc")) == 1


# ---------------------------------------------------------------------
# Приложения
# ---------------------------------------------------------------------


def test_appendix_label_scheme() -> None:
    """Буквенная схема обозначения приложений проверяется label_scheme."""
    letters = _doc(
        tree=[
            _node("a1", "Приложение А", role="appendix", heading_block_id="ha1"),
            _node("a2", "Приложение Б", role="appendix", heading_block_id="ha2"),
        ]
    )
    numbers = _doc(
        tree=[
            _node("a1", "Приложение 1", role="appendix", heading_block_id="ha1"),
            _node("a2", "Приложение 2", role="appendix", heading_block_id="ha2"),
        ]
    )
    engine = _engine(
        _rule(
            "app",
            "appendix_order",
            params={"label_scheme": "russian_letters", "new_page_each": False},
        )
    )

    assert _ids(engine.check(letters), "app") == []
    assert len(_ids(engine.check(numbers), "app")) == 2


# ---------------------------------------------------------------------
# Нумерация страниц
# ---------------------------------------------------------------------


def test_first_numbered_page() -> None:
    """Номер первой страницы с колонцифрой проверяется по PDF-аудиту."""
    pages = [_pdf_page(page) for page in (1, 2, 3)]
    document = _doc(
        blocks=[_para("p1", "Текст")],
        pdf_pages=pages,
    )
    matching = _engine(
        _rule(
            "pagenum",
            "page_number_position",
            params={
                "zone": "footer",
                "alignment": "center",
                "first_numbered_page": 1,
            },
        )
    )
    other = _engine(
        _rule(
            "pagenum",
            "page_number_position",
            params={
                "zone": "footer",
                "alignment": "center",
                "first_numbered_page": 2,
            },
        )
    )

    assert _ids(matching.check(document), "pagenum") == []
    assert len(_ids(other.check(document), "pagenum")) == 1


# ---------------------------------------------------------------------
# Таблицы
# ---------------------------------------------------------------------


def test_table_alignment() -> None:
    """Выравнивание таблицы проверяется параметром alignment."""
    document = _doc(blocks=[_table("tbl1", texts=("Ячейка",))])
    document.blocks[0].alignment = "center"  # type: ignore[union-attr]

    matching = _engine(
        _rule("tl", "table_layout", params={"alignment": "center"})
    )
    other = _engine(_rule("tl", "table_layout", params={"alignment": "left"}))

    assert _ids(matching.check(document), "tl") == []
    assert len(_ids(other.check(document), "tl")) == 1


def test_table_number_column_forbidden() -> None:
    """Колонка «№ п/п» запрещается параметром forbid_number_column."""
    with_number = _doc(blocks=[_table("tbl1", texts=("№ п/п",))])
    without_number = _doc(blocks=[_table("tbl1", texts=("Показатель",))])
    engine = _engine(
        _rule("tl", "table_layout", params={"forbid_number_column": True})
    )

    assert len(_ids(engine.check(with_number), "tl")) == 1
    assert _ids(engine.check(without_number), "tl") == []


def test_table_requires_header_row() -> None:
    """Без разметки шапки правило уходит в not_assessed, а не в находку."""
    with_header = _doc(blocks=[_table("tbl1", texts=("Наименование", "Значение"), header=True)])
    without_header = _doc(blocks=[_table("tbl1", texts=("Наименование",))])
    engine = _engine(
        _rule("th", "table_header_format", params={"require_header_row": True})
    )

    assert _ids(engine.check(with_header), "th") == []
    report = engine.check(without_header)
    assert _ids(report, "th") == []
    assert _assessment(report, "th").status == "not_assessed"


# ---------------------------------------------------------------------
# Обязательные подразделы
# ---------------------------------------------------------------------


def test_required_subsections() -> None:
    """Глава без подразделов даёт нарушение, глава с подразделом — нет."""
    with_child = _doc(
        blocks=[_heading("h1", "1 ГЛАВА"), _heading("h2", "1.1 ПАРАГРАФ", level=2)],
        tree=[
            _node(
                "n1",
                "ГЛАВА",
                role="main_chapter",
                heading_block_id="h1",
                children=[
                    _node(
                        "n1_1",
                        "ПАРАГРАФ",
                        role="sub_section",
                        level=2,
                        heading_block_id="h2",
                    )
                ],
            )
        ],
    )
    alone = _doc(
        blocks=[_heading("h1", "1 ГЛАВА")],
        tree=[_node("n1", "ГЛАВА", role="main_chapter", heading_block_id="h1")],
    )
    engine = _engine(
        _rule(
            "sec",
            "required_sections",
            params={"sections": [], "require_subsections": True},
        )
    )

    assert _ids(engine.check(with_child), "sec") == []
    assert len(_ids(engine.check(alone), "sec")) == 1


# ---------------------------------------------------------------------
# Шрифт
# ---------------------------------------------------------------------


def test_font_smaller_than_body_text() -> None:
    """Уменьшенный кегль допустим до предела 10 pt, ниже — нарушение."""
    def _document(size_pt: float):
        cell_paragraph = _para(
            "p_cell",
            "Значение",
            runs=[_run("r_cell", "Значение", size_pt=size_pt)],
            container_type="table_cell",
        )
        body = _para(
            "p_body",
            "Основной текст",
            runs=[_run("r_body", "Основной текст")],
        )
        return _doc(blocks=[body, cell_paragraph])

    engine = _engine(
        _rule(
            "font",
            "font",
            params={"smaller_than_body_text": True},
            scope="table_cells",
        )
    )

    assert _ids(engine.check(_document(12.0)), "font") == []
    assert len(_ids(engine.check(_document(9.0)), "font")) == 1


# ---------------------------------------------------------------------
# Объём
# ---------------------------------------------------------------------


def _volume_document(tree: Sequence[DocumentTreeNode], *, total_pages: int = 20) -> Any:
    """Документ с деревом разделов и заданным числом страниц."""
    document = _doc(blocks=[_para("p1", "Текст")], tree=tree)
    return document.model_copy(
        update={
            "page_count": PageCountInfo(
                total_pages=total_pages,
                method="docx_native_breaks",
                confidence="high",
            )
        }
    )


def test_volume_section_page_limit() -> None:
    """Предел страниц раздела проверяется параметром section_page_limits."""
    document = _volume_document(
        [_node("n1", "ВВЕДЕНИЕ", role="introduction", page_start=1, page_end=10)]
    )
    matching = _engine(
        _rule(
            "vol",
            "document_volume",
            params={"section_page_limits": {"introduction": 10}},
        )
    )
    other = _engine(
        _rule(
            "vol",
            "document_volume",
            params={"section_page_limits": {"introduction": 5}},
        )
    )

    assert _ids(matching.check(document), "vol") == []
    assert len(_ids(other.check(document), "vol")) == 1


def test_volume_section_min_pages() -> None:
    """Минимум страниц раздела проверяется параметром section_min_pages."""
    document = _volume_document(
        [_node("n1", "ЗАКЛЮЧЕНИЕ", role="conclusion", page_start=1, page_end=2)]
    )
    matching = _engine(
        _rule(
            "vol",
            "document_volume",
            params={"section_min_pages": {"conclusion": 1}},
        )
    )
    other = _engine(
        _rule(
            "vol",
            "document_volume",
            params={"section_min_pages": {"conclusion": 3}},
        )
    )

    assert _ids(matching.check(document), "vol") == []
    assert len(_ids(other.check(document), "vol")) == 1


def test_volume_appendix_share() -> None:
    """Доля страниц приложений ограничивается параметром max_appendix_share."""
    document = _volume_document(
        [_node("n1", "ПРИЛОЖЕНИЕ А", role="appendix", page_start=1, page_end=9)],
        total_pages=20,
    )
    matching = _engine(
        _rule(
            "vol",
            "document_volume",
            params={"max_appendix_share": 0.5},
        )
    )
    other = _engine(
        _rule(
            "vol",
            "document_volume",
            params={"max_appendix_share": 0.3},
        )
    )

    assert _ids(matching.check(document), "vol") == []
    assert len(_ids(other.check(document), "vol")) == 1


# ---------------------------------------------------------------------
# Библиография: порядок
# ---------------------------------------------------------------------


def test_bibliography_foreign_sources_last() -> None:
    """Иностранный источник перед русскоязычным нарушает порядок."""
    broken = _doc(
        bibliography=[
            _bib("b1", 1, "Smith J. Psychology. London: Sage, 2020."),
            _bib("b2", 2, "Иванов И. И. Психология. М.: Наука, 2021. 200 с."),
        ]
    )
    good = _doc(
        bibliography=[
            _bib("b1", 1, "Иванов И. И. Психология. М.: Наука, 2021. 200 с."),
            _bib("b2", 2, "Smith J. Psychology. London: Sage, 2020."),
        ]
    )
    engine = _engine(
        _rule(
            "order",
            "bibliography_order",
            params={"foreign_sources_last": True},
        )
    )

    assert len(_ids(engine.check(broken), "order")) == 1
    assert _ids(engine.check(good), "order") == []


def test_bibliography_number_style() -> None:
    """Римские номера записей проверяются параметром number_style."""
    roman = _doc(
        bibliography=[
            _bib("b1", 1, "Иванов И. И. Психология. М.: Наука, 2021.", prefix="I"),
            _bib("b2", 2, "Петров П. П. Педагогика. М.: Наука, 2020.", prefix="II"),
        ]
    )
    arabic = _doc(
        bibliography=[
            _bib("b1", 1, "Иванов И. И. Психология. М.: Наука, 2021.", prefix="1"),
        ]
    )
    engine = _engine(
        _rule("order", "bibliography_order", params={"number_style": "roman"})
    )

    assert _ids(engine.check(roman), "order") == []
    assert len(_ids(engine.check(arabic), "order")) == 1


def test_bibliography_author_group_sort() -> None:
    """Работы одного автора упорядочиваются по названиям."""
    broken = _doc(
        bibliography=[
            _bib("b1", 1, "Яковлев Я. Я. Психология. М.: Наука, 2021."),
            _bib("b2", 2, "Яковлев Я. Я. Анатомия. М.: Наука, 2020."),
        ]
    )
    good = _doc(
        bibliography=[
            _bib("b1", 1, "Яковлев Я. Я. Анатомия. М.: Наука, 2020."),
            _bib("b2", 2, "Яковлев Я. Я. Психология. М.: Наука, 2021."),
        ]
    )
    other_authors = _doc(
        bibliography=[
            _bib("b1", 1, "Яковлев Я. Я. Психология. М.: Наука, 2021."),
            _bib("b2", 2, "Абрамов А. А. Педагогика. М.: Наука, 2020."),
        ]
    )
    engine = _engine(
        _rule("order", "bibliography_order", params={"author_group_sort": True})
    )

    assert len(_ids(engine.check(broken), "order")) == 1
    assert _ids(engine.check(good), "order") == []
    assert _ids(engine.check(other_authors), "order") == []


def test_bibliography_legal_force_order() -> None:
    """Акты располагаются по убыванию юридической силы."""
    broken = _doc(
        bibliography=[
            _bib("b1", 1, "Указ Президента РФ от 1 января 2020 г. N 1.", category="normative_act_law"),
            _bib("b2", 2, "Закон РФ от 1 января 2019 г. N 2.", category="normative_act_law"),
        ]
    )
    good = _doc(
        bibliography=[
            _bib("b1", 1, "Закон РФ от 1 января 2019 г. N 2.", category="normative_act_law"),
            _bib("b2", 2, "Указ Президента РФ от 1 января 2020 г. N 1.", category="normative_act_law"),
        ]
    )
    engine = _engine(
        _rule("order", "bibliography_order", params={"legal_force_order": True})
    )

    assert len(_ids(engine.check(broken), "order")) == 1
    assert _ids(engine.check(good), "order") == []


# ---------------------------------------------------------------------
# Библиография: формат
# ---------------------------------------------------------------------


def test_bibliography_require_author() -> None:
    """Автор обязателен, если включён require_author."""
    with_author = _doc(
        bibliography=[
            _bib("b1", 1, "Иванов И. И. Психология. М.: Наука, 2021. 200 с."),
        ]
    )
    without_author = _doc(
        bibliography=[
            _bib("b1", 1, "Психология. М.: Наука, 2021. 200 с."),
        ]
    )
    engine = _engine(
        _rule(
            "fmt",
            "bibliography_format",
            params={"require_author": True},
        )
    )

    assert _ids(engine.check(with_author), "fmt") == []
    assert len(_ids(engine.check(without_author), "fmt")) == 1


def test_bibliography_require_journal_title() -> None:
    """Название журнала после «//» обязательно при require_journal_title."""
    good = _doc(
        bibliography=[
            _bib(
                "b1",
                1,
                "Иванов И. И. Статья // Вопросы психологии. 2020. С. 10-20.",
                category="journal_article",
            ),
        ]
    )
    bad = _doc(
        bibliography=[
            _bib(
                "b1",
                1,
                "Иванов И. И. Статья // 2020. С. 10-20.",
                category="journal_article",
            ),
        ]
    )
    engine = _engine(
        _rule("fmt", "bibliography_format", params={"require_journal_title": True})
    )

    assert _ids(engine.check(good), "fmt") == []
    assert len(_ids(engine.check(bad), "fmt")) == 1


def test_bibliography_require_publication_place() -> None:
    """Место издания проверяется параметром require_publication_place."""
    good = _doc(
        bibliography=[
            _bib("b1", 1, "Иванов И. И. Психология. М.: Наука, 2021. 200 с."),
        ]
    )
    bad = _doc(
        bibliography=[
            _bib("b1", 1, "Иванов И. И. Психология. Наука, 2021. 200 с."),
        ]
    )
    engine = _engine(
        _rule(
            "fmt",
            "bibliography_format",
            params={"require_publication_place": True},
        )
    )

    assert _ids(engine.check(good), "fmt") == []
    assert len(_ids(engine.check(bad), "fmt")) == 1


def test_bibliography_require_publication_source() -> None:
    """Источник публикации обязателен для статьи и книги."""
    good = _doc(
        bibliography=[
            _bib(
                "b1",
                1,
                "Иванов И. И. Статья // Вопросы психологии. 2020. С. 10-20.",
                category="journal_article",
            ),
        ]
    )
    bad = _doc(
        bibliography=[
            _bib("b1", 1, "Иванов И. И. Название. 2020. 200 с."),
        ]
    )
    engine = _engine(
        _rule(
            "fmt",
            "bibliography_format",
            params={"require_publication_source": True},
        )
    )

    assert _ids(engine.check(good), "fmt") == []
    assert len(_ids(engine.check(bad), "fmt")) == 1


def test_bibliography_require_archive_details() -> None:
    """Архивные реквизиты обязательны для диссертации при require_archive_details."""
    good = _doc(
        bibliography=[
            _bib(
                "b1",
                1,
                "Иванов И. И. Психология личности: дис. ... канд. психол. наук. М., 2020. 200 с.",
                category="dissertation_autoref",
            ),
        ]
    )
    bad = _doc(
        bibliography=[
            _bib(
                "b1",
                1,
                "Иванов И. И. Психология личности. М., 2020. 200 с.",
                category="dissertation_autoref",
            ),
        ]
    )
    engine = _engine(
        _rule(
            "fmt",
            "bibliography_format",
            params={"require_archive_details": True},
        )
    )

    assert _ids(engine.check(good), "fmt") == []
    assert len(_ids(engine.check(bad), "fmt")) == 1


def test_bibliography_description_schema() -> None:
    """Порядок областей описания проверяется параметром description_schema."""
    ordered = _doc(
        bibliography=[
            _bib("b1", 1, "Иванов И. И. Психология. М.: Наука, 2021. 200 с."),
        ]
    )
    engine_ok = _engine(
        _rule(
            "fmt",
            "bibliography_format",
            params={"description_schema": ["author", "title", "year", "pages"]},
        )
    )
    engine_bad = _engine(
        _rule(
            "fmt",
            "bibliography_format",
            params={"description_schema": ["year", "author"]},
        )
    )

    assert _ids(engine_ok.check(ordered), "fmt") == []
    assert len(_ids(engine_bad.check(ordered), "fmt")) == 1


# ---------------------------------------------------------------------
# Формулы
# ---------------------------------------------------------------------


def test_equation_number_position() -> None:
    """Положение номера формулы проверяется параметром number_position."""
    document = _doc_with_equations(
        _doc(blocks=[_para("p_eq", "E = mc2 (1)")]),
        [_equation("eq1", number="(1)", alignment="right")],
    )
    matching = _engine(
        _rule(
            "eq",
            "equation_numbering",
            params={"require_numbering": True, "number_position": "right"},
        )
    )
    other = _engine(
        _rule(
            "eq",
            "equation_numbering",
            params={"require_numbering": True, "number_position": "center"},
        )
    )

    assert _ids(matching.check(document), "eq") == []
    assert len(_ids(other.check(document), "eq")) == 1


def test_equation_number_style() -> None:
    """Форма записи номера формулы проверяется параметром number_style."""
    roman = _doc_with_equations(
        _doc(blocks=[_para("p_eq", "E = mc2 (IV)")]),
        [_equation("eq1", number="(IV)")],
    )
    arabic = _doc_with_equations(
        _doc(blocks=[_para("p_eq", "E = mc2 (4)")]),
        [_equation("eq1", number="(4)")],
    )
    engine = _engine(
        _rule(
            "eq",
            "equation_numbering",
            params={"require_numbering": True, "number_style": "roman"},
        )
    )

    assert _ids(engine.check(roman), "eq") == []
    assert len(_ids(engine.check(arabic), "eq")) == 1
