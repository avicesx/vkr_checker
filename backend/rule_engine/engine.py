"""Движок проверки документа по декларативным правилам нормоконтроля.

Движок не разбирает OOXML заново: он берет ParsedDocxDocument, где каскад
стилей уже разрешен, а единицы приведены к пунктам и миллиметрам. Каждая
группа правил (шрифты, отступы, поля) сравнивает эффективные значения с
ожидаемыми и формирует нарушение с адресом, ожиданием и фактом.

Проверяются только объекты, которые правило действительно описывает: по
умолчанию из выборки исключены заголовки, подписи рисунков и таблиц, элементы
списков, пустые абзацы и строки оглавления. Это позволяет применять правило
«основной текст» без ручных исключений в наборе правил.

Титульный лист и листинги кода — служебные зоны: они набраны по требованиям
методички к титулу и коду, а не к основному тексту. Их контекст определяет
`rule_engine.context`; `RuleTarget.exclude_contexts` перечисляет контексты,
которые правило не проверяет (по умолчанию оба).

Сверка шрифта сравнивает гарнитуры по нормализованному имени: Word пишет одну
и ту же гарнитуру как «Times New Roman», «TimesNewRoman», «TimesNewRomanPSMT»
или «ABCDEF+TimesNewRoman-BoldMT», поэтому subset-префикс и стилевые маркеры
отбрасываются (`rule_engine._fonts`). Символические раны — гарнитуры Symbol,
Wingdings и раны из одних небуквенных знаков (±, ≤, ×) — правилом шрифта не
проверяются: кегль и начертание у математических знаков не осмысленны.

Заголовки калиброваны по корпусу ВКР. Уровень нумерованного заголовка равен
глубине номера (`3.1.1` — третий уровень), поэтому расхождение с `outlineLvl`
стиля не превращается в перескок уровня; проверка глубины номера при этом
сравнивает его с местом в иерархии, а не с уровнем, вычисленным из него же.
Метки служебных страниц («ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА», «ЗАДАНИЕ»)
разбор заголовками не считает, список литературы узнается по синонимам
названий (`SECTION_TITLE_SYNONYMS`: «СПИСОК ИСТОЧНИКОВ», «БИБЛИОГРАФИЧЕСКИЙ
СПИСОК», «ИСТОЧНИКИ»), а структурные роли (`NUMBERLESS_HEADING_ROLES`) номер не
требуют.

Оглавление сверяется с заголовками и приложениями. Запись «ПРИЛОЖЕНИЕ А»
указывает на приложение по букве: правило ищет ее среди заголовков приложений,
подписей «Продолжение ПРИЛОЖЕНИЯ А» и сущностей `entity_type="appendix"`, а если
буквы в подписи нет — по названию («(обязательное) Название»). Одна запись
приложения покрывает и подзаголовки внутри него. `TocExpectation.max_depth`
задает глубину, до которой заголовки обязаны быть в оглавлении: при `max_depth:
2` подраздел «3.1.1» пропуском не считается, а пропуск главы остается
нарушением.

Правила деградируют по `DocumentCapabilities`: в `pdf_only`
(`capabilities.styles_parsed=False`) нет ни `w:pPr`, ни стилей, ни
`numbering.xml`, поэтому отступы абзаца не проверяются (источник требования
ненадежен — см. `_check_indent_rule`), complex script не сверяется, а допуск
полей страницы расширяется до геометрии PDF (±1.5 мм). Гарнитура, кегль,
начертание, заголовки и оглавление проверяются и в этом режиме: запись шрифта
PDF (`TimesNewRoman`, `ABCDEF+…`, `LiberationSans`) сравнивается по
нормализованному имени, поэтому запись шрифта не шумит, а настоящая другая
гарнитура — нарушение.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from parser.core.schema import (
    BlockUnion,
    DocumentTreeNode,
    ParagraphBlock,
    ParsedDocxDocument,
    RunFontPropertiesEffective,
    SectionProperties,
    TableBlock,
    TextRun,
    TocContainerBlock,
    TocEntryItem,
)

from ._fonts import (
    is_symbolic_family as _is_symbolic_family,
    same_family as _same_family,
)
from .findings import (
    DocumentRef,
    RuleCheckReport,
    Violation,
    ViolationLocation,
    build_summary,
)
from .rules import (
    NUMBERLESS_HEADING_ROLES,
    FontExpectation,
    FontRule,
    HeadingFormatRule,
    HeadingLevelsRule,
    HeadingNumberingRule,
    IndentRule,
    MarginRule,
    RequiredSectionsRule,
    Rule,
    RuleSet,
    RuleTarget,
    SectionSpec,
    TocRule,
    load_ruleset,
)


INDENT_LABELS: Dict[str, str] = {
    "first_line_mm": "отступ первой строки",
    "left_mm": "отступ слева",
    "right_mm": "отступ справа",
}

MARGIN_LABELS: Dict[str, str] = {
    "left_mm": "левое поле",
    "right_mm": "правое поле",
    "top_mm": "верхнее поле",
    "bottom_mm": "нижнее поле",
    "gutter_mm": "корешковый отступ",
}

ALIGNMENT_LABELS: Dict[str, str] = {
    "left": "по левому краю",
    "center": "по центру",
    "right": "по правому краю",
    "both": "по ширине",
    "distribute": "по ширине с разрядкой",
}

CASE_LABELS: Dict[str, str] = {
    "uppercase": "заголовок прописными буквами",
    "capitalized": "заголовок с прописной буквы",
    "not_uppercase": "заголовок не целиком прописными буквами",
}

FOUND_CASE_LABELS: Dict[str, str] = {
    "uppercase": "прописными буквами",
    "capitalized": "с прописной буквы",
    "lowercase_start": "со строчной буквы",
}

NUMBER_ENDING_LABELS: Dict[str, str] = {
    "with_dot": "точка после номера",
    "without_dot": "без точки после номера",
}

CONFIDENCE_RANK: Dict[str, int] = {
    "explicit_outline_or_style": 0,
    "structural_keyword_match": 1,
    "heuristic_visual": 2,
}

RE_SECTION_NUMBER = re.compile(r"^\d+(?:\.\d+)*$")

#: Группы синонимов названий разделов: один и тот же раздел ВКР называется
#: по-разному. Сравнение нормализованных названий идет внутри группы, поэтому
#: «СПИСОК ИСТОЧНИКОВ», «БИБЛИОГРАФИЧЕСКИЙ СПИСОК» и «ИСТОЧНИКИ» удовлетворяют
#: требованию «список литературы» из набора правил.
SECTION_TITLE_SYNONYMS: Tuple[frozenset[str], ...] = (
    frozenset(
        {
            "СПИСОК ЛИТЕРАТУРЫ",
            "СПИСОК ИСПОЛЬЗОВАННОЙ ЛИТЕРАТУРЫ",
            "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ",
            "СПИСОК ИСПОЛЬЗУЕМЫХ ИСТОЧНИКОВ",
            "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ И ЛИТЕРАТУРЫ",
            "СПИСОК ИСТОЧНИКОВ",
            "БИБЛИОГРАФИЧЕСКИЙ СПИСОК",
            "БИБЛИОГРАФИЯ",
            "ИСТОЧНИКИ",
            "ЛИТЕРАТУРА",
        }
    ),
)
"""Синонимы названий разделов; сравнение идет по нормализованному названию."""

RE_APPENDIX_CAPTION = re.compile(
    r"^\s*(?:(?P<continuation>продолжение|окончание)\s+)?"
    r"приложени[ея]\s+(?P<letter>[А-ЯЁA-Z0-9]+)\s*"
    r"(?:\(\s*(?:обязательное|справочное|рекомендуемое)\s*\)\s*)?"
    r"(?P<title>.*)$",
    re.IGNORECASE | re.DOTALL,
)
"""Подпись приложения: «ПРИЛОЖЕНИЕ А», «ПРИЛОЖЕНИЕ А (обязательное) Название».

