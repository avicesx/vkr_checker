"""Упоминания объектов в тексте для общего слоя обогащения документа.

Модуль работает поверх готового ``ParsedDocument.blocks`` и не зависит от
DOCX/PDF-конвейеров, поэтому одинаково применим к ``docx_only``, ``pdf_only``
и ``docx_pdf``. Поддерживаются три формы упоминаний:

* числовая ссылка на источник в квадратных скобках — ``[3]``, ``[3, с. 15]``,
  ``[1–4]`` (разбор переиспользуется из пакета ``citations``);
* ссылка на пронумерованный объект — «рис. 2» / «рисунок 2», «табл. 3» /
  «таблица 3», «приложение Б», в том числе с вводным «см.»; парадигма
  совпадает с DOCX-регексом (падежные формы «таблицах», «рисунками»,
  «таблицей»), а перечисление «5,6» даёт одно упоминание с несколькими
  целями; диапазоны «1–4» и «2.1–2.3» раскрываются в отдельные цели по тем
  же правилам, что и в DOCX-конвейере;
* авторско-годовая ссылка — ``(Иванов, 2020)`` (регулярное выражение также
  переиспользуется из ``citations``).

Раздел литературы исключается из поиска: его абзацы размечены в
``document_tree`` ролью ``semantic_role="bibliography"``, поэтому записи списка
не попадают в результат как упоминания в тексте. Приём повторяет
``backend/citations/extract.py::_bibliography_block_ids``. По той же причине
пропускаются абзацы-заголовки и абзацы-подписи (``caption_block_id`` из
результата ``extract_captions``): подпись называет объект, но не ссылается на
него из текста.
"""
from __future__ import annotations

import re
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

from citations.patterns import (
    RE_AUTHOR_YEAR_CITATION,
    RE_BRACKET_CITATION,
    parse_author_year_citation,
    parse_bracket_citation,
)
from parser.core.schema import (
    BibliographyEntry,
    DocumentTreeNode,
    NumberedCaptionEntity,
    ParagraphBlock,
    ParsedDocument,
    TextCrossReferenceMention,
)

#: Слово-маркер упоминания объекта без завершающей точки. Парадигма повторяет
#: DOCX-регекс ``RE_ENTITY_MENTION`` (``parser/docx/structure_crossref_parser.py``),
#: чтобы падежные формы распознавались одинаково в обоих конвейерах.
_MENTION_WORD = (
    r"(?:"
    r"рисунк(?:ами|ах|ам|ом|ов|е|у|и|а)?|рисунок|рис|"
    r"таблиц(?:ами|ах|ам|ей|ею|ы|е|у|а)?|табл|"
    r"приложени[еяий]|прил"
    r")"
)

#: Разделитель между словом и номером: точка с необязательным пробелом
#: («рис. 2», «рис.2») либо один и более пробелов («рисунок 2»).
_MENTION_SEPARATOR = r"(?:\.(?P<dot_ws>[\s\u00A0]*)|(?P<ws>[\s\u00A0]+))"

#: Номер объекта: «2», «2.1», «Б» (для приложений), перечисление «5,6» или
#: «1, 2, 3», диапазон «1–4» / «2.1–2.3» (дефис, en dash или em dash).
#: Точка входит в номер только перед цифрой («2.1»), поэтому конец
#: предложения «таблица 5.» не съедается; цели перечисления разделяются
#: запятой или точкой с запятой.
_MENTION_NUMBER = (
    r"[А-ЯЁA-Z0-9]+(?:\s*[.\-\u2013\u2014]\s*\d+)*"
    r"(?:\s*[,;]\s*\d+(?:\s*[.\-\u2013\u2014]\s*\d+)*)*"
)

#: Предел раскрытия диапазона целей: как в DOCX-парсере
#: (``StructureCrossRefParser._expand_entity_target_range``), более длинный
#: диапазон не раскрывается и остаётся двумя концами.
_MAX_RANGE_SPAN = 20

#: Тире диапазона: дефис, en dash, em dash.
_RANGE_DASH_RE = re.compile(r"[\-\u2013\u2014]")

#: Упоминание рисунка, таблицы или приложения. «см.» перед словом необязательно.
RE_OBJECT_MENTION = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё_])"
    r"(?:см\.?[\s\u00A0]+)?"
    rf"(?P<word>{_MENTION_WORD})"
    rf"{_MENTION_SEPARATOR}"
    rf"(?P<number>{_MENTION_NUMBER})"
    r"(?![0-9A-Za-zА-Яа-яЁё_])",
    re.IGNORECASE,
)


