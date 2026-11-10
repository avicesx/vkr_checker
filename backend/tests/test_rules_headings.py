"""Проверки правил заголовков: уровни, нумерация и оформление.

Тесты собирают минимальные DOCX с явными заголовками (стиль Heading1 и
outlineLvl) и проверяют, что каждое правило заголовков ловит ровно свою ошибку:
перескок уровня, разрыв нумерации, неверную глубину номера, точку в конце и
регистр. Все ограничения задаются данными правила, а не кодом движка.
"""
from __future__ import annotations

from conftest import DocxPackageBuilder
from parser.docx.pipeline import DocxParserPipeline
from parser.core.schema import ParsedDocxDocument
from rule_engine import RuleCheckReport, RuleEngine, RuleSet

TEXT = """
<w:p><w:r><w:t>Основной текст документа, оформленный по требованиям</w:t></w:r></w:p>
"""


def _heading(text: str, level: int = 1, align: str = "center") -> str:
    """Собирает абзац заголовка заданного уровня и выравнивания."""
    if level == 1:
        props = f'<w:pStyle w:val="Heading1"/><w:jc w:val="{align}"/>'
    else:
        props = f'<w:outlineLvl w:val="{level - 1}"/><w:jc w:val="{align}"/>'
    return f"<w:p><w:pPr>{props}</w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>"


def _parse(builder: DocxPackageBuilder, body: str) -> ParsedDocxDocument:
    """Собирает DOCX из тела и возвращает результат разбора."""
    builder.set_body(body)
    return DocxParserPipeline(builder.build("headings.docx")).parse()


def _check(builder: DocxPackageBuilder, body: str, rule: dict) -> RuleCheckReport:
    """Прогоняет документ по набору из одного правила."""
    document = _parse(builder, body)
    ruleset = RuleSet.model_validate(
        {"ruleset_id": "headings", "ruleset_version": "1.0.0", "rules": [rule]}
    )
    return RuleEngine(ruleset).check(document)


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


def _format_rule(**overrides: object) -> dict:
    rule = {
        "rule_id": "heading_format",
        "kind": "heading_format",
        "message": "Оформление заголовка",
        "target": {"scope": "headings"},
        "expected": {"trailing_dot": "forbid"},
    }
    rule.update(overrides)
    return rule


def test_skipped_heading_level_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Переход с первого уровня сразу на третий нарушает иерархию."""
    body = _heading("1 Название раздела") + _heading("1.1.1 Пункт", level=3)

    report = _check(docx_builder, body, _levels_rule())

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.kind == "heading_levels"
    assert violation.expected == {"max_level_after_previous": 2}
    assert violation.found == {"level": 3}


def test_skipped_levels_can_be_allowed(docx_builder: DocxPackageBuilder) -> None:
    """Флаг allow_skipped_levels снимает требование смежных уровней."""
    body = _heading("1 Название раздела") + _heading("1.1.1 Пункт", level=3)

    report = _check(
        docx_builder,
        body,
        _levels_rule(expected={"allow_skipped_levels": True}),
    )

    assert report.summary.violations_total == 0


def test_first_heading_must_start_from_configured_level(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Документ, начинающийся с подраздела, получает нарушение стартового уровня."""
    body = _heading("1.1 Подраздел без раздела", level=2) + TEXT

    report = _check(docx_builder, body, _levels_rule())

    assert report.summary.violations_total == 1
    assert report.violations[0].expected == {"first_level": 1}
    assert report.violations[0].found == {"level": 2}


