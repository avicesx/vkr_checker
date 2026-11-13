"""Точные границы списка литературы в DOCX.

Проверяем устойчивость извлечения записей: записью считается только абзац с
номерным префиксом («1.», «2)») или его перенос строки, а обычный текст после
списка в библиографию не попадает. Отдельные кейсы фиксируют, что корректные
списки литературы по-прежнему разбираются полностью.

Документы собираются синтетически через DocxPackageBuilder, поэтому тесты не
зависят от бинарных образцов.
"""
from __future__ import annotations

from conftest import DocxPackageBuilder
from parser.core.schema import ParsedDocument
from parser.docx.pipeline import DocxParserPipeline

NUMBERING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="1">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:suff w:val="space"/></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="2">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="-"/><w:suff w:val="space"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
  <w:num w:numId="2"><w:abstractNumId w:val="2"/></w:num>
</w:numbering>"""


# ---------------------------------------------------------------------
# Сборка документов
# ---------------------------------------------------------------------


def _paragraph(text: str, num_id: int | None = None) -> str:
    """Абзац основного текста, при необходимости с автонумерацией Word."""
    num_pr = (
        f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num_id}"/></w:numPr>'
        if num_id is not None
        else ""
    )
    return (
        f"<w:p><w:pPr>{num_pr}<w:jc w:val=\"both\"/></w:pPr>"
        f"<w:r><w:t>{text}</w:t></w:r></w:p>"
    )


def _heading(text: str) -> str:
    """Абзац заголовка первого уровня."""
    return (
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:jc w:val="center"/></w:pPr>'
        f"<w:r><w:t>{text}</w:t></w:r></w:p>"
    )


def _parse(
    builder: DocxPackageBuilder,
    body: str,
    *,
    numbering: bool = False,
    filename: str = "structure.docx",
) -> ParsedDocument:
    """Собирает DOCX и возвращает результат полного разбора."""
    if numbering:
        builder.set_xml("word/numbering.xml", NUMBERING_XML)
    builder.set_body(body)
    return DocxParserPipeline(builder.build(filename)).parse()


def _bibliography(document: ParsedDocument) -> list:
    """Записи списка литературы разобранного документа."""
    return list(document.cross_ref_index.bibliography)


# ---------------------------------------------------------------------
# Границы списка
# ---------------------------------------------------------------------


def test_numbered_entries_then_plain_text_stops_collection(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Список из 3 записей и обычные абзацы после него дают ровно 3 записи."""
    body = (
        _heading("СПИСОК ЛИТЕРАТУРЫ")
        + _paragraph("1. Иванов И.И. Теория управления. — М.: Наука, 2020. — 200 с.")
        + _paragraph("2. Петров П.П. Практика управления. — СПб.: Питер, 2021. — 150 с.")
        + _paragraph("3. Сидоров С.С. Управление проектами. — М.: ЮНИТИ, 2019. — 300 с.")
        + _paragraph("Обычный текст после списка, не являющийся библиографией.")
        + _paragraph("Еще один абзац обычного текста без нумерации.")
        + _paragraph("И третий абзац обычного текста после списка.")
    )
    document = _parse(docx_builder, body)
    entries = _bibliography(document)

    assert len(entries) == 3
    assert [entry.raw_number_prefix for entry in entries] == ["1.", "2.", "3."]
    assert [entry.order_index for entry in entries] == [1, 2, 3]
    assert "Иванов" in entries[0].raw_text
    assert "Сидоров" in entries[2].raw_text
    assert all("Обычный текст" not in entry.raw_text for entry in entries)


def test_lettered_paragraphs_after_list_are_not_entries(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Пункты «а)», «б)» после списка записями литературы не считаются."""
    body = (
        _heading("СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ")
        + _paragraph("1. Иванов И.И. Теория управления. — М.: Наука, 2020.")
        + _paragraph("2. Петров П.П. Практика управления. — СПб.: Питер, 2021.")
        + _paragraph("а) пополнение собственного оборотного капитала.")
        + _paragraph("б) снижение издержек обращения предприятия.")
        + _paragraph("Выводы по результатам анализа деятельности организации.")
    )
    document = _parse(docx_builder, body)
    entries = _bibliography(document)

    assert len(entries) == 2
    assert [entry.raw_number_prefix for entry in entries] == ["1.", "2."]


def test_number_with_parenthesis_prefix_is_recognized(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Форма «1)» — такой же номерной префикс, как «1.»."""
    body = (
        _heading("СПИСОК ЛИТЕРАТУРЫ")
        + _paragraph("1) Иванов И.И. Теория управления. — М.: Наука, 2020.")
        + _paragraph("2) Петров П.П. Практика управления. — СПб.: Питер, 2021.")
        + _paragraph("Посторонний текст без номера.")
    )
    document = _parse(docx_builder, body)
    entries = _bibliography(document)

    assert len(entries) == 2
    assert [entry.raw_number_prefix for entry in entries] == ["1)", "2)"]


# ---------------------------------------------------------------------
# Перенос строки внутри записи
# ---------------------------------------------------------------------


