"""Структурные проверки вне заголовков: порядок приложений и титульный лист.

Оба кода работают по ``DocumentTreeNode``: узел с ролью ``appendix`` или
``title_page`` уже найден разбором, поэтому правила не угадывают разделы по
тексту. Если таких узлов в документе нет (в корпусе нет приложений), правило
честно не оценивается, а не сообщает «все хорошо».
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

from ..findings import RuleAssessment, Violation, ViolationLocation
from ..rules import (
    AppendixOrderRule,
    TitlePageContentRule,
)
from .builders import (
    _build_assessment,
    _build_violation,
    _collapse_text,
    _counted_assessment,
)
from .common import _safe_page_est
from parser.core.schema import DocumentTreeNode, ParagraphBlock, ParsedDocxDocument

TITLE_FIELD_PATTERNS: Dict[str, str] = {
    "министерство": r"министерств",
    "вуз": r"университет|высшего образования|академи|вуз",
    "институт": r"институт",
    "кафедра": r"кафедр",
    "вид работы": r"курсов(?:ая|ой)\s+работ|выпускная квалификационная|\bвкр\b|дипломн",
    "тема": r"\bтем[аы]\b",
    "автор": r"студент|выполнил|обучающ",
    "руководитель": r"руководител",
    "оценка": r"оценк",
    "город": r"(?:^|\n)\s*(?:г\.\s*)?[А-ЯЁ][а-яё\-]{3,}\s*,?\s*(?:19|20)\d{2}\s*$",
    "год": r"\b(?:19|20)\d{2}\b",
}
"""Признаки реквизитов титульного листа: ключ — термин методички, значение — выражение."""

TITLE_FIELD_LABELS: Dict[str, str] = {
    "министерство": "наименование министерства",
    "вуз": "наименование вуза",
    "институт": "наименование института",
    "кафедра": "наименование кафедры",
    "вид работы": "вид исследовательской работы",
    "тема": "название темы",
    "автор": "сведения об авторе",
    "руководитель": "сведения о научном руководителе",
    "оценка": "строка для проставления оценки",
    "город": "город написания",
    "год": "год написания",
}
"""Подписи реквизитов титульного листа для текста находок."""


#: Синонимы вида работы: на титуле пишут полное название, а в требовании —
#: аббревиатуру (и наоборот), поэтому сверяем по семейству формулировок.
WORK_TYPE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "вкр": (
        r"выпускн\w*\s+квалификационн\w*\s+работ\w*",
        r"\bвкр\b",
    ),
    "выпускная квалификационная работа": (
        r"выпускн\w*\s+квалификационн\w*\s+работ\w*",
        r"\bвкр\b",
    ),
    "дипломная работа": (
        r"дипломн\w*\s+работ\w*",
    ),
}


def _tree_nodes(document: ParsedDocxDocument) -> List[DocumentTreeNode]:
    """Возвращает все узлы дерева документа в порядке обхода."""
    tree = document.document_tree
    if not tree:
        return []
    # Дерево может быть как одним корнем, так и лесом корневых разделов.
    roots = tree if isinstance(tree, list) else [tree]
    result: List[DocumentTreeNode] = []
    stack: List[DocumentTreeNode] = list(reversed(roots))
    while stack:
        node = stack.pop()
        result.append(node)
        stack.extend(reversed(node.children))
    return result


def _nodes_by_role(
    document: ParsedDocxDocument,
    roles: Iterable[str],
) -> List[DocumentTreeNode]:
    """Возвращает узлы с указанными ролями раздела."""
    wanted = set(roles)
    return [node for node in _tree_nodes(document) if node.semantic_role in wanted]


def _node_anchor(node: DocumentTreeNode) -> str:
    """Возвращает идентификатор блока для адреса находки."""
    if node.heading_block_id:
        return node.heading_block_id
    if node.direct_block_ids:
        return node.direct_block_ids[0]
    return f"node:{node.node_id}"


def _node_location(node: DocumentTreeNode, snippet: str) -> ViolationLocation:
    """Создает локацию узла дерева: блок, страница и фрагмент."""
    return ViolationLocation(
        block_id=_node_anchor(node),
        page_start_est=_safe_page_est(node.page_start_est),
        page_end_est=_safe_page_est(node.page_end_est),
        snippet=_collapse_text(snippet or node.title or "раздел"),
    )


def _top_level_role_nodes(
    document: ParsedDocxDocument,
    role: str,
) -> List[DocumentTreeNode]:
    """Возвращает узлы роли без предка той же роли.

    Разбор вкладывает в приложение служебные узлы (продолжения, листинги) с
    той же ролью ``appendix``; порядок и начало с нового листа проверяются
    только по самостоятельным приложениям, иначе вложенный узел выглядит как
    приложение, начатое на уже занятой странице.
    """
    result: List[DocumentTreeNode] = []
    tree = document.document_tree
    if not tree:
        return result
    roots = tree if isinstance(tree, list) else [tree]
    stack: List[Tuple[DocumentTreeNode, bool]] = [
        (node, False) for node in reversed(roots)
    ]
    while stack:
        node, inside_role = stack.pop()
        matched = node.semantic_role == role
        if matched and not inside_role:
            result.append(node)
        nested = inside_role or matched
        for child in reversed(node.children):
            stack.append((child, nested))
    return result


def _mention_ranks(document: ParsedDocxDocument) -> Dict[str, int]:
    """Строит порядок первого упоминания приложений: узел → номер упоминания."""
    index = document.cross_ref_index
    ranks: Dict[str, int] = {}
    if index is None:
        return ranks
    for order, mention in enumerate(index.mentions):
        if mention.ref_type != "appendix":
            continue
        for node_id in mention.resolved_structure_node_ids or []:
            ranks.setdefault(node_id, order)
    return ranks


def _check_appendix_order_rule(
    rule: AppendixOrderRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет порядок приложений и начало каждого с нового листа.

    Порядок сверяется с первым упоминанием приложения в тексте: приложение,
    упомянутое раньше, должно и стоять раньше. Если упоминаний нет, порядок не
    оценивается, но требование нового листа проверяется по страницам узлов.
    """
    expected = rule.params
    nodes = _top_level_role_nodes(document, "appendix")
    expected_text = "приложения идут в порядке упоминания, каждое с нового листа"
    if not nodes:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    "в документе нет разделов с ролью «appendix»: проверять порядок "
                    "приложений нечего"
                ),
                confidence="low",
                details={"appendices": 0, "tree_nodes": len(_tree_nodes(document))},
            ),
        )

    ordered = sorted(
        nodes,
        key=lambda node: (
            node.page_start_est is None,
            node.page_start_est if node.page_start_est is not None else 0,
        ),
    )
    ranks = _mention_ranks(document)
    violations: List[Violation] = []

    order_checked = expected.order == "by_first_mention" and bool(ranks)
    if order_checked:
        previous_rank: Optional[int] = None
        previous_node: Optional[DocumentTreeNode] = None
        for node in ordered:
            rank = ranks.get(node.node_id)
            if rank is None:
                continue
            if previous_rank is not None and rank < previous_rank:
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{_node_anchor(node)}:order",
                        location=_node_location(node, node.title),
                        expected={"order": "by_first_mention"},
                        found={
                            "appendix": node.title,
                            "mention_rank": rank,
                            "previous_appendix": previous_node.title if previous_node else None,
                            "previous_mention_rank": previous_rank,
                        },
                        expected_text="приложения расположены в порядке первого упоминания",
                        found_text=(
                            f"приложение «{node.title}» упомянуто раньше, чем "
                            f"«{previous_node.title if previous_node else ''}», но стоит после"
                        ),
                    )
                )
            previous_rank = rank
            previous_node = node

    if expected.new_page_each:
        previous: Optional[DocumentTreeNode] = None
        for node in ordered:
            if (
                previous is not None
                and node.page_start_est is not None
                and previous.page_end_est is not None
                and node.page_start_est <= previous.page_end_est
            ):
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{_node_anchor(node)}:new_page",
                        location=_node_location(node, node.title),
                        expected={"new_page": True},
                        found={
                            "appendix": node.title,
                            "start_page": node.page_start_est,
                            "previous_end_page": previous.page_end_est,
                        },
                        expected_text="каждое приложение начинается с нового листа",
                        found_text=(
                            f"приложение «{node.title}» начинается на странице "
                            f"{node.page_start_est}, предыдущее заканчивается на "
                            f"{previous.page_end_est}"
                        ),
                    )
                )
            previous = node

    assessment = _counted_assessment(
        rule,
        violations,
        len(nodes),
        expected_text=expected_text,
        confidence="medium",
        empty_observed="приложений в документе не найдено",
        details={
            "appendices": len(nodes),
            "mentions_found": len(ranks),
            "order_checked": order_checked,
            "new_page_checked": expected.new_page_each,
        },
    )
    if not order_checked and expected.order == "by_first_mention":
        assessment = assessment.model_copy(
            update={
                "details": {
                    **assessment.details,
                    "order_note": (
                        "порядок не оценен: в cross_ref_index нет упоминаний приложений "
                        "с разобранными узлами"
                    ),
                }
            }
        )
    return violations, len(nodes), assessment