Буква приложения — идентификатор: в оглавлении ВКР запись «ПРИЛОЖЕНИЕ А»
указывает на приложение, а не на заголовок с таким текстом. Продолжение
приложения («Продолжение ПРИЛОЖЕНИЯ А») доказывает, что приложение существует,
даже если подпись первого листа набрана без буквы.
"""

RE_APPENDIX_KIND_MARKER = re.compile(
    r"^\s*\(\s*(?:обязательное|справочное|рекомендуемое)\s*\)\s*(?P<title>.*)$",
    re.IGNORECASE | re.DOTALL,
)
"""Титульная строка приложения без буквы: «(обязательное) Название приложения»."""

TOC_TITLE_MIN_CHARS = 8
"""Минимальная длина названия для сверки записи оглавления с заголовком."""

APPENDIX_LETTERS = frozenset("АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ")
"""Буквы, которыми нумеруют приложения ВКР (кроме Ё, Й, Ъ, Ы, Ь)."""

SNIPPET_LIMIT = 80
EPSILON = 1e-9

#: Допуск полей страницы в pdf_only, мм: поля измерены по геометрии текста.
PDF_ONLY_MARGIN_TOLERANCE_MM = 1.5


@dataclass(frozen=True)
class _HeadingRecord:
    """Заголовок документа: узел дерева, его абзац и ближайший проверяемый родитель."""

    node: DocumentTreeNode
    paragraph: ParagraphBlock
    parent: Optional[DocumentTreeNode]


def _normalize_heading_text(text: str) -> str:
    """Нормализует название заголовка для сравнения: регистр, Ё, пробелы и крайние знаки."""
    collapsed = " ".join(text.split()).upper().replace("Ё", "Е")
    return collapsed.strip(" .:;")


def _parse_section_number(raw: Optional[str]) -> Optional[Tuple[int, ...]]:
    """Разбирает номер раздела вида '1', '2.3', '1.2.' в кортеж чисел."""
    if not raw:
        return None
    cleaned = raw.strip().strip(".")
    if not RE_SECTION_NUMBER.match(cleaned):
        return None
    return tuple(int(part) for part in cleaned.split("."))


def _format_section_number(parts: Tuple[int, ...]) -> str:
    """Собирает номер раздела обратно в строку через точку."""
    return ".".join(str(part) for part in parts)


def _heading_section_number(record: _HeadingRecord) -> Optional[str]:
    """Возвращает номер заголовка из метаданных абзаца или из узла дерева."""
    heading = record.paragraph.heading_info
    if heading is not None and heading.section_number_raw:
        return heading.section_number_raw
    return record.node.section_number


def _expected_number_depth(record: _HeadingRecord) -> Optional[int]:
    """Ожидаемая глубина номера заголовка: номер продолжает нумерацию родителя.

    Уровень нумерованного заголовка равен глубине его номера, поэтому сверять
    глубину номера с уровнем бессмысленно: уровень вычислен из нее же. Правило
    проверяет место номера в иерархии: у потомка глубина на единицу больше, чем
    у ближайшего пронумерованного предка. Так «3.1.1» внутри «3.1» корректен, а
    «3.1.1» сразу после главы «1» — нет.

    ``None`` — ожидание не определено: у заголовка нет родителя (номер верхнего
    уровня проверяет правило уровней) или родитель не нумерован (например,
    «ВВЕДЕНИЕ»), и тогда неизвестно, продолжением какой нумерации является номер.
    """
    parent = record.parent
    if parent is None:
        return None
    parent_numbers = _parse_section_number(parent.section_number)
    if parent_numbers is None:
        return None
    return len(parent_numbers) + 1


def _collect_heading_records(
    nodes: List[DocumentTreeNode],
    parent: Optional[DocumentTreeNode],
    target: RuleTarget,
    paragraphs: Dict[str, ParagraphBlock],
    collected: List[_HeadingRecord],
) -> List[_HeadingRecord]:
    """Обходит document_tree в порядке документа и отбирает заголовки под правило.

    Роли из exclude_roles убирают узел вместе с поддеревом (приложения, задание).
    Заголовки ниже min_heading_confidence пропускаются, а их дети поднимаются
    к ближайшему проверяемому родителю.
    """
    excluded_roles = set(target.exclude_roles)
    max_rank = CONFIDENCE_RANK.get(target.min_heading_confidence, 2)

    for node in nodes:
        if node.heading_block_id is None:
            # Синтетический узел («Преамбула»): его дети поднимаются к текущему родителю
            _collect_heading_records(node.children, parent, target, paragraphs, collected)
            continue
        if node.semantic_role in excluded_roles:
            continue
        paragraph = paragraphs.get(node.heading_block_id)
        heading = paragraph.heading_info if paragraph is not None else None
        if paragraph is None or heading is None:
            _collect_heading_records(node.children, parent, target, paragraphs, collected)
            continue
        if CONFIDENCE_RANK.get(heading.detection_confidence, 2) > max_rank:
            _collect_heading_records(node.children, parent, target, paragraphs, collected)
            continue

        collected.append(_HeadingRecord(node=node, paragraph=paragraph, parent=parent))
        _collect_heading_records(node.children, node, target, paragraphs, collected)

    return collected


def _section_matches(spec: SectionSpec, record: _HeadingRecord) -> bool:
    """Проверяет, описывает ли ожидание раздела данный заголовок."""
    if spec.role is not None and record.node.semantic_role != spec.role:
        return False
    if not spec.titles:
        return True
    heading = record.paragraph.heading_info
    title = heading.clean_title_text if heading is not None else record.node.title
    normalized = _normalize_heading_text(title)
    for token in spec.titles:
        if _title_matches(normalized, _normalize_heading_text(token)):
            return True
    return False


def _synonym_group(normalized_title: str) -> frozenset[str]:
    """Возвращает группу синонимов, в которой состоит нормализованное название."""
    for group in SECTION_TITLE_SYNONYMS:
        if normalized_title in group:
            return group
    return frozenset({normalized_title})


def _title_matches(normalized_actual: str, normalized_expected: str) -> bool:
    """Сравнивает названия разделов с учетом синонимов и уточнения в начале.

    «СПИСОК ИСТОЧНИКОВ» и «СПИСОК ЛИТЕРАТУРЫ» — одно и то же требование, а
    «СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ ПО ТЕМЕ РАБОТЫ» начинается с названия
    раздела, поэтому тоже подходит: проверяется каждое название из группы
    синонимов требования.
    """
    if not normalized_actual or not normalized_expected:
        return False
    for candidate in _synonym_group(normalized_expected):
        if normalized_actual == candidate or normalized_actual.startswith(candidate + " "):
            return True
    return False


def _collapse_text(text: str, limit: int = SNIPPET_LIMIT) -> str:
    """Сжимает пробелы и обрезает фрагмент текста для локации нарушения."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


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