def extract_mentions(
    document: ParsedDocument,
    *,
    bibliography: Sequence[BibliographyEntry] = (),
    captions: Sequence[NumberedCaptionEntity] = (),
) -> List[TextCrossReferenceMention]:
    """Находит упоминания объектов в основном тексте и разрешает их цели.

    ``bibliography`` и ``captions`` передаёт вызывающий слой обогащения; если
    они не переданы, используются уже разобранные индексы документа
    (``cross_ref_index``), чтобы функция была пригодна и для ``docx_only``.
    Подписи нужны не только для разрешения целей: их абзацы исключаются из
    поиска, чтобы подпись не помечала свой объект упомянутым.
    Упоминания нумеруются по порядку следования в документе: ``men_001``,
    ``men_002`` и так далее.
    """
    if not bibliography:
        bibliography = document.cross_ref_index.bibliography
    if not captions:
        index = document.cross_ref_index
        captions = [*index.figures, *index.tables, *index.Apps]

    paragraphs = _body_paragraphs(document.blocks)
    skipped = _bibliography_block_ids(paragraphs, document.document_tree)
    # Строка-заголовок описывает структуру, а не ссылается на объект: даже если
    # в ней встретилось «рис. 2», это название раздела, а не упоминание.
    skipped |= _heading_block_ids(document)
    # Абзац-подпись сам называет объект («Рисунок 1 — Схема»), но это не
    # упоминание в тексте: иначе подпись помечала бы свой же рисунок как
    # упомянутый, и правило «рисунок не упомянут в тексте» не срабатывало бы.
    skipped |= {
        caption.caption_block_id
        for caption in captions
        if caption.caption_block_id
    }
    bib_by_order: Dict[str, str] = {
        str(entry.order_index): entry.bib_id for entry in bibliography
    }
    captions_by_type = _caption_index(captions)

    mentions: List[TextCrossReferenceMention] = []
    for block in paragraphs:
        if block.block_id in skipped:
            continue
        text = block.text_visible
        if not text or not text.strip():
            continue
        for found in _iter_mentions(
            text, bib_by_order, captions_by_type, bibliography
        ):
            mentions.append(
                TextCrossReferenceMention(
                    mention_id=f"men_{len(mentions) + 1:03d}",
                    block_id=block.block_id,
                    page_est=block.page_start_est,
                    start_char=found.start,
                    end_char=found.end,
                    raw_snippet=found.raw,
                    ref_type=found.ref_type,  # type: ignore[arg-type]
                    extracted_targets=list(found.targets),
                    resolved_entity_ids=list(found.resolved),
                    uses_short_abbrev=found.uses_short_abbrev,
                    has_nbsp_before_number=found.has_nbsp_before_number,
                )
            )
    return mentions


# ---------------------------------------------------------------------
# Поиск вхождений
# ---------------------------------------------------------------------


class _FoundMention:
    """Найденное вхождение до сборки модели ``TextCrossReferenceMention``."""

    __slots__ = (
        "start",
        "end",
        "raw",
        "ref_type",
        "targets",
        "resolved",
        "uses_short_abbrev",
        "has_nbsp_before_number",
    )

    def __init__(
        self,
        *,
        start: int,
        end: int,
        raw: str,
        ref_type: str,
        targets: Sequence[str],
        resolved: Sequence[str] = (),
        uses_short_abbrev: bool = False,
        has_nbsp_before_number: bool = False,
    ) -> None:
        self.start = start
        self.end = end
        self.raw = raw
        self.ref_type = ref_type
        self.targets = list(targets)
        self.resolved = list(resolved)
        self.uses_short_abbrev = uses_short_abbrev
        self.has_nbsp_before_number = has_nbsp_before_number


def _iter_mentions(
    text: str,
    bib_by_order: Dict[str, str],
    captions_by_type: Dict[str, Dict[str, List[str]]],
    bibliography: Sequence[BibliographyEntry],
) -> Iterator[_FoundMention]:
    """Перебирает вхождения всех форм упоминаний в порядке следования по тексту."""
    found: List[_FoundMention] = []
    found.extend(_bracket_mentions(text, bib_by_order))
    found.extend(_author_year_mentions(text, bibliography))
    found.extend(_object_mentions(text, captions_by_type))

    # Пересекающиеся вхождения пропускаем: длинное совпадение с тем же началом
    # имеет приоритет, иначе одна и та же ссылка попала бы в отчёт дважды.
    found.sort(key=lambda item: (item.start, -item.end))
    last_end = -1
    for item in found:
        if item.start < last_end:
            continue
        last_end = item.end
        yield item


