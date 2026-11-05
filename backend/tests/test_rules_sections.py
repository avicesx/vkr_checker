"""Проверки правила обязательных разделов: состав и порядок следования.

Правило описано данными: каждая секция задается ролью и/или допустимыми
названиями. Тесты фиксируют, что пропуск раздела, перестановка разделов и
подмена раздела главой «Выводы» дают разные нарушения, а необязательные
разделы не влияют на результат.
"""
from __future__ import annotations

from conftest import DocxPackageBuilder
from docx_parser.pipeline import DocxParserPipeline
from docx_parser.schema import ParsedDocxDocument
from rule_engine import RuleCheckReport, RuleEngine, RuleSet

TEXT = """
<w:p><w:r><w:t>Основной текст документа, оформленный по требованиям</w:t></w:r></w:p>
"""

SECTIONS_SPEC = [
    {"label": "введение", "role": "introduction", "titles": ["ВВЕДЕНИЕ"]},
    {"label": "основные разделы", "role": "main_chapter"},
    {"label": "заключение", "role": "conclusion", "titles": ["ЗАКЛЮЧЕНИЕ"]},
    {"label": "список литературы", "role": "bibliography", "titles": ["СПИСОК ЛИТЕРАТУРЫ"]},
]


def _heading(text: str, level: int = 1) -> str:
    """Собирает абзац заголовка заданного уровня."""
    if level == 1:
        props = '<w:pStyle w:val="Heading1"/>'
    else:
        props = f'<w:outlineLvl w:val="{level - 1}"/>'
    return f"<w:p><w:pPr>{props}</w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>"


def _parse(builder: DocxPackageBuilder, body: str) -> ParsedDocxDocument:
    """Собирает DOCX из тела и возвращает результат разбора."""
    builder.set_body(body)
    return DocxParserPipeline(builder.build("sections.docx")).parse()


def _check(
    builder: DocxPackageBuilder,
    body: str,
    sections: list[dict] | None = None,
) -> RuleCheckReport:
    """Прогоняет документ по правилу обязательных разделов."""
    document = _parse(builder, body)
    ruleset = RuleSet.model_validate(
        {
            "ruleset_id": "sections",
            "ruleset_version": "1.0.0",
            "rules": [
                {
                    "rule_id": "required_sections",
                    "kind": "required_sections",
                    "message": "Состав и порядок разделов",
                    "target": {"scope": "document_structure"},
                    "expected": {"sections": sections or SECTIONS_SPEC},
                }
            ],
        }
    )
    return RuleEngine(ruleset).check(document)


def _compliant_body() -> str:
    """Документ с корректным порядком разделов."""
    return (
        _heading("ВВЕДЕНИЕ")
        + TEXT
        + _heading("1 Основные понятия работы")
        + TEXT
        + _heading("ЗАКЛЮЧЕНИЕ")
        + TEXT
        + _heading("СПИСОК ЛИТЕРАТУРЫ")
        + TEXT
    )


def test_required_sections_pass_in_expected_order(docx_builder: DocxPackageBuilder) -> None:
    """Документ с разделами в нужном порядке не получает нарушений."""
    report = _check(docx_builder, _compliant_body())

    assert report.summary.violations_total == 0
    assert report.passed is True
    assert report.summary.objects_checked == len(SECTIONS_SPEC)

def test_missing_section_is_reported_with_stable_id(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Пропущенный раздел дает нарушение уровня документа со стабильным id."""
    body = (
        _heading("ВВЕДЕНИЕ")
        + TEXT
        + _heading("1 Основные понятия работы")
        + TEXT
        + _heading("ЗАКЛЮЧЕНИЕ")
        + TEXT
    )

    report = _check(docx_builder, body)

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.violation_id == "required_sections:section:список литературы"
    assert violation.location.block_id is None
    assert violation.found == {"section": "список литературы", "present": False}
    assert violation.found_text == "раздел «список литературы» не найден"


def test_sections_in_wrong_order_are_reported(docx_builder: DocxPackageBuilder) -> None:
    """Раздел, оказавшийся выше предыдущего, получает нарушение порядка."""
    body = (
        _heading("ВВЕДЕНИЕ")
        + TEXT
        + _heading("1 Основные понятия работы")
        + TEXT
        + _heading("СПИСОК ЛИТЕРАТУРЫ")
        + TEXT
        + _heading("ЗАКЛЮЧЕНИЕ")
        + TEXT
    )

    report = _check(docx_builder, body)

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.expected == {"section": "список литературы", "after": "заключение"}
    assert violation.location.block_id is not None
    assert "СПИСОК ЛИТЕРАТУРЫ" in violation.location.snippet
    assert violation.found_text == "раздел «список литературы» расположен выше"


def test_optional_section_may_be_absent(docx_builder: DocxPackageBuilder) -> None:
    """Необязательный раздел не требует присутствия в документе."""
    sections = SECTIONS_SPEC + [
        {"label": "приложения", "role": "appendix", "required": False}
    ]

    report = _check(docx_builder, _compliant_body(), sections)

    assert report.summary.violations_total == 0


def test_chapter_conclusions_do_not_replace_final_conclusion(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Глава «Выводы» имеет ту же роль, но не заменяет структурное «Заключение»."""
    body = (
        _heading("ВВЕДЕНИЕ")
        + TEXT
        + _heading("1 Основные понятия работы")
        + TEXT
        + _heading("ВЫВОДЫ")
        + TEXT
        + _heading("СПИСОК ЛИТЕРАТУРЫ")
        + TEXT
    )

    report = _check(docx_builder, body)

    assert report.summary.violations_total == 1
    assert report.violations[0].violation_id == "required_sections:section:заключение"


def test_conclusion_with_chapter_synonyms_passes(docx_builder: DocxPackageBuilder) -> None:
    """«Выводы» по главам не мешают найти итоговое «Заключение»."""
    body = (
        _heading("ВВЕДЕНИЕ")
        + TEXT
        + _heading("1 Основные понятия работы")
        + TEXT
        + _heading("ВЫВОДЫ")
        + TEXT
        + _heading("ЗАКЛЮЧЕНИЕ")
        + TEXT
        + _heading("СПИСОК ЛИТЕРАТУРЫ")
        + TEXT
    )

    report = _check(docx_builder, body)

    assert report.summary.violations_total == 0


def test_title_matching_ignores_colon_and_case(docx_builder: DocxPackageBuilder) -> None:
    """Названия разделов сравниваются без учета регистра, двоеточия и пробелов."""
    body = (
        _heading("Введение")
        + TEXT
        + _heading("1 Основные понятия работы")
        + TEXT
        + _heading("Заключение")
        + TEXT
        + _heading("Список   литературы:")
        + TEXT
    )

    report = _check(docx_builder, body)

    assert report.summary.violations_total == 0

