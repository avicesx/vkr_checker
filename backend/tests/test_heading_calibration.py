"""Калибровка заголовков: уровень из номера, титульные метки, роли и синонимы.

Корпус ВКР давал шум правил заголовков по четырем причинам, и каждая проверена
здесь отдельно:

* стиль абзаца объявлял уровень, не совпадающий с нумерацией («Пункт» с
  ``outlineLvl=3`` у заголовка «3.1.1»), из-за чего уровень становился 4–5, а
  правило нумерации сообщало о неверной глубине номера. Уровень нумерованного
  заголовка теперь равен глубине номера (3.1.1 → 3), стиль ему не указ;
* реквизиты служебных страниц («ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА», «РАБОТА
  ВЫПОЛНЕНА», «ЗАДАНИЕ») набраны стилем заголовка и попадали в дерево
  документа первым «разделом»: правило уровней требовало первый уровень,
  правило нумерации — номер, оглавление — запись. Теперь это не заголовки;
* список литературы в разных ВКР называется «СПИСОК ИСТОЧНИКОВ»,
  «БИБЛИОГРАФИЧЕСКИЙ СПИСОК», «ИСТОЧНИКИ»: это синонимы требования «список
  литературы», а не другой раздел;
* структурные элементы работы (введение, заключение, список литературы,
  приложения, лист задания, перечень сокращений) не нумеруются, поэтому
  требование номера к ним не применяется, даже если роль перечислена в
  ``require_number_for_roles``.

Оформление заголовков (выравнивание «по ширине», точка в конце) не
фильтруется: это реальные нарушения, и тесты фиксируют, что правило их видит.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

import pytest

from conftest import DocxPackageBuilder
from parser.core.heading_labels import is_title_page_label
from parser.core.schema import ParagraphBlock, ParsedDocument
from parser.docx.pipeline import DocxParserPipeline
from parser.pdf.extractor import PdfLine, PdfSpan
from parser.pdf.structure import PdfStructureBuilder
from rule_engine import RuleCheckReport, RuleEngine, RuleSet, load_default_ruleset
from rule_engine.rules import NUMBERLESS_HEADING_ROLES

TEXT = """
<w:p><w:r><w:t>Основной текст документа, оформленный по требованиям методички.</w:t></w:r></w:p>
"""

NUMBERING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="1">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:suff w:val="space"/></w:lvl>
    <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2."/><w:suff w:val="space"/></w:lvl>
    <w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2.%3."/><w:suff w:val="space"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>"""


# ---------------------------------------------------------------------
# Сборка документов
# ---------------------------------------------------------------------


def _heading(text: str, level: int = 1, align: str = "center") -> str:
    """Абзац заголовка: Heading1 или абзац с заданным outlineLvl."""
    if level == 1:
        props = f'<w:pStyle w:val="Heading1"/><w:jc w:val="{align}"/>'
    else:
        props = f'<w:outlineLvl w:val="{level - 1}"/><w:jc w:val="{align}"/>'
    return f"<w:p><w:pPr>{props}</w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>"


def _styled_heading(text: str, style_id: str, align: str = "center") -> str:
    """Абзац заголовка пользовательским стилем абзаца."""
    return (
        f'<w:p><w:pPr><w:pStyle w:val="{style_id}"/><w:jc w:val="{align}"/></w:pPr>'
        f"<w:r><w:t>{text}</w:t></w:r></w:p>"
    )


def _autonum_heading(
    text: str,
    ilvl: int,
    outline_lvl: int | None = None,
    align: str = "center",
) -> str:
    """Абзац с автонумерацией w:numPr: маркер рисует numbering.xml."""
    outline = f'<w:outlineLvl w:val="{outline_lvl}"/>' if outline_lvl is not None else ""
    return (
        f"<w:p><w:pPr><w:numPr><w:ilvl w:val=\"{ilvl}\"/><w:numId w:val=\"1\"/></w:numPr>"
        f'{outline}<w:jc w:val="{align}"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'
    )


def _parse(builder: DocxPackageBuilder, body: str, numbering: bool = False) -> ParsedDocument:
    """Собирает DOCX и возвращает результат разбора."""
    if numbering:
        builder.set_xml("word/numbering.xml", NUMBERING_XML)
    builder.set_body(body)
    return DocxParserPipeline(builder.build("headings.docx")).parse()


