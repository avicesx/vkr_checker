"""Интеграционная регрессия: ВКР с титульным листом и листингом в приложении.

Фикстура собирается `DocxPackageBuilder` и воспроизводит оба кластера шума из
отчета по реальной работе: титульный лист (реквизиты вуза 11 pt, подписные
строки 10 pt) и листинг C# в приложении (стиль 6112, Courier New 12 pt, больше
двадцати абзацев подряд). В отчете эти зоны дали 471 и 485 срабатываний шрифта и
отступа, хотя относятся к служебному оформлению, а не к основному тексту.

Фикстура воспроизводит природу кластеров компактно: 12 строк титула и 27 строк
листинга дают при выключенном фильтре по 37 нарушений на каждое текстовое
правило. С фильтром по умолчанию на весь документ остается два нарушения — от
заведомо кривого абзаца основного текста.

Тесты фиксируют три свойства:

* контекст-фильтр по умолчанию снимает шум титула и листинга, а штатные
  правила заголовков, оглавления и обязательных разделов продолжают работать;
* настоящий дефект основного текста (Arial 12 pt без красной строки) не
  скрывается: он дает ровно одно нарушение шрифта и одно нарушение отступа;
* при `exclude_contexts: []` те же абзацы снова попадают в отчет, то есть
  фикстура действительно содержит оба кластера, а не пустые зоны.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

import pytest
from conftest import DocxPackageBuilder
from parser.core.schema import ParsedDocxDocument, ParagraphBlock, TocContainerBlock
from parser.docx.pipeline import DocxParserPipeline
from rule_engine import (
    DEFAULT_RULESET_PATH,
    RuleEngine,
    RuleSet,
    load_default_ruleset,
    load_ruleset,
)
from rule_engine.context import classify_document_contexts

#: Стиль листинга из реального отчета: Courier New 12 pt без красной строки.
LISTING_STYLE_ID = "6112"

#: Титульный лист: реквизиты вуза 11 pt и подписные строки 10 pt.
TITLE_LINES: Tuple[Tuple[str, float], ...] = (
    ("Министерство науки и высшего образования Российской Федерации", 11.0),
    (
        "Федеральное государственное бюджетное образовательное учреждение "
        "высшего образования",
        11.0,
    ),
    ("Кафедра информационных систем и технологий", 11.0),
    ("ДИПЛОМНАЯ РАБОТА", 11.0),
    (
        "на тему выпускной квалификационной работы "
        "«Разработка информационной системы учёта заявок»",
        11.0,
    ),
    ("Студент: Иванов И. И., группа ИС-41", 11.0),
    ("Руководитель: кандидат технических наук, доцент Петров П. П.", 11.0),
    ("Консультант: Сидоров С. С.", 11.0),
    ("Заведующий кафедрой: Смирнов А. А.", 11.0),
    ("Допустить к защите: заведующий кафедрой Смирнов А. А.", 11.0),
    ("(М.П.)        (Подпись)", 10.0),
    ("Иваново 2025 год", 11.0),
)

#: Разделы документа: заголовок и уровень. Приложение в оглавление не входит.
SECTIONS: Tuple[Tuple[str, int], ...] = (
    ("ВВЕДЕНИЕ", 1),
    ("1 Основные понятия работы", 1),
    ("1.1 Определение ключевых понятий", 2),
    ("ЗАКЛЮЧЕНИЕ", 1),
    ("СПИСОК ЛИТЕРАТУРЫ", 1),
    ("ПРИЛОЖЕНИЕ А", 1),
)

#: Разделы, которые обязаны попасть в оглавление.
TOC_SECTIONS = SECTIONS[:-1]

#: Абзацы основного текста по разделам: Times New Roman 14 pt, отступ 12,5 мм.
SECTION_BODY: Dict[str, Tuple[str, ...]] = {
    "ВВЕДЕНИЕ": (
        "Актуальность работы определяется ростом объёма заявок в организациях.",
        "Целью работы является автоматизация обработки и распределения заявок.",
    ),
    "1 Основные понятия работы": (
        "Информационная система учёта заявок автоматизирует обработку обращений.",
    ),
    "1.1 Определение ключевых понятий": (
        "Заявка описывается набором атрибутов и историей изменения состояний.",
    ),
    "ЗАКЛЮЧЕНИЕ": (
        "В ходе работы разработана информационная система учёта заявок.",
    ),
    "СПИСОК ЛИТЕРАТУРЫ": (
        "Иванов И. И. Проектирование информационных систем. Москва: Юрайт, 2023. 320 с.",
    ),
    "ПРИЛОЖЕНИЕ А": (
        "Ниже приведён фрагмент контроллера системы учёта заявок.",
    ),
}

#: Заведомо кривой абзац основного текста: Arial 12 pt и без красной строки.
BAD_BODY_TEXT = (
    "Этот абзац набран гарнитурой Arial двенадцатого кегля и без красной строки."
)

#: Строки листинга C#: фрагмент контроллера ASP.NET Core из приложения.
CODE_LINES: Tuple[str, ...] = (
    "using System;",
    "using System.Collections.Generic;",
    "using System.Linq;",
    "using Microsoft.AspNetCore.Mvc;",
    "",
    "namespace RequestAccounting.Controllers",
    "{",
    "    [ApiController]",
    '    [Route("api/[controller]")]',
    "    public class RequestsController : ControllerBase",
    "    {",
    "        private readonly IRequestService _service;",
    "",
    "        public RequestsController(IRequestService service)",
    "        {",
    "            _service = service;",
    "        }",
    "",
    "        // Получение списка активных заявок",
    "        [HttpGet]",
    "        public ActionResult<IEnumerable<RequestDto>> GetAll()",
    "        {",
    "            var items = _service.GetActive();",
    "            return Ok(items);",
    "        }",
    "    }",
    "}",
)

#: Стиль листинга: моноширинная гарнитура и нулевая красная строка.
LISTING_STYLE_XML = f"""
<w:style w:type="paragraph" w:styleId="{LISTING_STYLE_ID}">
  <w:name w:val="Код C#"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr>
    <w:ind w:firstLine="0"/>
    <w:spacing w:after="0" w:line="240" w:lineRule="auto"/>
  </w:pPr>
  <w:rPr>
    <w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New"/>
    <w:sz w:val="24"/>
  </w:rPr>
