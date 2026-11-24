"""Правила нумерации сносок."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from parser.core.schema import FootnoteItem, ParagraphBlock, ParsedDocument

from ..findings import RuleAssessment, Violation
from ..rules import FootnoteNumberingRule, Rule
from .builders import _build_assessment, _build_violation, _paragraph_location
from .common import _paragraph_index

RE_FOOTNOTE_NUMBER = re.compile(r"^\s*(\d+)[.)]?\s+")


def _printed_number(footnote: FootnoteItem, paragraphs: Dict[str, ParagraphBlock]) -> Optional[int]:
    """Печатный номер сноски: надстрочный маркер или число в начале текста."""
    paragraph = paragraphs.get(footnote.referenced_in_block_id or "")
    if paragraph is not None:
        for run in paragraph.runs:
            vertical = str(run.effective_props.vert_align or "")
            if "superscript" in vertical and run.text.strip().isdigit():
                return int(run.text.strip())
    for block in footnote.blocks:
        text = (getattr(block, "text_visible", "") or "").strip()
        match = RE_FOOTNOTE_NUMBER.match(text)
        if match:
            return int(match.group(1))
    return None


def _footnote_location(footnote: FootnoteItem, number: Optional[int], paragraphs: Dict[str, ParagraphBlock]):
    """Адрес сноски по абзацу-владельцу или первому абзацу сноски."""
    paragraph = paragraphs.get(footnote.referenced_in_block_id or "")
    if paragraph is None and footnote.blocks:
        first = footnote.blocks[0]
        if isinstance(first, ParagraphBlock):
            paragraph = first
    if paragraph is not None:
        return _paragraph_location(paragraph)
    return None


def _check_footnote_numbering_rule(
    rule: FootnoteNumberingRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Сноски нумеруются сквозным образом или с единицы на каждой странице."""
    expectations = rule.params
    footnotes = list(document.footnotes)
    if not footnotes:
        return [], 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="сноски в разборе не найдены",
            details={"footnotes": 0},
        )
    paragraphs = _paragraph_index(document)
    numbered: List[Tuple[FootnoteItem, int]] = []
    without_number = 0
    for footnote in footnotes:
        number = _printed_number(footnote, paragraphs)
        if number is None:
            without_number += 1
            continue
        numbered.append((footnote, number))
    details = {
        "footnotes": len(footnotes),
        "numbered": len(numbered),
        "without_number": without_number,
        "scheme": expectations.scheme,
    }
    if len(numbered) < 2:
        return [], len(footnotes), _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="печатные номера сносок не извлечены: нумерацию проверить нельзя",
            details=details,
        )
    violations: List[Violation] = []
    if expectations.scheme == "per_page":
        by_page: Dict[int, List[Tuple[FootnoteItem, int]]] = {}
        for footnote, number in numbered:
            by_page.setdefault(footnote.page_est or 0, []).append((footnote, number))
        for page, items in sorted(by_page.items()):
            expected_number = expectations.start_at
            for footnote, number in items:
                if number != expected_number:
                    violations.append(
                        _build_violation(
                            rule,
                            address=f"{footnote.footnote_id}:number",
                            location=_footnote_location(footnote, number, paragraphs),
                            expected={"number": expected_number, "page": page},
                            found={"number": number, "page": page},
                            expected_text=f"на странице {page} номер сноски: {expected_number}",
                            found_text=f"на странице {page} номер сноски: {number}",
                        )
                    )
                expected_number = number + 1
    else:
        expected_number = expectations.start_at
        for footnote, number in numbered:
            if expectations.scheme == "continuous" and number != expected_number:
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{footnote.footnote_id}:number",
                        location=_footnote_location(footnote, number, paragraphs),
                        expected={"number": expected_number},
                        found={"number": number},
                        expected_text=f"сквозной номер сноски: {expected_number}",
                        found_text=f"номер сноски: {number}",
                    )
                )
            expected_number = number + 1
    return violations, len(footnotes), _build_assessment(
        rule,
        "fail" if violations else "pass",
        expected_text=rule.message,
        observed_text=(
            f"нарушений нумерации сносок: {len(violations)} из {len(numbered)}"
            if violations
            else f"нумерация верна у {len(numbered)} сносок"
        ),
        confidence="medium",
        details=details,
    )