def _format_measure(value: Optional[float], unit: str) -> str:
    """Форматирует число с единицей измерения в русской десятичной записи."""
    if value is None:
        return "не задано"
    rounded = round(float(value), 1)
    if abs(rounded - round(rounded)) < EPSILON:
        number = str(int(round(rounded)))
    else:
        number = f"{rounded:.1f}".replace(".", ",")
    return f"{number} {unit}"


def _iter_paragraphs(blocks: Iterable[BlockUnion]) -> List[ParagraphBlock]:
    """Собирает абзацы рекурсивно по таблицам, пропуская строки оглавления."""
    collected: List[ParagraphBlock] = []
    for block in blocks:
        if isinstance(block, ParagraphBlock):
            collected.append(block)
        elif isinstance(block, TableBlock):
            for row in block.rows:
                for cell in row.cells:
                    collected.extend(_iter_paragraphs(cell.blocks))
    return collected


def _iter_section_paragraphs(sections: Iterable[SectionProperties]) -> List[ParagraphBlock]:
    """Собирает абзацы колонтитулов всех секций документа."""
    collected: List[ParagraphBlock] = []
    for section in sections:
        for content in list(section.headers.values()) + list(section.footers.values()):
            collected.extend(_iter_paragraphs(content.blocks))
    return collected