def _title_page_paragraphs(
    document: ParsedDocxDocument,
    node: DocumentTreeNode,
) -> List[ParagraphBlock]:
    """Возвращает абзацы титульного узла вместе с вложенными узлами."""
    block_ids: List[str] = list(node.direct_block_ids or [])
    stack: List[DocumentTreeNode] = list(node.children)
    while stack:
        child = stack.pop()
        block_ids.extend(child.direct_block_ids or [])
        stack.extend(child.children)
    wanted = set(block_ids)
    return [
        block
        for block in document.blocks
        if isinstance(block, ParagraphBlock) and block.block_id in wanted
    ]


def _check_title_page_content_rule(
    rule: TitlePageContentRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет наличие обязательных реквизитов на титульном листе.

    Реквизиты ищутся по тексту абзацев узла ``title_page``: разбор не выделяет
    их отдельными полями, поэтому проверяется наличие характерных слов. Если
    узла титульного листа нет, правило не оценивается.
    """
    expected = rule.params
    nodes = _nodes_by_role(document, {"title_page"})
    expected_text = "титульный лист содержит обязательные реквизиты"
    if not nodes:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    "в документе нет раздела с ролью «title_page»: проверять реквизиты "
                    "титульного листа нечего"
                ),
                confidence="low",
                details={"title_pages": 0, "tree_nodes": len(_tree_nodes(document))},
            ),
        )

    violations: List[Violation] = []
    checked = 0
    found: Dict[str, List[str]] = {}
    missing: List[str] = []

    # Реквизиты ищутся только в извлечённом тексте: если титульный лист
    # свёрстан рамками или надписями и разбор не дал ни одного абзаца с
    # текстом, честнее не оценивать правило, чем сообщить о пропаже всего.
    page_entries: List[Tuple[DocumentTreeNode, List[ParagraphBlock], str]] = []
    for node in nodes:
        paragraphs = _title_page_paragraphs(document, node)
        node_text = "\n".join(paragraph.text_visible or "" for paragraph in paragraphs)
        if node_text.strip():
            page_entries.append((node, paragraphs, node_text))
    if not page_entries:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    "текст титульного листа не извлечён разбором (вероятно, свёрстан "
                    "рамками или надписями): реквизиты проверить нельзя"
                ),
                confidence="low",
                details={
                    "title_pages": len(nodes),
                    "pages_with_text": 0,
                    "text_note": "в узле title_page нет абзацев с непустым текстом",
                },
            ),
        )

    for node, paragraphs, text in page_entries:
        anchor_location = _node_location(
            node,
            next(
                (paragraph.text_visible for paragraph in paragraphs if paragraph.text_visible),
                node.title,
            ),
        )

        for field_name in expected.required_fields:
            pattern = TITLE_FIELD_PATTERNS.get(field_name)
            if pattern is None:
                continue
            checked += 1
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                found.setdefault(field_name, []).append(_collapse_text(match.group(0)))
                continue
            missing.append(field_name)
            violations.append(
                _build_violation(
                    rule,
                    address=f"{_node_anchor(node)}:{field_name}",
                    location=anchor_location,
                    expected={"field": field_name},
                    found={"field": None, "paragraphs": len(paragraphs)},
                    expected_text=(
                        f"на титульном листе есть {TITLE_FIELD_LABELS[field_name]}"
                    ),
                    found_text=(
                        f"реквизит «{TITLE_FIELD_LABELS[field_name]}» не найден в тексте "
                        "титульного листа"
                    ),
                )
            )

        if expected.work_type:
            checked += 1
            aliases = WORK_TYPE_ALIASES.get(
                expected.work_type.strip().casefold(),
                (re.escape(expected.work_type),),
            )
            if any(re.search(pattern, text, re.IGNORECASE) for pattern in aliases):
                found.setdefault("work_type", []).append(expected.work_type)
            else:
                missing.append("work_type")
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{_node_anchor(node)}:work_type",
                        location=anchor_location,
                        expected={"work_type": expected.work_type},
                        found={"work_type": None},
                        expected_text=f"на титульном листе указан вид работы «{expected.work_type}»",
                        found_text="вид работы не найден в тексте титульного листа",
                    )
                )

    assessment = _counted_assessment(
        rule,
        violations,
        checked,
        expected_text=expected_text,
        confidence="medium",
        empty_observed="реквизитов для проверки не задано",
        details={
            "title_pages": len(nodes),
            "pages_with_text": len(page_entries),
            "fields_checked": checked,
            "fields_found": sorted(found),
            "fields_missing": missing,
        },
    )
    return violations, checked, assessment
