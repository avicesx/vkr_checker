"""Семейство основного текста: шрифт, абзацный отступ, выравнивание."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

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
                rule.params,
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


RE_DEFINITION_ITEM = re.compile(
    r"^(?=[^\s]*[A-Za-zА-ЯЁа-яё])[A-Za-zА-ЯЁа-яё0-9]{1,12}\s+[-–—]\s+\S"
)
"""Строка списка определений: короткий термин, тире и определение.

Термин — одно слово до 12 знаков (``БД``, ``DI``, ``СУБД``, ``WPF``), затем
пробел, тире и текст определения. Проверяется только форма строки; решение
принимает ``_definition_list_block_ids`` по паре с нулевым отступом.
"""


def _definition_list_block_ids(targets: List[ParagraphBlock]) -> Set[str]:
    """Находит абзацы списка определений, которые не являются прозой.

    Глоссарий («БД - база данных», «DI - Dependency Injection») набирают без
    красной строки: это перечень, а не абзацы основного текста, поэтому
    правило отступа не должно требовать в нем 12,5 мм. Список узнается по
    двум признакам одновременно: строка вида «термин - определение» и
    измеренный нулевой отступ первой строки. Дополнительно требуется, чтобы
    такие строки шли подряд (от двух) или продолжали вводную фразу с
    двоеточием: одиночная строка с тире в обычном абзаце остается прозой.

    :param targets: абзацы области действия правила в порядке документа.
    :return: ``block_id`` абзацев, которые правило отступа пропускает.
    """
    ordered = [paragraph for paragraph in targets if paragraph.text_visible.strip()]
    excluded: Set[str] = set()
    run: List[ParagraphBlock] = []

    def flush(previous_text: str) -> None:
        if run and (len(run) >= 2 or previous_text.endswith(":")):
            excluded.update(paragraph.block_id for paragraph in run)
        run.clear()

    previous_text = ""
    for paragraph in ordered:
        text = paragraph.text_visible.strip()
        indent = paragraph.effective_props.indent_first_line_mm or 0.0
        if RE_DEFINITION_ITEM.match(text) and abs(indent) < 0.1:
            run.append(paragraph)
            continue
        flush(previous_text)
        previous_text = text
    flush(previous_text)
    return excluded


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

    Строки списка определений/глоссария («БД - база данных») набирают без
    красной строки намеренно: это перечень терминов, а не абзацы прозы. Такие
    строки исключаются из области проверки по форме строки и нулевому отступу,
    чтобы не требовать в них 12,5 мм.
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
    definition_list_ids = _definition_list_block_ids(targets)
    if definition_list_ids:
        targets = [
            paragraph
            for paragraph in targets
            if paragraph.block_id not in definition_list_ids
        ]

    for paragraph in targets:
        props = paragraph.effective_props
        expected_values, found_values, expected_parts, found_parts = _compare_measures(
            expected_values={
                "first_line_mm": rule.params.first_line_mm,
                "left_mm": rule.params.left_mm,
                "right_mm": rule.params.right_mm,
            },
            actual_values={
                "first_line_mm": props.indent_first_line_mm,
                "left_mm": props.indent_left_mm,
                "right_mm": props.indent_right_mm,
            },
            labels=INDENT_LABELS,
            tolerance=rule.params.tolerance_mm,
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
        details={
            "scope": rule.target.scope,
            "tolerance_mm": rule.params.tolerance_mm,
            "definition_list_paragraphs": len(definition_list_ids),
        },
    )
    return violations, len(targets), assessment


def _pdf_line_count(paragraph: ParagraphBlock) -> Optional[int]:
    """Возвращает число строк абзаца по данным PDF-разбора."""
    if paragraph.pdf_total_lines_count is not None:
        return paragraph.pdf_total_lines_count
    start = paragraph.pdf_lines_count_on_start_page
    end = paragraph.pdf_lines_count_on_end_page
    if start is None and end is None:
        return None
    return (start or 0) + (end or 0)


def _single_line_by_geometry(paragraph: ParagraphBlock) -> bool:
    """Оценивает однострочность по высоте блока, если счетчиков строк нет.

    Запасной путь для JSON, разобранных до появления
    ``pdf_lines_count_*``: высота абзаца сравнивается с одной строкой кегля
    доминирующего рана. Если геометрии нет вовсе, блок не считается
    однострочным: лучше проверить, чем молча пропустить нарушение.
    """
    top = paragraph.pdf_y_top_mm
    bottom = paragraph.pdf_y_bottom_mm
    if top is None or bottom is None:
        return False
    sizes = [
        run.effective_props.size_pt
        for run in paragraph.runs
        if run.effective_props.size_pt
    ]
    if not sizes:
        return False
    single_line_mm = max(sizes) * 0.3528 * 1.2
    if single_line_mm <= 0:
        return False
    return (bottom - top) <= single_line_mm * 1.5


#: Сколько строк нужно, чтобы выключка по ширине стала измеримой.
#: У абзаца из двух строк вторая строка — последняя, и её правый край всегда
#: рваный; отличить выключку от левого края по ней нельзя. Нужны минимум две
#: полные строки, то есть три строки в абзаце.
PDF_MIN_MEASURABLE_LINES = 3


def _pdf_alignment_unmeasurable(paragraph: ParagraphBlock) -> bool:
    """True, если выравнивание абзаца из PDF физически не определить.

    В PDF выключка по ширине видна только по полным строкам, упирающимся в оба
    края. У короткого абзаца (одна-две строки) последняя строка заведомо
    короче, и оценка выравнивания неотличима от левого края; у перечислений,
    склеенных парсером в один блок, оценка ещё и случайна. Парсер помечает
    такое выравнивание ``pdf_estimate``; считать его вердиктом нельзя —
    отсутствие измерения не превращается в нарушение. Абзацы от трёх строк
    измеряются по краям и проверяются как обычно.
    """
    if paragraph.effective_props.alignment_source != "pdf_estimate":
        return False
    lines = _pdf_line_count(paragraph)
    if lines is not None:
        return lines < PDF_MIN_MEASURABLE_LINES
    return _single_line_by_geometry(paragraph)


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
    expected_alignment = rule.params.alignment
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
    unmeasurable_pdf = 0
    for paragraph in measured:
        actual_alignment = paragraph.effective_props.alignment
        if actual_alignment is None:
            # Защита от старого JSON: поле «не измерено», а не «по левому краю».
            unmeasured += 1
            continue
        if (
            rule.target.scope in {"body_text", "body_paragraphs"}
            and _pdf_alignment_unmeasurable(paragraph)
        ):
            # PDF: у однострочного абзаца выключка по ширине неотличима от
            # левого края, поэтому это отсутствие измерения, а не нарушение.
            unmeasured += 1
            unmeasurable_pdf += 1
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
        "unmeasurable_pdf_short_lines": unmeasurable_pdf,
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
                    f"'{rule.target.scope}': пропущено {unmeasured} "
                    f"(из них коротких PDF-абзацев: {unmeasurable_pdf})"
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
    if unmeasurable_pdf:
        observed_text += (
            f"; выравнивание не определено для {unmeasurable_pdf} "
            "коротких абзацев PDF (менее трёх строк)"
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