def _caption_block_ids(document: ParsedDocxDocument) -> Set[str]:
    """Возвращает block_id всех подписей рисунков, таблиц и приложений."""
    index = document.cross_ref_index
    entities = [*index.figures, *index.tables, *index.Apps]
    return {entity.caption_block_id for entity in entities}


def _is_pdf_only(document: ParsedDocxDocument) -> bool:
    """True, если документ разобран только по PDF: стилей, pPr и numbering нет.

    Признак берется из матрицы возможностей, а не из имени файла: DOCX-конвейер
    всегда поднимает ``styles_parsed`` в True, PDF-парсер ставит False, потому
    что в PDF нет ни каскада стилей, ни ``w:sectPr``. По этому флагу правила
    отключают то, что физически не измерено, и расширяют допуск геометрии.
    """
    return not document.capabilities.styles_parsed


def _document_contexts(
    document: ParsedDocxDocument,
    caption_ids: Optional[Set[str]] = None,
) -> Dict[str, str]:
    """Считает контекст каждого блока документа для фильтра exclude_contexts.

    Импорт `context` ленивый: классификатор — отдельный слой поверх разбора, и
    движок не должен тянуть его при импорте пакета. Классификация выполняется
    один раз на документ, а не на каждое правило: региональный проход
    (префиксная зона титула, слияние листингов) зависит от соседних блоков.
    """
    from .context import classify_document_contexts

    if caption_ids is None:
        caption_ids = _caption_block_ids(document)
    return classify_document_contexts(
        document.blocks,
        caption_block_ids=caption_ids,
    )


def _is_symbolic_run(run: TextRun) -> bool:
    """Считает ран символическим: символьная гарнитура или только небуквенные знаки.

    Формулы, диапазоны и знаки (≤, ±, ×, →) Word набирает гарнитурой Symbol
    подстановкой или отдельным ран-ом из одних знаков. Кегль и начертание у них
    не осмысленны, поэтому правило шрифта такие раны не проверяет.
    """
    props = run.effective_props
    if _is_symbolic_family(props.font_ascii) or _is_symbolic_family(props.font_hansi):
        return True
    return not any(char.isalnum() for char in run.visible_text)


def _describe_bool(value: Optional[bool], enabled: str, disabled: str) -> str:
    """Переводит булево начертание в текст отчета."""
    if value is None:
        return "не задано"
    return enabled if value else disabled



def _compare_font(
    props: RunFontPropertiesEffective,
    expected: FontExpectation,
    check_family: bool = True,
    check_complex_script: Optional[bool] = None,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], List[str], List[str]]]:
    """Сравнивает шрифт рана с ожиданием и возвращает найденные расхождения.

    ``check_family=False`` полностью отключает сверку гарнитуры (вместе с
    complex script); ``check_complex_script=False`` отключает только сверку
    `font_cs`. Complex script отделен потому, что в ``pdf_only`` гарнитура
    сравнима по нормализованному имени, а `font_cs` в PDF не измеряется вовсе.
    Кегль и начертание проверяются всегда.
    """
    expected_values: Dict[str, Any] = {}
    found_values: Dict[str, Any] = {}
    expected_parts: List[str] = []
    found_parts: List[str] = []

    if check_family and expected.family is not None:
        if not _same_family(props.font_ascii, expected.family, expected.normalize_family):
            expected_values["family"] = expected.family
            found_values["family"] = props.font_ascii
            expected_parts.append(expected.family)
            found_parts.append(props.font_ascii or "не задано")
        wants_complex_script = (
            expected.check_complex_script and check_complex_script is not False
        )
        if wants_complex_script and not _same_family(
            props.font_cs, expected.family, expected.normalize_family
        ):
            expected_values["font_cs"] = expected.family
            found_values["font_cs"] = props.font_cs
            expected_parts.append(f"{expected.family} (complex script)")
            found_parts.append(f"{props.font_cs or 'не задано'} (complex script)")

    if expected.size_pt is not None:
        if abs(props.size_pt - expected.size_pt) > expected.size_tolerance_pt + EPSILON:
            expected_values["size_pt"] = float(expected.size_pt)
            found_values["size_pt"] = props.size_pt
            expected_parts.append(_format_measure(expected.size_pt, "pt"))
            found_parts.append(_format_measure(props.size_pt, "pt"))

    if expected.bold is not None and props.bold != expected.bold:
        expected_values["bold"] = expected.bold
        found_values["bold"] = props.bold
        expected_parts.append(_describe_bool(expected.bold, "полужирный", "обычный"))
        found_parts.append(_describe_bool(props.bold, "полужирный", "обычный"))

    if expected.italic is not None and props.italic != expected.italic:
        expected_values["italic"] = expected.italic
        found_values["italic"] = props.italic
        expected_parts.append(_describe_bool(expected.italic, "курсив", "прямой"))
        found_parts.append(_describe_bool(props.italic, "курсив", "прямой"))

    if not expected_values:
        return None
    return expected_values, found_values, expected_parts, found_parts


