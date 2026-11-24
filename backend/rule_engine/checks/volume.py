"""Семейство документа целиком: объём работы в страницах и словах."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..findings import (
    RuleAssessment,
    Violation,
    ViolationLocation,
)
from ..rules import DocumentVolumeRule
from .builders import _build_violation, _counted_assessment
from .common import _document_violation
from parser.core.schema import DocumentTreeNode, ParsedDocxDocument

#: Ниже этого числа страниц документ считается фрагментом или шаблоном, и объём
#: не оценивается: нормативы ВКР рассчитаны на полный текст, а не на выжимку.
VOLUME_MIN_MEASURABLE_PAGES = 10


def _check_lower_bound(
    rule: DocumentVolumeRule,
    address: str,
    measured: int,
    minimum: int,
    unit: str,
    confidence: str,
    location: ViolationLocation,
) -> Optional[Violation]:
    """Собирает нарушение нижней границы объёма, если она нарушена."""
    if measured >= minimum:
        return None
    return _document_violation(
        rule,
        address=address,
        expected_values={f"min_{unit}": minimum},
        found_values={unit: measured, "section": "document"},
        expected_text=f"не менее {minimum} {unit}",
        found_text=f"{unit}: {measured}",
        confidence=confidence,
        location=location,
    )


def _check_upper_bound(
    rule: DocumentVolumeRule,
    address: str,
    measured: int,
    maximum: int,
    unit: str,
    confidence: str,
    location: ViolationLocation,
) -> Optional[Violation]:
    """Собирает нарушение верхней границы объёма, если она нарушена."""
    if measured <= maximum:
        return None
    return _document_violation(
        rule,
        address=address,
        expected_values={f"max_{unit}": maximum},
        found_values={unit: measured, "section": "document"},
        expected_text=f"не более {maximum} {unit}",
        found_text=f"{unit}: {measured}",
        confidence=confidence,
        location=location,
    )


def _role_nodes(document: ParsedDocxDocument) -> Dict[str, List[Tuple[DocumentTreeNode, int]]]:
    """Собирает узлы дерева по ролям вместе с числом страниц раздела.

    Страницы раздела считаются по оценкам начала и конца узла; узлы без
    пагинации в проверку не попадают, чтобы не выдавать догадку за измерение.
    """
    result: Dict[str, List[Tuple[DocumentTreeNode, int]]] = {}
    tree = document.document_tree
    roots = tree if isinstance(tree, list) else [tree]
    stack = [node for node in roots if node is not None]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.page_start_est is None or node.page_end_est is None:
            continue
        pages = max(1, node.page_end_est - node.page_start_est + 1)
        result.setdefault(node.semantic_role, []).append((node, pages))
    return result


def _node_location(node: DocumentTreeNode, snippet: str) -> ViolationLocation:
    """Создает локацию раздела: заголовок, страницы и короткий фрагмент."""
    return ViolationLocation(
        block_id=node.heading_block_id,
        page_start_est=node.page_start_est,
        page_end_est=node.page_end_est,
        snippet=snippet,
    )


def _top_level_appendix_ranges(
    document: ParsedDocxDocument,
) -> List[Tuple[int, int]]:
    """Возвращает объединённые диапазоны страниц верхнеуровневых приложений.

    Вложенные узлы («Продолжение приложения А») входят в диапазон родителя,
    поэтому их страницы не суммируются повторно: иначе доля приложений может
    превысить весь объём работы.
    """
    intervals: List[Tuple[int, int]] = []
    tree = document.document_tree
    roots = tree if isinstance(tree, list) else [tree]
    stack: List[Tuple[DocumentTreeNode, bool]] = [
        (node, False) for node in roots if node is not None
    ]
    while stack:
        node, inside_appendix = stack.pop()
        is_appendix = node.semantic_role == "appendix"
        if is_appendix and not inside_appendix:
            if node.page_start_est is not None and node.page_end_est is not None:
                intervals.append((node.page_start_est, node.page_end_est))
        stack.extend(
            (child, inside_appendix or is_appendix) for child in node.children
        )
    merged: List[Tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _check_document_volume_rule(
    rule: DocumentVolumeRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Сравнивает объём работы с границами правила.

    Страницы берутся из ``page_count`` (точные для PDF и по разрывам DOCX), а
    не из ``meta.word_stat_pages``: статистика DOCX может врать (например,
    хранить 1 страницу для многостраничного файла). Число слов приходит из
    статистики DOCX или из извлеченного текста PDF; если парсер его не
    посчитал, проверка слов пропускается с пометкой в оценке.
    """
    expected = rule.params
    pages = document.page_count.total_pages
    words = document.meta.word_stat_words
    page_confidence = document.page_count.confidence

    if pages < VOLUME_MIN_MEASURABLE_PAGES:
        assessment = _counted_assessment(
            rule,
            [],
            0,
            expected_text=rule.message,
            confidence=page_confidence,
            empty_observed="объём работы не измерен: документ короче норматива проверки",
            details={"pages": pages, "words": words},
            note=(
                "документ короче "
                f"{VOLUME_MIN_MEASURABLE_PAGES} страниц — вероятно, это фрагмент или "
                "шаблон, объём работы не оценивается"
            ),
        )
        return [], 0, assessment

    first_block_id = document.blocks[0].block_id if document.blocks else None
    location = ViolationLocation(
        block_id=first_block_id,
        page_start_est=1,
        page_end_est=max(1, pages),
        snippet=(
            f"весь документ: {pages} страниц"
            + (f", {words} слов" if words is not None else "")
        ),
    )
    violations: List[Violation] = []
    checked = 1

    if expected.min_pages is not None:
        violation = _check_lower_bound(
            rule,
            "document:pages",
            pages,
            expected.min_pages,
            "страниц",
            page_confidence,
            location,
        )
        if violation is not None:
            violations.append(violation)
    if expected.max_pages is not None:
        violation = _check_upper_bound(
            rule,
            "document:pages",
            pages,
            expected.max_pages,
            "страниц",
            page_confidence,
            location,
        )
        if violation is not None:
            violations.append(violation)

    if words is not None:
        checked += 1
        if expected.min_words is not None:
            violation = _check_lower_bound(
                rule,
                "document:words",
                words,
                expected.min_words,
                "слов",
                page_confidence,
                location,
            )
            if violation is not None:
                violations.append(violation)
        if expected.max_words is not None:
            violation = _check_upper_bound(
                rule,
                "document:words",
                words,
                expected.max_words,
                "слов",
                page_confidence,
                location,
            )
            if violation is not None:
                violations.append(violation)

    section_checks = 0
    roles_without_pages: List[str] = []
    role_nodes = _role_nodes(document)
    if expected.section_page_limits or expected.section_min_pages:
        for role, limit in expected.section_page_limits.items():
            matches = role_nodes.get(role, [])
            if not matches:
                roles_without_pages.append(role)
                continue
            section_checks += 1
            for node, node_pages in matches:
                if node_pages > limit:
                    violations.append(
                        _build_violation(
                            rule,
                            address=f"section:{role}:pages",
                            location=_node_location(node, f"раздел «{node.title}»"),
                            expected={"max_pages": limit},
                            found={"role": role, "pages": node_pages},
                            expected_text=f"раздел «{node.title}» не длиннее {limit} страниц",
                            found_text=f"страниц в разделе: {node_pages}",
                            confidence=page_confidence,
                        )
                    )
        for role, limit in expected.section_min_pages.items():
            matches = role_nodes.get(role, [])
            if not matches:
                roles_without_pages.append(role)
                continue
            section_checks += 1
            for node, node_pages in matches:
                if node_pages < limit:
                    violations.append(
                        _build_violation(
                            rule,
                            address=f"section:{role}:min_pages",
                            location=_node_location(node, f"раздел «{node.title}»"),
                            expected={"min_pages": limit},
                            found={"role": role, "pages": node_pages},
                            expected_text=f"раздел «{node.title}» не короче {limit} страниц",
                            found_text=f"страниц в разделе: {node_pages}",
                            confidence=page_confidence,
                        )
                    )

    appendix_share = None
    if expected.max_appendix_share is not None:
        appendix_ranges = _top_level_appendix_ranges(document)
        if appendix_ranges:
            section_checks += 1
            appendix_pages = sum(end - start + 1 for start, end in appendix_ranges)
            appendix_share = round(appendix_pages / pages, 3)
            if appendix_share > expected.max_appendix_share:
                violations.append(
                    _document_violation(
                        rule,
                        address="section:appendix:share",
                        expected_values={
                            "max_appendix_share": expected.max_appendix_share
                        },
                        found_values={
                            "appendix_pages": appendix_pages,
                            "pages": pages,
                            "share": appendix_share,
                        },
                        expected_text=(
                            "доля приложений не более "
                            f"{round(expected.max_appendix_share * 100)} % объёма"
                        ),
                        found_text=(
                            f"приложения занимают {round(appendix_share * 100)} % "
                            f"({appendix_pages} из {pages} страниц)"
                        ),
                        confidence=page_confidence,
                        location=location,
                    )
                )
        else:
            roles_without_pages.append("appendix")

    checked += section_checks
    note = None
    if words is None:
        note = "число слов не определено парсером — проверка слов пропущена"
    assessment = _counted_assessment(
        rule,
        violations,
        checked,
        expected_text=rule.message,
        confidence=page_confidence,
        empty_observed="объём работы не измерен",
        details={
            "pages": pages,
            "words": words,
            "sections_checked": section_checks,
            "roles_without_pages": roles_without_pages,
            "appendix_share": appendix_share,
        },
        note=note,
    )
    return violations, checked, assessment
