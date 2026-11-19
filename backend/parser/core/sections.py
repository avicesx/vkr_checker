"""Утилиты обхода дерева документа: узлы и диапазоны блоков разделов.

Модуль знает только контрактные типы схемы и не зависит ни от какого
конвейера. Он нужен потребителям готового ``ParsedDocument``, чтобы границы
раздела не считали каждый своей копией приёма:

    iter_tree_nodes    — обход дерева в порядке следования узлов;
    section_block_ids  — абзацы диапазона раздела по его роли.

Границы раздела: от его заголовка до заголовка того же или более высокого
уровня; абзацы после последнего заголовка раздела до конца списка абзацев
считаются частью последнего раздела той же роли.

DOCX-конвейер здесь не покупатель: он знает ``direct_block_ids`` OOXML-дерева
и собирает разделы по ним.
"""
from __future__ import annotations

from typing import Iterator, List, Sequence, Set, Tuple

from .schema import DocumentTreeNode, ParagraphBlock


def iter_tree_nodes(nodes: Sequence[DocumentTreeNode]) -> Iterator[DocumentTreeNode]:
    """Обходит дерево документа в порядке следования узлов."""
    for node in nodes:
        yield node
        yield from iter_tree_nodes(node.children)


def section_block_ids(
    paragraphs: Sequence[ParagraphBlock],
    document_tree: Sequence[DocumentTreeNode],
    role: str,
) -> Set[str]:
    """Возвращает ``block_id`` абзацев разделов с указанной семантической ролью.

    :param paragraphs: абзацы в порядке следования (обычно срез от
        ``ParsedDocument.blocks``).
    :param document_tree: дерево документа из отчёта разбора.
    :param role: значение ``semantic_role`` интересующих разделов, например
        ``"bibliography"``.
    :return: идентификаторы абзацев всех найденных диапазонов; пустое
        множество, если раздел с такой ролью в дереве не размечен.
    """
    index_by_block = {paragraph.block_id: index for index, paragraph in enumerate(paragraphs)}
    marks: List[Tuple[int, DocumentTreeNode]] = []
    for node in iter_tree_nodes(document_tree):
        index = index_by_block.get(node.heading_block_id or "")
        if index is not None:
            marks.append((index, node))
    marks.sort(key=lambda pair: pair[0])

    selected: Set[str] = set()
    for position, (start, node) in enumerate(marks):
        if node.semantic_role != role:
            continue
        end = len(paragraphs)
        for next_index, next_node in marks[position + 1 :]:
            if next_node.level <= node.level:
                end = next_index
                break
        selected.update(paragraph.block_id for paragraph in paragraphs[start:end])
    return selected
