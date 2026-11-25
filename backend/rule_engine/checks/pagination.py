"""Семейство разбиения на страницы: висячие строки и неотрывность заголовков."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..findings import (
    RuleAssessment,
    Violation,
    ViolationLocation,
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

    expected_enabled = rule.params.enabled
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

    expected_enabled = rule.params.enabled
    roles = set(rule.params.roles)
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


def _heading_location(
    paragraph: ParagraphBlock,
    start_page: int,
) -> ViolationLocation:
    """Адрес находки: страница совпадает с найденной страницей заголовка.

    ``page_start_est`` — оценка по разбиению, а ``start_page`` учитывает
    разрыв в нулевой позиции, поэтому на границе страницы значения расходятся
    на единицу. В отчете читатель видит ``start_page``, и адрес берет его же;
    ``page_start_est`` остается резервом, когда страница не определена.
    """
    location = _paragraph_location(paragraph)
    if start_page is None or location.page_start_est == start_page:
        return location
    end_page = location.page_end_est
    if end_page is None or end_page < start_page:
        end_page = start_page
    return location.model_copy(
        update={"page_start_est": start_page, "page_end_est": end_page}
    )


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


def _has_explicit_page_breaks(document: ParsedDocxDocument) -> bool:
    """Проверяет, размечены ли в документе явные разрывы страницы.

    Без таких блоков разбиение на страницы оценочное: по пустым абзацам нельзя
    понять, начинается глава с новой страницы или просто с отбивки.
    """
    for block in document.blocks:
        if not isinstance(block, ParagraphBlock):
            continue
        if block.rendered_page_breaks_offsets:
            return True
        if block.effective_props.page_break_before:
            return True
    return False


def _heading_starts_with_break(paragraph: ParagraphBlock) -> bool:
    """Проверяет, что заголовок начинается с явного разрыва страницы."""
    if 0 in paragraph.rendered_page_breaks_offsets:
        return True
    return bool(paragraph.effective_props.page_break_before)


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
    Требование и запрет разрыва — разные смыслы, поэтому в профиле это два
    правила: глава обязана начинаться с новой страницы, параграф — нет.
    """
    roles = set(rule.params.roles)
    forbidden_roles = set(rule.params.forbid_new_page_roles)
    # В PDF границы страниц точные, поэтому оценка по page_start_est надёжна.
    # Явный разрыв нужен только там, где страницы DOCX расставлены оценкой.
    explicit_mode = rule.params.require_page_break and not _is_pdf_only(document)
    all_records = _heading_records(document, rule.target)
    chapters = [
        record
        for record in all_records
        if record.node.semantic_role == "main_chapter"
    ]
    # При включённом параметре главы проверяются по явному разрыву, а не по
    # оценке страницы: иначе одна и та же глава могла бы дать две находки.
    records = [
        record
        for record in all_records
        if record.node.semantic_role in roles
        and not (explicit_mode and record.node.semantic_role == "main_chapter")
    ]
    forbidden_records = [
        record
        for record in all_records
        if record.node.semantic_role in forbidden_roles
        and record.node.semantic_role not in roles
    ]
    violations: List[Violation] = []
    confidence = "high" if _is_pdf_only(document) else "medium"

    explicit_breaks_found = _has_explicit_page_breaks(document)
    if explicit_mode and chapters and not explicit_breaks_found:
        # В документе нет ни одного явного разрыва: страницы расставлены
        # оценкой, поэтому проверять положение главы нельзя.
        return [], 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text="глава должна начинаться с явного разрыва страницы",
            observed_text=(
                "документ не содержит явных разрывов страницы, "
                "положение главы не определено"
            ),
            confidence="low",
            details={
                "chapters": len(chapters),
                "explicit_breaks_found": 0,
                "require_page_break": True,
            },
        )

    for record in chapters if explicit_mode else []:
        paragraph = record.paragraph
        if _heading_starts_with_break(paragraph):
            continue
        violations.append(
            _build_violation(
                rule,
                address=paragraph.block_id,
                location=_paragraph_location(paragraph),
                expected={"page_break": True},
                found={
                    "page_break": False,
                    "page_start": paragraph.page_start_est,
                },
                expected_text="глава должна начинаться с явного разрыва страницы",
                found_text=(
                    "в начале главы нет явного разрыва страницы "
                    f"(страница {paragraph.page_start_est})"
                ),
                confidence=confidence,
            )
        )

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
                location=_heading_location(paragraph, start_page),
                expected={"start": "new_page"},
                found={
                    "start_page": start_page,
                    "previous_text_page": previous_page,
                },
                expected_text="раздел должен начинаться с новой страницы",
                found_text=(
                    f"раздел начинается на странице {start_page}, "
                    f"предыдущий текст заканчивается на странице {previous_page}"
                ),
                confidence=confidence,
            )
        )

    # Разделы из forbid_new_page_roles, наоборот, обязаны продолжаться на
    # той же странице: перенос такого заголовка на новую страницу — нарушение.
    for record in forbidden_records:
        paragraph = record.paragraph
        previous_page = _previous_text_page(document, paragraph.block_id)
        if previous_page is None:
            continue
        start_page = _heading_start_page(paragraph)
        if start_page <= previous_page:
            continue
        violations.append(
            _build_violation(
                rule,
                address=paragraph.block_id,
                location=_heading_location(paragraph, start_page),
                expected={"start": "same_page"},
                found={
                    "start_page": start_page,
                    "previous_text_page": previous_page,
                },
                expected_text="параграф не должен начинаться с новой страницы",
                found_text=(
                    f"параграф начинается на странице {start_page}, "
                    f"предыдущий текст заканчивается на странице {previous_page}"
                ),
                confidence=confidence,
            )
        )

    checked = len(records) + len(forbidden_records)
    if explicit_mode:
        checked += len(chapters)
    assessment = _counted_assessment(
        rule,
        violations,
        checked,
        expected_text=rule.message,
        confidence=confidence,
        empty_observed="структурные заголовки не найдены",
        details={
            "headings": checked,
            "roles": sorted(roles),
            "forbid_new_page_roles": sorted(forbidden_roles),
            "require_page_break": rule.params.require_page_break,
            "chapters": len(chapters) if explicit_mode else 0,
            "explicit_breaks_found": explicit_breaks_found,
            "page_mode": "pdf_exact" if _is_pdf_only(document) else "docx_estimate",
        },
    )
    return violations, checked, assessment
