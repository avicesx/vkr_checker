"""Семейство оглавления: записи, приложения, покрытие заголовков."""
from __future__ import annotations

from dataclasses import dataclass

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ..findings import (
    Confidence,
    RuleAssessment,
    Violation,
    ViolationLocation,
)
from ..rules import TocRule
from .builders import (
    _build_violation,
    _collapse_text,
    _counted_assessment,
    _paragraph_location,
)
from .common import (
    _HeadingRecord,
    _document_violation,
    _heading_records,
    _heading_section_number,
    _is_pdf_only,
    _normalize_heading_text,
    _paragraph_index,
    _parse_section_number,
    _section_matches,
)
from .constants import (
    ALIGNMENT_LABELS,
    APPENDIX_LETTERS,
    RE_APPENDIX_CAPTION,
    RE_APPENDIX_KIND_MARKER,
    TOC_TITLE_MIN_CHARS,
)
from parser.core.schema import (
    DocumentTreeNode,
    ParagraphBlock,
    ParsedDocxDocument,
    TocContainerBlock,
    TocEntryItem,
)


@dataclass(frozen=True)
class _AppendixEvidence:
    """Приложение документа: буква, название и заголовок, если он найден."""

    letter: Optional[str]
    title: str
    heading_block_id: Optional[str]


def _iter_tree_nodes(nodes: Iterable[DocumentTreeNode]) -> Iterable[DocumentTreeNode]:
    """Обходит дерево документа в порядке следования."""
    for node in nodes:
        yield node
        yield from _iter_tree_nodes(node.children)


def _parse_appendix_caption(text: str) -> Optional[Tuple[Optional[str], str]]:
    """Разбирает подпись приложения на букву и название.

    Возвращает ``(letter, title)`` для строк «ПРИЛОЖЕНИЕ А», «ПРИЛОЖЕНИЕ А
    (обязательное) Название», «Продолжение ПРИЛОЖЕНИЯ Б» и «(справочное)
    Название» (последняя — титульная строка приложения без буквы). ``None`` —
    строка не похожа на подпись приложения.
    """
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    caption = RE_APPENDIX_CAPTION.match(collapsed)
    if caption is not None:
        letter = (caption.group("letter") or "").upper()
        return (letter or None), caption.group("title").strip()
    marker = RE_APPENDIX_KIND_MARKER.match(collapsed)
    if marker is not None:
        return None, (marker.group("title") or "").strip()
    return None


def _collect_appendix_evidence(
    document: ParsedDocxDocument,
) -> List[_AppendixEvidence]:
    """Собирает приложения документа по заголовкам, подписям и продолжениям.

    Источники намеренно разные: в части ВКР подпись приложения распознается как
    заголовок (роль ``appendix``), в части — как обычный абзац «Продолжение
    ПРИЛОЖЕНИЯ А», а титульный лист приложения может быть набран вообще без
    буквы: «(обязательное) Общее техническое задание…». Запись оглавления
    «ПРИЛОЖЕНИЕ <буква>» ищется по букве, а если буквы в тексте нет — по
    названию приложения.
    """
    evidence: List[_AppendixEvidence] = []
    seen: Set[Tuple[Optional[str], str, Optional[str]]] = set()
    tree_nodes = list(_iter_tree_nodes(document.document_tree))

    def add(letter: Optional[str], title: str, block_id: Optional[str]) -> None:
        key = (letter, _normalize_heading_text(title), block_id)
        if key in seen:
            return
        seen.add(key)
        evidence.append(
            _AppendixEvidence(
                letter=letter,
                title=_normalize_heading_text(title),
                heading_block_id=block_id,
            )
        )

    for node in tree_nodes:
        role = node.semantic_role
        if role != "appendix" and node.heading_block_id is None:
            continue
        caption = _parse_appendix_caption(node.title)
        if caption is None:
            continue
        letter, title = caption
        if role == "appendix":
            add(letter, title, node.heading_block_id)
        else:
            add(letter, title, None)

    for entity in document.cross_ref_index.Apps:
        if entity.entity_type != "appendix":
            continue
        letter = (entity.number_str or "").strip().upper() or None
        add(
            letter,
            entity.title_text or "",
            entity.caption_block_id or entity.target_object_id,
        )

    for block in document.blocks:
        if not isinstance(block, ParagraphBlock):
            continue
        caption = _parse_appendix_caption(block.text_visible)
        if caption is None:
            continue
        letter, title = caption
        is_heading = block.heading_info is not None
        add(letter, title, block.block_id if is_heading else None)

    return evidence


