"""Сверка оглавления: приложения по букве и глубина проверки полноты.

Корпус ВКР давал два вида ложных нарушений правила оглавления:

* «ПРИЛОЖЕНИЕ А … заголовок не найден» — запись оглавления указывает на
  приложение по букве, а разбор сопоставляет записи только по точному названию
  заголовка. Правило теперь ищет приложение по букве, а если буквы в тексте нет
  (титул приложения набран как «(обязательное) Название») — по названию; запись
  приложения покрывает и подзаголовки внутри него;
* «раздел «подраздел» не найден в оглавлении» по «3.1.1» — оглавление ВКР
  содержит только главы и разделы. ``max_depth`` задает глубину, до которой
  заголовки обязаны быть в оглавлении: при ``2`` подразделы третьего уровня
  пропуском не считаются, а пропуск главы остается нарушением на любой глубине.
"""
from __future__ import annotations

import pytest

from conftest import DocxPackageBuilder
from parser.core.schema import ParsedDocxDocument, TocContainerBlock
from parser.docx.pipeline import DocxParserPipeline
from rule_engine import RuleCheckReport, RuleEngine, RuleSet, load_default_ruleset

TEXT = """
<w:p><w:r><w:t>Основной текст документа, оформленный по требованиям</w:t></w:r></w:p>
"""

TOC_TITLE = """
<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>СОДЕРЖАНИЕ</w:t></w:r></w:p>
"""


def _heading(text: str, level: int = 1) -> str:
    """Абзац заголовка: Heading1 или абзац с заданным outlineLvl."""
    if level == 1:
        props = '<w:pStyle w:val="Heading1"/>'
    else:
        props = f'<w:outlineLvl w:val="{level - 1}"/>'
    return f"<w:p><w:pPr>{props}</w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>"


def _plain(text: str, align: str = "center") -> str:
    """Обычный абзац без стиля заголовка."""
    return f'<w:p><w:pPr><w:jc w:val="{align}"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'


def _toc_entry(text: str, page: str = "1", style: str | None = None) -> str:
    """Запись оглавления с номером страницы через табуляцию."""
    props = f'<w:pStyle w:val="{style}"/>' if style else ""
    return (
        f"<w:p><w:pPr>{props}</w:pPr><w:r><w:t>{text}</w:t></w:r>"
        f"<w:r><w:tab/><w:t>{page}</w:t></w:r></w:p>"
    )


def _dotted_toc_entry(text: str, style: str = "TOC2") -> str:
    """Запись оглавления с отточием и без номера страницы (как отдает PDF)."""
    return (
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
        f"<w:r><w:t>{text}{'.' * 12}</w:t></w:r></w:p>"
    )


def _parse(builder: DocxPackageBuilder, body: str) -> ParsedDocxDocument:
    """Собирает DOCX из тела и возвращает результат разбора."""
    builder.set_body(body)
    return DocxParserPipeline(builder.build("toc_matching.docx")).parse()


def _report(
    document: ParsedDocxDocument,
    expected: dict | None = None,
) -> RuleCheckReport:
    """Прогоняет документ по правилу оглавления с заданными ожиданиями."""
    payload = {
        "require_toc": True,
        "require_page_numbers": True,
        "check_titles": True,
        "check_completeness": True,
        "required_entries": [
            {"label": "раздел", "role": "main_chapter"},
            {"label": "подраздел", "role": "sub_section"},
        ],
    }
    if expected is not None:
        payload.update(expected)
    ruleset = RuleSet.model_validate(
        {
            "ruleset_id": "toc",
            "ruleset_version": "1.0.0",
            "rules": [
                {
                    "rule_id": "toc",
                    "kind": "toc",
                    "message": "Оглавление",
                    "target": {"scope": "toc"},
                    "expected": payload,
                }
            ],
        }
    )
    return RuleEngine(ruleset).check(document)


def _check(
    builder: DocxPackageBuilder,
    body: str,
    expected: dict | None = None,
) -> RuleCheckReport:
    """Разбирает документ и прогоняет по правилу оглавления."""
    return _report(_parse(builder, body), expected)


def _tocs(document: ParsedDocxDocument) -> list[TocContainerBlock]:
    """Блоки оглавления документа."""
    return [block for block in document.blocks if isinstance(block, TocContainerBlock)]


# ---------------------------------------------------------------------
# 1. Приложения: запись оглавления ссылается на приложение по букве
# ---------------------------------------------------------------------

