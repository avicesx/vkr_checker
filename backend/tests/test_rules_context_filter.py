"""Регрессии контекст-фильтра движка: титульный лист и листинги не шумят.

Фильтр `RuleTarget.exclude_contexts` отделяет служебные зоны документа от
основного текста. Тесты фиксируют три свойства:

* старый набор правил без нового поля загружается и проверяет абзацы основного
  текста без изменений (поле получает значение по умолчанию);
* пустой `exclude_contexts` возвращает прежнее поведение — листинг и титул
  снова дают нарушения шрифта и отступа;
* по умолчанию шумные блоки не проверяются, но настоящий абзац с неверным
  шрифтом продолжает падать, а заголовки и структурные правила не задеты.
"""
from __future__ import annotations

from typing import Dict, Optional

import pytest
from conftest import DocxPackageBuilder
from parser.core.schema import ParsedDocxDocument
from parser.docx.pipeline import DocxParserPipeline
from pydantic import ValidationError
from rule_engine import (
    DEFAULT_RULESET_PATH,
    RuleEngine,
    RuleSet,
    RuleTarget,
    load_ruleset,
)
from rule_engine.context import classify_document_contexts

PLAIN_PARAGRAPH = """
<w:p><w:r><w:t>Основной текст документа для проверки шрифта</w:t></w:r></w:p>
"""

#: Строка титульного листа: по центру, 11 pt, реквизит вуза, без красной строки.
TITLE_PAGE_PARAGRAPH = """
<w:p>
  <w:pPr><w:jc w:val="center"/><w:ind w:firstLine="0"/></w:pPr>
  <w:r>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr>
    <w:t>Министерство науки и высшего образования Российской Федерации</w:t>
  </w:r>
</w:p>
"""

#: Строка листинга: Courier New 12 pt, без красной строки.
CODE_LISTING_PARAGRAPH = """
<w:p>
  <w:pPr><w:ind w:firstLine="0"/></w:pPr>
  <w:r>
    <w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/><w:sz w:val="24"/></w:rPr>
    <w:t>public class Calculator</w:t>
  </w:r>
</w:p>
"""

#: Настоящий абзац основного текста с чужой гарнитурой: 14 pt, точка в конце.
BAD_FONT_BODY_PARAGRAPH = """
<w:p>
  <w:r>
    <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr>
    <w:t>Фрагмент основного текста набран другой гарнитурой.</w:t>
  </w:r>
</w:p>
"""

#: Короткий абзац 11 pt без точки в конце и без реквизита титула: это не титул.
SMALL_BODY_PARAGRAPH = """
<w:p>
  <w:r>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr>
    <w:t>Фрагмент набран другим шрифтом</w:t>
  </w:r>
</w:p>
"""

#: Заголовок, похожий на титул (по центру, 11 pt), но с heading_info.
HEADING_PARAGRAPH = """
<w:p>
  <w:pPr><w:pStyle w:val="Heading1"/><w:jc w:val="center"/></w:pPr>
  <w:r>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr>
    <w:t>1 ВВЕДЕНИЕ</w:t>
  </w:r>
</w:p>
"""

#: Абзац основного текста, в котором слово «подпись» — часть предложения.
SIGNATURE_MENTION_PARAGRAPH = """
<w:p>
  <w:r>
    <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr>
    <w:t>Документ содержит подпись руководителя и печать организации.</w:t>
  </w:r>
</w:p>
"""


def _service_sheet_line(
    text: str,
    *,
    size_pt: float = 10.0,
    alignment: str = "left",
) -> str:
    """Собирает строку сервисного листа: мелкий кегль и нулевая красная строка.

    Такие строки (акт внедрения, лист задания) оформлены по требованиям
    методички к служебным страницам, поэтому «основной текст» их не проверяет.
    """
    return (
        f'<w:p><w:pPr><w:jc w:val="{alignment}"/><w:ind w:firstLine="0"/></w:pPr>'
        '<w:r><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
        f'<w:sz w:val="{int(round(size_pt * 2))}"/></w:rPr>'
        f"<w:t>{text}</w:t></w:r></w:p>"
    )


def _parse(builder: DocxPackageBuilder, body: str) -> ParsedDocxDocument:
    """Собирает DOCX из тела и возвращает результат разбора."""
    builder.set_body(body)
    return DocxParserPipeline(builder.build()).parse()


