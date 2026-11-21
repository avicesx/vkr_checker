"""Семейство заголовков: уровни, нумерация, начертание и регистр."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..findings import (
    RuleAssessment,
    Violation,
)
from ..rules import (
    HeadingFormatRule,
    HeadingLevelsRule,
    HeadingNumberingRule,
    NUMBERLESS_HEADING_ROLES,
)
from .builders import _counted_assessment
from .common import (
    _HeadingRecord,
    _expected_number_depth,
    _format_section_number,
    _heading_records,
    _heading_section_number,
    _heading_violation,
    _is_pdf_only,
    _parse_section_number,
)
from .constants import (
    ALIGNMENT_LABELS,
    CASE_LABELS,
    FOUND_CASE_LABELS,
    NUMBER_ENDING_LABELS,
)
from parser.core.schema import ParsedDocxDocument


def _check_heading_levels_rule(
    rule: HeadingLevelsRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет, что уровни заголовков растут без пропусков и не глубже лимита."""
    records = _heading_records(document, rule.target)
    expected = rule.expected
    violations: List[Violation] = []
    prev_level: Optional[int] = None

    for record in records:
        level = record.node.level
        expected_values: Dict[str, Any] = {}
        found_values: Dict[str, Any] = {}
        expected_parts: List[str] = []
        found_parts: List[str] = []

        if prev_level is None and level > expected.first_level:
            expected_values["first_level"] = expected.first_level
            found_values["level"] = level
            expected_parts.append(f"первый заголовок уровня {expected.first_level}")
            found_parts.append(f"первый заголовок уровня {level}")

        if (
            prev_level is not None
            and not expected.allow_skipped_levels
            and level > prev_level + 1
        ):
            expected_values["max_level_after_previous"] = prev_level + 1
            found_values["level"] = level
            expected_parts.append(f"после уровня {prev_level} идет уровень {prev_level + 1}")
            found_parts.append(f"уровень {level}")

        if expected.max_level is not None and level > expected.max_level:
            expected_values["max_level"] = expected.max_level
            found_values["level"] = level
            expected_parts.append(f"уровень не глубже {expected.max_level}")
            found_parts.append(f"уровень {level}")

        prev_level = level
        if not expected_values:
            continue
        violations.append(
            _heading_violation(
                rule, record, expected_values, found_values, expected_parts, found_parts
            )
        )

    assessment = _counted_assessment(
        rule,
        violations,
        len(records),
        expected_text=rule.message,
        confidence="medium" if _is_pdf_only(document) else "high",
        empty_observed="заголовки в области действия правила не найдены",
        details={"headings": len(records)},
    )
    return violations, len(records), assessment


