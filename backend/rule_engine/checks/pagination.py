"""Семейство разбиения на страницы: висячие строки и неотрывность заголовков."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..findings import (
    RuleAssessment,
    Violation,
)
from ..rules import (
    HeadingPageBreakRule,
    KeepNextRule,
    WidowControlRule,
)
from .builders import (
    _build_assessment,
    _build_violation,
    _counted_assessment,
    _paragraph_location,
)
from .common import (
    _heading_records,
    _is_pdf_only,
    _paragraph_targets,
)
from parser.core.schema import (
    ParagraphBlock,
    ParsedDocxDocument,
)


def _state_label(enabled: bool, property_name: str) -> str:
    """Описывает ожидаемое состояние логического свойства абзаца."""
    return f"{property_name} {'включен' if enabled else 'выключен'}"


def _check_widow_control_rule(
    rule: WidowControlRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Ищет абзацы, разорванные между страницами без контроля висячих строк.

    Проверяются только абзацы, внутри текста которых есть разрыв страницы:
    у остальных абзацев висячая строка возникнуть не может. В ``pdf_only``
    свойства абзаца не извлекаются, поэтому правило честно отдает
    ``not_assessed`` вместо выдуманного ``pass``.
    """
    if _is_pdf_only(document):
        return [], 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="PDF не сохраняет свойства абзаца",
            confidence="low",
            note="widowControl доступен только в DOCX",
        )

    expected_enabled = rule.expected.enabled
    targets = _paragraph_targets(document, rule.target, contexts)
    violations: List[Violation] = []

    for paragraph in targets:
        actual_enabled = paragraph.effective_props.widow_control
        if actual_enabled == expected_enabled:
            continue
        text_length = len(paragraph.text_visible)
        breaks = [
            offset
            for offset in paragraph.rendered_page_breaks_offsets
            if 0 < offset < text_length
        ]
        if not breaks:
            continue
        violations.append(
            _build_violation(
                rule,
                address=paragraph.block_id,
                location=_paragraph_location(paragraph),
                expected={"widow_control": expected_enabled},
                found={"widow_control": actual_enabled},
                expected_text=_state_label(expected_enabled, "контроль висячих строк"),
                found_text=_state_label(actual_enabled, "контроль висячих строк"),
                occurrences=len(breaks),
                confidence="medium",
            )
        )

    assessment = _counted_assessment(
        rule,
        violations,
        len(targets),
        expected_text=rule.message,
        confidence="medium",
        empty_observed="абзацы основного текста не найдены",
        details={"split_paragraphs": len(violations)},
    )
    return violations, len(targets), assessment


def _check_keep_next_rule(
    rule: KeepNextRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет, что заголовки не отрываются от следующего за ними текста.

    Свойство ``keepNext`` есть только в DOCX; в ``pdf_only`` оно не
    извлекается и правило отдает ``not_assessed``. Проверяются роли из
    ожидания правила: по умолчанию главы и подразделы.
    """
    if _is_pdf_only(document):
        return [], 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="PDF не сохраняет свойства абзаца",
            confidence="low",
            note="keepNext доступен только в DOCX",
        )

    expected_enabled = rule.expected.enabled
    roles = set(rule.expected.roles)
    records = [
        record
        for record in _heading_records(document, rule.target)
        if record.node.semantic_role in roles
    ]
    violations: List[Violation] = []

    for record in records:
        actual_enabled = record.paragraph.effective_props.keep_next
        if actual_enabled == expected_enabled:
            continue
        violations.append(
            _build_violation(
                rule,
                address=record.paragraph.block_id,
                location=_paragraph_location(record.paragraph),
                expected={"keep_next": expected_enabled},
                found={"keep_next": actual_enabled},
                expected_text=_state_label(expected_enabled, "keepNext"),
                found_text=_state_label(actual_enabled, "keepNext"),
                confidence="high",
            )
        )

    assessment = _counted_assessment(
        rule,
        violations,
        len(records),
        expected_text=rule.message,
        confidence="high",
        empty_observed="заголовки выбранных ролей не найдены",
        details={"headings": len(records), "roles": sorted(roles)},
    )
    return violations, len(records), assessment


def _heading_start_page(paragraph: ParagraphBlock) -> int:
    """Возвращает страницу, на которой начинается текст заголовка.

    Разрыв в нулевой позиции означает, что заголовок перенесен на следующую
    страницу, даже если счётчик страниц абзаца уже указывает на неё.
    """
    start_page = paragraph.page_start_est
    if not paragraph.effective_props.page_break_before and 0 in (
        paragraph.rendered_page_breaks_offsets
    ):
        start_page += 1
    return start_page


def _previous_text_page(
    document: ParsedDocxDocument,
    block_id: str,
) -> Optional[int]:
    """Находит страницу окончания предыдущего непустого абзаца документа."""
    previous_page: Optional[int] = None
    for block in document.blocks:
        if not isinstance(block, ParagraphBlock):
            continue
        if block.block_id == block_id:
            return previous_page
        if block.text_visible.strip():
            previous_page = block.page_end_est
    return None


def _check_heading_page_break_rule(
    rule: HeadingPageBreakRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет, что структурные разделы начинаются с новой страницы.

    Страница заголовка сравнивается со страницей окончания предыдущего
    непустого абзаца. Пустые абзацы и разрывы перед ними не считаются
    содержимым: иначе глава, начатая сразу после пустой строки, выглядела бы
    нарушением. В PDF границы страниц точные, в DOCX — оценка по разрывам.
    """
    roles = set(rule.expected.roles)
    records = [
        record
        for record in _heading_records(document, rule.target)
        if record.node.semantic_role in roles
    ]
    violations: List[Violation] = []
    confidence = "high" if _is_pdf_only(document) else "medium"

    for record in records:
        paragraph = record.paragraph
        previous_page = _previous_text_page(document, paragraph.block_id)
        if previous_page is None:
            continue
        start_page = _heading_start_page(paragraph)
        if start_page > previous_page:
            continue
        violations.append(
            _build_violation(
                rule,
                address=paragraph.block_id,
                location=_paragraph_location(paragraph),
                expected={"start": "new_page"},
                found={
                    "start_page": start_page,
                    "previous_text_page": previous_page,
                },
                expected_text="раздел начинается с новой страницы",
                found_text=(
                    f"раздел начинается на странице {start_page}, "
                    f"предыдущий текст заканчивается на странице {previous_page}"
                ),
                confidence=confidence,
            )
        )

    assessment = _counted_assessment(
        rule,
        violations,
        len(records),
        expected_text=rule.message,
        confidence=confidence,
        empty_observed="структурные заголовки не найдены",
        details={"headings": len(records), "roles": sorted(roles)},
    )
    return violations, len(records), assessment
