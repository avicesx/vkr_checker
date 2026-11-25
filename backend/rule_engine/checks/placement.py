"""Правила размещения рисунков и таблиц относительно ссылок и друг друга."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from parser.core.schema import NumberedCaptionEntity, ParagraphBlock, ParsedDocument

from ..findings import RuleAssessment, Violation, ViolationLocation
from ..rules import (
    FigureAfterReferenceRule,
    FigureSpacingRule,
    Rule,
    TableAfterReferenceRule,
)
from .builders import _build_assessment, _build_violation, _paragraph_location
from .captions import _caption_entities
from .common import _paragraph_index
from .constants import CAPTION_ENTITY_LABELS


def _block_positions(document: ParsedDocument) -> Dict[str, int]:
    """Порядковый номер каждого блока в документе."""
    return {block.block_id: index for index, block in enumerate(document.blocks)}


def _entity_position(
    entity: NumberedCaptionEntity, positions: Dict[str, int]
) -> Optional[int]:
    """Позиция объекта в потоке блоков: подпись или сам объект."""
    if entity.caption_block_id and entity.caption_block_id in positions:
        return positions[entity.caption_block_id]
    if entity.target_object_id and entity.target_object_id in positions:
        return positions[entity.target_object_id]
    return None


def _mention_positions(
    document: ParsedDocument, positions: Dict[str, int]
) -> Dict[str, List[int]]:
    """Позиции упоминаний каждого объекта в тексте."""
    index = document.cross_ref_index
    found: Dict[str, List[int]] = {}
    for mention in index.mentions:
        position = positions.get(mention.block_id or "")
        if position is None:
            continue
        for entity_id in mention.resolved_entity_ids:
            found.setdefault(entity_id, []).append(position)
    return found


def _caption_location(
    entity: NumberedCaptionEntity, paragraphs: Dict[str, ParagraphBlock]
) -> ViolationLocation:
    """Адрес подписи объекта или самого объекта, если подпись не найдена."""
    paragraph = paragraphs.get(entity.caption_block_id or "")
    if paragraph is not None:
        return _paragraph_location(paragraph)
    return ViolationLocation(
        block_id=entity.caption_block_id or entity.target_object_id,
        page_start_est=entity.page_est,
        snippet=entity.title_text[:80],
    )


def _reference_order_violation(
    rule: Rule,
    entity: NumberedCaptionEntity,
    entity_label: str,
    caption_position: int,
    reference_position: int,
    location: ViolationLocation,
) -> Violation:
    """Нарушение порядка: ссылка стоит в тексте после объекта."""
    return _build_violation(
        rule,
        address=f"{entity.entity_id}:order",
        location=location,
        expected={"reference_position": "before_object"},
        found={
            "object_position": caption_position,
            "reference_position": reference_position,
        },
        expected_text=f"ссылка на {entity_label} стоит в тексте до объекта",
        found_text=f"ссылка на {entity_label} стоит после объекта",
    )


def _check_figure_after_reference_rule(
    rule: FigureAfterReferenceRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Рисунок должен стоять после первой ссылки на него в тексте."""
    entities = _caption_entities(document, "figure")
    if not entities:
        return [], 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="рисунки в разборе не найдены",
            details={"entities": 0},
        )
    positions = _block_positions(document)
    mentions = _mention_positions(document, positions)
    paragraphs = _paragraph_index(document)
    violations: List[Violation] = []
    checked = 0
    skipped_without_reference = 0
    for entity in entities:
        caption_position = _entity_position(entity, positions)
        if caption_position is None:
            skipped_without_reference += 1
            continue
        reference_positions = mentions.get(entity.entity_id) or []
        if not reference_positions:
            skipped_without_reference += 1
            continue
        checked += 1
        first_reference = min(reference_positions)
        if first_reference > caption_position:
            violations.append(
                _reference_order_violation(
                    rule,
                    entity,
                    CAPTION_ENTITY_LABELS["figure"],
                    caption_position,
                    first_reference,
                    _caption_location(entity, paragraphs),
                )
            )
    details = {
        "entities": len(entities),
        "references_found": checked,
        "skipped_without_reference": skipped_without_reference,
        "violations": len(violations),
    }
    if checked == 0:
        return violations, len(entities), _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="ссылок на рисунки в тексте не найдено: порядок проверить нельзя",
            details=details,
        )
    assessment = _build_assessment(
        rule,
        "fail" if violations else "pass",
        expected_text=rule.message,
        observed_text=(
            f"нарушений порядка: {len(violations)} из {checked}"
            if violations
            else f"порядок верен у {checked} рисунков"
        ),
        confidence="medium",
        details=details,
    )
    return violations, len(entities), assessment