APPENDIX_HEADING = "ПРИЛОЖЕНИЕ А\n(обязательное)\n\nТехнические требования к системе"
APPENDIX_TOC = TOC_TITLE + _toc_entry("ПРИЛОЖЕНИЕ А", page="70")


def test_appendix_entry_matches_appendix_heading(docx_builder: DocxPackageBuilder) -> None:
    """Запись «ПРИЛОЖЕНИЕ А» находит приложение, даже если разбор не связал их."""
    document = _parse(docx_builder, APPENDIX_TOC + _heading(APPENDIX_HEADING) + TEXT)
    toc = _tocs(document)[0]

    assert toc.entries[0].matched_heading_block_id is None, "разбор не связывает по букве"

    report = _report(document, expected={"check_completeness": False})

    assert report.summary.violations_total == 0


def test_appendix_entry_with_title_matches_by_letter(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Запись «ПРИЛОЖЕНИЕ А (обязательное) Матрица ответственности» тоже находится."""
    body = (
        TOC_TITLE
        + _toc_entry("ПРИЛОЖЕНИЕ А (обязательное) Матрица ответственности", page="53")
        + _heading("ВВЕДЕНИЕ")
        + _heading("ПРИЛОЖЕНИЕ А\n(обязательное)")
        + _heading("Матрица ответственности", level=2)
        + TEXT
    )

    report = _check(docx_builder, body, expected={"check_completeness": False})

    assert report.summary.violations_total == 0


def test_appendix_entry_without_appendix_is_reported(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Негативный тест: запись про приложение, которого нет в документе, падает."""
    body = TOC_TITLE + _toc_entry("ПРИЛОЖЕНИЕ Я", page="90") + _heading("ВВЕДЕНИЕ") + TEXT

    report = _check(docx_builder, body, expected={"check_completeness": False})

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.expected == {"matched_heading": True}
    assert violation.found == {"matched_heading": False}


def test_appendix_continuation_proves_appendix_exists(
    docx_builder: DocxPackageBuilder,
) -> None:
    """«Продолжение ПРИЛОЖЕНИЯ Б» доказывает, что приложение Б есть в документе."""
    body = (
        TOC_TITLE
        + _toc_entry("ПРИЛОЖЕНИЕ Б", page="74")
        + _heading("ВВЕДЕНИЕ")
        + TEXT
        + _plain("Продолжение ПРИЛОЖЕНИЯ Б")
        + TEXT
    )

    report = _check(docx_builder, body, expected={"check_completeness": False})

    assert report.summary.violations_total == 0


def test_appendix_title_without_letter_matches_by_title(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Титул приложения без буквы сопоставляется с записью по названию.

    В ВКР УрФУ подпись приложения иногда набрана как «(справочное) Название»:
    буквы в тексте нет, но запись оглавления «ПРИЛОЖЕНИЕ Г (справочное)
    Название» указывает именно на этот лист.
    """
    title = "Кодовая реализация объявления и настройки DI контейнеров"
    body = (
        TOC_TITLE
        + _toc_entry(f"ПРИЛОЖЕНИЕ Г (справочное) {title}", page="87")
        + _heading("ВВЕДЕНИЕ")
        + TEXT
        + _plain(f"(справочное) {title}")
    )

    report = _check(docx_builder, body, expected={"check_completeness": False})

    assert report.summary.violations_total == 0


def test_appendix_entry_covers_its_subheadings(docx_builder: DocxPackageBuilder) -> None:
    """Одна запись приложения покрывает подзаголовки внутри него."""
    body = (
        TOC_TITLE
        + _toc_entry("ПРИЛОЖЕНИЕ А (обязательное) Матрица ответственности", page="53")
        + _heading("ВВЕДЕНИЕ")
        + _heading("ПРИЛОЖЕНИЕ А\n(обязательное)")
        + _heading("Матрица ответственности", level=2)
        + TEXT
    )

    report = _check(
        docx_builder,
        body,
        expected={"required_entries": [{"label": "подраздел", "role": "sub_section"}]},
    )

    assert report.summary.violations_total == 0


# ---------------------------------------------------------------------
# 2. Глубина оглавления: max_depth
# ---------------------------------------------------------------------

DEEP_SECTIONS = (
    ("1 Раздел", 1),
    ("1.1 Подраздел", 2),
    ("1.1.1 Пункт", 3),
)


def _deep_body(toc_titles: tuple[str, ...]) -> str:
    """Документ с тремя уровнями заголовков и оглавлением из выбранных записей."""
    entries = "".join(_toc_entry(title) for title in toc_titles)
    content = "".join(_heading(title, level) + TEXT for title, level in DEEP_SECTIONS)
    return TOC_TITLE + entries + content


def test_max_depth_two_ignores_third_level_heading(
    docx_builder: DocxPackageBuilder,
) -> None:
    """При max_depth=2 подраздел «1.1.1» не считается пропуском в оглавлении."""
    body = _deep_body(("1 Раздел", "1.1 Подраздел"))

    report = _check(docx_builder, body, expected={"max_depth": 2})

    assert report.summary.violations_total == 0


def test_third_level_heading_is_required_without_max_depth(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Без max_depth сверяются все уровни: «1.1.1» должен быть в оглавлении."""
    body = _deep_body(("1 Раздел", "1.1 Подраздел"))

    report = _check(docx_builder, body)

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.violation_id.startswith("toc:toc:missing:")
    assert violation.location.snippet == "1.1.1 Пункт"


def test_missing_chapter_is_reported_with_max_depth(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Пропуск главы — нарушение при любой глубине сверки."""
    body = _deep_body(("1.1 Подраздел",))

    report = _check(
        docx_builder,
        body,
        expected={
            "max_depth": 2,
            "required_entries": [{"label": "раздел", "role": "main_chapter"}],
        },
    )

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.expected == {"toc_entry": True, "section": "раздел"}
    assert violation.location.snippet == "1 Раздел"


def test_deep_toc_entry_is_not_a_violation_with_max_depth(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Запись глубже max_depth допускается: max_depth задает только полноту.

    Оглавление может перечислять подразделы третьего уровня (стиль TOC3), даже
    если сверять полноту требуется до второго: уровень записи — оформление
    оглавления, а не нарушение.
    """
    body = (
        TOC_TITLE
        + _toc_entry("1 Раздел", page="1", style="TOC1")
        + _toc_entry("1.1 Подраздел", page="1", style="TOC2")
        + _toc_entry("1.1.1 Пункт", page="2", style="TOC3")
        + "".join(_heading(title, level) + TEXT for title, level in DEEP_SECTIONS)
    )

    report = _check(docx_builder, body, expected={"max_depth": 2})

    assert report.summary.violations_total == 0


def test_max_depth_requires_completeness() -> None:
    """max_depth без сверки полноты — ошибка набора правил, а не молчаливый no-op."""
    with pytest.raises(ValueError, match="max_depth"):
        RuleSet.model_validate(
            {
                "ruleset_id": "toc",
                "ruleset_version": "1.0.0",
                "rules": [
                    {
                        "rule_id": "toc",
                        "kind": "toc",
                        "message": "Оглавление",
                        "target": {"scope": "toc"},
                        "expected": {"check_titles": True, "max_depth": 2},
                    }
                ],
            }
        )


def test_default_ruleset_checks_toc_to_second_level() -> None:
    """Штатный набор сверяет оглавление до глав и разделов."""
    toc_rule = load_default_ruleset().get_rule("toc")

    assert toc_rule.expected.max_depth == 2
    assert toc_rule.expected.check_completeness is True


# ---------------------------------------------------------------------
# 3. Записи, которые разбор не сопоставил из-за отточия
# ---------------------------------------------------------------------


def test_dotted_entry_matches_heading_by_number_and_title(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Запись PDF с отточием и без номера страницы не теряет свой заголовок."""
    body = (
        TOC_TITLE
        + _toc_entry("1 Раздел", page="1", style="TOC1")
        + _dotted_toc_entry("1.1 Подраздел")
        + _heading("1 Раздел")
        + _heading("1.1 Подраздел")
        + TEXT
    )

    report = _check(
        docx_builder,
        body,
        expected={
            "require_page_numbers": False,
            "required_entries": [{"label": "подраздел", "role": "sub_section"}],
        },
    )

    assert report.summary.violations_total == 0


def test_dotted_entry_with_unrelated_title_stays_unmatched(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Негативный тест: запись с чужим названием не находит заголовок."""
    body = (
        TOC_TITLE
        + _dotted_toc_entry("1.3 Совершенно другой текст")
        + _heading("1 Раздел")
        + _heading("1.1 Подраздел")
        + TEXT
    )

    report = _check(
        docx_builder,
        body,
        expected={"require_page_numbers": False, "check_completeness": False},
    )

    assert report.summary.violations_total == 1
    assert report.violations[0].found == {"matched_heading": False}