def _compare_measures(
    expected_values: Dict[str, Optional[float]],
    actual_values: Dict[str, Optional[float]],
    labels: Dict[str, str],
    tolerance: float,
    unit: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[str], List[str]]:
    """Сравнивает набор однотипных размеров и собирает расхождения.

    Поле с фактическим значением ``None`` не сравнивается: это «не измерено»
    (pdf_only без геометрии абзаца), а не «ноль». Придумывать нарушение из
    отсутствующего измерения нельзя.
    """
    expected_found: Dict[str, Any] = {}
    actual_found: Dict[str, Any] = {}
    expected_parts: List[str] = []
    found_parts: List[str] = []

    for field, expected_value in expected_values.items():
        if expected_value is None:
            continue
        actual_value = actual_values.get(field)
        if actual_value is None:
            continue
        if abs(float(actual_value) - float(expected_value)) <= tolerance + EPSILON:
            continue
        expected_found[field] = float(expected_value)
        actual_found[field] = float(actual_value)
        expected_parts.append(f"{labels[field]} {_format_measure(expected_value, unit)}")
        found_parts.append(f"{labels[field]} {_format_measure(actual_value, unit)}")

    return expected_found, actual_found, expected_parts, found_parts


def _paragraph_location(paragraph: ParagraphBlock) -> ViolationLocation:
    """Строит локацию нарушения по абзацу документа."""
    return ViolationLocation(
        block_id=paragraph.block_id,
        section_idx=paragraph.section_idx,
        page_start_est=paragraph.page_start_est,
        page_end_est=paragraph.page_end_est,
        container_type=paragraph.container_type,
        style_id=paragraph.style_id,
        snippet=_collapse_text(paragraph.text_visible),
    )


def _run_location(paragraph: ParagraphBlock, run: TextRun) -> ViolationLocation:
    """Строит локацию нарушения по конкретному рану абзаца."""
    location = _paragraph_location(paragraph)
    return location.model_copy(
        update={
            "run_id": run.run_id,
            "char_start": run.start_char,
            "char_end": run.end_char,
            "snippet": _collapse_text(run.visible_text) or location.snippet,
        }
    )


