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
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from docx_parser.schema import (
    BlockUnion,
    ParagraphBlock,
    ParsedDocxDocument,
    RunFontPropertiesEffective,
    SectionProperties,
    TableBlock,
    TextRun,
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
    IndentRule,
    MarginRule,
    RuleSet,
    RuleTarget,
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

SNIPPET_LIMIT = 80
EPSILON = 1e-9


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