</w:style>
"""

#: Титул «Содержание» над записями оглавления.
TOC_TITLE_XML = (
    '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
    "<w:r><w:t>СОДЕРЖАНИЕ</w:t></w:r></w:p>"
)


def _xml_text(text: str) -> str:
    """Экранирует текст для вставки в w:t: в листинге встречаются «<» и «>»."""
    return escape(text)


def _half_points(size_pt: float) -> int:
    """Переводит кегль в полупункты OOXML (w:sz)."""
    return int(round(size_pt * 2))


def _title_paragraph(text: str, size_pt: float, *, page_break_after: bool = False) -> str:
    """Собирает строку титульного листа: по центру, без отступа, кегль методички."""
    page_break_run = '<w:r><w:br w:type="page"/></w:r>' if page_break_after else ""
    return (
        '<w:p><w:pPr><w:jc w:val="center"/><w:ind w:firstLine="0"/></w:pPr>'
        '<w:r><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>'
        f'<w:sz w:val="{_half_points(size_pt)}"/></w:rPr>'
        f'<w:t xml:space="preserve">{_xml_text(text)}</w:t></w:r>'
        f"{page_break_run}</w:p>"
    )


def _body_paragraph(text: str) -> str:
    """Собирает абзац основного текста: параметры берет из стиля Normal."""
    return f'<w:p><w:r><w:t xml:space="preserve">{_xml_text(text)}</w:t></w:r></w:p>'


def _bad_body_paragraph(text: str) -> str:
    """Собирает абзац с нарушением: Arial 12 pt и firstLine=0."""
    return (
        '<w:p><w:pPr><w:ind w:firstLine="0"/></w:pPr>'
        '<w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>'
        '<w:sz w:val="24"/></w:rPr>'
        f'<w:t xml:space="preserve">{_xml_text(text)}</w:t></w:r></w:p>'
    )


def _heading_paragraph(text: str, level: int, *, alignment: str = "center") -> str:
    """Собирает заголовок: стиль Heading1 или явный outlineLvl для подраздела."""
    props = (
        '<w:pStyle w:val="Heading1"/>'
        if level == 1
        else f'<w:outlineLvl w:val="{level - 1}"/>'
    )
    return (
        f'<w:p><w:pPr>{props}<w:jc w:val="{alignment}"/></w:pPr>'
        f'<w:r><w:t xml:space="preserve">{_xml_text(text)}</w:t></w:r></w:p>'
    )


def _toc_entry(text: str, page: str) -> str:
    """Собирает запись оглавления: название, табуляция и номер страницы."""
    return (
        f'<w:p><w:r><w:t xml:space="preserve">{_xml_text(text)}</w:t></w:r>'
        f"<w:r><w:tab/><w:t>{page}</w:t></w:r></w:p>"
    )


def _listing_paragraph(code: str) -> str:
    """Собирает строку листинга со стилем 6112."""
    return (
        f'<w:p><w:pPr><w:pStyle w:val="{LISTING_STYLE_ID}"/></w:pPr>'
        f'<w:r><w:t xml:space="preserve">{_xml_text(code)}</w:t></w:r></w:p>'
    )


def _fixture_body(
    *,
    misaligned_heading: Optional[str] = None,
    toc_entry_omitted: Optional[str] = None,
) -> str:
    """Собирает тело фикстуры: титул, оглавление, разделы и приложение с кодом.

    :param misaligned_heading: заголовок, который нужно выровнять по левому краю
        (положительный контроль правила заголовков).
    :param toc_entry_omitted: раздел, запись которого не попадает в оглавление
        (положительный контроль правила оглавления).
    """
    title_parts = [
        _title_paragraph(text, size_pt, page_break_after=index == len(TITLE_LINES) - 1)
        for index, (text, size_pt) in enumerate(TITLE_LINES)
    ]

    toc_entries = "".join(
        _toc_entry(text, str(3 + index))
        for index, (text, _level) in enumerate(TOC_SECTIONS)
        if text != toc_entry_omitted
    )

    content_parts: List[str] = []
    for title, level in SECTIONS:
        alignment = "left" if title == misaligned_heading else "center"
        content_parts.append(_heading_paragraph(title, level, alignment=alignment))
        content_parts.extend(_body_paragraph(text) for text in SECTION_BODY[title])
        if title == "1 Основные понятия работы":
            content_parts.append(_bad_body_paragraph(BAD_BODY_TEXT))

    listing = "".join(_listing_paragraph(code) for code in CODE_LINES)
    return (
        "".join(title_parts)
        + TOC_TITLE_XML
        + toc_entries
        + "".join(content_parts)
        + listing
    )


def _parse(docx_builder: DocxPackageBuilder, body: str) -> ParsedDocxDocument:
    """Собирает DOCX из тела фикстуры и разбирает его в режиме docx_only."""
    docx_builder.add_style_xml(LISTING_STYLE_XML)
    docx_builder.set_body(body)
    return DocxParserPipeline(docx_builder.build("vkr_context.docx")).parse_docx_only()


@pytest.fixture
def vkr_document(docx_builder: DocxPackageBuilder) -> ParsedDocxDocument:
    """Разобранный документ фикстуры: титул, оглавление, разделы, приложение."""
    return _parse(docx_builder, _fixture_body())


def _paragraphs(document: ParsedDocxDocument) -> List[ParagraphBlock]:
    """Абзацы верхнего уровня в порядке следования."""
    return [block for block in document.blocks if isinstance(block, ParagraphBlock)]


def _block_id(document: ParsedDocxDocument, prefix: str) -> str:
    """Возвращает block_id первого абзаца, начинающегося с указанного текста."""
    for paragraph in _paragraphs(document):
        if paragraph.text_visible.strip().startswith(prefix):
            return paragraph.block_id
    raise AssertionError(f"абзац «{prefix}» не найден в документе")


def _title_block_ids(document: ParsedDocxDocument) -> List[str]:
    """block_id всех строк титульного листа."""
    return [_block_id(document, text[:40]) for text, _size in TITLE_LINES]


def _listing_block_ids(document: ParsedDocxDocument) -> List[str]:
    """block_id всех абзацев листинга (включая пустые строки внутри кода)."""
    return [
        paragraph.block_id
        for paragraph in _paragraphs(document)
        if paragraph.style_id == LISTING_STYLE_ID
    ]


def _text_rules_engine(exclude_contexts: Optional[List[str]] = None) -> RuleEngine:
    """Собирает движок из правила шрифта и правила отступа с общей областью."""
    target: Dict[str, object] = {"scope": "body_text"}
    if exclude_contexts is not None:
        target["exclude_contexts"] = exclude_contexts
    payload = {
        "ruleset_id": "vkr-context-integration",
        "ruleset_version": "1.0.0",
        "rules": [
            {
                "rule_id": "font_body_text",
                "kind": "font",
                "message": "Основной текст набирается Times New Roman, 14 pt",
                "target": target,
                "expected": {"family": "Times New Roman", "size_pt": 14.0},
            },
            {
                "rule_id": "indent_first_line",
                "kind": "indent",
                "message": "Абзац основного текста начинается с отступа 12,5 мм",
                "target": target,
                "expected": {"first_line_mm": 12.5, "tolerance_mm": 0.5},
            },
        ],
    }
    return RuleEngine(RuleSet.model_validate(payload))


def test_fixture_reproduces_title_and_listing_contexts(
    vkr_document: ParsedDocxDocument,
) -> None:
    """Титул и листинг распознаны как служебные зоны, остальное — основной текст."""
    contexts = classify_document_contexts(vkr_document.blocks)

    title_ids = _title_block_ids(vkr_document)
    listing_ids = _listing_block_ids(vkr_document)

    assert len(title_ids) == len(TITLE_LINES)
    assert len(listing_ids) == len(CODE_LINES) >= 20
    assert len(title_ids) + len(listing_ids) == 39
    assert all(contexts[block_id] == "title_page" for block_id in title_ids)
    assert all(contexts[block_id] == "code_listing" for block_id in listing_ids)
    assert contexts[_block_id(vkr_document, BAD_BODY_TEXT[:20])] == "body"

    headings = [
        paragraph
        for paragraph in _paragraphs(vkr_document)
        if paragraph.heading_info is not None
        and paragraph.heading_info.is_heading
        and paragraph.text_visible.strip() in {title for title, _level in SECTIONS}
    ]
    # Заголовок «СОДЕРЖАНИЕ» тоже структурный, поэтому сравниваем по разделам.
    assert len(headings) == len(SECTIONS)
    assert all(contexts[paragraph.block_id] == "body" for paragraph in headings)


def test_default_ruleset_reports_no_violations_from_service_zones(
    vkr_document: ParsedDocxDocument,
) -> None:
    """Штатный набор не находит нарушений шрифта и отступа на титуле и листинге."""
    report = RuleEngine(load_default_ruleset()).check(vkr_document)
    contexts = classify_document_contexts(vkr_document.blocks)
    service_ids = set(_title_block_ids(vkr_document)) | set(
        _listing_block_ids(vkr_document)
    )

    assert report.summary.by_kind == {"font": 1, "indent": 1}
    assert report.summary.by_rule == {"font_body_text": 1, "indent_first_line": 1}
    assert report.summary.violations_total == 2
    assert not service_ids & {
        violation.location.block_id for violation in report.violations
    }
    assert all(
        contexts[violation.location.block_id] == "body"
        for violation in report.violations
    )


def test_bad_body_paragraph_gives_exactly_font_and_indent(
    vkr_document: ParsedDocxDocument,
) -> None:
    """Кривой абзац основного текста ловится ровно двумя нарушениями."""
    report = RuleEngine(load_default_ruleset()).check(vkr_document)
    bad_block_id = _block_id(vkr_document, BAD_BODY_TEXT[:20])
    by_rule = {violation.rule_id: violation for violation in report.violations}

    assert set(by_rule) == {"font_body_text", "indent_first_line"}
    font_violation = by_rule["font_body_text"]
    assert font_violation.location.block_id == bad_block_id
    assert font_violation.found["family"] == "Arial"
    assert font_violation.found["size_pt"] == pytest.approx(12.0)
    assert font_violation.severity == "error"

    indent_violation = by_rule["indent_first_line"]
    assert indent_violation.location.block_id == bad_block_id
    assert indent_violation.found["first_line_mm"] == pytest.approx(0.0, abs=0.01)
    assert indent_violation.severity == "error"


def test_text_rules_count_only_body_paragraphs(
    vkr_document: ParsedDocxDocument,
) -> None:
    """Счетчики текстовых правил включают только абзацы, прошедшие фильтр."""
    compliant_count = sum(len(texts) for texts in SECTION_BODY.values())
    report = _text_rules_engine().check(vkr_document)

    assert report.summary.by_rule == {"font_body_text": 1, "indent_first_line": 1}
    assert report.summary.objects_checked == 2 * (compliant_count + 1)


def test_unfiltered_text_rules_reproduce_service_zone_noise(
    vkr_document: ParsedDocxDocument,
) -> None:
    """exclude_contexts=[] возвращает проверку титула и листинга: шум воспроизводим."""
    compliant_count = sum(len(texts) for texts in SECTION_BODY.values())
    non_empty_code = [code for code in CODE_LINES if code.strip()]
    noise_per_rule = len(TITLE_LINES) + len(non_empty_code) + 1

    report = _text_rules_engine(exclude_contexts=[]).check(vkr_document)
    flagged = {violation.location.block_id for violation in report.violations}
    empty_listing_ids = {
        paragraph.block_id
        for paragraph in _paragraphs(vkr_document)
        if paragraph.style_id == LISTING_STYLE_ID and paragraph.is_empty_or_whitespace
    }
    symbolic_listing_ids = {
        paragraph.block_id
        for paragraph in _paragraphs(vkr_document)
        if paragraph.style_id == LISTING_STYLE_ID
        and paragraph.text_visible.strip()
        and not any(char.isalnum() for char in paragraph.text_visible)
    }
    expected_flagged = (
        set(_title_block_ids(vkr_document))
        | set(_listing_block_ids(vkr_document)) - empty_listing_ids
        | {_block_id(vkr_document, BAD_BODY_TEXT[:20])}
    )

    # Строки листинга из одних скобок — символические раны: правило шрифта их не
    # проверяет (кегль и начертание знаков не осмысленны), правило отступа — проверяет.
    font_noise = noise_per_rule - len(symbolic_listing_ids)
    assert symbolic_listing_ids

    assert report.summary.by_rule == {
        "font_body_text": font_noise,
        "indent_first_line": noise_per_rule,
    }
    assert report.summary.objects_checked == 2 * (noise_per_rule + compliant_count)
    assert flagged == expected_flagged


def test_heading_and_toc_rules_are_not_regressed(
    vkr_document: ParsedDocxDocument,
) -> None:
    """Правила заголовков, оглавления и разделов отрабатывают без нарушений."""
    report = RuleEngine(load_default_ruleset()).check(vkr_document)

    assert report.summary.rules_applied == 9
    for rule_id in (
        "page_margins",
        "heading_levels",
        "heading_numbering",
        "heading_format_text",
        "heading_alignment",
        "required_sections",
        "toc",
    ):
        assert rule_id not in report.summary.by_rule

    toc_blocks = [
        block for block in vkr_document.blocks if isinstance(block, TocContainerBlock)
    ]
    assert len(toc_blocks) == 1
    entries = toc_blocks[0].entries
    assert len(entries) == len(TOC_SECTIONS)
    assert all(entry.matched_heading_block_id is not None for entry in entries)
    # Структурные правила посчитали свои объекты, а не пропустили их: их доля
    # счетчика не нулевая (заголовки, записи оглавления и обязательные разделы).
    text_objects = 2 * (sum(len(texts) for texts in SECTION_BODY.values()) + 1)
    assert report.summary.objects_checked > text_objects


def test_heading_rule_still_flags_misaligned_heading(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Заголовок по левому краю ловится правилом выравнивания, а не фильтруется."""
    document = _parse(
        docx_builder,
        _fixture_body(misaligned_heading="ЗАКЛЮЧЕНИЕ"),
    )

    report = RuleEngine(load_ruleset(DEFAULT_RULESET_PATH)).check(document)

    assert report.summary.by_rule.get("heading_alignment") == 1
    heading_violation = next(
        violation
        for violation in report.violations
        if violation.rule_id == "heading_alignment"
    )
    assert heading_violation.location.block_id == _block_id(document, "ЗАКЛЮЧЕНИЕ")
    assert heading_violation.found["alignment"] == "left"
    assert report.summary.by_kind == {"font": 1, "indent": 1, "heading_format": 1}


