"""Дополнительные правила оформления текста: десятичный разделитель и стиль абзаца."""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from parser.core.schema import ParagraphBlock, ParsedDocument

from ..findings import RuleAssessment, Violation
from ..rules import NumberFormatRule, ParagraphStyleRule, Rule
from .builders import _build_assessment, _build_violation, _paragraph_location
from .common import _paragraph_targets

RE_DECIMAL_DOT = re.compile(r"(?<![\w№.,])(\d{1,3})\.(\d{1,3})(?![\d.,])")
RE_AFTER_UPPER_WORD = re.compile(r"\s+[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z\-]+")
RE_STANDARD_CONTEXT = re.compile(r"(?:гост|№|п\.|рис\.|табл\.|гл\.|таблица|рисунок)\s*$", re.IGNORECASE)
RE_DECIMAL_COMMA = re.compile(r"(?<![\w№.,])(\d{1,3}),(\d{1,3})(?![\d.,])")


def _check_number_format_rule(
    rule: NumberFormatRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Десятичные числа записаны с запятой, а не с точкой."""
    expected = rule.params
    pattern = RE_DECIMAL_DOT if expected.decimal_separator == "comma" else RE_DECIMAL_COMMA
    paragraphs = _paragraph_targets(document, rule.target, contexts)
    violations: List[Violation] = []
    checked = 0
    for paragraph in paragraphs:
        if paragraph.heading_info is not None:
            continue
        text = paragraph.text_visible or ""
        matches = []
        for match in pattern.finditer(text):
            tail = text[match.end() : match.end() + 40]
            head = text[max(0, match.start() - 16) : match.start()]
            if RE_AFTER_UPPER_WORD.match(tail):
                continue
            if RE_STANDARD_CONTEXT.search(head):
                continue
            matches.append(match)
        if not matches:
            continue
        checked += len(matches)
        first = matches[0]
        found_token = first.group(0)
        violations.append(
            _build_violation(
                rule,
                address=f"{paragraph.block_id}:decimal",
                location=_paragraph_location(paragraph),
                expected={"decimal_separator": expected.decimal_separator},
                found={"token": found_token, "count": len(matches)},
                expected_text=(
                    "десятичный разделитель — запятая"
                    if expected.decimal_separator == "comma"
                    else "десятичный разделитель — точка"
                ),
                found_text=f"число записано как {found_token}",
                occurrences=len(matches),
            )
        )
    details = {"paragraphs_checked": len(paragraphs), "numbers_checked": checked, "violations": len(violations)}
    if checked == 0:
        return violations, 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="десятичных чисел с проверяемым разделителем в тексте не найдено",
            details=details,
        )
    return violations, checked, _build_assessment(
        rule,
        "fail" if violations else "pass",
        expected_text=rule.message,
        observed_text=(
            f"абзацев с неверным разделителем: {len(violations)} из {checked} чисел"
            if violations
            else f"разделитель верен в {checked} числах"
        ),
        confidence="medium",
        details=details,
    )


def _direct_formatting_keys(paragraph: ParagraphBlock) -> List[str]:
    """Какие свойства заданы напрямую, а не стилем."""
    direct = paragraph.direct_props
    if direct is None:
        return []
    keys: List[str] = []
    for field, value in direct.model_dump(exclude_none=True).items():
        if field in {"alignment_source"}:
            continue
        if isinstance(value, bool):
            if value:
                keys.append(field)
        elif value not in (None, "", [], {}):
            keys.append(field)
    return keys


def _style_allowed(style_id: str | None, allowed: List[str]) -> bool:
    """Входит ли стиль абзаца в разрешённый список."""
    if not style_id:
        return True
    normalized = style_id.strip().casefold()
    return normalized in {value.strip().casefold() for value in allowed}


def _check_paragraph_style_rule(
    rule: ParagraphStyleRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Абзац оформлен базовым стилем без прямого форматирования."""
    expected = rule.params
    paragraphs = _paragraph_targets(document, rule.target, contexts)
    violations: List[Violation] = []
    checked = 0
    for paragraph in paragraphs:
        if not (paragraph.text_visible or "").strip():
            continue
        checked += 1
        if not _style_allowed(paragraph.style_id, expected.allowed_styles):
            violations.append(
                _build_violation(
                    rule,
                    address=f"{paragraph.block_id}:style",
                    location=_paragraph_location(paragraph),
                    expected={"style": expected.allowed_styles},
                    found={"style": paragraph.style_id},
                    expected_text="абзац оформлен базовым стилем",
                    found_text=f"стиль абзаца: {paragraph.style_id}",
                )
            )
            continue
        if expected.forbid_direct_formatting:
            keys = _direct_formatting_keys(paragraph)
            if keys:
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{paragraph.block_id}:direct",
                        location=_paragraph_location(paragraph),
                        expected={"direct_formatting": False},
                        found={"direct_formatting": keys},
                        expected_text="абзац без прямого форматирования",
                        found_text="заданы напрямую: " + ", ".join(keys[:6]),
                    )
                )
    details = {"paragraphs_checked": checked, "violations": len(violations)}
    if checked == 0:
        return violations, 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="абзацев с текстом в области действия правила не найдено",
            details=details,
        )
    return violations, checked, _build_assessment(
        rule,
        "fail" if violations else "pass",
        expected_text=rule.message,
        observed_text=(
            f"абзацев с отклонением от базового стиля: {len(violations)} из {checked}"
            if violations
            else f"стиль верен у {checked} абзацев"
        ),
        confidence="medium",
        details=details,
    )
