"""Правила размещения формул и переноса в них."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from parser.core.schema import ParsedDocument

from ..findings import RuleAssessment, Violation
from ..rules import FormulaLineBreakRule, FormulaPlacementRule, Rule
from .builders import _build_assessment, _build_violation
from .common import _paragraph_index
from .constants import ALIGNMENT_LABELS
from .equations import _equation_location

RE_OPERATOR_START = re.compile(r"^\s*(?:=|＋|\+|−|-|×|·|:|/)\s*")


def _formula_alignment(value: Optional[str]) -> Optional[str]:
    """Приводит выравнивание формулы к метке правила."""
    if value is None:
        return None
    return {"both": "justify", "distribute": "justify"}.get(value, value)


def _check_formula_placement_rule(
    rule: FormulaPlacementRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Формула выносится в отдельную строку и выравнивается заданным образом."""
    equations = list(document.cross_ref_index.equations)
    if not equations:
        return [], 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="формулы в разборе не найдены",
            details={"equations": 0},
        )
    expected = rule.params
    blocks = _paragraph_index(document)
    violations: List[Violation] = []
    checked = 0
    alignment_not_set = 0
    for equation in equations:
        location = _equation_location(equation, blocks)
        if expected.require_standalone_line:
            checked += 1
            if not equation.is_standalone_line:
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{equation.equation_id}:standalone",
                        location=location,
                        expected={"standalone_line": True},
                        found={"standalone_line": False},
                        expected_text="формула вынесена в отдельную строку",
                        found_text="формула расположена внутри текста",
                    )
                )
        if expected.alignment is not None and equation.is_standalone_line:
            actual = _formula_alignment(equation.alignment)
            if actual is None:
                alignment_not_set += 1
                continue
            checked += 1
            if actual != expected.alignment:
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{equation.equation_id}:alignment",
                        location=location,
                        expected={"alignment": ALIGNMENT_LABELS.get(expected.alignment, expected.alignment)},
                        found={"alignment": ALIGNMENT_LABELS.get(actual, actual)},
                        expected_text=(
                            "формула выровнена: "
                            + ALIGNMENT_LABELS.get(expected.alignment, expected.alignment)
                        ),
                        found_text="формула выровнена: "
                        + ALIGNMENT_LABELS.get(actual, actual),
                    )
                )
    details = {
        "equations": len(equations),
        "checked": checked,
        "alignment_not_set": alignment_not_set,
        "violations": len(violations),
    }
    return violations, len(equations), _build_assessment(
        rule,
        "fail" if violations else "pass",
        expected_text=rule.message,
        observed_text=(
            f"нарушений размещения формул: {len(violations)} из {checked}"
            if violations
            else f"размещение верно у {checked} формул"
        ),
        confidence="medium",
        details=details,
    )


def _formula_lines(equation) -> List[str]:
    """Строки формулы по данным разбора."""
    text = equation.text_linearized or ""
    if "\n" in text:
        return [line for line in text.splitlines() if line.strip()]
    return [text] if text.strip() else []


def _check_formula_line_break_rule(
    rule: FormulaLineBreakRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """При переносе формулы знак остаётся в начале следующей строки."""
    equations = [equation for equation in document.cross_ref_index.equations if _formula_lines(equation)]
    if not equations:
        return [], 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="текст формул в разборе не найден",
            details={"equations": 0},
        )
    expected = rule.params
    blocks = _paragraph_index(document)
    violations: List[Violation] = []
    multi_line = 0
    checked = 0
    for equation in equations:
        lines = _formula_lines(equation)
        if len(lines) < 2:
            continue
        multi_line += 1
        location = _equation_location(equation, blocks)
        for line_number, line in enumerate(lines[1:], start=2):
            checked += 1
            if expected.require_operator_repeat and not RE_OPERATOR_START.match(line):
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{equation.equation_id}:line{line_number}",
                        location=location,
                        expected={"operator_at_line_start": True},
                        found={"line": line.strip()[:60]},
                        expected_text="строка переноса начинается со знака",
                        found_text=f"строка {line_number} начинается без знака",
                    )
                )
    details = {
        "equations": len(equations),
        "multi_line": multi_line,
        "lines_checked": checked,
        "violations": len(violations),
    }
    if multi_line == 0:
        return violations, len(equations), _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="многострочных формул в разборе нет: перенос проверить нельзя",
            details=details,
        )
    return violations, len(equations), _build_assessment(
        rule,
        "fail" if violations else "pass",
        expected_text=rule.message,
        observed_text=(
            f"нарушений переноса: {len(violations)} из {checked} строк"
            if violations
            else f"перенос верен в {checked} строках"
        ),
        confidence="medium",
        details=details,
    )
