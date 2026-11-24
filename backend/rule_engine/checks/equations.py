"""Семейство формул: нумерация вынесенных формул.

Разбор уже нашёл формулы (``document.cross_ref_index.equations``) и их номера;
правило проверяет только то, что вынесенная в отдельную строку формула
пронумерована. Формулы внутри строки не нумеруются, поэтому в выборку не
попадают. Если формул нет, правило честно не оценивается.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..findings import (
    RuleAssessment,
    Violation,
    ViolationLocation,
)
from ..rules import EquationNumberingRule
from .builders import (
    _build_violation,
    _collapse_text,
    _counted_assessment,
    _paragraph_location,
)
from .common import _paragraph_index
from parser.core.schema import (
    EquationObject,
    ParagraphBlock,
    ParsedDocxDocument,
)


def _equation_location(
    equation: EquationObject,
    blocks: Dict[str, ParagraphBlock],
) -> ViolationLocation:
    """Строит локацию формулы: абзац-владелец, страница и линейный текст."""
    block = blocks.get(equation.parent_block_id)
    if block is not None:
        location = _paragraph_location(block)
    else:
        location = ViolationLocation(
            block_id=equation.parent_block_id,
            page_start_est=equation.page_est,
            page_end_est=equation.page_est,
        )
    snippet = _collapse_text(equation.text_linearized or "")
    if snippet:
        location = location.model_copy(update={"snippet": snippet})
    return location


#: Римская запись номера формулы: «IV», «XII».
RE_ROMAN_EQUATION_NUMBER = re.compile(r"^\(?[IVXLCDM]+\)?[,.]?$", re.IGNORECASE)

#: Арабская запись номера формулы: «(1)», «2.3».
RE_ARABIC_EQUATION_NUMBER = re.compile(r"^\(?\d+(?:\.\d+)*\)?[,.]?$")

EQUATION_ALIGNMENT_LABELS: Dict[str, str] = {
    "left": "у левого поля",
    "center": "по центру",
    "right": "у правого поля",
}

#: Приведение выравнивания формулы к трём положениям номера.
ALIGNMENT_TO_POSITION: Dict[str, str] = {
    "left": "left",
    "start": "left",
    "center": "center",
    "right": "right",
    "end": "right",
    "both": "right",
    "distribute": "right",
}


def _equation_number_style(equation: EquationObject) -> Optional[str]:
    """Определяет форму записи номера формулы: «arabic», «roman» или None."""
    raw = (equation.number_clean or equation.number_raw or "").strip()
    if not raw:
        return None
    if RE_ARABIC_EQUATION_NUMBER.match(raw):
        return "arabic"
    if RE_ROMAN_EQUATION_NUMBER.match(raw):
        return "roman"
    return None


def _check_equation_numbering_rule(

    rule: EquationNumberingRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет, что каждая вынесенная формула имеет номер.

    Вынесенной считается формула с ``is_standalone_line=True``: именно её
    нумеруют по методическим указаниям. Формула без номера получает нарушение
    с адресом ``equation_id`` и локацией абзаца-владельца. Номера формул в
    DOCX и PDF разбираются по-разному, поэтому уверенность оценки средняя.
    """
    expected = rule.params
    index = document.cross_ref_index
    equations = list(index.equations) if index is not None else []
    standalone = [equation for equation in equations if equation.is_standalone_line]
    expected_text = "вынесенная формула пронумерована"
    if not standalone:
        return ([], 0, _counted_assessment(
            rule,
            [],
            0,
            expected_text=expected_text,
            confidence="medium",
            empty_observed="вынесенные формулы не найдены",
            details={"equations": len(equations)},
        ))

    blocks = _paragraph_index(document)
    violations: List[Violation] = []
    style_checked = 0
    position_checked = 0
    for equation in standalone:
        if equation.has_number:
            if expected.number_style is not None:
                actual_style = _equation_number_style(equation)
                if actual_style is not None:
                    style_checked += 1
                    if actual_style != expected.number_style:
                        violations.append(
                            _build_violation(
                                rule,
                                address=f"{equation.equation_id}:number_style",
                                location=_equation_location(equation, blocks),
                                expected={"number_style": expected.number_style},
                                found={
                                    "number_style": actual_style,
                                    "number_raw": equation.number_clean
                                    or equation.number_raw,
                                },
                                expected_text=(
                                    "номер формулы: арабские цифры"
                                    if expected.number_style == "arabic"
                                    else "номер формулы: римские цифры"
                                ),
                                found_text=(
                                    f"номер формулы: {equation.number_clean or equation.number_raw}"
                                ),
                                confidence="medium",
                            )
                        )
            if expected.number_position is not None:
                raw_alignment = (equation.alignment or "").strip().casefold()
                actual_position = ALIGNMENT_TO_POSITION.get(raw_alignment)
                if actual_position is not None:
                    position_checked += 1
                    if actual_position != expected.number_position:
                        label = EQUATION_ALIGNMENT_LABELS[
                            expected.number_position
                        ]
                        violations.append(
                            _build_violation(
                                rule,
                                address=f"{equation.equation_id}:number_position",
                                location=_equation_location(equation, blocks),
                                expected={"number_position": expected.number_position},
                                found={
                                    "number_position": actual_position,
                                    "alignment": equation.alignment,
                                },
                                expected_text=f"номер формулы {label}",
                                found_text=(
                                    "номер формулы "
                                    f"{EQUATION_ALIGNMENT_LABELS[actual_position]}"
                                ),
                                confidence="medium",
                            )
                        )
            continue
        violations.append(
            _build_violation(
                rule,
                address=equation.equation_id,
                location=_equation_location(equation, blocks),
                expected={"has_number": True},
                found={
                    "has_number": False,
                    "number_raw": equation.number_raw,
                    "number_positioning_method": equation.number_positioning_method,
                    "representation_kind": equation.representation_kind,
                },
                expected_text=expected_text,
                found_text="у вынесенной формулы нет номера",
                confidence="medium",
            )
        )

    assessment = _counted_assessment(
        rule,
        violations,
        len(standalone),
        expected_text=expected_text,
        confidence="medium",
        empty_observed="вынесенные формулы не найдены",
        details={
            "equations": len(equations),
            "standalone_equations": len(standalone),
            "unnumbered": sum(
                1 for equation in standalone if not equation.has_number
            ),
            "number_style": expected.number_style,
            "number_position": expected.number_position,
            "style_checked": style_checked,
            "position_checked": position_checked,
        },
    )
    return violations, len(standalone), assessment