def _check_heading_numbering_rule(
    rule: HeadingNumberingRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет последовательность, родителя, глубину и запись номеров заголовков."""
    records = _heading_records(document, rule.target)
    expected = rule.expected
    violations: List[Violation] = []

    sequence_issues: Dict[str, Tuple[Tuple[int, ...], Tuple[int, ...]]] = {}
    if expected.check_sequence:
        groups: Dict[Optional[str], List[_HeadingRecord]] = {}
        for record in records:
            key = record.parent.node_id if record.parent is not None else None
            groups.setdefault(key, []).append(record)
        for group in groups.values():
            counters: Dict[int, int] = {}
            for record in group:
                numbers = _parse_section_number(_heading_section_number(record))
                if numbers is None:
                    continue
                depth = len(numbers)
                expected_last = counters.get(depth, 0) + 1
                if numbers[-1] != expected_last:
                    sequence_issues[record.node.node_id] = (
                        numbers[:-1] + (expected_last,),
                        numbers,
                    )
                counters[depth] = numbers[-1]

    for record in records:
        numbers = _parse_section_number(_heading_section_number(record))
        heading = record.paragraph.heading_info
        expected_values: Dict[str, Any] = {}
        found_values: Dict[str, Any] = {}
        expected_parts: List[str] = []
        found_parts: List[str] = []

        if numbers is None:
            if (
                record.node.semantic_role in expected.require_number_for_roles
                and record.node.semantic_role not in NUMBERLESS_HEADING_ROLES
            ):
                expected_values["section_number"] = "номер раздела"
                found_values["section_number"] = record.node.section_number
                expected_parts.append("нумерованный заголовок")
                found_parts.append("номер не найден")
            if expected_values:
                violations.append(
                    _heading_violation(
                        rule,
                        record,
                        expected_values,
                        found_values,
                        expected_parts,
                        found_parts,
                    )
                )
            continue

        if expected.check_depth_matches_level:
            expected_depth = _expected_number_depth(record)
            if expected_depth is not None and len(numbers) != expected_depth:
                expected_values["number_depth"] = expected_depth
                found_values["number_depth"] = len(numbers)
                found_values["section_number"] = _format_section_number(numbers)
                expected_parts.append(f"номер глубины {expected_depth}")
                found_parts.append(
                    f"номер '{_format_section_number(numbers)}' глубины {len(numbers)}"
                )

        if expected.check_parent_number:
            if record.parent is None:
                if len(numbers) != 1:
                    expected_values["parent_number"] = None
                    found_values["section_number"] = _format_section_number(numbers)
                    expected_parts.append("номер первого уровня без родителя")
                    found_parts.append(
                        f"номер '{_format_section_number(numbers)}' без родительского раздела"
                    )
            else:
                parent_numbers = _parse_section_number(record.parent.section_number)
                if parent_numbers is not None and numbers[:-1] != parent_numbers:
                    expected_values["parent_number"] = record.parent.section_number
                    found_values["section_number"] = _format_section_number(numbers)
                    expected_parts.append(
                        f"номер с началом '{record.parent.section_number}.'"
                    )
                    found_parts.append(f"номер '{_format_section_number(numbers)}'")

        if record.node.node_id in sequence_issues:
            expected_number, found_number = sequence_issues[record.node.node_id]
            expected_values["next_section_number"] = _format_section_number(expected_number)
            found_values["section_number"] = _format_section_number(found_number)
            expected_parts.append(
                f"следующий номер '{_format_section_number(expected_number)}'"
            )
            found_parts.append(f"номер '{_format_section_number(found_number)}'")

        if expected.number_ending != "any" and heading is not None:
            wants_dot = expected.number_ending == "with_dot"
            if heading.has_trailing_dot_in_number != wants_dot:
                actual_ending = (
                    "with_dot" if heading.has_trailing_dot_in_number else "without_dot"
                )
                expected_values["number_ending"] = expected.number_ending
                found_values["number_ending"] = actual_ending
                expected_parts.append(NUMBER_ENDING_LABELS[expected.number_ending])
                found_parts.append(NUMBER_ENDING_LABELS[actual_ending])

        if not expected_values:
            continue
        violations.append(
            _heading_violation(
                rule, record, expected_values, found_values, expected_parts, found_parts
            )
        )

    assessment = _counted_assessment(
        rule,
        violations,
        len(records),
        expected_text=rule.message,
        confidence="medium" if _is_pdf_only(document) else "high",
        empty_observed="заголовки в области действия правила не найдены",
        details={"headings": len(records)},
    )
    return violations, len(records), assessment


def _check_heading_format_rule(
    rule: HeadingFormatRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет точку в конце заголовка, регистр и выравнивание."""
    records = _heading_records(document, rule.target)
    expected = rule.expected
    violations: List[Violation] = []

    for record in records:
        heading = record.paragraph.heading_info
        if heading is None:
            continue
        expected_values: Dict[str, Any] = {}
        found_values: Dict[str, Any] = {}
        expected_parts: List[str] = []
        found_parts: List[str] = []

        if expected.trailing_dot != "any":
            wants_dot = expected.trailing_dot == "require"
            if heading.has_trailing_dot_in_title != wants_dot:
                actual = "require" if heading.has_trailing_dot_in_title else "forbid"
                expected_values["trailing_dot"] = expected.trailing_dot
                found_values["trailing_dot"] = actual
                expected_parts.append(
                    "точка в конце заголовка" if wants_dot else "без точки в конце заголовка"
                )
                found_parts.append(
                    "точка в конце заголовка"
                    if heading.has_trailing_dot_in_title
                    else "без точки в конце заголовка"
                )

        if expected.trailing_dot_in_number != "any" and heading.section_number_raw:
            wants_dot = expected.trailing_dot_in_number == "require"
            if heading.has_trailing_dot_in_number != wants_dot:
                actual = "require" if heading.has_trailing_dot_in_number else "forbid"
                expected_values["trailing_dot_in_number"] = expected.trailing_dot_in_number
                found_values["trailing_dot_in_number"] = actual
                expected_parts.append(
                    "точка после номера" if wants_dot else "без точки после номера"
                )
                found_parts.append(
                    "точка после номера"
                    if heading.has_trailing_dot_in_number
                    else "без точки после номера"
                )

        if expected.case != "any":
            letters = [char for char in heading.clean_title_text if char.isalpha()]
            is_upper = bool(letters) and all(char.isupper() for char in letters)
            starts_upper = bool(letters) and letters[0].isupper()
            case_problem = False
            if expected.case == "uppercase" and not is_upper:
                case_problem = True
            elif expected.case == "not_uppercase" and is_upper and len(letters) > 1:
                case_problem = True
            elif expected.case == "capitalized" and (
                not starts_upper or (is_upper and len(letters) > 1)
            ):
                case_problem = True
            if case_problem:
                if is_upper and len(letters) > 1:
                    found_case = "uppercase"
                elif starts_upper:
                    found_case = "capitalized"
                else:
                    found_case = "lowercase_start"
                expected_values["case"] = expected.case
                found_values["case"] = found_case
                expected_parts.append(CASE_LABELS[expected.case])
                found_parts.append(FOUND_CASE_LABELS[found_case])

        if expected.alignment is not None:
            actual_alignment = record.paragraph.effective_props.alignment
            if actual_alignment != expected.alignment:
                expected_values["alignment"] = expected.alignment
                found_values["alignment"] = actual_alignment
                expected_parts.append(ALIGNMENT_LABELS[expected.alignment])
                found_parts.append(ALIGNMENT_LABELS.get(actual_alignment, "не задано"))

        if not expected_values:
            continue
        violations.append(
            _heading_violation(
                rule, record, expected_values, found_values, expected_parts, found_parts
            )
        )

    assessment = _counted_assessment(
        rule,
        violations,
        len(records),
        expected_text=rule.message,
        confidence="medium" if _is_pdf_only(document) else "high",
        empty_observed="заголовки в области действия правила не найдены",
        details={"headings": len(records)},
    )
    return violations, len(records), assessment