def _bracket_mentions(
    text: str, bib_by_order: Dict[str, str]
) -> Iterator[_FoundMention]:
    """Числовые ссылки в квадратных скобках — упоминания списка литературы."""
    for match in RE_BRACKET_CITATION.finditer(text):
        parsed = parse_bracket_citation(match.group(0))
        if parsed is None or not parsed.targets:
            continue
        resolved = _dedupe(
            [bib_by_order[target] for target in parsed.targets if target in bib_by_order]
        )
        yield _FoundMention(
            start=match.start(),
            end=match.end(),
            raw=match.group(0),
            ref_type="bibliography",
            targets=parsed.targets,
            resolved=resolved,
        )


def _author_year_mentions(
    text: str, bibliography: Sequence[BibliographyEntry]
) -> Iterator[_FoundMention]:
    """Авторско-годовые ссылки — тоже упоминания списка литературы."""
    for match in RE_AUTHOR_YEAR_CITATION.finditer(text):
        parsed = parse_author_year_citation(match.group(0))
        if parsed is None:
            continue
        yield _FoundMention(
            start=match.start(),
            end=match.end(),
            raw=match.group(0),
            ref_type="bibliography",
            targets=parsed.targets,
            resolved=_resolve_author_year(parsed.authors, parsed.year, bibliography),
        )


def _object_mentions(
    text: str, captions_by_type: Dict[str, Dict[str, List[str]]]
) -> Iterator[_FoundMention]:
    """Ссылки «рис. N», «табл. N», «приложение N/Б» с разрешением по подписям."""
    for match in RE_OBJECT_MENTION.finditer(text):
        word = match.group("word")
        ref_type = _classify_object(word)
        if ref_type is None:
            continue

        number = _strip_spaces(match.group("number"))
        if not number:
            continue
        targets = _split_targets(number, ref_type)
        if not targets:
            continue

        resolved = _dedupe(
            [
                entity_id
                for target in targets
                for entity_id in captions_by_type.get(ref_type, {}).get(
                    _normalize_number(target), []
                )
            ]
        )
        dot_ws = match.group("dot_ws")
        separator = dot_ws if dot_ws is not None else match.group("ws") or ""
        yield _FoundMention(
            start=match.start(),
            end=match.end(),
            raw=match.group(0),
            ref_type=ref_type,
            targets=targets,
            resolved=resolved,
            uses_short_abbrev=dot_ws is not None,
            has_nbsp_before_number="\u00A0" in separator,
        )


def _split_targets(number: str, ref_type: str) -> List[str]:
    """Разбивает перечисление номеров на отдельные цели упоминания.

    «5,6» и «1, 2, 3» — одно упоминание с несколькими целями (как в DOCX).
    Диапазоны «1–4» и «2.1–2.3» раскрываются по тем же правилам, что и в
    ``StructureCrossRefParser._expand_entity_target_range``. Буквенные номера
    допустимы только для приложений: «рис. Б» — не объект, а «приложение
    А–В» не раскрывается.
    """
    targets: List[str] = []
    for segment in re.split(r"[,;]", number):
        if not segment:
            continue
        if ref_type == "appendix":
            if segment[0].isdigit() or segment.isalpha():
                targets.append(segment)
        elif segment[0].isdigit():
            targets.extend(_expand_target_range(segment))
    return targets


def _expand_target_range(target: str) -> List[str]:
    """Раскрывает диапазон «1–4» / «2.1–2.3» так же, как DOCX-парсер.

    Без тире цель одна; два числовых конца раскрываются при возрастании и
    длине не больше ``_MAX_RANGE_SPAN``; «2.1–2.3» с общим префиксом — по
    второму уровню; в остальных случаях возвращаются оба конца как есть.
    """
    clean = _strip_spaces(target)
    if not _RANGE_DASH_RE.search(clean):
        return [clean]
    parts = _RANGE_DASH_RE.split(clean)
    if len(parts) != 2:
        return [clean]
    left, right = parts
    if left.isdigit() and right.isdigit():
        start, end = int(left), int(right)
        if start <= end and (end - start) <= _MAX_RANGE_SPAN:
            return [str(index) for index in range(start, end + 1)]
        return [left, right]
    left_match = re.match(r"^(\d+\.)(\d+)$", left)
    right_match = re.match(r"^(\d+\.)(\d+)$", right)
    if (
        left_match is not None
        and right_match is not None
        and left_match.group(1) == right_match.group(1)
    ):
        prefix = left_match.group(1)
        start, end = int(left_match.group(2)), int(right_match.group(2))
        if start <= end and (end - start) <= _MAX_RANGE_SPAN:
            return [f"{prefix}{index}" for index in range(start, end + 1)]
    return [left, right]


