"""Ссылки внутри текста: квадратные скобки, «см.» и цитата не по первоисточнику.

Один код ``inline_reference_format`` покрывает семь требований методички,
поэтому проверка собирает все ссылки указанного вида (``ref_type``) и сверяет
каждую с формой записи (``style``), наличием страницы, номера и сокращения.
Если ссылок нужного вида в тексте нет, правило не оценивается: отсутствие
ссылок — отдельный вопрос, а не нарушение формата.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..findings import RuleAssessment, Violation, ViolationLocation
from ..rules import InlineReferenceFormatRule
from .builders import (
    _build_assessment,
    _build_violation,
    _collapse_text,
    _counted_assessment,
    _paragraph_location,
)
from .common import _paragraph_targets
from parser.core.schema import ParsedDocxDocument, ParagraphBlock

STYLE_LABELS = {
    "square_brackets": "квадратные скобки",
    "parentheses_see": "круглые скобки с «см.»",
    "ibid": "«там же»",
    "quoted_from": "«Цит. по кн.»",
    "author_then_number_and_page": "фамилия автора, номер источника и страница",
    "any": "любая форма",
}
"""Подписи форм записи ссылки для текста находок."""

REF_TYPE_LABELS = {
    "bibliography": "источник списка литературы",
    "figure": "рисунок",
    "table": "таблица",
    "appendix": "приложение",
    "section": "раздел работы",
    "any": "ссылка",
}
"""Подписи видов ссылок для текста находок."""

RE_BRACKET = re.compile(r"\[[^\[\]]{1,160}\]")
"""Ссылка или пояснение в квадратных скобках."""

RE_PAREN_SEE = re.compile(r"\(\s*см\.\s*[^()]{1,120}\)", re.IGNORECASE)
"""Круглые скобки с сокращением «см.»."""

RE_AUTHOR_REF = re.compile(
    r"[А-ЯЁ][а-яё]+\s*[\[(]\s*\d{1,4}\s*[,;]\s*(?:с\.|стр\.)\s*\d{1,5}",
)
"""Запись вида «Иванов [5, с. 10]»."""

RE_PAGE_NUMBER = re.compile(r"(?:^|[\s,;])(?:с\.|стр\.)\s*\d{1,5}", re.IGNORECASE)
"""Номер страницы в ссылке: «с. 15», «С. 240»."""

RE_DIGITS = re.compile(r"\d{1,4}")
"""Номер источника или объекта в ссылке."""

_ABBREV_REF_TYPES = (
    ("рис", "figure"),
    ("табл", "table"),
    ("прил", "appendix"),
    ("пп", "section"),
    ("п", "section"),
    ("разд", "section"),
    ("гл", "section"),
)
"""Сокращения, по которым определяется вид ссылки в круглых скобках."""


def _normalize(text: str) -> str:
    """Приводит фрагмент к нижнему регистру и схлопывает пробелы."""
    return " ".join((text or "").split()).casefold()


def _has_abbrev(snippet: str, abbrev: str) -> bool:
    """Проверяет наличие сокращения как отдельного слова без учета точки."""
    base = abbrev.strip().rstrip(".")
    if not base:
        return True
    pattern = re.compile(
        rf"(?<![А-Яа-яЁёA-Za-z]){re.escape(base)}\.?(?![А-Яа-яЁёA-Za-z])",
        re.IGNORECASE,
    )
    return pattern.search(snippet) is not None


def _ref_type_by_abbrev(text: str) -> Optional[str]:
    """Определяет вид ссылки по сокращению внутри фрагмента."""
    normalized = _normalize(text)
    for base, ref_type in _ABBREV_REF_TYPES:
        if re.search(
            rf"(?<![А-Яа-яЁёa-z]){re.escape(base)}\.?(?![А-Яа-яЁёa-z])",
            normalized,
        ):
            return ref_type
    return None


def _classify(snippet: str) -> Optional[Dict[str, object]]:
    """Определяет вид, форму и признаки одной ссылки.

    Возвращает ``None``, если фрагмент не похож на ссылку: обычный текст в
    квадратных скобках (правка, пометка) не должен становиться находкой.
    """
    text = " ".join((snippet or "").split())
    if not text:
        return None
    normalized = _normalize(text)

    if re.match(r"^\[\s*цит\.\s*по\b", normalized):
        return {
            "ref_type": "bibliography",
            "style": "quoted_from",
            "has_page": bool(RE_PAGE_NUMBER.search(text)),
            "has_number": bool(RE_DIGITS.search(text)),
        }
    if re.match(r"^\[\s*там\s+же\b", normalized):
        return {
            "ref_type": "bibliography",
            "style": "ibid",
            "has_page": bool(RE_PAGE_NUMBER.search(text)),
            "has_number": bool(RE_DIGITS.search(text)),
        }
    if RE_AUTHOR_REF.search(text):
        return {
            "ref_type": "bibliography",
            "style": "author_then_number_and_page",
            "has_page": True,
            "has_number": True,
        }
    if normalized.startswith("(") or normalized.startswith("["):
        ref_type = _ref_type_by_abbrev(text)
        if ref_type is not None:
            style = "parentheses_see" if ref_type == "section" else "square_brackets"
            if text.lstrip().startswith("("):
                style = "parentheses_see"
            return {
                "ref_type": ref_type,
                "style": style,
                "has_page": bool(RE_PAGE_NUMBER.search(text)),
                "has_number": bool(RE_DIGITS.search(text)),
            }
    if text.lstrip().startswith("["):
        if not RE_DIGITS.search(text):
            return None
        return {
            "ref_type": "bibliography",
            "style": "square_brackets",
            "has_page": bool(RE_PAGE_NUMBER.search(text)),
            "has_number": True,
        }
    return None


def _iter_paragraphs(
    document: ParsedDocxDocument,
    rule: InlineReferenceFormatRule,
    contexts: Dict[str, str],
) -> List[ParagraphBlock]:
    """Возвращает абзацы области действия правила."""
    return _paragraph_targets(document, rule.target, contexts)


def _candidates(
    document: ParsedDocxDocument,
    paragraph: ParagraphBlock,
) -> List[Tuple[str, int, Dict[str, object]]]:
    """Собирает классифицированные ссылки одного абзаца."""
    found: List[Tuple[str, int, Dict[str, object]]] = []
    text = paragraph.text_visible or ""
    seen = set()
    for match in RE_BRACKET.finditer(text):
        snippet = match.group(0)
        if snippet in seen:
            continue
        seen.add(snippet)
        classified = _classify(snippet)
        if classified is None:
            continue
        found.append((snippet, match.start(), classified))
    for match in RE_PAREN_SEE.finditer(text):
        snippet = match.group(0)
        if snippet in seen:
            continue
        seen.add(snippet)
        classified = _classify(snippet)
        if classified is None:
            continue
        found.append((snippet, match.start(), classified))
    if not found:
        mentions = document.cross_ref_index
        if mentions is not None:
            for mention in mentions.mentions:
                if mention.block_id != paragraph.block_id:
                    continue
                snippet = " ".join((mention.raw_snippet or "").split())
                if not snippet or snippet in seen:
                    continue
                classified = _classify(snippet)
                if classified is None:
                    classified = {
                        "ref_type": mention.ref_type,
                        "style": "square_brackets",
                        "has_page": False,
                        "has_number": bool(RE_DIGITS.search(snippet)),
                    }
                seen.add(snippet)
                found.append((snippet, mention.start_char or 0, classified))
    return found


def _matches_type(classified: Dict[str, object], ref_type: str) -> bool:
    """Проверяет, относится ли ссылка к выбранному виду."""
    if ref_type == "any":
        return True
    return classified["ref_type"] == ref_type


def _matches_patterns(snippet: str, patterns: List[str]) -> bool:
    """Проверяет, подходит ли ссылка хотя бы под одно выражение."""
    for pattern in patterns:
        if re.search(pattern, snippet, re.IGNORECASE):
            return True
    return False


def _reference_violation(
    rule: InlineReferenceFormatRule,
    paragraph: ParagraphBlock,
    snippet: str,
    position: int,
    expected_values: Dict[str, object],
    found_values: Dict[str, object],
    expected_text: str,
    found_text: str,
) -> Violation:
    """Создает находку по ссылке с адресом абзаца и позицией в тексте."""
    location = _paragraph_location(paragraph)
    if position >= 0:
        location = ViolationLocation(
            block_id=location.block_id,
            section_idx=location.section_idx,
            page_start_est=location.page_start_est,
            page_end_est=location.page_end_est,
            container_type=location.container_type,
            style_id=location.style_id,
            char_start=position,
            char_end=position + len(snippet),
            snippet=_collapse_text(snippet),
        )
    return _build_violation(
        rule,
        address=f"{paragraph.block_id}:ref{position}",
        location=location,
        expected=expected_values,
        found={**found_values, "snippet": _collapse_text(snippet)},
        expected_text=expected_text,
        found_text=found_text,
    )


def _check_inline_reference_format_rule(
    rule: InlineReferenceFormatRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет форму ссылки на источник, рисунок, таблицу или приложение.

    Каждая ссылка классифицируется по виду и форме записи; затем сверяются
    требуемая форма (``style``), номер страницы, номер объекта и сокращение.
    Проверяются только ссылки выбранного вида: правило про рисунки не трогает
    ссылки на источники.
    """
    expected = rule.params
    paragraphs = _iter_paragraphs(document, rule, contexts)
    ref_type = expected.ref_type
    expected_text = (
        f"ссылка на {REF_TYPE_LABELS[ref_type]} оформлена "
        f"как {STYLE_LABELS[expected.style]}"
    )

    violations: List[Violation] = []
    candidates: List[Tuple[ParagraphBlock, str, int, Dict[str, object]]] = []
    for paragraph in paragraphs:
        for snippet, position, classified in _candidates(document, paragraph):
            if not _matches_type(classified, ref_type):
                continue
            candidates.append((paragraph, snippet, position, classified))

    if not candidates:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    f"в тексте не найдено ссылок вида «{REF_TYPE_LABELS[ref_type]}»: "
                    "проверять форму записи нечего"
                ),
                confidence="low",
                details={
                    "references": 0,
                    "ref_type": ref_type,
                    "paragraphs_checked": len(paragraphs),
                },
            ),
        )

    in_scope: List[Tuple[ParagraphBlock, str, int, Dict[str, object]]] = []
    for paragraph, snippet, position, classified in candidates:
        actual_style = str(classified["style"])
        if expected.patterns is not None:
            # Образцы уточняют форму записи: чужие формы (обычная скобочная
            # ссылка в правиле про «Цит. по») не проверяются этим правилом.
            if actual_style == expected.style or _matches_patterns(
                snippet, expected.patterns
            ):
                in_scope.append((paragraph, snippet, position, classified))
            continue
        if expected.style != "any" and actual_style != expected.style:
            continue
        in_scope.append((paragraph, snippet, position, classified))

    skipped_other_styles = len(candidates) - len(in_scope)
    if not in_scope:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    "ссылок нужной формы записи в тексте не найдено: "
                    "проверять нечего"
                ),
                confidence="low",
                details={
                    "references": 0,
                    "ref_type": ref_type,
                    "paragraphs_checked": len(paragraphs),
                    "skipped_other_styles": skipped_other_styles,
                },
            ),
        )

    for paragraph, snippet, position, classified in in_scope:
        actual_style = str(classified["style"])
        if expected.patterns is not None:
            if not _matches_patterns(snippet, expected.patterns):
                violations.append(
                    _reference_violation(
                        rule,
                        paragraph,
                        snippet,
                        position,
                        {"patterns": expected.patterns},
                        {"style": STYLE_LABELS.get(actual_style, actual_style)},
                        "ссылка оформлена по образцу методички",
                        f"ссылка не совпала ни с одним образцом: «{_collapse_text(snippet)}»",
                    )
                )
        elif expected.style != "any" and actual_style != expected.style:
            violations.append(
                _reference_violation(
                    rule,
                    paragraph,
                    snippet,
                    position,
                    {"style": expected.style},
                    {"style": actual_style},
                    f"ссылка оформлена как {STYLE_LABELS[expected.style]}",
                    f"форма записи ссылки: {STYLE_LABELS.get(actual_style, actual_style)}",
                )
            )
        if expected.require_page and not classified["has_page"]:
            violations.append(
                _reference_violation(
                    rule,
                    paragraph,
                    snippet,
                    position,
                    {"page": True},
                    {"page": False},
                    "в ссылке указана страница источника",
                    f"в ссылке нет номера страницы: «{_collapse_text(snippet)}»",
                )
            )
        if expected.require_number and not classified["has_number"]:
            violations.append(
                _reference_violation(
                    rule,
                    paragraph,
                    snippet,
                    position,
                    {"number": True},
                    {"number": False},
                    "в ссылке указан номер источника или объекта",
                    f"в ссылке нет номера: «{_collapse_text(snippet)}»",
                )
            )
        if expected.require_abbrev and not _has_abbrev(snippet, expected.require_abbrev):
            violations.append(
                _reference_violation(
                    rule,
                    paragraph,
                    snippet,
                    position,
                    {"abbrev": expected.require_abbrev},
                    {"abbrev": None},
                    f"перед номером стоит сокращение «{expected.require_abbrev}»",
                    f"сокращение «{expected.require_abbrev}» не найдено: "
                    f"«{_collapse_text(snippet)}»",
                )
            )

    by_style: Dict[str, int] = {}
    for _, _, _, classified in in_scope:
        style = str(classified["style"])
        by_style[style] = by_style.get(style, 0) + 1

    assessment = _counted_assessment(
        rule,
        violations,
        len(in_scope),
        expected_text=expected_text,
        confidence="medium",
        empty_observed="ссылок выбранного вида в тексте не найдено",
        details={
            "references": len(in_scope),
            "ref_type": ref_type,
            "paragraphs_checked": len(paragraphs),
            "by_style": by_style,
            "skipped_other_styles": skipped_other_styles,
        },
    )
    return violations, len(in_scope), assessment
