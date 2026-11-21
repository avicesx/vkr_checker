"""Семейство основного текста: шрифт, абзацный отступ, выравнивание."""
from __future__ import annotations

from typing import Dict, List, Set, Tuple

from ..findings import (
    RuleAssessment,
    Violation,
)
from ..rules import (
    AlignmentRule,
    FontRule,
    IndentRule,
)
from .builders import (
    _build_assessment,
    _build_violation,
    _counted_assessment,
    _paragraph_location,
    _run_location,
)
from .captions import _caption_entities
from .common import (
    _alignment_matches,
    _compare_font,
    _compare_measures,
    _is_pdf_only,
    _is_symbolic_run,
    _paragraph_index,
    _paragraph_targets,
)
from .constants import (
    ALIGNMENT_LABELS,
    INDENT_LABELS,
)
from parser.core.schema import (
    ParagraphBlock,
    ParsedDocxDocument,
)


def _check_font_rule(
    rule: FontRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Сравнивает эффективный шрифт видимых ранов с ожиданием правила.

    Гарнитура сверяется в обоих режимах. В ``pdf_only`` имя приходит из PDF
    как запись шрифта (``TimesNewRoman``, ``ABCDEF+TimesNewRoman-BoldMT``),
    поэтому сравнение идет по нормализованному имени: запись шрифта не дает
    шума, а настоящая другая гарнитура (``LiberationSans``,
    ``NimbusRomNo9L``) — нарушение. Complex script (``font_cs``) в PDF не
    измеряется и не сверяется; кегль и начертание проверяются всегда.
    """
    violations: List[Violation] = []
    targets = _paragraph_targets(document, rule.target, contexts)
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
            _build_violation(
                rule,
                address=paragraph.block_id,
                location=_run_location(paragraph, run),
                expected=expected_values,
                found=found_values,
                expected_text=", ".join(expected_parts),
                found_text=", ".join(found_parts),
                occurrences=len(mismatched),
            )
        )

    assessment = _counted_assessment(
        rule,
        violations,
        len(targets),
        expected_text=rule.message,
        confidence="medium" if pdf_only else "high",
        empty_observed="в области действия правила нет абзацев",
        details={"scope": rule.target.scope, "pdf_only": pdf_only},
    )
    return violations, len(targets), assessment


def _check_indent_rule(
    rule: IndentRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Сравнивает эффективные отступы абзацев с ожиданием правила.

    В ``pdf_only`` отступы абзаца не проверяются, хотя измерение есть: парсер
    считает красную строку как сдвиг первой строки относительно второй. Как
    источник требования отступ ненадежен — маркер списка или выключка сдвигают
    первую строку и дают ложные +20 мм и больше, — поэтому правило не создает
    нарушений и не увеличивает счетчик проверенных объектов. Измеренные
    значения остаются в разборе (`ParagraphPropertiesEffective`).
    """
    if _is_pdf_only(document):
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=rule.message,
                observed_text=(
                    "отступы абзацев в pdf_only не оцениваются: "
                    "источник измерения ненадежен"
                ),
                confidence="low",
                details={"objects_checked": 0, "violations": 0, "source": "pdf_only"},
                note=(
                    "Красная строка в PDF — сдвиг первой строки относительно второй; "
                    "маркер списка или выключка дают ложные отклонения"
                ),
            ),
        )

    violations: List[Violation] = []
    targets = _paragraph_targets(document, rule.target, contexts)

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
            _build_violation(
                rule,
                address=paragraph.block_id,
                location=_paragraph_location(paragraph),
                expected=expected_values,
                found=found_values,
                expected_text=", ".join(expected_parts),
                found_text=", ".join(found_parts),
            )
        )

    assessment = _counted_assessment(
        rule,
        violations,
        len(targets),
        expected_text=rule.message,
        confidence="high",
        empty_observed="в области действия правила нет абзацев",
        details={"scope": rule.target.scope, "tolerance_mm": rule.expected.tolerance_mm},
    )
    return violations, len(targets), assessment


def _check_alignment_rule(
    rule: AlignmentRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Сверяет выравнивание абзацев выбранной области с ожиданием.

    Область ``captions`` берет абзацы подписей по ``caption_block_id``,
    ``table_cells`` — абзацы внутри ячеек таблиц, ``body_text`` — абзацы
    основного текста. Если выравнивание не измерено (``None`` в старом JSON
    или абзац подписи отсутствует в разборе), объект пропускается с
    причиной, а не считается нарушением: как у правила шрифта, отсутствие
    измерения не превращается в вердикт.
    """
    expected_alignment = rule.expected.alignment
    expected_text = f"выравнивание {ALIGNMENT_LABELS[expected_alignment]}"
    measured: List[ParagraphBlock] = []
    unmeasured = 0
    if rule.target.scope == "captions":
        blocks = _paragraph_index(document)
        seen_blocks: Set[str] = set()
        for entity in _caption_entities(document, "any"):
            if entity.caption_block_id in seen_blocks:
                continue
            seen_blocks.add(entity.caption_block_id)
            block = blocks.get(entity.caption_block_id)
            if block is None:
                unmeasured += 1
                continue
            measured.append(block)
    else:
        measured = _paragraph_targets(document, rule.target, contexts)

    violations: List[Violation] = []
    checked = 0
    for paragraph in measured:
        actual_alignment = paragraph.effective_props.alignment
        if actual_alignment is None:
            # Защита от старого JSON: поле «не измерено», а не «по левому краю».
            unmeasured += 1
            continue
        checked += 1
        if _alignment_matches(expected_alignment, actual_alignment):
            continue
        violations.append(
            _build_violation(
                rule,
                address=paragraph.block_id,
                location=_paragraph_location(paragraph),
                expected={"alignment": expected_alignment},
                found={
                    "alignment": actual_alignment,
                    "alignment_source": paragraph.effective_props.alignment_source,
                },
                expected_text=expected_text,
                found_text=(
                    "выравнивание "
                    f"{ALIGNMENT_LABELS.get(actual_alignment, actual_alignment)}"
                ),
            )
        )

    details = {
        "scope": rule.target.scope,
        "objects_considered": len(measured),
        "checked": checked,
        "skipped_unmeasured": unmeasured,
        "mismatches_total": len(violations),
    }
    if checked == 0:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    "выравнивание не измерено ни для одного объекта области "
                    f"'{rule.target.scope}': пропущено {unmeasured}"
                ),
                confidence="low",
                details=details,
            ),
        )
    status = "fail" if violations else "pass"
    observed_text = (
        f"не соответствует требованию: {len(violations)} из {checked} объектов"
        if violations
        else (
            f"все {checked} объектов выровнены "
            f"{ALIGNMENT_LABELS[expected_alignment]}"
        )
    )
    return (
        violations,
        checked,
        _build_assessment(
            rule,
            status,
            expected_text=expected_text,
            observed_text=observed_text,
            confidence="high",
            details=details,
        ),
    )