def _entry_appendix_caption(entry: TocEntryItem) -> Optional[Tuple[Optional[str], str]]:
    """Разбирает запись оглавления как подпись приложения (буква и название)."""
    for text in (entry.extracted_title, entry.raw_text):
        caption = _parse_appendix_caption(text)
        if caption is not None:
            letter, title = caption
            if letter is not None:
                return caption
            if title:
                return caption
    number = (entry.extracted_number or "").strip().upper()
    if len(number) == 1 and number in APPENDIX_LETTERS:
        return number, ""
    return None


def _match_appendix_entry(
    entry: TocEntryItem,
    evidence: List[_AppendixEvidence],
) -> Optional[_AppendixEvidence]:
    """Находит приложение для записи оглавления: сначала по букве, затем по названию.

    Буква приложения — идентификатор записи, название вторично: в оглавлении
    пишут «ПРИЛОЖЕНИЕ А (обязательное) Матрица ответственности», а на листе
    приложения — «ПРИЛОЖЕНИЕ А (обязательное)». Если буквы в документе нет
    (титул приложения набран без нее), приложение ищется по названию: подпись
    «(обязательное) Общее техническое задание…» отвечает записи оглавления.
    """
    caption = _entry_appendix_caption(entry)
    if caption is None:
        return None
    letter, title = caption
    normalized_title = _normalize_heading_text(title)

    if letter:
        for candidate in evidence:
            if candidate.letter == letter:
                return candidate

    if len(normalized_title) < TOC_TITLE_MIN_CHARS:
        return None
    best: Optional[_AppendixEvidence] = None
    for candidate in evidence:
        candidate_title = candidate.title
        if len(candidate_title) < TOC_TITLE_MIN_CHARS:
            continue
        if not (
            normalized_title.startswith(candidate_title)
            or candidate_title.startswith(normalized_title)
        ):
            continue
        if best is None or len(candidate_title) > len(best.title):
            best = candidate
    return best


def _appendix_subtree_heading_ids(document: ParsedDocxDocument) -> Set[str]:
    """Заголовки, которые находятся внутри приложений (поддерево роли ``appendix``)."""
    collected: Set[str] = set()

    def walk(nodes: Iterable[DocumentTreeNode], inside: bool) -> None:
        for node in nodes:
            node_inside = inside or node.semantic_role == "appendix"
            if node_inside and node.heading_block_id is not None:
                collected.add(node.heading_block_id)
            walk(node.children, node_inside)

    walk(document.document_tree, False)
    return collected


def _appendix_covered_heading_ids(
    entry: TocEntryItem,
    subtree_ids: Set[str],
    headings_by_block: Dict[str, _HeadingRecord],
) -> Set[str]:
    """Заголовки приложения, которые названы внутри записи оглавления.

    В оглавлении приложение перечислено одной записью «ПРИЛОЖЕНИЕ А
    (обязательное) Матрица ответственности», а в тексте у приложения есть
    заголовок «Матрица ответственности». Такой заголовок считается указанным в
    оглавлении: он входит в название записи.
    """
    text = _normalize_heading_text(f"{entry.extracted_title} {entry.raw_text}")
    covered: Set[str] = set()
    for block_id in subtree_ids:
        if block_id not in headings_by_block:
            continue
        title = _normalize_heading_text(headings_by_block[block_id].node.title)
        if len(title) < TOC_TITLE_MIN_CHARS:
            continue
        if title in text:
            covered.add(block_id)
    return covered