def test_toc_rule_still_flags_missing_entry(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Раздел, забытый в оглавлении, ловится правилом оглавления."""
    document = _parse(
        docx_builder,
        _fixture_body(toc_entry_omitted="ЗАКЛЮЧЕНИЕ"),
    )

    report = RuleEngine(load_default_ruleset()).check(document)

    assert report.summary.by_rule.get("toc") == 1
    toc_violation = next(
        violation for violation in report.violations if violation.rule_id == "toc"
    )
    assert "toc:missing:" in toc_violation.violation_id
    assert toc_violation.found["toc_entry"] is False
    assert toc_violation.location.snippet == "ЗАКЛЮЧЕНИЕ"
    assert report.summary.by_kind == {"font": 1, "indent": 1, "toc": 1}


def test_fixture_report_is_deterministic(vkr_document: ParsedDocxDocument) -> None:
    """Три прогона фикстуры дают побайтово одинаковый JSON отчета."""
    engine = RuleEngine(load_default_ruleset())

    first = engine.check(vkr_document)
    second = engine.check(vkr_document)
    third = engine.check(vkr_document)

    first_json = first.model_dump_json(indent=2)
    assert first_json == second.model_dump_json(indent=2)
    assert first_json == third.model_dump_json(indent=2)
    assert first.summary.violations_total == 2


# =====================================================================
# СЕРВИСНЫЕ ЛИСТЫ: ТИТУЛ ПОСЛЕ ЛИСТА ЗАДАНИЯ
# =====================================================================

#: Строки листа задания: так они выглядят в ВКР УрФУ (12 pt, без отступа).
TASK_SHEET_LINES: Tuple[str, ...] = (
    "ЗАДАНИЕ",
    "на выполнение выпускной квалификационной работы",
    "студента Бутакова Никиты Сергеевича группы РИ-420932",
    "(фамилия, имя, отчество)",
    "4. Календарный план",
)

#: Титул, который в ВКР УрФУ стоит после листа задания (11 pt, по центру).
TITLE_AFTER_TASK_LINES: Tuple[str, ...] = (
    "Министерство науки и высшего образования Российской Федерации",
    "«Уральский федеральный университет имени первого Президента России Б.Н. Ельцина»",
    "Кафедра/департамент Школа бакалавриата",
    "Екатеринбург",
    "2026",
    "(подпись)",
)


def test_title_after_task_sheet_is_not_checked_as_body_text(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Лист задания и следующий за ним титул не дают нарушений шрифта и отступа.

    Строки набраны Arial 12 pt без красной строки — по правилам «основной
    текст» каждая из них была бы нарушением. Но это служебные страницы: лист
    задания занимает две страницы и открывает зону, а титул продолжает ее на
    третьей странице, хотя прежний фильтр знал только про страницы 1–2.
    """
    page_break = '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
    sheet_first_page = "".join(
        _bad_body_paragraph(line) for line in TASK_SHEET_LINES[:3]
    )
    sheet_second_page = "".join(
        _bad_body_paragraph(line) for line in TASK_SHEET_LINES[3:]
    )
    title = "".join(_bad_body_paragraph(line) for line in TITLE_AFTER_TASK_LINES)
    body = (
        sheet_first_page
        + page_break
        + sheet_second_page
        + page_break
        + title
        + _heading_paragraph("ВВЕДЕНИЕ", 1)
        + _bad_body_paragraph(BAD_BODY_TEXT)
        + _body_paragraph("Актуальность работы определяется ростом объёма заявок.")
    )

    document = _parse(docx_builder, body)
    contexts = classify_document_contexts(document.blocks)
    bad_block_id = _block_id(document, BAD_BODY_TEXT[:20])

    for line in TASK_SHEET_LINES + TITLE_AFTER_TASK_LINES:
        block_id = _block_id(document, line[:20])
        assert contexts[block_id] == "title_page", line
    assert contexts[bad_block_id] == "body"

    pages = {
        block.block_id: block.page_start_est
        for block in _paragraphs(document)
        if block.block_id in {
            _block_id(document, line[:20]) for line in TITLE_AFTER_TASK_LINES
        }
    }
    assert min(pages.values()) == 3

    report = _text_rules_engine().check(document)

    assert {violation.location.block_id for violation in report.violations} == {
        bad_block_id
    }
    assert report.summary.by_rule == {"font_body_text": 1, "indent_first_line": 1}

