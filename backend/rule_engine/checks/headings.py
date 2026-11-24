"""Семейство заголовков: уровни, нумерация, начертание и регистр."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..findings import (
    RuleAssessment,
    Violation,
)
from ..rules import (
    HeadingFormatRule,
    HeadingHyphenationRule,
    HeadingLevelsRule,
    HeadingNumberingRule,
    NUMBERLESS_HEADING_ROLES,
)
from .builders import _counted_assessment
from .common import (
    _HeadingRecord,
    _compare_font,
    _expected_number_depth,
    _format_section_number,
    _heading_records,
    _heading_section_number,
    _heading_violation,
    _is_pdf_only,
    _is_symbolic_run,
    _parse_section_number,
    _section_number_digit_style,
    _section_number_prefix_word,
)
from .constants import (
    ALIGNMENT_LABELS,
    CASE_LABELS,
    FOUND_CASE_LABELS,
    NUMBER_ENDING_LABELS,
)
from parser.core.schema import ParagraphBlock, ParsedDocxDocument

LINE_UNIT_PT = 21.0
"""Один межстрочный интервал при кегле 14 пт: 14 × 1,5 = 21 пт."""

QUOTE_CHARS = "«»„“”\""
"""Кавычки, которые проверяет параметр forbid_quotes."""

NUMBER_STYLE_LABELS = {
    "arabic": "арабские цифры",
    "roman": "римские цифры",
    "chapter": "слово «Глава» перед номером",
}


def _is_chapter_prefix(word: Optional[str]) -> bool:
    """Проверяет, что слово перед номером является словом «Глава»."""
    return bool(word) and word.strip().casefold().startswith("глав")


def _starts_with_word(text: Optional[str], word: str) -> bool:
    """Проверяет, что текст начинается с заданного слова (без учета регистра)."""
    if not text or not word:
        return False
    pattern = r"^\s*" + re.escape(word.strip()) + r"(?![А-Яа-яЁёA-Za-z])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _same_word(left: Optional[str], right: str) -> bool:
    """Сравнивает слова перед номером без учета регистра и краевых знаков."""
    if not left or not right:
        return False
    return left.strip().strip(".").casefold() == right.strip().strip(".").casefold()


def _check_heading_levels_rule(
    rule: HeadingLevelsRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет, что уровни заголовков растут без пропусков и не глубже лимита."""
    records = _heading_records(document, rule.target)
    expected = rule.params
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
    expected = rule.params
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

        raw_number = _heading_section_number(record)
        if expected.number_style != "any":
            prefix_word = _section_number_prefix_word(raw_number)
            digit_style = _section_number_digit_style(raw_number)
            if expected.number_style == "chapter":
                style_ok = _is_chapter_prefix(prefix_word)
                found_style = (
                    "chapter"
                    if style_ok
                    else (digit_style if digit_style is not None else "без номера")
                )
            else:
                style_ok = digit_style == expected.number_style
                found_style = digit_style if digit_style is not None else "не распознан"
            if not style_ok:
                expected_values["number_style"] = expected.number_style
                found_values["number_style"] = found_style
                expected_parts.append(
                    "номер: " + NUMBER_STYLE_LABELS[expected.number_style]
                )
                found_parts.append(
                    "номер: "
                    + (
                        "без слова «Глава»"
                        if expected.number_style == "chapter"
                        else "стиль не тот"
                    )
                )

        if expected.forbid_prefix_words:
            raw_prefix = _section_number_prefix_word(raw_number)
            found_word: Optional[str] = None
            for word in expected.forbid_prefix_words:
                if _same_word(raw_prefix, word):
                    found_word = raw_prefix
                    break
            if found_word is None:
                title = heading.clean_title_text if heading is not None else None
                for word in expected.forbid_prefix_words:
                    if _starts_with_word(title, word):
                        found_word = word
                        break
            if found_word is not None:
                expected_values["prefix_word"] = None
                found_values["prefix_word"] = found_word
                expected_parts.append("номер без слова перед ним")
                found_parts.append(f"слово «{found_word}» перед номером")

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
    expected = rule.params
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

        if expected.prefix_word and _heading_section_number(record):
            actual_prefix = _section_number_prefix_word(_heading_section_number(record))
            if not _same_word(actual_prefix, expected.prefix_word):
                expected_values["prefix_word"] = expected.prefix_word
                found_values["prefix_word"] = actual_prefix
                expected_parts.append(f"слово «{expected.prefix_word}» перед номером")
                found_parts.append(
                    f"слово «{actual_prefix}»"
                    if actual_prefix
                    else "слово перед номером отсутствует"
                )

        if expected.forbid_quotes:
            title_text = heading.clean_title_text or record.paragraph.text_visible or ""
            quotes = sorted({char for char in title_text if char in QUOTE_CHARS})
            if quotes:
                expected_values["quotes"] = False
                found_values["quotes"] = quotes
                expected_parts.append("без кавычек в заголовке")
                found_parts.append("кавычки: " + " ".join(f"«{char}»" for char in quotes))

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

        if expected.underline != "any":
            visible_runs = [
                run
                for run in record.paragraph.runs
                if run.visible_text.strip()
                and not run.in_field
                and not run.effective_props.vanish_hidden
                and not _is_symbolic_run(run)
            ]
            has_underline = any(
                bool(run.effective_props.underline)
                and str(run.effective_props.underline).strip().lower()
                not in ("", "none", "false")
                for run in visible_runs
            )
            if expected.underline == "forbid" and has_underline:
                expected_values["underline"] = False
                found_values["underline"] = True
                expected_parts.append("без подчеркивания")
                found_parts.append("заголовок подчеркнут")
            elif expected.underline == "require" and visible_runs and not has_underline:
                expected_values["underline"] = True
                found_values["underline"] = False
                expected_parts.append("с подчеркиванием")
                found_parts.append("заголовок без подчеркивания")

        props = record.paragraph.effective_props
        if expected.space_after_lines is not None:
            want_pt = round(expected.space_after_lines * LINE_UNIT_PT, 1)
            actual_pt = props.space_after_pt
            label = f"после заголовка {expected.space_after_lines:g} интервала ({want_pt} пт)"
            if props.space_after_auto:
                expected_values["space_after_pt"] = want_pt
                found_values["space_after_pt"] = "auto"
                expected_parts.append(label)
                found_parts.append("интервал после заголовка задан автоматически")
            elif actual_pt is None or abs(actual_pt - want_pt) > expected.tolerance_pt:
                expected_values["space_after_pt"] = want_pt
                found_values["space_after_pt"] = (
                    round(actual_pt, 1) if actual_pt is not None else None
                )
                expected_parts.append(label)
                found_parts.append(
                    "интервал после заголовка: "
                    + (f"{round(actual_pt, 1)} пт" if actual_pt is not None else "не задан")
                )

        if expected.space_before_lines is not None:
            want_pt = round(expected.space_before_lines * LINE_UNIT_PT, 1)
            actual_pt = props.space_before_pt
            label = f"перед заголовком {expected.space_before_lines:g} интервала ({want_pt} пт)"
            if props.space_before_auto:
                expected_values["space_before_pt"] = want_pt
                found_values["space_before_pt"] = "auto"
                expected_parts.append(label)
                found_parts.append("интервал перед заголовком задан автоматически")
            elif actual_pt is None or abs(actual_pt - want_pt) > expected.tolerance_pt:
                expected_values["space_before_pt"] = want_pt
                found_values["space_before_pt"] = (
                    round(actual_pt, 1) if actual_pt is not None else None
                )
                expected_parts.append(label)
                found_parts.append(
                    "интервал перед заголовком: "
                    + (f"{round(actual_pt, 1)} пт" if actual_pt is not None else "не задан")
                )

        occurrences = 1
        if expected.font is not None:
            mismatched_font = []
            for run in record.paragraph.runs:
                if not run.visible_text.strip() or run.in_field:
                    continue
                if run.effective_props.vanish_hidden or _is_symbolic_run(run):
                    continue
                diff = _compare_font(
                    run.effective_props,
                    expected.font,
                    check_family=True,
                    check_complex_script=not _is_pdf_only(document),
                )
                if diff is not None:
                    mismatched_font.append(diff)
            if mismatched_font:
                font_expected, font_found, font_expected_parts, font_found_parts = (
                    mismatched_font[0]
                )
                expected_values["font"] = font_expected
                found_values["font"] = font_found
                expected_parts.extend(font_expected_parts)
                found_parts.extend(font_found_parts)
                occurrences = len(mismatched_font)

        if not expected_values:
            continue
        violations.append(
            _heading_violation(
                rule,
                record,
                expected_values,
                found_values,
                expected_parts,
                found_parts,
                occurrences=occurrences,
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

RE_TRAILING_HYPHEN = re.compile(r"[А-Яа-яЁёA-Za-z]-\s*$")
"""Дефис в конце строки: признак переноса слова."""

RE_ABBREVIATION = re.compile(r"(?<![А-ЯЁA-Z])[А-ЯЁA-Z]{2,}(?![А-ЯЁA-Z])")
"""Аббревиатура из двух и более прописных букв."""

RE_LOWERCASE = re.compile(r"[а-яёa-z]")
"""Строчная буква: признак обычного текста, а не аббревиатуры."""

ABBREVIATION_STOP_WORDS = frozenset(
    {
        "реферат",
        "введение",
        "заключение",
        "содержание",
        "оглавление",
        "приложение",
        "приложения",
        "список",
        "литература",
        "литературы",
        "глава",
        "раздел",
        "таблица",
        "рисунок",
        "схема",
        "тема",
        "цель",
        "задачи",
        "выводы",
    }
)
"""Слова-заголовки, которые не являются аббревиатурами."""

SOFT_HYPHEN = "\u00ad"
"""Мягкий перенос: невидимый знак, который ставит Word при переносе."""


def _heading_hyphenation_issues(
    paragraph: ParagraphBlock,
) -> Tuple[bool, Optional[str]]:
    """Ищет перенос слова и аббревиатуру в заголовке.

    Переносом считается мягкий перенос, разрыв строки по дефису на конце и
    признак ``pdf_has_hyphenated_break``: дефис внутри слова («учебно-
    методический») переносом не является и находкой не становится.
    """
    text = (paragraph.text_visible or "").strip()
    hyphenated = (
        SOFT_HYPHEN in text
        or bool(paragraph.pdf_has_hyphenated_break)
        or RE_TRAILING_HYPHEN.search(text) is not None
    )
    abbreviation = None
    if RE_LOWERCASE.search(text) is not None:
        for word in text.split():
            candidate = word.strip(".,;:()[]«»\"'")
            if not RE_ABBREVIATION.fullmatch(candidate):
                continue
            if len(candidate) > 8:
                continue
            if candidate.casefold() in ABBREVIATION_STOP_WORDS:
                continue
            abbreviation = candidate
            break
    return hyphenated, abbreviation


def _check_heading_hyphenation_rule(
    rule: HeadingHyphenationRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет отсутствие переносов и аббревиатур в заголовках."""
    expected = rule.params
    records = _heading_records(document, rule.target)
    violations: List[Violation] = []
    for record in records:
        hyphenated, abbreviation = _heading_hyphenation_issues(record.paragraph)
        expected_values: Dict[str, Any] = {}
        found_values: Dict[str, Any] = {}
        expected_parts: List[str] = []
        found_parts: List[str] = []
        if expected.forbid_hyphenation and hyphenated:
            expected_values["hyphenation"] = False
            found_values["hyphenation"] = True
            expected_parts.append("без переноса слов")
            found_parts.append("перенос слова в заголовке")
        if expected.forbid_abbreviations and abbreviation:
            expected_values["abbreviation"] = False
            found_values["abbreviation"] = abbreviation
            expected_parts.append("без аббревиатур")
            found_parts.append(f"аббревиатура «{abbreviation}»")
        if not expected_values:
            continue
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