class RuleEngine:
    """Проверяет разобранный документ по набору правил и формирует отчет."""

    def __init__(self, ruleset: RuleSet) -> None:
        """Сохраняет набор правил для последующих проверок."""
        self.ruleset = ruleset

    @classmethod
    def from_json_file(cls, ruleset_path: str | Path) -> "RuleEngine":
        """Создает движок из JSON-файла набора правил."""
        return cls(load_ruleset(ruleset_path))

    def check(self, document: ParsedDocxDocument) -> RuleCheckReport:
        """Проверяет документ по всем правилам набора и возвращает отчет."""
        violations: List[Violation] = []
        objects_checked = 0
        caption_ids = _caption_block_ids(document)
        contexts = _document_contexts(document, caption_ids)

        for rule in self.ruleset.rules:
            if isinstance(rule, FontRule):
                rule_violations, checked = self._check_font_rule(rule, document, contexts)
            elif isinstance(rule, IndentRule):
                rule_violations, checked = self._check_indent_rule(rule, document, contexts)
            elif isinstance(rule, MarginRule):
                rule_violations, checked = self._check_margin_rule(rule, document)
            elif isinstance(rule, HeadingLevelsRule):
                rule_violations, checked = self._check_heading_levels_rule(rule, document)
            elif isinstance(rule, HeadingNumberingRule):
                rule_violations, checked = self._check_heading_numbering_rule(rule, document)
            elif isinstance(rule, HeadingFormatRule):
                rule_violations, checked = self._check_heading_format_rule(rule, document)
            elif isinstance(rule, RequiredSectionsRule):
                rule_violations, checked = self._check_required_sections_rule(rule, document)
            elif isinstance(rule, TocRule):
                rule_violations, checked = self._check_toc_rule(rule, document)
            else:
                continue
            violations.extend(rule_violations)
            objects_checked += checked

        meta = document.meta
        total_pages = document.sections[-1].page_end_est if document.sections else 0
        return RuleCheckReport(
            ruleset_id=self.ruleset.ruleset_id,
            ruleset_version=self.ruleset.ruleset_version,
            parser_schema_version=document.schema_version,
            document=DocumentRef(
                filename=meta.filename,
                file_sha256=meta.file_sha256,
                source_format=meta.source_format,
                pagination_source=meta.pagination_source,
                total_pages_est=total_pages,
            ),
            summary=build_summary(violations, len(self.ruleset.rules), objects_checked),
            violations=violations,
            passed=not any(violation.severity == "error" for violation in violations),
        )

    def check_json_file(self, document_json_path: str | Path) -> RuleCheckReport:
        """Проверяет документ, сохраненный ранее в JSON парсера."""
        raw_text = Path(document_json_path).read_text(encoding="utf-8")
        return self.check(ParsedDocxDocument.model_validate(json.loads(raw_text)))

    def _paragraph_targets(
        self,
        document: ParsedDocxDocument,
        target: RuleTarget,
        contexts: Dict[str, str],
    ) -> List[ParagraphBlock]:
        """Собирает абзацы выбранной области с учетом флагов include_*."""
        if target.scope in {"body_text", "body_paragraphs"}:
            paragraphs = [block for block in document.blocks if isinstance(block, ParagraphBlock)]
        elif target.scope == "headings":
            paragraphs = [record.paragraph for record in self._heading_records(document, target)]
        elif target.scope == "table_cells":
            paragraphs = [
                paragraph
                for paragraph in _iter_paragraphs(document.blocks)
                if paragraph.container_type == "table_cell"
            ]
        elif target.scope == "headers_footers":
            paragraphs = _iter_section_paragraphs(document.sections)
        elif target.scope == "footnotes":
            paragraphs = [
                paragraph
                for footnote in document.footnotes
                for paragraph in _iter_paragraphs(footnote.blocks)
            ]
        else:
            paragraphs = (
                _iter_paragraphs(document.blocks)
                + _iter_section_paragraphs(document.sections)
                + [
                    paragraph
                    for footnote in document.footnotes
                    for paragraph in _iter_paragraphs(footnote.blocks)
                ]
            )

        caption_ids = _caption_block_ids(document)
        return [
            paragraph
            for paragraph in paragraphs
            if self._is_target_paragraph(paragraph, target, caption_ids, contexts)
        ]

    def _is_target_paragraph(
        self,
        paragraph: ParagraphBlock,
        target: RuleTarget,
        caption_ids: Set[str],
        contexts: Dict[str, str],
    ) -> bool:
        """Проверяет, попадает ли абзац в выборку правила.

        Контекст (титульный лист, листинг) отсекается только в абзацных областях
        основного текста: заголовки, таблицы, сноски и оглавление идут по своим
        правилам и контекстным фильтром не затрагиваются.
        """
        if target.style_ids and (paragraph.style_id or "") not in target.style_ids:
            return False
        if (
            target.exclude_contexts
            and target.scope in {"body_text", "body_paragraphs"}
            and contexts.get(paragraph.block_id) in target.exclude_contexts
        ):
            return False
        if target.scope != "body_text":
            return True
        if not target.include_headings and paragraph.heading_info is not None:
            return False
        if not target.include_captions and paragraph.block_id in caption_ids:
            return False
        if not target.include_list_items:
            if paragraph.list_info is not None or paragraph.effective_props.num_id is not None:
                return False
        if not target.include_empty:
            if paragraph.is_empty_or_whitespace or paragraph.contains_only_visuals_or_math:
                return False
        return True

    def _check_font_rule(
        self,
        rule: FontRule,
        document: ParsedDocxDocument,
        contexts: Dict[str, str],
    ) -> Tuple[List[Violation], int]:
        """Сравнивает эффективный шрифт видимых ранов с ожиданием правила.

        Гарнитура сверяется в обоих режимах. В ``pdf_only`` имя приходит из PDF
        как запись шрифта (``TimesNewRoman``, ``ABCDEF+TimesNewRoman-BoldMT``),
        поэтому сравнение идет по нормализованному имени: запись шрифта не дает
        шума, а настоящая другая гарнитура (``LiberationSans``,
        ``NimbusRomNo9L``) — нарушение. Complex script (``font_cs``) в PDF не
        измеряется и не сверяется; кегль и начертание проверяются всегда.
        """
        violations: List[Violation] = []
        targets = self._paragraph_targets(document, rule.target, contexts)
        pdf_only = _is_pdf_only(document)

        for paragraph in targets:
            mismatched = []
            for run in paragraph.runs:
                if not run.visible_text.strip() or run.in_field:
                    continue
                if run.effective_props.vanish_hidden:
                    continue
                if _is_symbolic_run(run):
                    continue
                diff = _compare_font(
                    run.effective_props,
                    rule.expected,
                    check_family=True,
                    check_complex_script=not pdf_only,
                )
                if diff is not None:
                    mismatched.append((run, diff))

            if not mismatched:
                continue

            run, (expected_values, found_values, expected_parts, found_parts) = mismatched[0]
            violations.append(
                Violation(
                    violation_id=f"{rule.rule_id}:{paragraph.block_id}",
                    rule_id=rule.rule_id,
                    kind=rule.kind,
                    severity=rule.severity,
                    message=rule.message,
                    requirement_ref=rule.requirement_ref,
                    location=_run_location(paragraph, run),
                    expected=expected_values,
                    found=found_values,
                    expected_text=", ".join(expected_parts),
                    found_text=", ".join(found_parts),
                    occurrences=len(mismatched),
                )
            )

        return violations, len(targets)

    def _check_indent_rule(
        self,
        rule: IndentRule,
        document: ParsedDocxDocument,
        contexts: Dict[str, str],
    ) -> Tuple[List[Violation], int]:
        """Сравнивает эффективные отступы абзацев с ожиданием правила.

        В ``pdf_only`` отступы абзаца не проверяются, хотя измерение есть: парсер
        считает красную строку как сдвиг первой строки относительно второй. Как
        источник требования отступ ненадежен — маркер списка или выключка сдвигают
        первую строку и дают ложные +20 мм и больше, — поэтому правило не создает
        нарушений и не увеличивает счетчик проверенных объектов. Измеренные
        значения остаются в разборе (`ParagraphPropertiesEffective`).
        """
        if _is_pdf_only(document):
            return [], 0

        violations: List[Violation] = []
        targets = self._paragraph_targets(document, rule.target, contexts)

        for paragraph in targets:
            props = paragraph.effective_props
            expected_values, found_values, expected_parts, found_parts = _compare_measures(
                expected_values={
                    "first_line_mm": rule.expected.first_line_mm,
                    "left_mm": rule.expected.left_mm,
                    "right_mm": rule.expected.right_mm,
                },
                actual_values={
                    "first_line_mm": props.indent_first_line_mm,
                    "left_mm": props.indent_left_mm,
                    "right_mm": props.indent_right_mm,
                },
                labels=INDENT_LABELS,
                tolerance=rule.expected.tolerance_mm,
                unit="мм",
            )
            if not expected_values:
                continue

            found_values["first_line_source"] = props.indent_first_line_source
            violations.append(
                Violation(
                    violation_id=f"{rule.rule_id}:{paragraph.block_id}",
                    rule_id=rule.rule_id,
                    kind=rule.kind,
                    severity=rule.severity,
                    message=rule.message,
                    requirement_ref=rule.requirement_ref,
                    location=_paragraph_location(paragraph),
                    expected=expected_values,
                    found=found_values,
                    expected_text=", ".join(expected_parts),
                    found_text=", ".join(found_parts),
                )
            )

        return violations, len(targets)

    def _check_margin_rule(
        self,
        rule: MarginRule,
        document: ParsedDocxDocument,
    ) -> Tuple[List[Violation], int]:
        """Сравнивает поля страниц каждой секции с ожиданием правила.

        В pdf_only поля измерены по геометрии текста, поэтому допуск правила
        расширяется до ``PDF_ONLY_MARGIN_TOLERANCE_MM`` (±1.5 мм): сдвиг 1.5 мм —
        погрешность оценки, а не нарушение. Поле 13.7 мм при ожидаемых 15 мм
        (отклонение 1.3 мм) в pdf_only попадает в допуск и нарушением не
        является; нарушением остается расхождение больше допуска (например,
        нижнее поле 26.5 мм при ожидаемых 20 мм).
        """
        violations: List[Violation] = []
        sections = document.sections
        tolerance = rule.expected.tolerance_mm
        if _is_pdf_only(document):
            tolerance = max(tolerance, PDF_ONLY_MARGIN_TOLERANCE_MM)

        for section in sections:
            margins = section.margins
            expected_values, found_values, expected_parts, found_parts = _compare_measures(
                expected_values={
                    "left_mm": rule.expected.left_mm,
                    "right_mm": rule.expected.right_mm,
                    "top_mm": rule.expected.top_mm,
                    "bottom_mm": rule.expected.bottom_mm,
                    "gutter_mm": rule.expected.gutter_mm,
                },
                actual_values={
                    "left_mm": margins.left_mm,
                    "right_mm": margins.right_mm,
                    "top_mm": margins.top_mm,
                    "bottom_mm": margins.bottom_mm,
                    "gutter_mm": margins.gutter_mm,
                },
                labels=MARGIN_LABELS,
                tolerance=tolerance,
                unit="мм",
            )
            if not expected_values:
                continue

            violations.append(
                Violation(
                    violation_id=f"{rule.rule_id}:section:{section.section_idx}",
                    rule_id=rule.rule_id,
                    kind=rule.kind,
                    severity=rule.severity,
                    message=rule.message,
                    requirement_ref=rule.requirement_ref,
                    location=ViolationLocation(
                        section_idx=section.section_idx,
                        page_start_est=section.page_start_est,
                        page_end_est=section.page_end_est,
                        snippet=(
                            f"секция {section.section_idx + 1}, "
                            f"страницы {section.page_start_est}–{section.page_end_est}, "
                            f"{section.orientation}"
                        ),
                    ),
                    expected=expected_values,
                    found=found_values,
                    expected_text=", ".join(expected_parts),
                    found_text=", ".join(found_parts),
                )
            )

        return violations, len(sections)

    def _heading_records(
        self,
        document: ParsedDocxDocument,
        target: RuleTarget,
    ) -> List[_HeadingRecord]:
        """Возвращает заголовки документа в порядке следования с учетом фильтров правила."""
        paragraphs = {
            block.block_id: block
            for block in document.blocks
            if isinstance(block, ParagraphBlock)
        }
        records = _collect_heading_records(
            document.document_tree, None, target, paragraphs, []
        )
        if target.style_ids:
            records = [
                record
                for record in records
                if (record.paragraph.style_id or "") in target.style_ids
            ]
        return records

    @staticmethod
    def _toc_blocks(document: ParsedDocxDocument) -> List[TocContainerBlock]:
        """Возвращает блоки оглавления в порядке следования."""
        return [block for block in document.blocks if isinstance(block, TocContainerBlock)]

    @staticmethod
    def _heading_violation(
        rule: Rule,
        record: _HeadingRecord,
        expected_values: Dict[str, Any],
        found_values: Dict[str, Any],
        expected_parts: List[str],
        found_parts: List[str],
    ) -> Violation:
        """Формирует нарушение, привязанное к абзацу заголовка."""
        return Violation(
            violation_id=f"{rule.rule_id}:{record.paragraph.block_id}",
            rule_id=rule.rule_id,
            kind=rule.kind,
            severity=rule.severity,
            message=rule.message,
            requirement_ref=rule.requirement_ref,
            location=_paragraph_location(record.paragraph),
            expected=expected_values,
            found=found_values,
            expected_text=", ".join(expected_parts),
            found_text=", ".join(found_parts),
        )

    @staticmethod
    def _document_violation(
        rule: Rule,
        violation_id: str,
        expected_values: Dict[str, Any],
        found_values: Dict[str, Any],
        expected_text: str,
        found_text: str,
    ) -> Violation:
        """Формирует нарушение уровня документа без привязки к абзацу."""
        return Violation(
            violation_id=violation_id,
            rule_id=rule.rule_id,
            kind=rule.kind,
            severity=rule.severity,
            message=rule.message,
            requirement_ref=rule.requirement_ref,
            location=ViolationLocation(snippet="структура документа"),
            expected=expected_values,
            found=found_values,
            expected_text=expected_text,
            found_text=found_text,
        )


    def _check_heading_levels_rule(
        self,
        rule: HeadingLevelsRule,
        document: ParsedDocxDocument,
    ) -> Tuple[List[Violation], int]:
        """Проверяет, что уровни заголовков растут без пропусков и не глубже лимита."""
        records = self._heading_records(document, rule.target)
        expected = rule.expected
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
                self._heading_violation(
                    rule, record, expected_values, found_values, expected_parts, found_parts
                )
            )

        return violations, len(records)


    def _check_heading_numbering_rule(
        self,
        rule: HeadingNumberingRule,
        document: ParsedDocxDocument,
    ) -> Tuple[List[Violation], int]:
        """Проверяет последовательность, родителя, глубину и запись номеров заголовков."""
        records = self._heading_records(document, rule.target)
        expected = rule.expected
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
                        self._heading_violation(
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

            if not expected_values:
                continue
            violations.append(
                self._heading_violation(
                    rule, record, expected_values, found_values, expected_parts, found_parts
                )
            )

        return violations, len(records)


    def _check_heading_format_rule(
        self,
        rule: HeadingFormatRule,
        document: ParsedDocxDocument,
    ) -> Tuple[List[Violation], int]:
        """Проверяет точку в конце заголовка, регистр и выравнивание."""
        records = self._heading_records(document, rule.target)
        expected = rule.expected
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

            if not expected_values:
                continue
            violations.append(
                self._heading_violation(
                    rule, record, expected_values, found_values, expected_parts, found_parts
                )
            )

        return violations, len(records)


    def _check_required_sections_rule(
        self,
        rule: RequiredSectionsRule,
        document: ParsedDocxDocument,
    ) -> Tuple[List[Violation], int]:
        """Проверяет наличие и порядок обязательных разделов документа."""
        records = self._heading_records(document, rule.target)
        violations: List[Violation] = []
        position = -1
        previous_label: Optional[str] = None

        for spec in rule.expected.sections:
            matches = [
                index
                for index, record in enumerate(records)
                if _section_matches(spec, record)
            ]
            if not matches:
                if spec.required:
                    violations.append(
                        self._document_violation(
                            rule,
                            f"{rule.rule_id}:section:{spec.label}",
                            {"section": spec.label, "present": True},
                            {"section": spec.label, "present": False},
                            f"раздел «{spec.label}» присутствует",
                            f"раздел «{spec.label}» не найден",
                        )
                    )
                continue

            following = [index for index in matches if index > position]
            if not following:
                record = records[matches[0]]
                expected_text = (
                    f"раздел «{spec.label}» после «{previous_label}»"
                    if previous_label
                    else f"раздел «{spec.label}»"
                )
                violations.append(
                    self._heading_violation(
                        rule,
                        record,
                        {"section": spec.label, "after": previous_label},
                        {"section": spec.label, "position": matches[0]},
                        [expected_text],
                        [f"раздел «{spec.label}» расположен выше"],
                    )
                )
                continue

            position = following[0]
            previous_label = spec.label

        return violations, len(rule.expected.sections)


    def _check_toc_rule(
        self,
        rule: TocRule,
        document: ParsedDocxDocument,
    ) -> Tuple[List[Violation], int]:
        """Проверяет оглавление и сверяет его записи с заголовками документа."""
        expected = rule.expected
        tocs = self._toc_blocks(document)
        violations: List[Violation] = []

        if not tocs:
            if expected.require_toc:
                violations.append(
                    self._document_violation(
                        rule,
                        f"{rule.rule_id}:toc",
                        {"toc": True},
                        {"toc": False},
                        "оглавление присутствует",
                        "оглавление не найдено",
                    )
                )
            return violations, 0

        headings = self._heading_records(document, rule.target)
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

        for toc in tocs:
            checked += len(toc.entries)
            if expected.require_automated and not toc.is_automated_field_or_sdt:
                violations.append(
                    Violation(
                        violation_id=f"{rule.rule_id}:toc:{toc.block_id}:automated",
                        rule_id=rule.rule_id,
                        kind=rule.kind,
                        severity=rule.severity,
                        message=rule.message,
                        requirement_ref=rule.requirement_ref,
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

                if not expected_values:
                    continue
                violations.append(
                    Violation(
                        violation_id=(
                            f"{rule.rule_id}:toc:{toc.block_id}:entry{entry.entry_index}"
                        ),
                        rule_id=rule.rule_id,
                        kind=rule.kind,
                        severity=rule.severity,
                        message=rule.message,
                        requirement_ref=rule.requirement_ref,
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
                        Violation(
                            violation_id=f"{rule.rule_id}:toc:missing:{block_id}",
                            rule_id=rule.rule_id,
                            kind=rule.kind,
                            severity=rule.severity,
                            message=rule.message,
                            requirement_ref=rule.requirement_ref,
                            location=_paragraph_location(record.paragraph),
                            expected={"toc_entry": True, "section": spec.label},
                            found={"toc_entry": False, "section": spec.label},
                            expected_text=f"раздел «{spec.label}» включен в оглавление",
                            found_text=f"раздел «{spec.label}» не найден в оглавлении",
                        )
                    )

        return violations, checked