def _match_entry_heading(
    entry: TocEntryItem,
    headings: List[_HeadingRecord],
    matched_ids: Set[str],
) -> Optional[str]:
    """Ищет заголовок для записи, которую разбор не сопоставил сам.

    PDF отдает запись оглавления вместе с отточием («1.2 Сбор и анализ
    требований......»), и точного совпадения названия с заголовком нет. Если
    номер записи совпадает с номером заголовка, а название заголовка входит в
    запись, это та же запись: заголовок считается указанным в оглавлении.
    Запись без номера сопоставляется только по названию.
    """
    entry_number = _parse_section_number(entry.extracted_number)
    entry_text = _normalize_heading_text(f"{entry.extracted_title} {entry.raw_text}")
    if not entry_text:
        return None
    best: Optional[Tuple[int, str]] = None
    for record in headings:
        block_id = record.paragraph.block_id
        if block_id in matched_ids:
            continue
        title = _normalize_heading_text(record.node.title)
        if len(title) < TOC_TITLE_MIN_CHARS or title not in entry_text:
            continue
        heading_number = _parse_section_number(_heading_section_number(record))
        if entry_number is not None and heading_number != entry_number:
            # Номер в записи — сильный признак: заголовок с другим номером не пара.
            continue
        if best is None or len(title) > best[0]:
            best = (len(title), block_id)
    return best[1] if best is not None else None


def _toc_blocks(document: ParsedDocxDocument) -> List[TocContainerBlock]:
    """Возвращает блоки оглавления в порядке следования."""
    return [block for block in document.blocks if isinstance(block, TocContainerBlock)]