def _check_table_after_reference_rule(
    rule: TableAfterReferenceRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Таблица должна стоять после первой ссылки на неё в тексте."""
    entities = _caption_entities(document, "table")
    if not entities:
        return [], 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="таблицы с подписями в разборе не найдены",
            details={"entities": 0},
        )
    positions = _block_positions(document)
    mentions = _mention_positions(document, positions)
    paragraphs = _paragraph_index(document)
    violations: List[Violation] = []
    checked = 0
    skipped_without_reference = 0
    for entity in entities:
        table_position = _entity_position(entity, positions)
        if table_position is None:
            skipped_without_reference += 1
            continue
        reference_positions = mentions.get(entity.entity_id) or []
        if not reference_positions:
            skipped_without_reference += 1
            continue
        checked += 1
        first_reference = min(reference_positions)
        if first_reference > table_position:
            violations.append(
                _reference_order_violation(
                    rule,
                    entity,
                    CAPTION_ENTITY_LABELS["table"],
                    table_position,
                    first_reference,
                    _caption_location(entity, paragraphs),
                )
            )
    details = {
        "entities": len(entities),
        "references_found": checked,
        "skipped_without_reference": skipped_without_reference,
        "violations": len(violations),
    }
    if checked == 0:
        return violations, len(entities), _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="ссылок на таблицы в тексте не найдено: порядок проверить нельзя",
            details=details,
        )
    assessment = _build_assessment(
        rule,
        "fail" if violations else "pass",
        expected_text=rule.message,
        observed_text=(
            f"нарушений порядка: {len(violations)} из {checked}"
            if violations
            else f"порядок верен у {checked} таблиц"
        ),
        confidence="medium",
        details=details,
    )
    return violations, len(entities), assessment


def _front_matter_block_ids(document: ParsedDocument) -> set:
    """Блоки титульного листа и служебных разделов: иллюстрации там не проверяем."""
    roles = {"title_page", "task_sheet", "abstract_referat"}
    ids = set()
    stack = list(document.document_tree)
    while stack:
        node = stack.pop()
        if node.semantic_role in roles:
            ids.update(node.direct_block_ids)
            if node.heading_block_id:
                ids.add(node.heading_block_id)
        stack.extend(node.children or [])
    return ids


def _visual_location(document: ParsedDocument, visual_id: str, page: int) -> ViolationLocation:
    """Адрес иллюстрации по её родительскому абзацу."""
    visual = document.visuals.get(visual_id)
    block_id = visual.parent_block_id if visual is not None else None
    paragraph = _paragraph_index(document).get(block_id or "")
    if paragraph is not None:
        return _paragraph_location(paragraph)
    return ViolationLocation(block_id=block_id, page_start_est=page, snippet=visual_id)


def _check_figure_spacing_rule(
    rule: FigureSpacingRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Отбивка между иллюстрациями и от иллюстрации до нижнего края страницы."""
    visuals = [
        visual
        for visual in document.visuals.values()
        if visual.pdf_y_top_mm is not None and visual.pdf_y_bottom_mm is not None
    ]
    if not visuals:
        return [], 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="координаты иллюстраций не измерены: нужен PDF-режим",
            details={"visuals": len(document.visuals), "measured": 0},
        )
    front_ids = _front_matter_block_ids(document)
    visuals = [
        visual for visual in visuals if (visual.parent_block_id or "") not in front_ids
    ]
    visuals.sort(key=lambda visual: (visual.page_est, visual.pdf_y_top_mm or 0.0))
    limits = document.sections
    violations: List[Violation] = []
    measured = 0
    skipped_section = 0
    skipped_overlap = 0
    for previous, current in zip(visuals, visuals[1:]):
        if previous.page_est != current.page_est:
            continue
        if previous.parent_block_id and previous.parent_block_id == current.parent_block_id:
            skipped_overlap += 1
            continue
        gap = (current.pdf_y_top_mm or 0.0) - (previous.pdf_y_bottom_mm or 0.0)
        if gap < 0:
            skipped_overlap += 1
            continue
        measured += 1
        if gap + rule.params.tolerance_mm < rule.params.min_gap_between_mm:
            violations.append(
                _build_violation(
                    rule,
                    address=f"{previous.visual_id}:gap",
                    location=_visual_location(document, previous.visual_id, previous.page_est),
                    expected={"gap_mm": rule.params.min_gap_between_mm},
                    found={"gap_mm": round(gap, 1)},
                    expected_text=f"отбивка между иллюстрациями не меньше {rule.params.min_gap_between_mm} мм",
                    found_text=f"отбивка между иллюстрациями: {round(gap, 1)} мм",
                )
            )
    for visual in visuals:
        if visual.section_idx >= len(limits):
            skipped_section += 1
            continue
        section = limits[visual.section_idx]
        bottom_limit = section.page_height_mm - section.margins.bottom_mm
        gap = bottom_limit - (visual.pdf_y_bottom_mm or 0.0)
        if gap < 0:
            skipped_overlap += 1
            continue
        measured += 1
        if gap + rule.params.tolerance_mm < rule.params.min_gap_to_page_bottom_mm:
            violations.append(
                _build_violation(
                    rule,
                    address=f"{visual.visual_id}:page_bottom",
                    location=_visual_location(document, visual.visual_id, visual.page_est),
                    expected={"gap_mm": rule.params.min_gap_to_page_bottom_mm},
                    found={"gap_mm": round(gap, 1)},
                    expected_text=f"отбивка до нижнего края страницы не меньше {rule.params.min_gap_to_page_bottom_mm} мм",
                    found_text=f"отбивка до нижнего края страницы: {round(gap, 1)} мм",
                )
            )
    details = {
        "visuals": len(visuals),
        "measured": measured,
        "skipped_section": skipped_section,
        "skipped_overlap": skipped_overlap,
        "violations": len(violations),
    }
    return violations, len(visuals), _build_assessment(
        rule,
        "fail" if violations else "pass",
        expected_text=rule.message,
        observed_text=(
            f"нарушений отбивки: {len(violations)} из {measured}"
            if violations
            else f"отбивка верна у {measured} измеренных иллюстраций"
        ),
        confidence="medium",
        details=details,
    )
