"""Правила ссылок на источники под объектами и ссылок при цитировании."""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from parser.core.schema import ParagraphBlock, ParsedDocument, TableBlock

from ..findings import RuleAssessment, Violation
from ..rules import (
    CitationRequiredRule,
    Rule,
    SourceLabelExpectation,
    SourceLabelRule,
    SourceNoteFigureRule,
    SourceNoteTableRule,
)
from .builders import _build_assessment, _build_violation, _paragraph_location
from .captions import _caption_entities
from .common import _paragraph_index, _paragraph_targets
from .lists import _bibliography_block_ids
from .tables import _caption_by_id, _table_blocks, _table_location, _table_snippet

RE_QUOTED = re.compile(r"[«“]([^»”]{40,})[»”]")
RE_QUOTE_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]{2,}")
RE_NUMBER_REFERENCE = re.compile(r"\[\s*\d")
RE_LABEL_SPLIT = re.compile(r"^\s*([^:：]{3,40})\s*[:：]\s*(.+)$")


def _block_order(document: ParsedDocument) -> List[str]:
    """Идентификаторы блоков в порядке документа."""
    return [block.block_id for block in document.blocks]


def _text_of(block) -> str:
    """Видимый текст абзаца."""
    return (getattr(block, "text_visible", "") or "").strip()


def _starts_with_word(text: str, words: Sequence[str]) -> bool:
    """Начинается ли абзац одним из слов-меток источника (целым словом)."""
    for word in words:
        pattern = r"^\s*" + re.escape(word) + r"(?![А-Яа-яЁёA-Za-z])"
        if re.match(pattern, text, re.IGNORECASE):
            return True
    return False


def _following_blocks(document: ParsedDocument, after_block_id: Optional[str], distance: int) -> List:
    """Блоки, идущие после указанного в пределах заданного расстояния."""
    if not after_block_id:
        return []
    order = _block_order(document)
    if after_block_id not in order:
        return []
    start = order.index(after_block_id) + 1
    blocks = []
    for block_id in order[start : start + distance]:
        block = document.blocks[order.index(block_id)]
        blocks.append(block)
    return blocks


def _search_note(
    document: ParsedDocument, after_block_id: Optional[str], words: Sequence[str], distance: int
) -> Optional[ParagraphBlock]:
    """Абзац-ссылка на источник сразу после объекта."""
    for block in _following_blocks(document, after_block_id, distance):
        if isinstance(block, ParagraphBlock) and _starts_with_word(_text_of(block), words):
            return block
    return None


def _check_source_note_figure_rule(
    rule: SourceNoteFigureRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Под рисунком указывается библиографическая ссылка на источник."""
    entities = _caption_entities(document, "figure")
    if not entities:
        return [], 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="рисунки в разборе не найдены",
            details={"entities": 0},
        )
    expected = rule.params
    paragraphs = _paragraph_index(document)
    violations: List[Violation] = []
    checked = 0
    for entity in entities:
        if not entity.caption_block_id:
            continue
        checked += 1
        note = _search_note(
            document, entity.caption_block_id, expected.source_words, expected.search_distance
        )
        if note is None:
            paragraph = paragraphs.get(entity.caption_block_id)
            location = (
                _paragraph_location(paragraph)
                if paragraph is not None
                else None
            )
            violations.append(
                _build_violation(
                    rule,
                    address=f"{entity.entity_id}:source_note",
                    location=location,
                    expected={"source_note": True},
                    found={"source_note": False},
                    expected_text="под рисунком указан источник",
                    found_text="под рисунком источник не указан",
                )
            )
    details = {"entities": len(entities), "captions": checked, "violations": len(violations)}
    return violations, len(entities), _build_assessment(
        rule,
        "fail" if violations else "pass",
        expected_text=rule.message,
        observed_text=(
            f"рисунков без источника: {len(violations)} из {checked}"
            if violations
            else f"источник указан у {checked} рисунков"
        ),
        confidence="medium",
        details=details,
    )


def _check_source_note_table_rule(
    rule: SourceNoteTableRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Под таблицей указывается библиографическая ссылка на источник."""
    tables = _table_blocks(document)
    if not tables:
        return [], 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="таблицы в разборе не найдены",
            details={"tables": 0},
        )
    expected = rule.params
    captions = _caption_by_id(document)
    violations: List[Violation] = []
    checked = 0
    for table in tables:
        checked += 1
        note = _search_note(
            document, table.block_id, expected.source_words, expected.search_distance
        )
        if note is None:
            violations.append(
                _build_violation(
                    rule,
                    address=f"{table.block_id}:source_note",
                    location=_table_location(table, _table_snippet(table, captions)),
                    expected={"source_note": True},
                    found={"source_note": False},
                    expected_text="под таблицей указан источник",
                    found_text="под таблицей источник не указан",
                )
            )
    details = {"tables": len(tables), "checked": checked, "violations": len(violations)}
    return violations, len(tables), _build_assessment(
        rule,
        "fail" if violations else "pass",
        expected_text=rule.message,
        observed_text=(
            f"таблиц без источника: {len(violations)} из {checked}"
            if violations
            else f"источник указан у {checked} таблиц"
        ),
        confidence="medium",
        details=details,
    )