def _check(builder: DocxPackageBuilder, body: str, rule: dict, numbering: bool = False) -> RuleCheckReport:
    """Прогоняет документ по набору из одного правила."""
    document = _parse(builder, body, numbering=numbering)
    ruleset = RuleSet.model_validate(
        {"ruleset_id": "headings", "ruleset_version": "1.0.0", "rules": [rule]}
    )
    return RuleEngine(ruleset).check(document)


def _default_check(document: ParsedDocument) -> RuleCheckReport:
    """Проверяет документ штатным набором правил."""
    return RuleEngine(load_default_ruleset()).check(document)


def _headings(document: ParsedDocument) -> List[ParagraphBlock]:
    """Абзацы-заголовки в порядке следования."""
    return [
        block
        for block in document.blocks
        if isinstance(block, ParagraphBlock) and block.heading_info is not None
    ]


def _heading_by_text(document: ParsedDocument, fragment: str) -> ParagraphBlock:
    """Заголовок, текст которого начинается с фрагмента."""
    for block in _headings(document):
        if block.text_visible.strip().startswith(fragment):
            return block
    raise AssertionError(f"заголовок «{fragment}» не найден")


def _tree_roles(document: ParsedDocument) -> Dict[str, str]:
    """Карта «название заголовка -> семантическая роль» из дерева документа."""
    roles: Dict[str, str] = {}

    def walk(nodes) -> None:
        for node in nodes:
            if node.heading_block_id is not None:
                roles[node.title] = node.semantic_role
            walk(node.children)

    walk(document.document_tree)
    return roles


def _levels_rule(**overrides: object) -> dict:
    rule = {
        "rule_id": "heading_levels",
        "kind": "heading_levels",
        "message": "Иерархия заголовков",
        "target": {"scope": "headings"},
        "expected": {"first_level": 1},
    }
    rule.update(overrides)
    return rule


def _numbering_rule(**overrides: object) -> dict:
    rule = {
        "rule_id": "heading_numbering",
        "kind": "heading_numbering",
        "message": "Нумерация заголовков",
        "target": {"scope": "headings"},
        "expected": {"number_ending": "any", "require_number_for_roles": []},
    }
    rule.update(overrides)
    return rule


def _sections_rule(sections: Sequence[dict]) -> dict:
    return {
        "rule_id": "required_sections",
        "kind": "required_sections",
        "message": "Обязательные разделы",
        "target": {"scope": "document_structure"},
        "expected": {"sections": list(sections)},
    }


# ---------------------------------------------------------------------
# 1. Уровень нумерованного заголовка = глубина номера
# ---------------------------------------------------------------------


def _tree_levels(document: ParsedDocument) -> Dict[str, int]:
    """Карта «название заголовка -> уровень» из дерева документа."""
    levels: Dict[str, int] = {}

    def walk(nodes) -> None:
        for node in nodes:
            if node.heading_block_id is not None:
                levels[node.title] = node.level
            walk(node.children)

    walk(document.document_tree)
    return levels


def test_number_depth_wins_over_outline_style(docx_builder: DocxPackageBuilder) -> None:
    """«3.1.1» получает третий уровень, хотя стиль объявляет пятый."""
    document = _parse(
        docx_builder,
        _heading("1 Раздел") + _heading("3.1.1 Блок автоматизации", level=5),
    )

    heading = _heading_by_text(document, "3.1.1")
    assert heading.heading_info is not None
    assert heading.heading_info.level == 3
    assert _tree_levels(document)["Блок автоматизации"] == 3


def test_multilevel_number_gives_second_level(docx_builder: DocxPackageBuilder) -> None:
    """«2.1» — второй уровень, даже если абзац оформлен стилем четвертого."""
    document = _parse(docx_builder, _heading("2.1 Подраздел", level=4))

    heading = _heading_by_text(document, "2.1")
    assert heading.heading_info is not None
    assert heading.heading_info.level == 2


