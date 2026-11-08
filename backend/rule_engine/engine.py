"""Движок проверки документа по декларативным правилам нормоконтроля.

Движок не разбирает OOXML заново: он берет ParsedDocxDocument, где каскад
стилей уже разрешен, а единицы приведены к пунктам и миллиметрам. Каждая
группа правил (шрифты, отступы, поля) сравнивает эффективные значения с
ожидаемыми и формирует нарушение с адресом, ожиданием и фактом.

Проверяются только объекты, которые правило действительно описывает: по
умолчанию из выборки исключены заголовки, подписи рисунков и таблиц, элементы
списков, пустые абзацы и строки оглавления. Это позволяет применять правило
«основной текст» без ручных исключений в наборе правил.
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
)

from .findings import (
    DocumentRef,
    RuleCheckReport,
    Violation,
    ViolationLocation,
    build_summary,
)
from .rules import (
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

SNIPPET_LIMIT = 80
EPSILON = 1e-9


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
        normalized_token = _normalize_heading_text(token)
        if normalized == normalized_token or normalized.startswith(normalized_token + " "):
            return True
    return False


def _collapse_text(text: str, limit: int = SNIPPET_LIMIT) -> str:
    """Сжимает пробелы и обрезает фрагмент текста для локации нарушения."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


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


def _same_family(actual: str, expected: str) -> bool:
    """Сравнивает гарнитуры без учета регистра и лишних пробелов."""
    return " ".join(actual.split()).casefold() == " ".join(expected.split()).casefold()


def _describe_bool(value: Optional[bool], enabled: str, disabled: str) -> str:
    """Переводит булево начертание в текст отчета."""
    if value is None:
        return "не задано"
    return enabled if value else disabled



def _compare_font(
    props: RunFontPropertiesEffective,
    expected: FontExpectation,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], List[str], List[str]]]:
    """Сравнивает шрифт рана с ожиданием и возвращает найденные расхождения."""
    expected_values: Dict[str, Any] = {}
    found_values: Dict[str, Any] = {}
    expected_parts: List[str] = []
    found_parts: List[str] = []

    if expected.family is not None:
        if not _same_family(props.font_ascii, expected.family):
            expected_values["family"] = expected.family
            found_values["family"] = props.font_ascii
            expected_parts.append(expected.family)
            found_parts.append(props.font_ascii or "не задано")
        if expected.check_complex_script and not _same_family(props.font_cs, expected.family):
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
    actual_values: Dict[str, float],
    labels: Dict[str, str],
    tolerance: float,
    unit: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[str], List[str]]:
    """Сравнивает набор однотипных размеров и собирает расхождения."""
    expected_found: Dict[str, Any] = {}
    actual_found: Dict[str, Any] = {}
    expected_parts: List[str] = []
    found_parts: List[str] = []

    for field, expected_value in expected_values.items():
        if expected_value is None:
            continue
        actual_value = float(actual_values[field])
        if abs(actual_value - float(expected_value)) <= tolerance + EPSILON:
            continue
        expected_found[field] = float(expected_value)
        actual_found[field] = actual_value
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

        for rule in self.ruleset.rules:
            if isinstance(rule, FontRule):
                rule_violations, checked = self._check_font_rule(rule, document)
            elif isinstance(rule, IndentRule):
                rule_violations, checked = self._check_indent_rule(rule, document)
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
            if self._is_target_paragraph(paragraph, target, caption_ids)
        ]

    def _is_target_paragraph(
        self,
        paragraph: ParagraphBlock,
        target: RuleTarget,
        caption_ids: Set[str],
    ) -> bool:
        """Проверяет, попадает ли абзац в выборку правила."""
        if target.style_ids and (paragraph.style_id or "") not in target.style_ids:
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
    ) -> Tuple[List[Violation], int]:
        """Сравнивает эффективный шрифт видимых ранов с ожиданием правила."""
        violations: List[Violation] = []
        targets = self._paragraph_targets(document, rule.target)

        for paragraph in targets:
            mismatched = []
            for run in paragraph.runs:
                if not run.visible_text.strip() or run.in_field:
                    continue
                if run.effective_props.vanish_hidden:
                    continue
                diff = _compare_font(run.effective_props, rule.expected)
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
    ) -> Tuple[List[Violation], int]:
        """Сравнивает эффективные отступы абзацев с ожиданием правила."""
        violations: List[Violation] = []
        targets = self._paragraph_targets(document, rule.target)

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
        """Сравнивает поля страниц каждой секции с ожиданием правила."""
        violations: List[Violation] = []
        sections = document.sections

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
                tolerance=rule.expected.tolerance_mm,
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
                if record.node.semantic_role in expected.require_number_for_roles:
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

            if expected.check_depth_matches_level and len(numbers) != record.node.level:
                expected_values["number_depth"] = record.node.level
                found_values["number_depth"] = len(numbers)
                found_values["section_number"] = _format_section_number(numbers)
                expected_parts.append(f"номер глубины {record.node.level}")
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
                    if entry.matched_heading_block_id is None:
                        expected_values["matched_heading"] = True
                        found_values["matched_heading"] = False
                        expected_parts.append("совпадение с заголовком документа")
                        found_parts.append("заголовок не найден")
                    elif entry.is_title_exact_match is False:
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

                if expected.max_depth is not None and entry.indent_level > expected.max_depth:
                    expected_values["indent_level"] = expected.max_depth
                    found_values["indent_level"] = entry.indent_level
                    expected_parts.append(f"уровень записи не глубже {expected.max_depth}")
                    found_parts.append(f"уровень записи {entry.indent_level}")

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

