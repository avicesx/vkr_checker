"""Дополнительные правила оформления текста: десятичный разделитель и стиль абзаца."""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from parser.core.schema import ParagraphBlock, ParsedDocument

from ..findings import RuleAssessment, Violation
from ..rules import NumberFormatRule, ParagraphStyleRule
from .builders import _build_assessment, _build_violation, _paragraph_location
from .common import _paragraph_targets
from .lists import _bibliography_block_ids

RE_DECIMAL_DOT = re.compile(r"(?<![\w№.,])(\d{1,3})\.(\d{1,3})(?![\d.,])")
RE_AFTER_UPPER_WORD = re.compile(r"\s+[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z\-]+")
RE_STANDARD_CONTEXT = re.compile(r"(?:гост|№|п\.|рис\.|табл\.|гл\.|таблица|рисунок)\s*$", re.IGNORECASE)
RE_DECIMAL_COMMA = re.compile(r"(?<![\w№.,])(\d{1,3}),(\d{1,3})(?![\d.,])")

# Идентификатор или версия: «FR-2.3», «ГОСТ Р-2.3». Перед числом стоит
# дефис, поэтому точка — часть обозначения, а не десятичный разделитель.
RE_IDENTIFIER_PREFIX = re.compile(r"[A-Za-zА-Яа-яЁё]{1,12}-$")
# Собственное имя перед числом: «Индустрия 4.0», «версия Python 3.11».
RE_PROPER_NAME_BEFORE = re.compile(
    r"(?:^|[\s(«\"])([А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z\-]{2,})\s*$"
)
# Версия платформы в скобках: «.NET (8.0)», «Python (3.11)».
RE_PAREN_VERSION = re.compile(r"([A-Za-z][A-Za-z0-9.#+\-]*)\s*\($")


def _check_number_format_rule(
    rule: NumberFormatRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Десятичные числа записаны с запятой, а не с точкой."""
    expected = rule.params
    pattern = RE_DECIMAL_DOT if expected.decimal_separator == "comma" else RE_DECIMAL_COMMA
    paragraphs = _paragraph_targets(document, rule.target, contexts)
    bibliography_blocks = _bibliography_block_ids(document)
    violations: List[Violation] = []
    checked = 0
    for paragraph in paragraphs:
        if paragraph.heading_info is not None:
            continue
        if paragraph.block_id in bibliography_blocks:
            # В описаниях источников точки принадлежат библиографической
            # записи («UML 2.0»), а не десятичным числам текста.
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
            if RE_IDENTIFIER_PREFIX.search(head):
                continue
            name_before = RE_PROPER_NAME_BEFORE.search(head)
            if name_before:
                # «Индустрия 4.0», «Python 3.11», «UML 2.0»: латинское имя
                # перед числом всегда обозначает версию, русское — только
                # когда после числа нет единицы («Срок 2.5 дня» — величина).
                latin_name = bool(re.fullmatch(r"[A-Za-z\-]+", name_before.group(1)))
                if latin_name or not tail.strip() or not re.match(r"\s*[а-яёa-z]", tail):
                    continue
            if RE_PAREN_VERSION.search(head.rstrip()) and tail.lstrip().startswith(")"):
                continue
            if match.group(2) == "000":
                # «25.000» — разделитель тысяч, а не дробная часть.
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


RE_NUMERIC_STYLE = re.compile(r"^\d+$")
# Внутренние идентификаторы Word: «a0», «af1», «aff9», «ab» — не названия
# стилей, сравнивать их с человекочитаемым списком нельзя.
RE_INTERNAL_STYLE = re.compile(r"^[a-z]{1,3}\d*$")
BODY_STYLE_MARKERS = ("normal", "body", "text", "основ", "обычн", "текст")


def _style_allowed(style_id: str | None, allowed: List[str]) -> bool | None:
    """Проверяет стиль абзаца; None — стиль сравнить нельзя.

    Word хранит внутренние идентификаторы стилей: числовые («22») или
    служебные имена («BodyText2»). Сравнивать их с человекочитаемыми
    названиями нельзя, поэтому такие стили не считаются нарушением.
    """
    if not style_id:
        return True
    raw = style_id.strip()
    if not raw or RE_NUMERIC_STYLE.match(raw) or RE_INTERNAL_STYLE.match(raw):
        return None
    normalized = re.sub(r"[^0-9a-zа-яё]+", "", raw.casefold())
    if not normalized:
        return None
    allowed_normalized = {
        re.sub(r"[^0-9a-zа-яё]+", "", value.casefold()) for value in allowed
    }
    if normalized in allowed_normalized:
        return True
    if normalized.startswith(("heading", "заголовок")):
        # Стиль заголовка проверяется правилами заголовков, а не стилем тела.
        return None
    if any(marker in normalized for marker in BODY_STYLE_MARKERS):
        return True
    return False


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
    styles_not_comparable = 0
    for paragraph in paragraphs:
        if not (paragraph.text_visible or "").strip():
            continue
        checked += 1
        style_allowed = _style_allowed(paragraph.style_id, expected.allowed_styles)
        if style_allowed is None:
            styles_not_comparable += 1
            continue
        if style_allowed is False:
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
    details = {
        "paragraphs_checked": checked,
        "styles_not_comparable": styles_not_comparable,
        "violations": len(violations),
    }
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
