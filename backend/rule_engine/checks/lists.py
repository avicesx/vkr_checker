"""Перечисления: допустимые маркеры по уровням вложенности.

Маркер берется из ``ParagraphBlock.list_info.rendered_marker`` — это уже
отрисованный знак списка, а не шаблон нумерации. Уровень без описания в
``level_markers`` не проверяется: правило не должно угадывать оформление
уровней, которые методичка не оговаривает.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..findings import RuleAssessment, Violation
from ..rules import ListFormatExpectation, ListFormatRule
from .builders import (
    _build_assessment,
    _build_violation,
    _collapse_text,
    _counted_assessment,
    _paragraph_location,
)
from .common import _paragraph_targets
from parser.core.schema import ParagraphBlock, ParsedDocxDocument

MARKER_LABELS = {
    "hyphen": "дефис",
    "dash": "тире",
    "bullet": "маркер-кружок",
    "arabic_dot": "арабская цифра с точкой",
    "arabic_parenthesis": "арабская цифра со скобкой",
    "russian_letter_dot": "русская буква с точкой",
    "russian_letter_parenthesis": "русская буква со скобкой",
    "other": "другой маркер",
}
"""Подписи видов маркеров для текста находок."""

RE_ARABIC = re.compile(r"^(\d{1,3})\s*([.)]?)\s*$")
"""Маркер-число: «1.», «1)» или «1»."""

RE_RUSSIAN_LETTER = re.compile(r"^([А-ЯЁа-яё])\s*([.)]?)\s*$")
"""Маркер-буква: «а)», «б.», «А)»."""

_BULLET_CHARS = frozenset({"•", "·", "◦", "▪", "▫", "*", "o"})


def _classify_marker(marker: str) -> str:
    """Определяет вид отрисованного маркера перечисления."""
    text = (marker or "").strip()
    if not text:
        return "other"
    first = text[0]
    if first in ("-", "\u2010", "\u2011"):
        return "hyphen"
    if first in ("\u2013", "\u2014"):
        return "dash"
    if first in _BULLET_CHARS:
        return "bullet"
    match = RE_ARABIC.match(text)
    if match:
        return "arabic_parenthesis" if match.group(2) == ")" else "arabic_dot"
    match = RE_RUSSIAN_LETTER.match(text)
    if match:
        return (
            "russian_letter_parenthesis" if match.group(2) == ")" else "russian_letter_dot"
        )
    return "other"


def _list_items(
    document: ParsedDocxDocument,
    rule: ListFormatRule,
    contexts: Dict[str, str],
) -> List[ParagraphBlock]:
    """Возвращает абзацы-элементы перечислений в области действия правила."""
    paragraphs = _paragraph_targets(document, rule.target, contexts)
    items: List[ParagraphBlock] = []
    for paragraph in paragraphs:
        info = paragraph.list_info
        if info is None or not info.is_list_item:
            continue
        if not (info.rendered_marker or "").strip():
            continue
        items.append(paragraph)
    return items


def _marker_spec(
    expected: ListFormatExpectation,
    level: Optional[int],
) -> Optional[List[str]]:
    """Возвращает допустимые маркеры для уровня или None, если он не описан."""
    if level is None:
        return None
    for spec in expected.level_markers:
        if spec.level == level:
            return list(spec.allowed)
    return None


def _check_list_format_rule(
    rule: ListFormatRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет вид маркера перечисления на каждом описанном уровне.

    Для каждого элемента списка берется вид маркера и сверяется с допустимыми
    для его уровня. Элементы на уровнях без описания не проверяются и не
    попадают в счетчик: иначе оценка говорила бы о проверке, которой не было.
    """
    expected = rule.params
    items = _list_items(document, rule, contexts)
    expected_text = "маркеры перечисления соответствуют уровням методички"
    if not items:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text="в области действия правила нет элементов перечислений",
                confidence="low",
                details={"list_items": 0},
            ),
        )

    violations: List[Violation] = []
    checked = 0
    described_levels = {spec.level: spec.allowed for spec in expected.level_markers}
    by_level: Dict[int, int] = {}
    skipped_levels: Dict[int, int] = {}

    for paragraph in items:
        info = paragraph.list_info
        level = info.level
        allowed = _marker_spec(expected, level)
        if allowed is None:
            if level is not None:
                skipped_levels[level] = skipped_levels.get(level, 0) + 1
            continue
        marker = (info.rendered_marker or "").strip()
        marker_kind = _classify_marker(marker)
        if marker_kind == "other" and not expected.forbid_other_markers:
            continue
        checked += 1
        if level is not None:
            by_level[level] = by_level.get(level, 0) + 1
        if marker_kind in allowed:
            continue
        violations.append(
            _build_violation(
                rule,
                address=f"{paragraph.block_id}:marker",
                location=_paragraph_location(paragraph),
                expected={"level": level, "markers": allowed},
                found={"marker": marker, "marker_kind": marker_kind, "level": level},
                expected_text=(
                    f"на уровне {level} допустимы маркеры: "
                    + ", ".join(MARKER_LABELS[item] for item in allowed)
                ),
                found_text=(
                    f"маркер «{_collapse_text(marker)}» ({MARKER_LABELS[marker_kind]})"
                ),
            )
        )

    assessment = _counted_assessment(
        rule,
        violations,
        checked,
        expected_text=expected_text,
        confidence="high",
        empty_observed="элементов перечислений на описанных уровнях не найдено",
        details={
            "list_items": len(items),
            "checked": checked,
            "levels": {str(level): count for level, count in sorted(by_level.items())},
            "skipped_levels": {
                str(level): count for level, count in sorted(skipped_levels.items())
            },
            "described_levels": sorted(described_levels),
        },
    )
    return violations, checked, assessment