def test_two_line_entry_is_merged_into_one_record(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Запись, разорванная на две строки, склеивается в одну запись."""
    body = (
        _heading("СПИСОК ЛИТЕРАТУРЫ")
        + _paragraph("1. Иванов И.И. Теория управления. — М.:")
        + _paragraph("Наука, 2020. — 200 с.")
        + _paragraph("2. Петров П.П. Практика управления. — СПб.: Питер, 2021.")
        + _paragraph("Обычный текст после списка.")
        + _paragraph("Еще один абзац обычного текста.")
    )
    document = _parse(docx_builder, body)
    entries = _bibliography(document)

    assert len(entries) == 2
    assert entries[0].raw_number_prefix == "1."
    assert "Наука, 2020" in entries[0].raw_text
    assert entries[0].year_extracted == 2020
    assert "Петров" in entries[1].raw_text


def test_entry_split_after_period_is_merged(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Перенос после точки, начинающийся с тире, — продолжение той же записи."""
    body = (
        _heading("СПИСОК ЛИТЕРАТУРЫ")
        + _paragraph("1. Иванов И.И. Теория управления.")
        + _paragraph("— М.: Наука, 2020. — 200 с.")
        + _paragraph("2. Петров П.П. Практика. — СПб., 2021.")
        + _paragraph("Посторонний текст после списка.")
    )
    document = _parse(docx_builder, body)
    entries = _bibliography(document)

    assert len(entries) == 2
    assert "Наука, 2020" in entries[0].raw_text
    assert "Посторонний текст" not in entries[0].raw_text


def test_continuation_does_not_swallow_body_text_after_terminal_period(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Строчный абзац после законченной записи не приклеивается к ней."""
    body = (
        _heading("СПИСОК ЛИТЕРАТУРЫ")
        + _paragraph("1. Иванов И.И. Теория управления. — М.: Наука, 2020.")
        + _paragraph("В результате анализа можно сделать следующие выводы:")
        + _paragraph("показатели деятельности организации улучшились.")
        + _paragraph("Рентабельность выросла на десять процентов.")
    )
    document = _parse(docx_builder, body)
    entries = _bibliography(document)

    assert len(entries) == 1
    assert "В результате анализа" not in entries[0].raw_text
    assert "показатели деятельности" not in entries[0].raw_text


# ---------------------------------------------------------------------
# Структурная нумерация Word
# ---------------------------------------------------------------------


def test_openxml_numbering_defines_entry_prefixes(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Автонумерация Word дает номерные префиксы записей."""
    body = (
        _heading("СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ")
        + _paragraph("Иванов И.И. Теория управления. — М.: Наука, 2020.", num_id=1)
        + _paragraph("Петров П.П. Практика управления. — СПб.: Питер, 2021.", num_id=1)
        + _paragraph("Сидоров С.С. Проекты. — М.: ЮНИТИ, 2019.", num_id=1)
        + _paragraph("Первый обычный пункт после списка.", num_id=2)
        + _paragraph("Второй обычный пункт после списка.", num_id=2)
    )
    document = _parse(docx_builder, body, numbering=True)
    entries = _bibliography(document)

    assert len(entries) == 3
    assert [entry.raw_number_prefix for entry in entries] == ["1.", "2.", "3."]


def test_bulleted_paragraphs_are_not_bibliography_entries(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Буллиты «-» не дают записей литературы, сбор прекращается."""
    body = (
        _heading("СПИСОК ЛИТЕРАТУРЫ")
        + _paragraph("1. Иванов И.И. Теория управления. — М.: Наука, 2020.", num_id=1)
        + _paragraph("2. Петров П.П. Практика управления. — СПб.: Питер, 2021.", num_id=1)
        + _paragraph("Первый обычный пункт после списка.", num_id=2)
        + _paragraph("Второй обычный пункт после списка.", num_id=2)
    )
    document = _parse(docx_builder, body, numbering=True)
    entries = _bibliography(document)

    assert len(entries) == 2
    assert [entry.raw_number_prefix for entry in entries] == ["1.", "2."]


# ---------------------------------------------------------------------
# Корректный список разбирается полностью
# ---------------------------------------------------------------------


def test_regular_numbered_list_keeps_parsed_fields(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Поля записей корректного списка литературы не меняются."""
    body = (
        _heading("СПИСОК ЛИТЕРАТУРЫ")
        + _paragraph("1. Иванов И.И. Теория управления. — М.: Наука, 2020. — 200 с.")
        + _paragraph("2. Петров П.П. Статья. //Журнал. 2021. № 2. С. 15-20.")
        + _paragraph(
            "3. Сидоров С.С. Электронный ресурс. — Режим доступа: "
            "https://example.org/article (дата обращения: 01.02.2022)."
        )
    )
    document = _parse(docx_builder, body)
    entries = _bibliography(document)

    assert len(entries) == 3
    assert [entry.order_index for entry in entries] == [1, 2, 3]
    assert entries[0].detected_category == "book_monograph"
    assert entries[1].detected_category == "journal_article"
    assert entries[1].has_double_slash_source is True
    assert entries[2].detected_category == "electronic_resource"
    assert entries[2].urls