def test_heading_deeper_than_max_level_is_reported(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Заголовок глубже max_level нарушает ограничение вложенности."""
    body = (
        _heading("1 Раздел")
        + _heading("1.1 Подраздел", level=2)
        + _heading("1.1.1 Пункт", level=3)
        + _heading("1.1.1.1 Подпункт", level=4)
    )

    report = _check(docx_builder, body, _levels_rule(expected={"max_level": 3}))

    assert report.summary.violations_total == 1
    assert report.violations[0].found == {"level": 4}
    assert report.violations[0].expected == {"max_level": 3}


def test_exclude_roles_hides_appendix_subtree(docx_builder: DocxPackageBuilder) -> None:
    """Приложение исключается из проверки вместе со своим поддеревом."""
    body = (
        _heading("1 Раздел")
        + _heading("ПРИЛОЖЕНИЕ А")
        + _heading("1.9 Пункт внутри приложения", level=3)
    )
    rule = _numbering_rule()

    without_exclusion = _check(docx_builder, body, rule)
    with_exclusion = _check(
        docx_builder,
        body,
        {
            **rule,
            "target": {"scope": "headings", "exclude_roles": ["appendix"]},
        },
    )

    assert without_exclusion.summary.violations_total == 1
    assert without_exclusion.summary.objects_checked == 3
    assert with_exclusion.summary.violations_total == 0
    assert with_exclusion.summary.objects_checked == 1

def test_numbering_gap_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Пропуск в нумерации разделов сообщает ожидаемый следующий номер."""
    body = _heading("1 Первый раздел") + _heading("3 Третий раздел")

    report = _check(docx_builder, body, _numbering_rule())

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.expected == {"next_section_number": "2"}
    assert violation.found == {"section_number": "3"}


def test_subsection_number_must_start_with_parent_number(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Подраздел с чужим номером родителя получает нарушение."""
    body = _heading("1 Первый раздел") + _heading("2.1 Чужой подраздел", level=2)

    report = _check(docx_builder, body, _numbering_rule())

    assert report.summary.violations_total == 1
    assert report.violations[0].expected == {"parent_number": "1"}
    assert report.violations[0].found == {"section_number": "2.1"}


def test_number_depth_must_match_heading_level(docx_builder: DocxPackageBuilder) -> None:
    """Номер глубины 3 у заголовка второго уровня нарушает соответствие."""
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


def test_required_number_for_role_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Если роль обязана быть пронумерована, заголовок без номера нарушает правило."""
    body = _heading("Анализ предметной области") + TEXT

    report = _check(
        docx_builder,
        body,
        _numbering_rule(expected={"require_number_for_roles": ["main_chapter"]}),
    )

    assert report.summary.violations_total == 1
    assert report.violations[0].found == {"section_number": None}
    assert report.violations[0].found_text == "номер не найден"


def test_number_ending_requirement_is_checked(docx_builder: DocxPackageBuilder) -> None:
    """Требование к точке после номера проверяет запись номера."""
    body = _heading("1.1. Подраздел", level=2)

    without_dot = _check(
        docx_builder,
        body,
        _numbering_rule(
            expected={
                "check_sequence": False,
                "check_parent_number": False,
                "number_ending": "without_dot",
                "require_number_for_roles": [],
            }
        ),
    )
    with_dot = _check(
        docx_builder,
        body,
        _numbering_rule(
            expected={
                "check_sequence": False,
                "check_parent_number": False,
                "number_ending": "with_dot",
                "require_number_for_roles": [],
            }
        ),
    )

    assert without_dot.summary.violations_total == 1
    assert without_dot.violations[0].expected == {"number_ending": "without_dot"}
    assert with_dot.summary.violations_total == 0


def test_trailing_dot_in_heading_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Точка в конце заголовка ловится при требовании trailing_dot=forbid."""
    body = _heading("1 Раздел с точкой.") + TEXT

    report = _check(docx_builder, body, _format_rule())

    assert report.summary.violations_total == 1
    assert report.violations[0].expected == {"trailing_dot": "forbid"}
    assert report.violations[0].found == {"trailing_dot": "require"}


def test_required_trailing_dot_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Отсутствие обязательной точки в конце заголовка тоже нарушение."""
    body = _heading("1 Раздел без точки") + TEXT

    report = _check(docx_builder, body, _format_rule(expected={"trailing_dot": "require"}))

    assert report.summary.violations_total == 1
    assert report.violations[0].found == {"trailing_dot": "forbid"}


def test_uppercase_requirement_is_checked(docx_builder: DocxPackageBuilder) -> None:
    """Заголовок со строчными буквами не проходит требование uppercase."""
    body = _heading("1 Раздел со строчными") + TEXT

    report = _check(docx_builder, body, _format_rule(expected={"case": "uppercase"}))

    assert report.summary.violations_total == 1
    assert report.violations[0].expected == {"case": "uppercase"}
    assert report.violations[0].found == {"case": "capitalized"}


def test_capitalized_requirement_rejects_uppercase_heading(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Полностью прописной заголовок не считается набранным с прописной буквы."""
    body = _heading("1 РАЗДЕЛ ЦЕЛИКОМ ПРОПИСНЫМИ") + TEXT

    report = _check(docx_builder, body, _format_rule(expected={"case": "capitalized"}))

    assert report.summary.violations_total == 1
    assert report.violations[0].found == {"case": "uppercase"}


def test_heading_alignment_is_checked(docx_builder: DocxPackageBuilder) -> None:
    """Выравнивание заголовка сравнивается с effective_props.alignment."""
    body = _heading("1 Раздел по левому краю", align="left") + TEXT

    report = _check(docx_builder, body, _format_rule(expected={"alignment": "center"}))

    assert report.summary.violations_total == 1
    assert report.violations[0].expected == {"alignment": "center"}
    assert report.violations[0].found == {"alignment": "left"}


def test_heading_rules_ignore_body_paragraphs(docx_builder: DocxPackageBuilder) -> None:
    """Обычные абзацы не проверяются правилами заголовков."""
    body = _heading("1 Раздел") + TEXT + "<w:p><w:r><w:t>Абзац с точкой.</w:t></w:r></w:p>"

    report = _check(docx_builder, body, _format_rule(expected={"alignment": "center"}))

    assert report.summary.violations_total == 0
    assert report.summary.objects_checked == 1

