"""Семейство структуры: обязательные разделы работы."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..findings import (
    RuleAssessment,
    Violation,
)
from ..rules import RequiredSectionsRule
from .builders import _counted_assessment
from .common import (
    _document_violation,
    _heading_records,
    _heading_violation,
    _is_pdf_only,
    _section_matches,
)
from parser.core.schema import ParsedDocxDocument


def _check_required_sections_rule(
    rule: RequiredSectionsRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет наличие и порядок обязательных разделов документа."""
    records = _heading_records(document, rule.target)
    violations: List[Violation] = []
    position = -1
    previous_label: Optional[str] = None

    for spec in rule.params.sections:
        matches = [
            index
            for index, record in enumerate(records)
            if _section_matches(spec, record)
        ]
        if not matches:
            if spec.required:
                violations.append(
                    _document_violation(
                        rule,
                        f"section:{spec.label}",
                        {"section": spec.label, "present": True},
                        {"section": spec.label, "present": False},
                        f"раздел «{spec.label}» присутствует",
                        f"раздел «{spec.label}» не найден",
                    )
                )
            continue

        following = [index for index in matches if index > position]
        if not following:
            record = records[matches[0]]
            expected_text = (
                f"раздел «{spec.label}» после «{previous_label}»"
                if previous_label
                else f"раздел «{spec.label}»"
            )
            violations.append(
                _heading_violation(
                    rule,
                    record,
                    {"section": spec.label, "after": previous_label},
                    {"section": spec.label, "position": matches[0]},
                    [expected_text],
                    [f"раздел «{spec.label}» расположен выше"],
                )
            )
            continue

        position = following[0]
        previous_label = spec.label

    checked = len(rule.params.sections)
    assessment = _counted_assessment(
        rule,
        violations,
        checked,
        expected_text=rule.message,
        confidence="medium" if _is_pdf_only(document) else "high",
        empty_observed="список ожидаемых разделов пуст",
        details={
            "sections_expected": checked,
            "sections_required": sum(
                1 for spec in rule.params.sections if spec.required
            ),
            "headings": len(records),
        },
    )
    return violations, checked, assessment
