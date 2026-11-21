"""Общие помощники семейств: разбор заголовков, выборка абзацев по области
правила, форматирование ожиданий, сравнение шрифтов и мер."""
from __future__ import annotations

from dataclasses import dataclass

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .._fonts import (
    is_symbolic_family as _is_symbolic_family,
    same_family as _same_family,
)
from ..findings import (
    Violation,
    ViolationLocation,
)
from ..rules import (
    FontExpectation,
    Rule,
    RuleTarget,
    SectionSpec,
)
from .builders import (
    _build_violation,
    _paragraph_location,
)
from .constants import (
    CONFIDENCE_RANK,
    EPSILON,
    RE_SECTION_NUMBER,
    SECTION_TITLE_SYNONYMS,
)
from parser.core.schema import (
    BlockUnion,
    DocumentTreeNode,
    ParagraphBlock,
    ParsedDocxDocument,
    RunFontPropertiesEffective,
    SectionProperties,
    TableBlock,
    TextCrossReferenceMention,
    TextRun,
)


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


def _format_number(value: float, digits: int = 1) -> str:
    """Форматирует число с русской запятой и без лишних нулей."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _format_ratio(value: Optional[float]) -> str:
    """Форматирует множитель межстрочного интервала."""
    if value is None:
        return "не определен"
    return _format_number(float(value), digits=2)


def _median(values: Iterable[float]) -> Optional[float]:
    """Считает медиану набора чисел, не меняя порядок исходных данных."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


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


def _safe_page_est(value: Optional[int]) -> Optional[int]:
    """Приводит номер страницы к допустимому в модели значению.

    ``ViolationLocation.page_start_est`` требует номер не меньше единицы, а
    парсер в старом JSON мог оставить 0, поэтому ноль и отрицательные значения
    превращаются в None («страница неизвестна»).
    """
    if value is None or value < 1:
        return None
    return int(value)


def _paragraph_index(document: ParsedDocxDocument) -> Dict[str, ParagraphBlock]:
    """Индексирует абзацы документа по block_id: тело, таблицы, колонтитулы, сноски.

    Индекс нужен правилам подписей: сущность ссылается на абзац по
    ``caption_block_id``, а сам абзац лежит в общем списке блоков.
    """
    paragraphs = (
        _iter_paragraphs(document.blocks)
        + _iter_section_paragraphs(document.sections)
        + [
            paragraph
            for footnote in document.footnotes
            for paragraph in _iter_paragraphs(footnote.blocks)
        ]
    )
    index: Dict[str, ParagraphBlock] = {}
    for paragraph in paragraphs:
        index.setdefault(paragraph.block_id, paragraph)
    return index


def _is_uppercase_text(text: Optional[str]) -> Optional[bool]:
    """True, если все буквы текста прописные; None — если букв нет или текста нет."""
    if text is None:
        return None
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return None
    return all(char.isupper() for char in letters)


def _alignment_matches(expected: str, actual: Optional[str]) -> bool:
    """Сверяет ожидаемое выравнивание с фактическим.

    ``justify`` — требование «по ширине»: разбор DOCX/PDF записывает его как
    ``both`` (двустороннее), а вариант с разрядкой — как ``distribute``.
    """
    if actual is None:
        return False
    if expected == actual:
        return True
    return expected == "justify" and actual in {"both", "distribute"}


def _mention_targets_text(mention: TextCrossReferenceMention) -> str:
    """Перечисляет номера источников, на которые ссылается упоминание."""
    return ", ".join(mention.extracted_targets) or mention.raw_snippet


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
    from ..context import classify_document_contexts

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


def _paragraph_targets(
    document: ParsedDocxDocument,
    target: RuleTarget,
    contexts: Dict[str, str],
) -> List[ParagraphBlock]:
    """Собирает абзацы выбранной области с учетом флагов include_*."""
    if target.scope in {"body_text", "body_paragraphs"}:
        paragraphs = [block for block in document.blocks if isinstance(block, ParagraphBlock)]
    elif target.scope == "headings":
        paragraphs = [record.paragraph for record in _heading_records(document, target)]
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
        if _is_target_paragraph(paragraph, target, caption_ids, contexts)
    ]


def _is_target_paragraph(
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


def _heading_records(
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


def _heading_violation(
    rule: Rule,
    record: _HeadingRecord,
    expected_values: Dict[str, Any],
    found_values: Dict[str, Any],
    expected_parts: List[str],
    found_parts: List[str],
) -> Violation:
    """Формирует нарушение, привязанное к абзацу заголовка."""
    return _build_violation(
        rule,
        address=record.paragraph.block_id,
        location=_paragraph_location(record.paragraph),
        expected=expected_values,
        found=found_values,
        expected_text=", ".join(expected_parts),
        found_text=", ".join(found_parts),
    )


def _document_violation(
    rule: Rule,
    address: str,
    expected_values: Dict[str, Any],
    found_values: Dict[str, Any],
    expected_text: str,
    found_text: str,
) -> Violation:
    """Формирует нарушение уровня документа без привязки к абзацу."""
    return _build_violation(
        rule,
        address=address,
        location=ViolationLocation(snippet="структура документа"),
        expected=expected_values,
        found=found_values,
        expected_text=expected_text,
        found_text=found_text,
    )