def _engine(
    scope: str = "body_text",
    exclude_contexts: Optional[list] = None,
    include_headings: bool = False,
) -> RuleEngine:
    """Собирает движок из правила шрифта и правила отступа с общей областью."""
    target: Dict[str, object] = {"scope": scope, "include_headings": include_headings}
    if exclude_contexts is not None:
        target["exclude_contexts"] = exclude_contexts
    payload = {
        "ruleset_id": "context-filter-test",
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


def _block_id_by_text(document: ParsedDocxDocument, startswith: str) -> str:
    """Возвращает block_id абзаца по началу видимого текста."""
    for block in document.blocks:
        text = getattr(block, "text_visible", "")
        if text.strip().startswith(startswith):
            return block.block_id
    raise AssertionError(f"абзац «{startswith}» не найден в документе")


def test_rule_target_default_excludes_service_contexts() -> None:
    """Новое поле по умолчанию отсекает титульный лист и листинг."""
    assert RuleTarget().exclude_contexts == ["title_page", "code_listing"]


def test_legacy_ruleset_json_without_context_field_loads_and_works(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Старый JSON без нового поля загружается и проверяет тело без изменений."""
    ruleset = load_ruleset(DEFAULT_RULESET_PATH)
    font_rule = ruleset.get_rule("font_body_text")

    assert font_rule.target.exclude_contexts == ["title_page", "code_listing"]

    document = _parse(docx_builder, PLAIN_PARAGRAPH + BAD_FONT_BODY_PARAGRAPH)
    report = RuleEngine(ruleset).check(document)

    assert report.summary.by_rule.get("font_body_text") == 1
    assert report.summary.by_rule.get("indent_first_line") is None
    font_violations = [
        violation for violation in report.violations if violation.rule_id == "font_body_text"
    ]
    assert font_violations[-1].found == {"family": "Arial"}


def test_default_contexts_skip_title_page_and_code_listing(
    docx_builder: DocxPackageBuilder,
) -> None:
    """По умолчанию титул и листинг не дают ни шрифтовых, ни отступных нарушений."""
    document = _parse(
        docx_builder,
        TITLE_PAGE_PARAGRAPH + CODE_LISTING_PARAGRAPH + PLAIN_PARAGRAPH,
    )
    contexts = classify_document_contexts(document.blocks)
    assert contexts[_block_id_by_text(document, "Министерство")] == "title_page"
    assert contexts[_block_id_by_text(document, "public class")] == "code_listing"

    report = _engine().check(document)

    assert report.violations == []
    assert report.passed is True
    # Счетчики считают только прошедшие фильтр блоки: один абзац на два правила.
    assert report.summary.objects_checked == 2


def test_empty_exclude_contexts_returns_old_behaviour(
    docx_builder: DocxPackageBuilder,
) -> None:
    """exclude_contexts=[] отключает фильтр: титул и листинг снова падают."""
    document = _parse(
        docx_builder,
        TITLE_PAGE_PARAGRAPH + CODE_LISTING_PARAGRAPH + PLAIN_PARAGRAPH,
    )
    title_id = _block_id_by_text(document, "Министерство")
    listing_id = _block_id_by_text(document, "public class")

    report = _engine(exclude_contexts=[]).check(document)

    assert report.passed is False
    # Три абзаца верхнего уровня на два правила: фильтр выключен.
    assert report.summary.objects_checked == 6
    flagged = {violation.location.block_id for violation in report.violations}
    assert flagged == {title_id, listing_id}
    assert report.summary.by_kind == {"font": 2, "indent": 2}


def test_body_paragraph_with_wrong_font_is_still_reported(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Фильтр не скрывает нарушение в настоящем абзаце основного текста."""
    document = _parse(
        docx_builder,
        TITLE_PAGE_PARAGRAPH + CODE_LISTING_PARAGRAPH + BAD_FONT_BODY_PARAGRAPH,
    )
    bad_font_id = _block_id_by_text(document, "Фрагмент основного текста")

    report = _engine().check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.rule_id == "font_body_text"
    assert violation.location.block_id == bad_font_id
    assert violation.found == {"family": "Arial"}
    assert report.summary.objects_checked == 2


def test_body_paragraphs_scope_also_applies_context_filter(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Область body_paragraphs фильтруется так же, как body_text."""
    document = _parse(
        docx_builder,
        TITLE_PAGE_PARAGRAPH + CODE_LISTING_PARAGRAPH + PLAIN_PARAGRAPH,
    )

    filtered = _engine(scope="body_paragraphs").check(document)
    unfiltered = _engine(scope="body_paragraphs", exclude_contexts=[]).check(document)

    assert filtered.violations == []
    assert filtered.summary.objects_checked == 2
    assert unfiltered.summary.by_kind == {"font": 2, "indent": 2}
    assert unfiltered.summary.objects_checked == 6


def test_headings_are_not_filtered_by_context(docx_builder: DocxPackageBuilder) -> None:
    """Заголовок не считается титулом: include_headings по-прежнему его проверяет."""
    document = _parse(docx_builder, HEADING_PARAGRAPH + PLAIN_PARAGRAPH)
    heading_id = _block_id_by_text(document, "1 ВВЕДЕНИЕ")
    assert classify_document_contexts(document.blocks)[heading_id] == "body"

    report = _engine(include_headings=True).check(document)

    assert [violation.location.block_id for violation in report.violations] == [heading_id]
    assert report.summary.objects_checked == 4


def test_structure_rules_still_see_service_zones(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Структурные правила работают по всему документу, фильтр их не касается."""
    document = _parse(
        docx_builder,
        TITLE_PAGE_PARAGRAPH + CODE_LISTING_PARAGRAPH + PLAIN_PARAGRAPH,
    )

    report = RuleEngine(load_ruleset(DEFAULT_RULESET_PATH)).check(document)

    assert report.summary.by_kind == {"required_sections": 4, "toc": 1}
    assert "font_body_text" not in report.summary.by_rule
    assert "indent_first_line" not in report.summary.by_rule


def test_small_paragraph_without_title_attribute_is_not_hidden(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Короткая строка 11 pt без реквизита титула остается основным текстом.

    Мелкий кегль и отсутствие точки в конце сами по себе не делают абзац
    титульным листом: иначе фильтр скрыл бы настоящее нарушение шрифта.
    """
    document = _parse(docx_builder, SMALL_BODY_PARAGRAPH)
    block_id = _block_id_by_text(document, "Фрагмент набран")

    assert classify_document_contexts(document.blocks)[block_id] == "body"

    report = _engine().check(document)

    assert [violation.location.block_id for violation in report.violations] == [block_id]
    assert report.violations[0].found == {"family": "Calibri", "size_pt": pytest.approx(11.0)}


def test_unknown_context_names_do_not_hide_body_paragraph(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Неизвестное имя контекста ничего не фильтрует: контракт — список строк."""
    document = _parse(docx_builder, CODE_LISTING_PARAGRAPH + BAD_FONT_BODY_PARAGRAPH)

    report = _engine(exclude_contexts=["stub_context"]).check(document)

    assert report.summary.by_kind == {"font": 2, "indent": 1}
    assert report.summary.objects_checked == 4


def test_unknown_target_field_is_rejected() -> None:
    """extra='forbid' сохранен: опечатка в имени поля падает на загрузке."""
    payload = {
        "ruleset_id": "context-filter-test",
        "ruleset_version": "1.0.0",
        "rules": [
            {
                "rule_id": "font_body_text",
                "kind": "font",
                "message": "Основной текст",
                "target": {"scope": "body_text", "exclude_context": ["body"]},
                "expected": {"family": "Times New Roman"},
            }
        ],
    }

    with pytest.raises(ValidationError):
        RuleSet.model_validate(payload)


# =====================================================================
# СЕРВИСНЫЕ ЛИСТЫ: АКТ ВНЕДРЕНИЯ, ЛИСТ ЗАДАНИЯ
# =====================================================================


def test_act_sheet_zone_is_not_checked_by_body_text_rules(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Строки акта внедрения не дают нарушений шрифта и отступа.

    Акт стоит в середине документа (страница не первая), набран 10 pt Calibri и
    без красной строки: по правилам «основной текст» это были бы нарушения, но
    служебный лист оформлен по другому разделу методички.
    """
    document = _parse(
        docx_builder,
        HEADING_PARAGRAPH
        + SIGNATURE_MENTION_PARAGRAPH
        + _service_sheet_line("АКТ ВНЕДРЕНИЯ", alignment="center")
        + _service_sheet_line("Нормоконтролер: Е.Д. Ливадний")
        + _service_sheet_line("(подпись)")
        + _service_sheet_line("Екатеринбург", alignment="center")
        + _service_sheet_line("2026", alignment="center"),
    )
    contexts = classify_document_contexts(document.blocks)
    mention_id = _block_id_by_text(document, "Документ содержит подпись")

    assert contexts[mention_id] == "body"
    for prefix in ("Нормоконтролер", "(подпись)", "Екатеринбург", "2026"):
        assert contexts[_block_id_by_text(document, prefix)] == "title_page", prefix

    report = _engine().check(document)

    assert report.summary.by_rule == {"font_body_text": 1}
    flagged = [violation.location.block_id for violation in report.violations]
    assert flagged == [mention_id]
    assert report.violations[0].found == {"family": "Arial"}


def test_body_paragraph_mentioning_signature_is_still_checked(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Упоминание подписи в абзаце не прячет нарушение: это основной текст."""
    document = _parse(docx_builder, HEADING_PARAGRAPH + SIGNATURE_MENTION_PARAGRAPH)
    mention_id = _block_id_by_text(document, "Документ содержит подпись")

    assert classify_document_contexts(document.blocks)[mention_id] == "body"

    report = _engine().check(document)

    flagged = [violation.location.block_id for violation in report.violations]
    assert flagged == [mention_id]
    assert report.violations[0].found == {"family": "Arial"}

