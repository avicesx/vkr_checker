"""Проверки исправлений дефектов точности из отчёта верификатора t38."""

from __future__ import annotations

from parser.core.schema import (
    BibliographyEntry,
    PageCountInfo,
    PageMarginsMm,
    PdfPageAudit,
    SectionProperties,
    TableBlock,
    TableCell,
    TableRow,
)
from rule_engine.rules import load_default_ruleset

from test_rules_urgpu import (
    _assessment,
    _borders,
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

# Один межстраничный интервал не нужен: проверяем только правки дефектов.


def _with_pages(document, total_pages: int):
    """Задает документу измеренное число страниц."""
    return document.model_copy(
        update={
            "page_count": PageCountInfo(
                total_pages=total_pages,
                method="docx_native_breaks",
                confidence="high",
            )
        }
    )


def _section(text_area_width_mm: float = 165.0) -> SectionProperties:
    """Сечение с заданной шириной текстового поля."""
    return SectionProperties(
        section_idx=0,
        margins=PageMarginsMm(),
        text_area_width_mm=text_area_width_mm,
    )


def _pdf_page(page_num: int, *, numbered: bool = True) -> PdfPageAudit:
    """Страница PDF с отрисованным номером в нижнем колонтитуле."""
    return PdfPageAudit(
        page_num=page_num,
        width_mm=210.0,
        height_mm=297.0,
        has_rendered_page_number=numbered,
        page_number_location="footer" if numbered else "none",
        page_number_alignment="center" if numbered else None,
        matches_physical_page_num=True,
    )


def _cell_table(block_id: str, run) -> TableBlock:
    """Таблица из одной ячейки с заданным текстовым прогоном."""
    paragraph = _para(f"{block_id}_p0", run.visible_text, container_type="table_cell", runs=[run])
    cell = TableCell(
        cell_id=f"{block_id}_c0",
        row_idx=0,
        col_idx=0,
        blocks=[paragraph],
        text_combined=run.visible_text,
        effective_borders=_borders(),
    )
    return TableBlock(
        block_id=block_id,
        section_idx=0,
        page_start_est=2,
        page_end_est=2,
        total_width_mm=160.0,
        table_borders=_borders(),
        rows=[TableRow(row_idx=0, cells=[cell])],
    )


def _bib(bib_id: str, block_id: str, order_index: int, raw_text: str) -> BibliographyEntry:
    """Запись списка литературы."""
    return BibliographyEntry(
        bib_id=bib_id,
        block_id=block_id,
        order_index=order_index,
        raw_text=raw_text,
    )


def test_appendix_share_counts_top_level_ranges_once() -> None:
    """D-A: вложенное «Продолжение приложения» не удваивает страницы."""
    document = _with_pages(
        _doc(
            tree=[
                _node(
                    "app_a",
                    "Приложение А",
                    role="appendix",
                    page_start=1,
                    page_end=5,
                    children=[
                        _node(
                            "app_a_cont",
                            "Продолжение приложения А",
                            role="appendix",
                            page_start=3,
                            page_end=5,
                        )
                    ],
                ),
                _node("app_b", "Приложение Б", role="appendix", page_start=6, page_end=8),
            ]
        ),
        20,
    )
    report = _engine(
        _rule("share", "document_volume", params={"max_appendix_share": 0.5})
    ).check(document)
    assessment = _assessment(report, "share")
    assert assessment.status == "pass"
    assert abs(assessment.details["appendix_share"] - 0.4) < 1e-6
    assert _ids(report, "share") == []


def test_appendix_share_reports_excess() -> None:
    """D-A негатив: доля приложений выше предела дает находку."""
    document = _with_pages(
        _doc(tree=[_node("app_a", "Приложение А", role="appendix", page_start=1, page_end=9)]),
        20,
    )
    report = _engine(
        _rule("share", "document_volume", params={"max_appendix_share": 0.3})
    ).check(document)
    assert len(_ids(report, "share")) == 1


def test_table_header_absent_goes_to_not_assessed() -> None:
    """D-B: без разметки шапки находок нет, оценка not_assessed."""
    document = _doc(blocks=[_table("t1", texts=("Ячейка",), header=False)])
    report = _engine(
        _rule("head", "table_header_format", params={"require_header_row": True})
    ).check(document)
    assessment = _assessment(report, "head")
    assert assessment.status == "not_assessed"
    assert _ids(report, "head") == []
    assert assessment.details["headerless_tables"] == 1


def test_table_header_present_is_checked() -> None:
    """D-B негатив: размеченная шапка проверяется как прежде."""
    document = _doc(blocks=[_table("t1", texts=("Наименование",), header=True)])
    report = _engine(
        _rule("head", "table_header_format", params={"require_header_row": True})
    ).check(document)
    assert _assessment(report, "head").status == "pass"


def test_paragraph_style_internal_ids_are_not_violations() -> None:
    """D-C: числовые и служебные идентификаторы стилей не сравниваются."""
    blocks = [
        _para("p1", "Первый абзац").model_copy(update={"style_id": "22"}),
        _para("p2", "Второй абзац").model_copy(update={"style_id": "BodyText2"}),
        _para("p3", "Третий абзац").model_copy(update={"style_id": "a0"}),
    ]
    report = _engine(
        _rule(
            "style",
            "paragraph_style",
            params={"allowed_styles": ["Normal"], "forbid_direct_formatting": False},
        )
    ).check(_doc(blocks=blocks))
    assessment = _assessment(report, "style")
    assert _ids(report, "style") == []
    assert assessment.details["styles_not_comparable"] == 2


def test_paragraph_style_custom_style_is_reported() -> None:
    """D-C негатив: читаемое имя чужого стиля по-прежнему нарушение."""
    blocks = [_para("p1", "Абзац").model_copy(update={"style_id": "МойСтиль"})]
    report = _engine(
        _rule(
            "style",
            "paragraph_style",
            params={"allowed_styles": ["Normal"], "forbid_direct_formatting": False},
        )
    ).check(_doc(blocks=blocks))
    assert _ids(report, "style") == ["style:p1:style"]


def test_number_format_skips_identifiers_and_versions() -> None:
    """D-D: «FR-2.3» и «Индустрия 4.0» не считаются дробными числами."""
    blocks = [_para("p1", "Модуль FR-2.3"), _para("p2", "Индустрия 4.0")]
    report = _engine(
        _rule("num", "number_format", params={"decimal_separator": "comma"})
    ).check(_doc(blocks=blocks))
    assert _ids(report, "num") == []


def test_number_format_reports_decimal_point() -> None:
    """D-D негатив: «2.5» в обычном тексте остается находкой."""
    report = _engine(
        _rule("num", "number_format", params={"decimal_separator": "comma"})
    ).check(_doc(blocks=[_para("p1", "Срок 2.5 дня")]))
    assert _ids(report, "num") == ["num:p1:decimal"]


def test_number_format_accepts_thousands_separator() -> None:
    """D-D: «25.000» — разделитель тысяч, а не дробная часть."""
    report = _engine(
        _rule("num", "number_format", params={"decimal_separator": "comma"})
    ).check(_doc(blocks=[_para("p1", "Стоимость 25.000 рублей")]))
    assert _ids(report, "num") == []


def test_bibliography_author_group_compares_titles_of_one_author() -> None:
    """D-E: у одного автора работы идут по названиям, разные авторы не сравниваются."""
    blocks = [
        _para("b1", "Иванов И. И. Яблоко. — М., 2000."),
        _para("b2", "Иванов И. И. Арбуз. — М., 2001."),
        _para("b3", "Петров П. П. Апельсин. — М., 2002."),
        _para("b4", "Постановление Правительства РФ от 01.01.2000 N 1."),
        _para("b5", "Иванов И. И. Береза. — М., 2003."),
    ]
    bibliography = [
        _bib("bib_1", "b1", 1, blocks[0].text_raw),
        _bib("bib_2", "b2", 2, blocks[1].text_raw),
        _bib("bib_3", "b3", 3, blocks[2].text_raw),
        _bib("bib_4", "b4", 4, blocks[3].text_raw),
        _bib("bib_5", "b5", 5, blocks[4].text_raw),
    ]
    report = _engine(
        _rule(
            "order",
            "bibliography_order",
            params={"author_group_sort": True, "allow_gaps": True},
        )
    ).check(_doc(blocks=blocks, bibliography=bibliography))
    assert _ids(report, "order") == ["order:bib_2:author_order"]


def test_table_alignment_text_has_no_double_preposition() -> None:
    """D-F: описание находки не содержит «по по центру»."""
    table = _table("t1", total_width_mm=80.0).model_copy(update={"alignment": "center"})
    report = _engine(
        _rule("align", "table_layout", params={"alignment": "left", "require_all_sides_borders": False})
    ).check(_doc(blocks=[table], sections=[_section()]))
    violation = report.violations[0]
    assert "по по" not in violation.found_text
    assert "таблица выровнена:" in violation.expected_text
def test_first_numbered_page_has_block_address() -> None:
    """D-G: у находки first_numbered_page есть block_id первой строки страницы."""
    document = _doc(
        blocks=[_para("p1", "Титульный лист", page=1), _para("p2", "Текст", page=2)],
        pdf_pages=[_pdf_page(1), _pdf_page(2), _pdf_page(3)],
    )
    report = _engine(
        _rule(
            "pagenum",
            "page_number_position",
            params={"zone": "footer", "alignment": "center", "first_numbered_page": 2},
        )
    ).check(document)
    violation = next(v for v in report.violations if v.violation_id.endswith("first_numbered_page"))
    assert violation.location.block_id == "p1"


def test_smaller_than_body_text_allows_above_ten_points() -> None:
    """D-I: кегль меньше основного, но не ниже 10 pt, допустим."""
    body = _para("p1", "Основной текст", runs=[_run("r1", "Основной текст", size_pt=14.0)])
    table = _cell_table("t1", _run("r2", "ячейка", size_pt=12.0))
    report = _engine(
        _rule(
            "font",
            "font",
            params={"smaller_than_body_text": True, "size_tolerance_pt": 0.5},
            scope="table_cells",
        )
    ).check(_doc(blocks=[body, table]))
    assert _ids(report, "font") == []


def test_smaller_than_body_text_reports_below_ten_points() -> None:
    """D-I негатив: кегль ниже 10 pt нарушает предел."""
    body = _para("p1", "Основной текст", runs=[_run("r1", "Основной текст", size_pt=14.0)])
    table = _cell_table("t1", _run("r2", "ячейка", size_pt=9.0))
    report = _engine(
        _rule(
            "font",
            "font",
            params={"smaller_than_body_text": True, "size_tolerance_pt": 0.5},
            scope="table_cells",
        )
    ).check(_doc(blocks=[body, table]))
    assert len(_ids(report, "font")) == 1


def test_heading_prefix_word_only_for_chapters() -> None:
    """D-J: слово перед номером требуется у глав и не требуется у подразделов."""
    blocks = [
        _heading("h1", "1 ВВЕДЕНИЕ", level=1, role="main_chapter"),
        _heading("h2", "2.1 ПОДРАЗДЕЛ", level=2, role="sub_section"),
        _heading("h3", "Глава 2. МЕТОДЫ", level=1, role="main_chapter"),
    ]
    tree = [
        _node("n1", "1 ВВЕДЕНИЕ", role="main_chapter", level=1, heading_block_id="h1"),
        _node("n3", "Глава 2. МЕТОДЫ", role="main_chapter", level=1, heading_block_id="h3"),
        _node("n2", "2.1 ПОДРАЗДЕЛ", role="sub_section", level=2, heading_block_id="h2"),
    ]
    report = _engine(
        _rule("head", "heading_format", params={"prefix_word": "Глава"})
    ).check(_doc(blocks=blocks, tree=tree))
    assert _ids(report, "head") == ["head:h1"]


def test_table_alignment_full_width_is_not_observed() -> None:
    """D-K: у таблицы на всю ширину выравнивание не наблюдаемо."""
    table = _table("t1", total_width_mm=164.0).model_copy(update={"alignment": "center"})
    report = _engine(
        _rule("align", "table_layout", params={"alignment": "left", "require_all_sides_borders": False})
    ).check(_doc(blocks=[table], sections=[_section()]))
    assessment = _assessment(report, "align")
    assert _ids(report, "align") == []
    assert assessment.details["alignment_not_observed"] == 1


def test_deferred_codes_are_not_in_default_ruleset() -> None:
    """D-H: шумные коды остаются вне набора по умолчанию."""
    codes = {rule.code for rule in load_default_ruleset().rules}
    assert not codes & {
        "source_note_figure",
        "source_note_table",
        "table_units",
        "citation_required",
        "number_format",
        "paragraph_style",
        "figure_spacing",
    }