def _check_toc_rule(
    rule: TocRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет оглавление и сверяет его записи с заголовками документа."""
    expected = rule.params
    tocs = _toc_blocks(document)
    violations: List[Violation] = []
    confidence: Confidence = "medium" if _is_pdf_only(document) else "high"

    if not tocs:
        if expected.require_toc:
            violations.append(
                _document_violation(
                    rule,
                    "toc",
                    {"toc": True},
                    {"toc": False},
                    "оглавление присутствует",
                    "оглавление не найдено",
                )
            )
        assessment = _counted_assessment(
            rule,
            violations,
            0,
            expected_text=rule.message,
            confidence=confidence,
            empty_observed="оглавление в документе не найдено",
            details={"toc_blocks": 0, "entries_checked": 0, "headings_compared": 0},
        )
        return violations, 0, assessment

    headings = _heading_records(document, rule.target)
    headings_by_block = {record.paragraph.block_id: record for record in headings}
    matched_ids = {
        entry.matched_heading_block_id
        for toc in tocs
        for entry in toc.entries
        if entry.matched_heading_block_id is not None
    }
    checked = 0

    # Приложения: запись «ПРИЛОЖЕНИЕ А» ссылается на приложение, а не на
    # заголовок с таким текстом. Парсер сопоставляет записи по названию и
    # букву не учитывает, поэтому правило доводит сопоставление само.
    appendix_evidence = _collect_appendix_evidence(document)
    appendix_subtree_ids = _appendix_subtree_heading_ids(document)
    appendix_matched_entries: Set[str] = set()
    for toc in tocs:
        for entry in toc.entries:
            evidence = (
                _match_appendix_entry(entry, appendix_evidence)
                if appendix_evidence
                else None
            )
            if evidence is None:
                continue
            appendix_matched_entries.add(entry.block_id)
            if evidence.heading_block_id is not None:
                matched_ids.add(evidence.heading_block_id)
            matched_ids.update(
                _appendix_covered_heading_ids(
                    entry, appendix_subtree_ids, headings_by_block
                )
            )

    # Записи, которые разбор не сопоставил из-за отточия или лишних
    # пробелов: сопоставляем по номеру и названию, чтобы существующая
    # запись оглавления не выглядела пропуском заголовка.
    text_matched_entries: Set[str] = set()
    for toc in tocs:
        for entry in toc.entries:
            if entry.matched_heading_block_id is not None:
                continue
            if entry.block_id in appendix_matched_entries:
                continue
            heading_block_id = _match_entry_heading(entry, headings, matched_ids)
            if heading_block_id is None:
                continue
            text_matched_entries.add(entry.block_id)
            matched_ids.add(heading_block_id)

    paragraph_index = _paragraph_index(document)
    entries_checked = 0
    alignment_not_set = 0
    for toc in tocs:
        checked += len(toc.entries)
        entries_checked += len(toc.entries)
        if expected.require_automated and not toc.is_automated_field_or_sdt:
            violations.append(
                _build_violation(
                    rule,
                    address=f"toc:{toc.block_id}:automated",
                    location=ViolationLocation(
                        block_id=toc.block_id,
                        section_idx=toc.section_idx,
                        page_start_est=toc.page_start_est,
                        page_end_est=toc.page_end_est,
                        snippet=f"оглавление, записей {len(toc.entries)}",
                    ),
                    expected={"automated": True},
                    found={"automated": False},
                    expected_text="автоматическое оглавление",
                    found_text="ручное оглавление",
                )
            )


        for entry in toc.entries:
            expected_values: Dict[str, Any] = {}
            found_values: Dict[str, Any] = {}
            expected_parts: List[str] = []
            found_parts: List[str] = []

            if expected.require_page_numbers and not entry.extracted_page_str:
                expected_values["page_number"] = True
                found_values["page_number"] = None
                expected_parts.append("номер страницы в записи")
                found_parts.append("номер страницы отсутствует")

            if expected.check_page_numbers and entry.is_page_number_match is False:
                expected_values["page_number"] = entry.actual_heading_page_est
                found_values["page_number"] = entry.extracted_page_str
                expected_parts.append(f"страница {entry.actual_heading_page_est}")
                found_parts.append(f"страница {entry.extracted_page_str}")

            if expected.check_titles:
                is_appendix_entry = entry.block_id in appendix_matched_entries
                is_text_matched = entry.block_id in text_matched_entries
                if (
                    entry.matched_heading_block_id is None
                    and not is_appendix_entry
                    and not is_text_matched
                ):
                    expected_values["matched_heading"] = True
                    found_values["matched_heading"] = False
                    expected_parts.append("совпадение с заголовком документа")
                    found_parts.append("заголовок не найден")
                elif (
                    entry.matched_heading_block_id is not None
                    and entry.is_title_exact_match is False
                    and not is_appendix_entry
                ):
                    heading_record = headings_by_block.get(entry.matched_heading_block_id)
                    heading = (
                        heading_record.paragraph.heading_info
                        if heading_record is not None
                        else None
                    )
                    expected_title = heading.clean_title_text if heading else None
                    expected_values["title"] = expected_title
                    found_values["title"] = entry.extracted_title
                    expected_parts.append(
                        f"название «{expected_title}»"
                        if expected_title
                        else "название как в заголовке"
                    )
                    found_parts.append(f"название «{entry.extracted_title}»")

            if expected.require_dot_leader and not entry.has_dot_leader:
                expected_values["dot_leader"] = True
                found_values["dot_leader"] = False
                expected_parts.append("отточие до номера страницы")
                found_parts.append("без отточия")

            if expected.forbid_dot_leader and entry.has_dot_leader:
                expected_values["dot_leader"] = False
                found_values["dot_leader"] = True
                expected_parts.append("без отточия")
                found_parts.append("отточие в строке содержания")

            entry_paragraph = paragraph_index.get(entry.block_id)
            if expected.entry_alignment is not None:
                actual_entry_alignment = (
                    entry_paragraph.effective_props.alignment
                    if entry_paragraph is not None
                    else None
                )
                if actual_entry_alignment is None:
                    # Выравнивание не задано явно: Word подставляет стиль, и
                    # находка была бы догадкой, поэтому строку не оцениваем.
                    alignment_not_set += 1
                elif actual_entry_alignment != expected.entry_alignment:
                    expected_values["entry_alignment"] = expected.entry_alignment
                    found_values["entry_alignment"] = actual_entry_alignment
                    expected_parts.append(
                        ALIGNMENT_LABELS[expected.entry_alignment]
                    )
                    found_parts.append(
                        ALIGNMENT_LABELS.get(actual_entry_alignment, "не задано")
                    )

            if expected.entry_bold is not None:
                entry_runs = (
                    [
                        run
                        for run in entry_paragraph.runs
                        if run.visible_text.strip() and not run.in_field
                    ]
                    if entry_paragraph is not None
                    else []
                )
                if entry_runs:
                    entry_is_bold = all(
                        run.effective_props.bold for run in entry_runs
                    )
                    if entry_is_bold != expected.entry_bold:
                        expected_values["entry_bold"] = expected.entry_bold
                        found_values["entry_bold"] = entry_is_bold
                        expected_parts.append(
                            "полужирное начертание"
                            if expected.entry_bold
                            else "обычное начертание"
                        )
                        found_parts.append(
                            "полужирное начертание"
                            if entry_is_bold
                            else "обычное начертание"
                        )

            if expected.capitalize_entry:
                title_letters = [
                    char for char in (entry.extracted_title or "") if char.isalpha()
                ]
                if title_letters and not title_letters[0].isupper():
                    expected_values["entry_case"] = "capitalized"
                    found_values["entry_case"] = "lowercase_start"
                    expected_parts.append("название с заглавной буквы")
                    found_parts.append("название со строчной буквы")

            entry_record = headings_by_block.get(entry.matched_heading_block_id)
            if entry_record is not None:
                for spec in expected.required_entries:
                    if spec.require_number is None:
                        continue
                    if not _section_matches(spec, entry_record):
                        continue
                    has_entry_number = bool(entry.extracted_number)
                    if spec.require_number and not has_entry_number:
                        expected_values["entry_number"] = True
                        found_values["entry_number"] = False
                        expected_parts.append(f"номер раздела у «{spec.label}»")
                        found_parts.append(f"без номера у «{spec.label}»")
                    elif spec.require_number is False and has_entry_number:
                        expected_values["entry_number"] = False
                        found_values["entry_number"] = entry.extracted_number
                        expected_parts.append(f"без номера у «{spec.label}»")
                        found_parts.append(
                            f"номер «{entry.extracted_number}» у «{spec.label}»"
                        )

            if not expected_values:
                continue
            violations.append(
                _build_violation(
                    rule,
                    address=f"toc:{toc.block_id}:entry{entry.entry_index}",
                    location=ViolationLocation(
                        block_id=entry.block_id,
                        section_idx=toc.section_idx,
                        page_start_est=toc.page_start_est,
                        page_end_est=toc.page_end_est,
                        snippet=_collapse_text(entry.raw_text),
                    ),
                    expected=expected_values,
                    found=found_values,
                    expected_text=", ".join(expected_parts),
                    found_text=", ".join(found_parts),
                )
            )


    if expected.check_completeness:
        checked += len(headings)
        reported: Set[str] = set()
        for spec in expected.required_entries:
            for record in headings:
                if not _section_matches(spec, record):
                    continue
                if (
                    expected.max_depth is not None
                    and record.node.level > expected.max_depth
                ):
                    # Оглавление сверяется до этой глубины: заголовок глубже
                    # (например «3.1.1» при max_depth=2) пропуском не считается.
                    continue
                block_id = record.paragraph.block_id
                if block_id in matched_ids or block_id in reported:
                    continue
                reported.add(block_id)
                violations.append(
                    _build_violation(
                        rule,
                        address=f"toc:missing:{block_id}",
                        location=_paragraph_location(record.paragraph),
                        expected={"toc_entry": True, "section": spec.label},
                        found={"toc_entry": False, "section": spec.label},
                        expected_text=f"раздел «{spec.label}» включен в оглавление",
                        found_text=f"раздел «{spec.label}» не найден в оглавлении",
                    )
                )

    assessment = _counted_assessment(
        rule,
        violations,
        checked,
        expected_text=rule.message,
        confidence=confidence,
        empty_observed="оглавление не содержит записей",
        details={
            "toc_blocks": len(tocs),
            "entries_checked": entries_checked,
            "alignment_not_set": alignment_not_set,
            "headings_compared": len(headings) if expected.check_completeness else 0,
        },
    )
    return violations, checked, assessment
