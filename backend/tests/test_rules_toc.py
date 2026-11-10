"""Проверки правила оглавления: наличие, записи, сверка с заголовками.

Тесты собирают документ с ручным и автоматическим оглавлением и проверяют, что
правило ловит отсутствующее оглавление, запись без номера страницы, запись без
заголовка, неверный номер страницы и заголовок, забытый в оглавлении.
"""
from __future__ import annotations

from conftest import DocxPackageBuilder
from parser.docx.pipeline import DocxParserPipeline
from parser.core.schema import ParsedDocxDocument, TocContainerBlock
from rule_engine import RuleCheckReport, RuleEngine, RuleSet

TEXT = """
<w:p><w:r><w:t>Основной текст документа, оформленный по требованиям</w:t></w:r></w:p>
"""

TOC_TITLE = """
<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>СОДЕРЖАНИЕ</w:t></w:r></w:p>
"""

SECTIONS = (
    ("ВВЕДЕНИЕ", 1),
    ("1 Основные понятия работы", 1),
    ("1.1 Определение ключевых понятий", 2),
    ("ЗАКЛЮЧЕНИЕ", 1),
    ("СПИСОК ЛИТЕРАТУРЫ", 1),
)

REQUIRED_ENTRIES = [
    {"label": "введение", "role": "introduction"},
    {"label": "основной раздел", "role": "main_chapter"},
    {"label": "заключение", "role": "conclusion", "titles": ["ЗАКЛЮЧЕНИЕ"]},
    {"label": "список литературы", "role": "bibliography"},
]


def _heading(text: str, level: int = 1) -> str:
    """Собирает абзац заголовка заданного уровня."""
    if level == 1:
        props = '<w:pStyle w:val="Heading1"/>'
    else:
        props = f'<w:outlineLvl w:val="{level - 1}"/>'
    return f"<w:p><w:pPr>{props}</w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>"


def _toc_entry(text: str, page: str = "1") -> str:
    """Собирает запись оглавления с номером страницы через табуляцию."""
    return f"<w:p><w:r><w:t>{text}</w:t></w:r><w:r><w:tab/><w:t>{page}</w:t></w:r></w:p>"


def _styled_toc_entry(text: str) -> str:
    """Собирает запись оглавления со стилем TOC: номер страницы не указан."""
    return f'<w:p><w:pPr><w:pStyle w:val="TOC1"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'


def _body(toc_entries: str | None = None) -> str:
    """Собирает документ с оглавлением (или без него) и корректными заголовками."""
    if toc_entries is None:
        toc_entries = "".join(_toc_entry(title) for title, _ in SECTIONS)
    toc = ""
    if toc_entries:
        toc = TOC_TITLE + toc_entries
    content = "".join(_heading(title, level) + TEXT for title, level in SECTIONS)
    return toc + content


def _parse(builder: DocxPackageBuilder, body: str) -> ParsedDocxDocument:
    """Собирает DOCX из тела и возвращает результат разбора."""
    builder.set_body(body)
    return DocxParserPipeline(builder.build("toc.docx")).parse()


def _report(
    document: ParsedDocxDocument,
    expected: dict | None = None,
) -> RuleCheckReport:
    """Прогоняет разобранный документ по правилу оглавления."""
    payload = {
        "require_toc": True,
        "require_page_numbers": True,
        "check_titles": True,
        "check_completeness": True,
        "required_entries": REQUIRED_ENTRIES,
    }
    if expected:
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


def test_compliant_toc_passes(docx_builder: DocxPackageBuilder) -> None:
    """Оглавление со всеми заголовками и номерами страниц не дает нарушений."""
    report = _check(docx_builder, _body())

    assert report.summary.violations_total == 0
    assert report.passed is True
    assert report.summary.objects_checked > 0


def test_missing_toc_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Документ без оглавления получает нарушение уровня документа."""
    report = _check(docx_builder, _body(toc_entries=""))

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.violation_id == "toc:toc"
    assert violation.expected == {"toc": True}
    assert violation.found == {"toc": False}
    assert violation.location.snippet == "структура документа"

def test_entry_without_page_number_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Запись оглавления без номера страницы не проходит require_page_numbers."""
    entries = _styled_toc_entry("ВВЕДЕНИЕ") + "".join(
        _toc_entry(title) for title, _ in SECTIONS[1:]
    )

    report = _check(docx_builder, _body(toc_entries=entries))

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.expected == {"page_number": True}
    assert violation.found == {"page_number": None}
    assert violation.found_text == "номер страницы отсутствует"


def test_entry_without_heading_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Запись, которой нет среди заголовков, получает нарушение сверки названий."""
    entries = "".join(_toc_entry(title) for title, _ in SECTIONS)
    entries += _toc_entry("НЕСУЩЕСТВУЮЩИЙ РАЗДЕЛ", page="7")

    report = _check(docx_builder, _body(toc_entries=entries))

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.expected == {"matched_heading": True}
    assert violation.found == {"matched_heading": False}
    assert violation.location.snippet.startswith("НЕСУЩЕСТВУЮЩИЙ РАЗДЕЛ")


def test_wrong_page_number_is_reported_when_checking_pages(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Сверка страниц включается флагом check_page_numbers и ловит расхождение.

    Разбор без PDF оставляет is_page_number_match=None (пагинация оценочная),
    поэтому результат сверки подставляется явно: проверяется обработчик правила.
    """
    document = _parse(docx_builder, _body())
    toc = next(
        block for block in document.blocks if isinstance(block, TocContainerBlock)
    )
    entry = toc.entries[-1]
    entry.extracted_page_str = "42"
    entry.actual_heading_page_est = 1
    entry.is_page_number_match = False

    report = _report(document, expected={"check_page_numbers": True})

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.expected == {"page_number": 1}
    assert violation.found == {"page_number": "42"}


def test_heading_missing_from_toc_is_reported(docx_builder: DocxPackageBuilder) -> None:
    """Заголовок обязательной роли, забытый в оглавлении, получает нарушение."""
    body = _body() + _heading("2 Второй раздел") + TEXT

    report = _check(docx_builder, body)

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.violation_id.startswith("toc:toc:missing:")
    assert violation.expected == {"toc_entry": True, "section": "основной раздел"}
    assert violation.found_text == "раздел «основной раздел» не найден в оглавлении"


def test_manual_toc_fails_automation_requirement(docx_builder: DocxPackageBuilder) -> None:
    """Ручное оглавление не проходит требование require_automated."""
    report = _check(docx_builder, _body(), expected={"require_automated": True})

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.violation_id.endswith(":automated")
    assert violation.expected == {"automated": True}
    assert violation.found == {"automated": False}


def test_dot_leader_requirement_is_checked(docx_builder: DocxPackageBuilder) -> None:
    """Требование отточия реагирует на записи без заполнителя до номера страницы."""
    dotted = "".join(
        '<w:p><w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9350"/>'
        f"</w:tabs></w:pPr><w:r><w:t>{title}</w:t></w:r>"
        "<w:r><w:tab/><w:t>1</w:t></w:r></w:p>"
        for title, _ in SECTIONS
    )

    without_dots = _check(docx_builder, _body(), expected={"require_dot_leader": True})
    with_dots = _check(
        docx_builder,
        _body(toc_entries=dotted),
        expected={"require_dot_leader": True},
    )

    assert without_dots.summary.violations_total == len(SECTIONS)
    assert without_dots.violations[0].found == {"dot_leader": False}
    assert with_dots.summary.violations_total == 0