def _classify_object(word: str) -> Optional[str]:
    """Определяет тип объекта по слову-маркеру упоминания."""
    low = word.casefold()
    if low.startswith("рис"):
        return "figure"
    if low.startswith("табл"):
        return "table"
    if low.startswith("прил"):
        return "appendix"
    return None


def _caption_index(
    captions: Sequence[NumberedCaptionEntity],
) -> Dict[str, Dict[str, List[str]]]:
    """Строит индекс подписей: тип объекта -> нормализованный номер -> entity_id."""
    index: Dict[str, Dict[str, List[str]]] = {"figure": {}, "table": {}, "appendix": {}}
    for caption in captions:
        by_number = index.setdefault(caption.entity_type, {})
        entity_ids = by_number.setdefault(_normalize_number(caption.number_str), [])
        if caption.entity_id not in entity_ids:
            entity_ids.append(caption.entity_id)
    return index


# ---------------------------------------------------------------------
# Сопоставление с библиографией
# ---------------------------------------------------------------------


def _resolve_author_year(
    authors: Sequence[str],
    year: Optional[int],
    bibliography: Sequence[BibliographyEntry],
) -> List[str]:
    """Сопоставляет фамилии и год с записями списка литературы по ``raw_text``."""
    if year is None or not authors:
        return []
    resolved: List[str] = []
    for entry in bibliography:
        haystack = entry.raw_text.casefold()
        if str(year) not in haystack:
            continue
        if any(author.casefold() in haystack for author in authors):
            resolved.append(entry.bib_id)
    return _dedupe(resolved)


# ---------------------------------------------------------------------
# Вспомогательные проходы по документу
# ---------------------------------------------------------------------


def _body_paragraphs(blocks: Sequence[object]) -> List[ParagraphBlock]:
    """Оставляет абзацы основного текста: колонтитулы, сноски и таблицы не сканируем."""
    return [
        block
        for block in blocks
        if isinstance(block, ParagraphBlock) and block.container_type == "body"
    ]


def _flatten_tree(nodes: Sequence[DocumentTreeNode]) -> Iterator[DocumentTreeNode]:
    """Обходит дерево документа в порядке следования узлов."""
    for node in nodes:
        yield node
        yield from _flatten_tree(node.children)


def _heading_block_ids(document: ParsedDocument) -> Set[str]:
    """Абзацы-заголовки: разметка ``heading_info`` и узлы ``document_tree``.

    Заголовки исключаются из поиска упоминаний: номер раздела («1.Структура»)
    не должен приниматься за ссылку на рисунок или таблицу.
    """
    ids: Set[str] = set()
    for block in document.blocks:
        if (
            isinstance(block, ParagraphBlock)
            and block.heading_info is not None
            and block.heading_info.is_heading
        ):
            ids.add(block.block_id)
    for node in _flatten_tree(document.document_tree):
        if node.heading_block_id:
            ids.add(node.heading_block_id)
    return ids


def _bibliography_block_ids(
    paragraphs: Sequence[ParagraphBlock],
    document_tree: Sequence[DocumentTreeNode],
) -> Set[str]:
    """Возвращает абзацы раздела литературы, чтобы не считать их записи упоминаниями."""
    index_by_block = {paragraph.block_id: index for index, paragraph in enumerate(paragraphs)}
    marks: List[Tuple[int, DocumentTreeNode]] = []
    for node in _flatten_tree(document_tree):
        index = index_by_block.get(node.heading_block_id or "")
        if index is not None:
            marks.append((index, node))
    marks.sort(key=lambda pair: pair[0])

    skipped: Set[str] = set()
    for position, (start, node) in enumerate(marks):
        if node.semantic_role != "bibliography":
            continue
        end = len(paragraphs)
        for next_index, next_node in marks[position + 1 :]:
            if next_node.level <= node.level:
                end = next_index
                break
        skipped.update(paragraph.block_id for paragraph in paragraphs[start:end])
    return skipped


def _normalize_number(value: str) -> str:
    """Нормализует номер объекта для сравнения с ``number_str`` подписи."""
    normalized = _strip_spaces(value)
    normalized = normalized.replace("–", "-").replace("—", "-")
    return normalized.casefold()


def _strip_spaces(value: str) -> str:
    """Убирает все пробелы, включая неразрывные, из номера объекта."""
    return re.sub(r"\s+", "", value)


def _dedupe(values: Sequence[str]) -> List[str]:
    """Убирает повторы, сохраняя порядок появления."""
    seen: Set[str] = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