def _check_source_label_rule(
    rule: SourceLabelRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Подпись «Источник» содержит описание, а под таблицами она есть."""
    expected: SourceLabelExpectation = rule.params
    paragraphs = _paragraph_targets(document, rule.target, contexts)
    violations: List[Violation] = []
    label_paragraphs = 0
    for paragraph in paragraphs:
        text = _text_of(paragraph)
        if not _starts_with_word(text, expected.label_words):
            continue
        label_paragraphs += 1
        if not expected.require_content:
            continue
        match = RE_LABEL_SPLIT.match(text)
        content = match.group(2).strip() if match else ""
        content = content.strip(" .,:;—-")
        if len(content) < 3:
            violations.append(
                _build_violation(
                    rule,
                    address=f"{paragraph.block_id}:label_content",
                    location=_paragraph_location(paragraph),
                    expected={"label_content": True},
                    found={"label_content": content or None},
                    expected_text="после слова «Источник» указано описание",
                    found_text="подпись источника без описания",
                )
            )
    tables_checked = 0
    if expected.require_under_tables:
        captions = _caption_by_id(document)
        for table in _table_blocks(document):
            tables_checked += 1
            if _search_note(document, table.block_id, expected.label_words, expected.search_distance) is None:
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{table.block_id}:label_missing",
                        location=_table_location(table, _table_snippet(table, captions)),
                        expected={"source_label": True},
                        found={"source_label": False},
                        expected_text="под таблицей стоит подпись «Источник»",
                        found_text="подпись «Источник» под таблицей не найдена",
                    )
                )
    details = {
        "label_paragraphs": label_paragraphs,
        "tables_checked": tables_checked,
        "violations": len(violations),
    }
    checked = label_paragraphs + tables_checked
    if checked == 0:
        return violations, 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="подписей «Источник» и таблиц для проверки не найдено",
            details=details,
        )
    return violations, checked, _build_assessment(
        rule,
        "fail" if violations else "pass",
        expected_text=rule.message,
        observed_text=(
            f"нарушений подписи источника: {len(violations)} из {checked}"
            if violations
            else f"подпись источника верна в {checked} случаях"
        ),
        confidence="medium",
        details=details,
    )


def _paragraph_has_reference(paragraph: ParagraphBlock, document: ParsedDocument, allow_footnote: bool) -> bool:
    """Есть ли в абзаце ссылка на источник."""
    if allow_footnote and paragraph.footnote_ids:
        return True
    if RE_NUMBER_REFERENCE.search(paragraph.text_visible or ""):
        return True
    for mention in document.cross_ref_index.mentions:
        if mention.block_id == paragraph.block_id and mention.ref_type == "bibliography":
            return True
    return False


def _check_citation_required_rule(
    rule: CitationRequiredRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """После каждой цитаты в кавычках стоит ссылка на источник."""
    expected = rule.params
    bibliography_blocks = _bibliography_block_ids(document)
    paragraphs = [
        paragraph
        for paragraph in _paragraph_targets(document, rule.target, contexts)
        if paragraph.block_id not in bibliography_blocks
    ]
    violations: List[Violation] = []
    quotes_checked = 0
    for paragraph in paragraphs:
        text = paragraph.text_visible or ""
        quotes = [
            match.group(1)
            for match in RE_QUOTED.finditer(text)
            if len(RE_QUOTE_WORD.findall(match.group(1))) >= 4
        ]
        if not quotes:
            continue
        if _paragraph_has_reference(paragraph, document, expected.allow_footnote_reference):
            quotes_checked += len(quotes)
            continue
        quotes_checked += len(quotes)
        violations.append(
            _build_violation(
                rule,
                address=f"{paragraph.block_id}:citation",
                location=_paragraph_location(paragraph),
                expected={"citation_reference": True},
                found={"quotes": len(quotes)},
                expected_text="после цитаты стоит ссылка на источник",
                found_text=f"цитат без ссылки: {len(quotes)}",
                occurrences=len(quotes),
            )
        )
    details = {
        "quotes_checked": quotes_checked,
        "paragraphs_checked": len(paragraphs),
        "violations": len(violations),
    }
    if quotes_checked == 0:
        return violations, 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="цитат в кавычках в области действия правила не найдено",
            details=details,
        )
    return violations, quotes_checked, _build_assessment(
        rule,
        "fail" if violations else "pass",
        expected_text=rule.message,
        observed_text=(
            f"цитат без ссылки: {len(violations)} абзацев из {quotes_checked} цитат"
            if violations
            else f"все {quotes_checked} цитат сопровождаются ссылкой"
        ),
        confidence="medium",
        details=details,
    )