def test_autonum_level_comes_from_marker_depth(docx_builder: DocxPackageBuilder) -> None:
    """Уровень заголовка с автонумерацией берется из глубины маркера «1.1.1»."""
    document = _parse(
        docx_builder,
        _heading("1 Раздел")
        + _autonum_heading("Подраздел", ilvl=1, outline_lvl=3)
        + _autonum_heading("Пункт", ilvl=2, outline_lvl=4),
        numbering=True,
    )

    levels = [
        (block.text_visible.strip(), block.heading_info.level)
        for block in _headings(document)
        if block.heading_info is not None
    ]
    assert ("Пункт", 3) in levels
    assert ("Подраздел", 2) in levels


def test_numeric_style_id_does_not_declare_first_level(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Стиль с номером в имени («1.3. Заголовок подраздела») не делает уровень первым."""
    docx_builder.add_style_xml(
        """<w:style w:type="paragraph" w:customStyle="1" w:styleId="sub13">
             <w:name w:val="1.3. Заголовок подраздела"/>
             <w:basedOn w:val="Normal"/>
             <w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr>
           </w:style>"""
    )
    document = _parse(
        docx_builder,
        _heading("1 Раздел") + _styled_heading("1.1 Суть вопроса", "sub13"),
        numbering=True,
    )

    heading = _heading_by_text(document, "1.1 Суть вопроса")
    assert heading.heading_info is not None
    assert heading.heading_info.level == 2


def test_consistent_numbering_has_no_level_violations(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Документ с «1», «1.1», «1.1.1» не получает нарушений уровней и нумерации.

    Это регрессия корпуса: стили объявляли для «1.1.1» уровень 5, правило
    уровней видело перескок, а правило нумерации — неверную глубину номера.
    """
    body = (
        _heading("1 Обзор предметной области", level=1)
        + _heading("1.1 Подходы к управлению", level=4)
        + _heading("1.1.1 Деталь реализации", level=5)
        + TEXT
    )

    report = _check(docx_builder, body, _levels_rule(expected={"first_level": 1, "max_level": 3}))
    numbering = _check(docx_builder, body, _numbering_rule())

    assert report.summary.violations_total == 0
    assert numbering.summary.violations_total == 0


def test_number_depth_mismatch_with_hierarchy_is_still_reported(
    docx_builder: DocxPackageBuilder,
) -> None:
    """«1.1.1» сразу после главы «1» — реальное нарушение глубины номера.

    Негативный тест к калибровке: уровень берется из номера, но место номера в
    иерархии по-прежнему проверяется, поэтому перескок не скрывается.
    """
    body = _heading("1 Раздел") + _heading("1.1.1 Пункт", level=2)

    report = _check(
        docx_builder,
        body,
        _numbering_rule(
            expected={
                "check_sequence": False,
                "check_parent_number": False,
                "require_number_for_roles": [],
            }
        ),
    )

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.expected == {"number_depth": 2}
    assert violation.found == {"number_depth": 3, "section_number": "1.1.1"}


def test_root_heading_number_depth_is_not_duplicated_by_numbering(
    docx_builder: DocxPackageBuilder,
) -> None:
    """У номера верхнего уровня глубину проверяет правило уровней, а не нумерации."""
    report = _check(
        docx_builder,
        _heading("1.1 Подраздел без раздела", level=2),
        _numbering_rule(
            expected={
                "check_sequence": False,
                "check_parent_number": False,
                "require_number_for_roles": [],
            }
        ),
    )

    assert report.summary.violations_total == 0


# ---------------------------------------------------------------------
# 2. Титульные метки не являются заголовками
# ---------------------------------------------------------------------

TITLE_LABELS = (
    "ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА",
    "Выпускная квалификационная работа на тему «Автоматизация»",
    "РАБОТА ВЫПОЛНЕНА",
    "Работа выполнена студентом группы РИ-420932",
    "ЗАДАНИЕ",
    "ЗАДАНИЕ НА ВЫПУСКНУЮ КВАЛИФИКАЦИОННУЮ РАБОТУ",
    "ЗАДАНИЕ НА ВКР",
    "Задание на дипломное проектирование",
)


@pytest.mark.parametrize("label", TITLE_LABELS)
def test_title_page_label_is_not_a_heading(
    docx_builder: DocxPackageBuilder, label: str
) -> None:
    """Реквизит служебной страницы не становится заголовком даже в стиле Heading1."""
    document = _parse(docx_builder, _heading(label) + TEXT)

    assert _headings(document) == []
    assert document.document_tree == [] or all(
        node.heading_block_id is None for node in document.document_tree
    )


def test_title_labels_do_not_produce_heading_violations(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Ложный «первый заголовок» с титула больше не дает нарушений уровней и оглавления."""
    body = (
        _heading("ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА")
        + _heading("РАБОТА ВЫПОЛНЕНА")
        + TEXT
    )

    document = _parse(docx_builder, body)
    report = _default_check(document)

    assert "heading_levels" not in report.summary.by_rule
    assert "heading_numbering" not in report.summary.by_rule
    # Титульные метки не попали в оглавление, поэтому полнота не проверяется:
    # записей нет, но и ложных «разделов» тоже нет.
    assert document.document_tree == [] or all(
        node.heading_block_id is None for node in document.document_tree
    )


@pytest.mark.parametrize(
    "text",
    [
        "ЗАДАНИЕ НА ПРОЕКТИРОВАНИЕ АСУ ТП",
        "Техническое задание, лист 1",
        "Задание на разработку подсистемы",
    ],
)
def test_other_task_mentions_stay_headings(
    docx_builder: DocxPackageBuilder, text: str
) -> None:
    """Негативный тест: обычный заголовок со словом «задание» заголовком остается."""
    document = _parse(docx_builder, _heading(text) + TEXT)

    heading = _heading_by_text(document, text)
    assert heading.heading_info is not None


@pytest.mark.parametrize(
    "label,expected",
    [
        ("ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА", True),
        ("Выпускная квалификационная работа на тему «Сервис»", True),
        ("РАБОТА ВЫПОЛНЕНА", True),
        ("ЗАДАНИЕ", True),
        ("ЗАДАНИЕ НА ВКР", True),
        ("ЗАДАНИЕ НА ПРОЕКТИРОВАНИЕ АСУ", False),
        ("Техническое задание, лист 1", False),
        ("ВВЕДЕНИЕ", False),
        ("СПИСОК ИСТОЧНИКОВ", False),
        ("", False),
    ],
)
def test_is_title_page_label(label: str, expected: bool) -> None:
    """Метка служебной страницы отличается от обычного заголовка."""
    assert is_title_page_label(label) is expected


# PDF-разбор: те же метки не должны становиться заголовками по кеглю и начертанию.


def _pdf_builder(base_font_size_pt: float = 12.0) -> PdfStructureBuilder:
    """Строитель структуры PDF с известным базовым кеглем (без документа и вёрстки)."""
    builder = PdfStructureBuilder.__new__(PdfStructureBuilder)
    builder.base_font_size_pt = base_font_size_pt
    return builder


def _pdf_line(
    text: str,
    size_pt: float = 12.0,
    bold: bool = False,
    page_num: int = 1,
) -> PdfLine:
    """Строка PDF с одним спаном."""
    bbox = (0.0, 0.0, 200.0, 12.0)
    span = PdfSpan(
        bbox_pt=bbox,
        text=text,
        font_name="TimesNewRoman",
        font_size_pt=size_pt,
        bold=bold,
    )
    return PdfLine(bbox_pt=bbox, page_num=page_num, spans=[span])


@pytest.mark.parametrize("label", TITLE_LABELS)
def test_pdf_title_labels_are_not_headings(label: str) -> None:
    """В PDF крупный полужирный кегль делал реквизит титула первым «разделом»."""
    builder = _pdf_builder()
    line = _pdf_line(label, size_pt=18.0, bold=True)

    assert builder._heading_info([line], line.text) is None


@pytest.mark.parametrize(
    "text,level",
    [
        ("ВВЕДЕНИЕ", 1),
        ("1.2 Сбор и анализ требований", 2),
        ("3.1.1 Блок автоматизации", 3),
    ],
)
def test_pdf_real_headings_are_still_detected(text: str, level: int) -> None:
    """Настоящие заголовки PDF продолжают распознаваться."""
    builder = _pdf_builder()
    line = _pdf_line(text, size_pt=14.0, bold=True)

    heading = builder._heading_info([line], line.text)
    assert heading is not None
    assert heading.level == level


def test_pdf_lowercase_source_sentence_is_not_a_heading() -> None:
    """Строка «определения потенциальных путей…» — текст, а не заголовок."""
    builder = _pdf_builder()
    line = _pdf_line("определения потенциальных путей развития", size_pt=14.0, bold=True)

    assert builder._heading_info([line], line.text) is None


# ---------------------------------------------------------------------
# 3. Синонимы списка литературы и роли PDF/DOCX
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,role",
    [
        ("СПИСОК ИСТОЧНИКОВ", "bibliography"),
        ("СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ", "bibliography"),
        ("Список используемых источников", "bibliography"),
        ("БИБЛИОГРАФИЧЕСКИЙ СПИСОК", "bibliography"),
        ("БИБЛИОГРАФИЯ", "bibliography"),
        ("ИСТОЧНИКИ", "bibliography"),
        ("ТЕРМИНЫ И ОПРЕДЕЛЕНИЯ", "abbreviations"),
        ("СПИСОК ТЕРМИНОВ", "abbreviations"),
        ("Продолжение ПРИЛОЖЕНИЯ А", "appendix"),
        ("Окончание приложения Б", "appendix"),
        ("ПРИЛОЖЕНИЕ А", "appendix"),
        ("ВВЕДЕНИЕ", "introduction"),
    ],
)
def test_pdf_structural_role_synonyms(text: str, role: str) -> None:
    """PDF-разбор знает синонимы названий разделов."""
    assert PdfStructureBuilder._structural_role(text) == role


@pytest.mark.parametrize(
    "text",
    [
        "Обзор использованных источников",
        "литература, ГОСТы",
        "Проведенный анализ источников",
    ],
)
def test_pdf_source_review_is_not_bibliography(text: str) -> None:
    """Негативный тест: упоминание источников в подзаголовке — не список литературы."""
    assert PdfStructureBuilder._structural_role(text) is None


@pytest.mark.parametrize(
    "text,role",
    [
        ("СПИСОК ИСТОЧНИКОВ", "bibliography"),
        ("БИБЛИОГРАФИЧЕСКИЙ СПИСОК", "bibliography"),
        ("ИСТОЧНИКИ", "bibliography"),
        ("ТЕРМИНЫ И ОПРЕДЕЛЕНИЯ", "abbreviations"),
    ],
)
def test_docx_heading_roles_use_synonyms(
    docx_builder: DocxPackageBuilder, text: str, role: str
) -> None:
    """DOCX-разбор назначает те же роли по синонимам названий."""
    document = _parse(docx_builder, _heading(text) + TEXT)

    roles = _tree_roles(document)
    assert roles[text] == role


def test_docx_source_review_is_not_bibliography(docx_builder: DocxPackageBuilder) -> None:
    """Подраздел «Обзор использованных источников» — не список литературы."""
    document = _parse(
        docx_builder,
        _heading("1 Анализ", level=1) + _heading("Обзор использованных источников", level=2),
    )

    roles = _tree_roles(document)
    assert roles["Обзор использованных источников"] != "bibliography"


def test_docx_appendix_continuation_is_appendix(docx_builder: DocxPackageBuilder) -> None:
    """«Продолжение ПРИЛОЖЕНИЯ А» — часть приложения, а не подраздел."""
    document = _parse(
        docx_builder,
        _heading("ПРИЛОЖЕНИЕ А", level=1) + _heading("Продолжение ПРИЛОЖЕНИЯ А", level=4),
    )

    roles = _tree_roles(document)
    assert roles["Продолжение ПРИЛОЖЕНИЯ А"] == "appendix"


@pytest.mark.parametrize(
    "title",
    [
        "СПИСОК ИСТОЧНИКОВ",
        "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ",
        "БИБЛИОГРАФИЧЕСКИЙ СПИСОК",
        "ИСТОЧНИКИ",
        "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ ПО ТЕМЕ РАБОТЫ",
    ],
)
def test_required_sections_accepts_literature_synonyms(
    docx_builder: DocxPackageBuilder, title: str
) -> None:
    """Требование «список литературы» выполняется любым названием из группы."""
    body = _heading("ВВЕДЕНИЕ") + _heading(title) + TEXT

    report = _check(
        docx_builder,
        body,
        _sections_rule([{"label": "список литературы", "titles": ["СПИСОК ЛИТЕРАТУРЫ"]}]),
    )

    assert report.summary.violations_total == 0


def test_required_sections_ignores_unrelated_source_heading(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Негативный тест: подраздел про источники не закрывает требование литературы."""
    body = (
        _heading("1 Анализ", level=1)
        + _heading("Обзор использованных источников", level=2)
        + TEXT
    )

    report = _check(
        docx_builder,
        body,
        _sections_rule([{"label": "список литературы", "titles": ["СПИСОК ЛИТЕРАТУРЫ"]}]),
    )

    assert report.summary.violations_total == 1
    assert report.violations[0].violation_id.endswith("section:список литературы")


def test_required_sections_matches_bibliography_role(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Штатный набор правил находит раздел литературы по роли после калибровки."""
    body = (
        _heading("ВВЕДЕНИЕ")
        + _heading("1 Глава", level=1)
        + _heading("ЗАКЛЮЧЕНИЕ")
        + _heading("СПИСОК ИСТОЧНИКОВ")
        + TEXT
    )

    document = _parse(docx_builder, body)
    report = _default_check(document)

    assert report.summary.by_rule.get("required_sections") is None


# ---------------------------------------------------------------------
# 4. Структурные роли не требуют номера
# ---------------------------------------------------------------------


def test_numberless_roles_constant_matches_contract() -> None:
    """Набор структурно безномерных ролей зафиксирован контрактом."""
    assert NUMBERLESS_HEADING_ROLES == frozenset(
        {
            "bibliography",
            "introduction",
            "conclusion",
            "appendix",
            "title_page",
            "task_sheet",
            "abbreviations",
        }
    )
    numbering_expected = load_default_ruleset().get_rule("heading_numbering")
    assert numbering_expected.expected.require_number_for_roles == [
        "main_chapter",
        "sub_section",
    ]
    assert not set(numbering_expected.expected.require_number_for_roles) & set(
        NUMBERLESS_HEADING_ROLES
    )


def test_numberless_roles_do_not_require_number(docx_builder: DocxPackageBuilder) -> None:
    """Даже явно перечисленные безномерные роли не получают требования номера."""
    body = (
        _heading("ВВЕДЕНИЕ")
        + _heading("ЗАКЛЮЧЕНИЕ")
        + _heading("СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ")
        + _heading("ТЕРМИНЫ И ОПРЕДЕЛЕНИЯ")
        + _heading("ПРИЛОЖЕНИЕ А")
        + TEXT
    )
    rule = _numbering_rule(
        expected={
            "check_sequence": False,
            "check_parent_number": False,
            "require_number_for_roles": sorted(NUMBERLESS_HEADING_ROLES),
        }
    )

    report = _check(docx_builder, body, rule)

    assert report.summary.violations_total == 0
    assert report.summary.objects_checked == 5


def test_numbered_chapter_still_requires_number(docx_builder: DocxPackageBuilder) -> None:
    """Негативный тест: глава без номера по-прежнему нарушает требование."""
    body = _heading("Анализ предметной области") + TEXT

    report = _check(
        docx_builder,
        body,
        _numbering_rule(expected={"require_number_for_roles": ["main_chapter"]}),
    )

    assert report.summary.violations_total == 1
    assert report.violations[0].found_text == "номер не найден"


# ---------------------------------------------------------------------
# 5. Оформление заголовков не фильтруется
# ---------------------------------------------------------------------


def test_heading_alignment_is_still_reported(docx_builder: DocxPackageBuilder) -> None:
    """Заголовок «по ширине» остается нарушением: это не шум калибровки."""
    body = _heading("1 Раздел", align="both") + TEXT
    rule = {
        "rule_id": "heading_alignment",
        "kind": "heading_format",
        "message": "Заголовки выравниваются по центру",
        "target": {"scope": "headings"},
        "expected": {"alignment": "center"},
    }

    report = _check(docx_builder, body, rule)

    assert report.summary.violations_total == 1
    assert report.violations[0].found["alignment"] == "both"
