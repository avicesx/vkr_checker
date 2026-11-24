"""Семейство страницы: поля, формат листа, номер страницы, интервал."""
from __future__ import annotations

import re

from typing import Any, Dict, List, Optional, Tuple

from ..findings import (
    RuleAssessment,
    Violation,
    ViolationLocation,
)
from ..rules import (
    LineSpacingRule,
    MarginRule,
    PageFormatRule,
    PageNumberPositionRule,
)
from .builders import (
    _build_assessment,
    _build_violation,
    _counted_assessment,
)
from .common import (
    _compare_measures,
    _format_number,
    _format_ratio,
    _is_pdf_only,
    _median,
    _paragraph_targets,
)
from .constants import (
    ALIGNMENT_LABELS,
    EPSILON,
    MARGIN_LABELS,
    ORIENTATION_LABELS,
    PDF_ONLY_MARGIN_TOLERANCE_MM,
    PDF_SINGLE_LINE_FACTOR,
    ZONE_LABELS,
)
from parser.core.schema import ParsedDocxDocument


def _pdf_page_number_counts(
    document: ParsedDocxDocument,
) -> Tuple[Dict[Tuple[str, str], int], int, int]:
    """Считает положения номеров страниц по PDF-аудиту и число страниц."""
    counts: Dict[Tuple[str, str], int] = {}
    unknown_alignment = 0
    for page in document.pdf_pages_audit:
        if page.page_number_location == "none":
            continue
        if page.page_number_alignment == "none":
            unknown_alignment += 1
            continue
        key = (page.page_number_location, page.page_number_alignment)
        counts[key] = counts.get(key, 0) + 1
    return counts, len(document.pdf_pages_audit), unknown_alignment


def _docx_page_number_counts(
    document: ParsedDocxDocument,
) -> Tuple[Dict[Tuple[str, str], int], int, int]:
    """Считает положения номеров в колонтитулах секций DOCX."""
    counts: Dict[Tuple[str, str], int] = {}
    unknown_alignment = 0
    for section in document.sections:
        for zone, container in (
            ("header", section.headers),
            ("footer", section.footers),
        ):
            for content in container.values():
                if not (content.has_page_field or content.has_hardcoded_number):
                    continue
                if content.page_number_alignment is None:
                    unknown_alignment += 1
                    continue
                key = (zone, content.page_number_alignment)
                counts[key] = counts.get(key, 0) + 1
    total_pages = sum(
        max(0, section.page_end_est - section.page_start_est + 1)
        for section in document.sections
    )
    return counts, total_pages, unknown_alignment


def _check_margin_rule(
    rule: MarginRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
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
    pdf_only = _is_pdf_only(document)
    tolerance = rule.params.tolerance_mm
    if pdf_only:
        tolerance = max(tolerance, PDF_ONLY_MARGIN_TOLERANCE_MM)

    for section in sections:
        margins = section.margins
        expected_values, found_values, expected_parts, found_parts = _compare_measures(
            expected_values={
                "left_mm": rule.params.left_mm,
                "right_mm": rule.params.right_mm,
                "top_mm": rule.params.top_mm,
                "bottom_mm": rule.params.bottom_mm,
                "gutter_mm": rule.params.gutter_mm,
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
            _build_violation(
                rule,
                address=f"section:{section.section_idx}",
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

    assessment = _counted_assessment(
        rule,
        violations,
        len(sections),
        expected_text=rule.message,
        confidence="medium" if pdf_only else "high",
        empty_observed="в документе нет секций с измеренными полями",
        details={"tolerance_mm": tolerance, "pdf_only": pdf_only},
    )
    return violations, len(sections), assessment


def _page_number_position_basics(
    rule: PageNumberPositionRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Сверяет преобладающее положение номера страницы с ожиданием правила.

    Для PDF положение берется из `pdf_pages_audit`, и проверка включается
    только при достаточном числе распознанных номеров: порог `max(3, N // 3)`
    перенесен из `pdf_norm_control_parser`. Для DOCX источником служат
    колонтитулы секций: номер объявлен один раз на секцию, поэтому порог
    по страницам к ним не применяется.
    """
    expected_text = (
        f"{ZONE_LABELS[rule.params.zone]}, "
        f"{ALIGNMENT_LABELS.get(rule.params.alignment, rule.params.alignment)}"
    )
    expected_values = {
        "zone": rule.params.zone,
        "alignment": rule.params.alignment,
    }

    if document.pdf_pages_audit:
        counts, total_pages, unknown_alignment = _pdf_page_number_counts(document)
        number_count = sum(counts.values())
        source = "pdf_pages_audit"
        threshold = max(3, total_pages // 3)
        if number_count < threshold:
            return (
                [],
                number_count,
                _build_assessment(
                    rule,
                    "not_assessed",
                    expected_text=expected_text,
                    observed_text=(
                        f"распознано номеров страниц: {number_count} из {total_pages}; "
                        f"для оценки положения нужно не меньше {threshold}"
                    ),
                    confidence="low",
                    details={
                        "source": source,
                        "pages_with_number": number_count,
                        "pages_total": total_pages,
                        "threshold": threshold,
                        "unknown_alignment": unknown_alignment,
                    },
                    note=(
                        "Положение номера проверяется только при достаточном числе "
                        "распознанных номеров страниц"
                    ),
                ),
            )
        confidence = "medium"
    else:
        counts, total_pages, unknown_alignment = _docx_page_number_counts(document)
        number_count = sum(counts.values())
        source = "docx_headers_footers"
        if not counts:
            reason = (
                "номер страницы найден в колонтитулах, но выравнивание не определено"
                if unknown_alignment
                else "в колонтитулах DOCX не найден номер страницы"
            )
            return (
                [],
                0,
                _build_assessment(
                    rule,
                    "not_assessed",
                    expected_text=expected_text,
                    observed_text=reason,
                    confidence="low",
                    details={
                        "source": source,
                        "pages_total": total_pages,
                        "unknown_alignment": unknown_alignment,
                    },
                ),
            )
        confidence = "high"

    detected_key = sorted(
        counts.items(),
        key=lambda item: (-item[1], item[0][0], item[0][1]),
    )[0][0]
    detected_values = {"zone": detected_key[0], "alignment": detected_key[1]}
    observed_text = (
        f"{ZONE_LABELS.get(detected_key[0], detected_key[0])}, "
        f"{ALIGNMENT_LABELS.get(detected_key[1], detected_key[1])} "
        f"({number_count} из {total_pages} страниц)"
    )
    details: Dict[str, Any] = {
        "source": source,
        "pages_with_number": number_count,
        "pages_total": total_pages,
        "unknown_alignment": unknown_alignment,
        "distribution": {
            f"{zone}:{alignment}": count
            for (zone, alignment), count in sorted(counts.items())
        },
    }

    if detected_values == expected_values:
        return (
            [],
            number_count,
            _build_assessment(
                rule,
                "pass",
                expected_text=expected_text,
                observed_text=observed_text,
                confidence=confidence,
                details=details,
            ),
        )

    violation = _build_violation(
        rule,
        address="document",
        location=ViolationLocation(snippet="положение номера страницы"),
        expected=expected_values,
        found={
            **detected_values,
            "pages_with_number": number_count,
            "pages_total": total_pages,
        },
        expected_text=expected_text,
        found_text=observed_text,
        occurrences=number_count,
        confidence=confidence,
    )
    return (
        [violation],
        number_count,
        _build_assessment(
            rule,
            "fail",
            expected_text=expected_text,
            observed_text=observed_text,
            confidence=confidence,
            details=details,
        ),
    )


def _check_page_format_rule(
    rule: PageFormatRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Сверяет размеры и ориентацию каждой секции с ожидаемым форматом листа.

    `is_a4_standard` — только контекст: он попадает в `found`, но вердикт
    выносится по числовому сравнению сторон с `tolerance_mm` и по
    ориентации. Флаг парсера считает A4 с собственным допуском (±2 мм), и
    при допуске правила ±1 мм он не должен подменять измерение.
    """
    expected = rule.params
    sections = document.sections
    expected_size_text = (
        f"{_format_number(expected.width_mm)} × {_format_number(expected.height_mm)} мм"
    )
    expected_parts = [
        expected_size_text,
        f"допуск ±{_format_number(expected.tolerance_mm)} мм",
    ]
    if expected.orientation != "any":
        expected_parts.append(ORIENTATION_LABELS[expected.orientation])
    expected_text = ", ".join(expected_parts)

    if not sections:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text="секции в документе не найдены",
                confidence="low",
                details={"source": "sections"},
            ),
        )

    violations: List[Violation] = []
    descriptions: List[str] = []
    checked = 0
    for section in sections:
        checked += 1
        width_ok = (
            abs(section.page_width_mm - expected.width_mm)
            <= expected.tolerance_mm + EPSILON
        )
        height_ok = (
            abs(section.page_height_mm - expected.height_mm)
            <= expected.tolerance_mm + EPSILON
        )
        orientation_ok = (
            expected.orientation == "any"
            or section.orientation == expected.orientation
        )
        section_text = (
            f"секция {section.section_idx + 1}: "
            f"{_format_number(section.page_width_mm)} × "
            f"{_format_number(section.page_height_mm)} мм, "
            f"{ORIENTATION_LABELS.get(section.orientation, section.orientation)}"
        )
        descriptions.append(section_text)
        if width_ok and height_ok and orientation_ok:
            continue

        expected_values: Dict[str, Any] = {}
        found_values: Dict[str, Any] = {}
        if not width_ok:
            expected_values["width_mm"] = expected.width_mm
            found_values["width_mm"] = section.page_width_mm
        if not height_ok:
            expected_values["height_mm"] = expected.height_mm
            found_values["height_mm"] = section.page_height_mm
        if not orientation_ok:
            expected_values["orientation"] = expected.orientation
            found_values["orientation"] = section.orientation
        found_values["is_a4_standard"] = section.is_a4_standard

        violations.append(
            _build_violation(
                rule,
                address=f"section:{section.section_idx}",
                location=ViolationLocation(
                    section_idx=section.section_idx,
                    page_start_est=section.page_start_est,
                    page_end_est=section.page_end_est,
                    snippet=section_text,
                ),
                expected=expected_values,
                found=found_values,
                expected_text=expected_text,
                found_text=(
                    f"{_format_number(section.page_width_mm)} × "
                    f"{_format_number(section.page_height_mm)} мм, "
                    f"{ORIENTATION_LABELS.get(section.orientation, section.orientation)}, "
                    f"A4 по парсеру: "
                    f"{'да' if section.is_a4_standard else 'нет'}"
                ),
                confidence="high",
            )
        )

    observed_text = "; ".join(descriptions)
    details: Dict[str, Any] = {
        "source": "sections",
        "sections_checked": checked,
        "violations": len(violations),
    }
    if violations:
        return (
            violations,
            checked,
            _build_assessment(
                rule,
                "fail",
                expected_text=expected_text,
                observed_text=observed_text,
                confidence="high",
                details=details,
            ),
        )
    return (
        [],
        checked,
        _build_assessment(
            rule,
            "pass",
            expected_text=expected_text,
            observed_text=observed_text,
            confidence="high",
            details=details,
        ),
    )


def _check_line_spacing_rule(
    rule: LineSpacingRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Сверяет медианный множитель межстрочного интервала с ожиданием.

    Для DOCX берутся абзацы основного текста с множительным интервалом
    (`lineRule="auto"`); интервал в пунктах (`exact`/`atLeast`) в множитель
    не переводится. Абзацы PDF-структуры (`line_spacing_source ==
    "pdf_estimate"`) исключаются: там оценка структуры PDF вида
    шаг / (кегль × 1,2), а не множитель Word.

    Для pdf_only используется агрегат t2-оценки `meta.pdf_median_leading_ratio`,
    а при его отсутствии — медиана постраничных оценок из `pdf_pages_audit`.
    Обе оценки типографические: шаг / кегль. Семантика `expected.ratio`
    едина для DOCX и PDF — множитель Word, поэтому измеренное отношение
    делится на `PDF_SINGLE_LINE_FACTOR` (одиночная строка ≈ 1,15 кегля).
    В `details` и `found` попадают оба значения: исходное типографическое
    (`median_ratio`) и приведенное к шкале Word (`word_multiplier`).
    """
    expected = rule.params
    expected_text = (
        f"множитель {_format_ratio(expected.ratio)}, "
        f"допуск ±{_format_ratio(expected.tolerance)}"
    )
    paragraphs = _paragraph_targets(document, rule.target, contexts)
    docx_ratios: List[float] = []
    fixed_spacing_count = 0
    pdf_estimate_paragraphs = 0
    for paragraph in paragraphs:
        props = paragraph.effective_props
        if props.line_spacing_source == "pdf_estimate":
            pdf_estimate_paragraphs += 1
            continue
        if props.line_spacing_rule == "auto":
            docx_ratios.append(float(props.line_spacing_value))
        else:
            fixed_spacing_count += 1

    source = "docx_paragraphs"
    median_ratio = _median(docx_ratios)
    sample_count = len(docx_ratios)
    confidence = "high"
    unit_text = "абзацам"
    typographic_ratio: Optional[float] = None
    if median_ratio is None:
        source = "pdf_leading"
        unit_text = "парам строк"
        sample_count = document.meta.pdf_leading_sample_count
        median_ratio = document.meta.pdf_median_leading_ratio
        confidence = "medium"
        if median_ratio is None:
            page_ratios = [
                float(page.leading_ratio)
                for page in document.pdf_pages_audit
                if page.leading_ratio is not None
            ]
            median_ratio = _median(page_ratios)
            sample_count = len(page_ratios)
        if sample_count <= 0:
            sample_count = sum(
                1
                for page in document.pdf_pages_audit
                if page.leading_ratio is not None
            )
        if median_ratio is None:
            return (
                [],
                0,
                _build_assessment(
                    rule,
                    "not_assessed",
                    expected_text=expected_text,
                    observed_text=(
                        "нет данных об интервале: в DOCX не найдены абзацы "
                        "с множительным интервалом, в PDF интервал не оценен"
                    ),
                    confidence="low",
                    details={
                        "source": "none",
                        "paragraphs_considered": len(paragraphs),
                        "fixed_spacing_paragraphs": fixed_spacing_count,
                        "pdf_estimate_paragraphs": pdf_estimate_paragraphs,
                    },
                    note=(
                        "Интервал в пунктах (exact/atLeast) в множитель не переводится"
                    ),
                ),
            )
        # t2 измеряет типографское отношение шаг / кегль, а expected.ratio
        # задан множителем Word: приводим измерение к общей шкале.
        typographic_ratio = float(median_ratio)
        median_ratio = typographic_ratio / PDF_SINGLE_LINE_FACTOR

    difference = abs(float(median_ratio) - expected.ratio)
    if typographic_ratio is None:
        observed_text = (
            f"медианный множитель {_format_ratio(median_ratio)} "
            f"по {sample_count} {unit_text}"
        )
    else:
        observed_text = (
            f"множитель {_format_ratio(median_ratio)} "
            f"(типографское отношение шаг/кегль "
            f"{_format_ratio(typographic_ratio)} при одиночной строке "
            f"{_format_ratio(PDF_SINGLE_LINE_FACTOR)}) "
            f"по {sample_count} {unit_text}"
        )
    details: Dict[str, Any] = {
        "source": source,
        "median_ratio": (
            round(float(typographic_ratio), 4)
            if typographic_ratio is not None
            else round(float(median_ratio), 4)
        ),
        "sample_count": sample_count,
        "paragraphs_considered": len(paragraphs),
        "fixed_spacing_paragraphs": fixed_spacing_count,
        "pdf_estimate_paragraphs": pdf_estimate_paragraphs,
    }
    if typographic_ratio is not None:
        details["word_multiplier"] = round(float(median_ratio), 4)
        details["pdf_single_line_factor"] = PDF_SINGLE_LINE_FACTOR

    status = "pass" if difference <= expected.tolerance + EPSILON else "fail"
    violations: List[Violation] = []
    if status == "fail":
        found_values: Dict[str, Any] = {
            "ratio": (
                round(float(typographic_ratio), 4)
                if typographic_ratio is not None
                else round(float(median_ratio), 4)
            ),
            "sample_count": sample_count,
            "source": source,
        }
        if typographic_ratio is not None:
            found_values["word_multiplier"] = round(float(median_ratio), 4)
            found_values["pdf_single_line_factor"] = PDF_SINGLE_LINE_FACTOR
        violations.append(
            _build_violation(
                rule,
                address="document",
                location=ViolationLocation(snippet="межстрочный интервал основного текста"),
                expected={"ratio": expected.ratio, "tolerance": expected.tolerance},
                found=found_values,
                expected_text=expected_text,
                found_text=observed_text,
                occurrences=max(1, sample_count),
                confidence=confidence,
            )
        )
    return (
        violations,
        sample_count,
        _build_assessment(
            rule,
            status,
            expected_text=expected_text,
            observed_text=observed_text,
            confidence=confidence,
            details=details,
        ),
    )

RE_ARABIC_PAGE_NUMBER = re.compile(r"^\d+[.)]?$")
"""Номер страницы арабскими цифрами, допускается завершающая точка или скобка."""

PAGE_NUMBER_PROPERTY_LABELS = {
    "skip_first_page": "первая страница без номера",
    "require_arabic_digits": "номер страницы арабскими цифрами",
    "forbid_trailing_dot": "без точки после номера страницы",
    "require_continuous": "сквозная нумерация без перезапуска",
    "include_appendices": "нумерация страниц в приложениях",
}
"""Подписи дополнительных требований к номеру страницы."""


def _appendix_page_ranges(document: ParsedDocxDocument) -> List[Tuple[int, int]]:
    """Возвращает диапазоны страниц разделов с ролью «appendix»."""
    ranges: List[Tuple[int, int]] = []
    tree = document.document_tree
    roots = tree if isinstance(tree, list) else [tree]
    stack = [node for node in roots if node is not None]
    while stack:
        node = stack.pop()
        if node.semantic_role == "appendix":
            if node.page_start_est is not None:
                ranges.append(
                    (node.page_start_est, node.page_end_est or node.page_start_est)
                )
        stack.extend(node.children)
    return ranges


def _pdf_page_number_property_violations(
    rule: PageNumberPositionRule,
    document: ParsedDocxDocument,
) -> Tuple[List[Violation], int, Dict[str, Any]]:
    """Проверяет дополнительные требования к номеру страницы по данным PDF.

    Все проверки идут по ``pdf_pages_audit``: номер страницы виден только в
    отрендеренном документе. Каждая находка адресуется конкретной странице.
    """
    params = rule.params
    pages = document.pdf_pages_audit
    numbered = [page for page in pages if page.has_rendered_page_number]
    violations: List[Violation] = []
    checks = 0
    details: Dict[str, Any] = {
        "source": "pdf_pages_audit",
        "pages_total": len(pages),
        "numbered_pages": len(numbered),
    }

    def _add(
        page: Any,
        values_expected: Dict[str, Any],
        values_found: Dict[str, Any],
        expected_text: str,
        found_text: str,
    ) -> None:
        violations.append(
            _build_violation(
                rule,
                address=f"page:{page.page_num}",
                location=ViolationLocation(
                    page_start_est=page.page_num,
                    snippet=f"страница {page.page_num}",
                ),
                expected=values_expected,
                found=values_found,
                expected_text=expected_text,
                found_text=found_text,
                confidence="medium",
            )
        )

    if params.skip_first_page:
        checks += 1
        first = pages[0]
        if first.has_rendered_page_number:
            _add(
                first,
                {"skip_first_page": True},
                {
                    "page": first.page_num,
                    "rendered_number": first.rendered_page_number_raw,
                },
                PAGE_NUMBER_PROPERTY_LABELS["skip_first_page"],
                f"первая страница пронумерована: {first.rendered_page_number_raw}",
            )

    if params.require_arabic_digits:
        checks += 1
        for page in numbered:
            raw = (page.rendered_page_number_raw or "").strip()
            if not raw:
                continue
            if RE_ARABIC_PAGE_NUMBER.match(raw) is None:
                _add(
                    page,
                    {"require_arabic_digits": True},
                    {"page": page.page_num, "rendered_number": page.rendered_page_number_raw},
                    PAGE_NUMBER_PROPERTY_LABELS["require_arabic_digits"],
                    f"номер страницы не арабскими цифрами: {page.rendered_page_number_raw}",
                )

    if params.forbid_trailing_dot:
        checks += 1
        for page in numbered:
            raw = (page.rendered_page_number_raw or "").rstrip()
            if raw.endswith("."):
                _add(
                    page,
                    {"forbid_trailing_dot": True},
                    {"page": page.page_num, "rendered_number": page.rendered_page_number_raw},
                    PAGE_NUMBER_PROPERTY_LABELS["forbid_trailing_dot"],
                    f"после номера страницы стоит точка: {page.rendered_page_number_raw}",
                )

    if params.require_continuous:
        checks += 1
        for page in numbered:
            if page.matches_physical_page_num is False:
                _add(
                    page,
                    {"require_continuous": True},
                    {
                        "page": page.page_num,
                        "rendered_number": page.rendered_page_number_raw,
                    },
                    PAGE_NUMBER_PROPERTY_LABELS["require_continuous"],
                    (
                        f"номер {page.rendered_page_number_raw} не совпадает с физической "
                        f"страницей {page.page_num}"
                    ),
                )

    if params.include_appendices:
        ranges = _appendix_page_ranges(document)
        if not ranges:
            details["include_appendices_note"] = (
                "приложений в документе нет: проверять нумерацию в них нечего"
            )
        else:
            checks += 1
            for page in pages:
                if not any(start <= page.page_num <= end for start, end in ranges):
                    continue
                if not page.has_rendered_page_number:
                    _add(
                        page,
                        {"include_appendices": True},
                        {"page": page.page_num, "numbered": False},
                        PAGE_NUMBER_PROPERTY_LABELS["include_appendices"],
                        f"страница приложения {page.page_num} без номера",
                    )
    return violations, checks, details


def _docx_page_number_property_violations(
    rule: PageNumberPositionRule,
    document: ParsedDocxDocument,
) -> Tuple[List[Violation], int, Dict[str, Any]]:
    """Проверяет дополнительные требования к номеру страницы по DOCX.

    В DOCX номер хранится полем колонтитула, поэтому проверяются только
    свойства, видимые в разметке: исключение первой страницы, формат номера и
    перезапуск нумерации в секциях. Точка и арабские цифры в отрисованном виде
    требуют PDF-режима и здесь не оцениваются.
    """
    params = rule.params
    sections = document.sections
    violations: List[Violation] = []
    checks = 0
    details: Dict[str, Any] = {"source": "docx_headers_footers", "sections": len(sections)}
    skipped: List[str] = []

    def _add(
        section: Any,
        values_expected: Dict[str, Any],
        values_found: Dict[str, Any],
        expected_text: str,
        found_text: str,
    ) -> None:
        violations.append(
            _build_violation(
                rule,
                address=f"section:{section.section_idx}",
                location=ViolationLocation(
                    section_idx=section.section_idx,
                    page_start_est=section.page_start_est,
                    page_end_est=section.page_end_est,
                    snippet=f"секция {section.section_idx + 1}",
                ),
                expected=values_expected,
                found=values_found,
                expected_text=expected_text,
                found_text=found_text,
                confidence="medium",
            )
        )

    if params.skip_first_page and sections:
        checks += 1
        first = sections[0]
        containers = list(first.headers.values()) + list(first.footers.values())
        numbered = [
            content
            for content in containers
            if content.has_page_field or content.has_hardcoded_number
        ]
        if numbered and not first.different_first_page:
            _add(
                first,
                {"skip_first_page": True},
                {"different_first_page": False, "number_in_footer": True},
                PAGE_NUMBER_PROPERTY_LABELS["skip_first_page"],
                "первая страница не исключена из нумерации: в колонтитуле есть номер",
            )

    if params.require_arabic_digits:
        checks += 1
        for section in sections:
            page_format = (section.page_num_fmt or "decimal").strip()
            if page_format != "decimal":
                _add(
                    section,
                    {"require_arabic_digits": True},
                    {"page_num_fmt": page_format},
                    PAGE_NUMBER_PROPERTY_LABELS["require_arabic_digits"],
                    f"формат номера страницы: {page_format}",
                )

    if params.forbid_trailing_dot:
        skipped.append("forbid_trailing_dot")

    if params.require_continuous:
        checks += 1
        for section in sections:
            if section.section_idx == 0:
                continue
            if section.page_num_start_override is not None:
                _add(
                    section,
                    {"require_continuous": True},
                    {
                        "page_num_start_override": section.page_num_start_override,
                    },
                    PAGE_NUMBER_PROPERTY_LABELS["require_continuous"],
                    (
                        "нумерация начинается заново с "
                        f"{section.page_num_start_override}"
                    ),
                )

    if params.include_appendices:
        skipped.append("include_appendices")

    if skipped:
        details["skipped_docx"] = skipped
        details["skipped_note"] = (
            "в DOCX эти требования видны только в отрисованном документе: нужен PDF-режим"
        )
    return violations, checks, details


def _page_number_property_result(
    rule: PageNumberPositionRule,
    document: ParsedDocxDocument,
) -> Tuple[List[Violation], int, Dict[str, Any]]:
    """Выбирает источник данных для дополнительных требований к номеру."""
    if document.pdf_pages_audit:
        return _pdf_page_number_property_violations(rule, document)
    return _docx_page_number_property_violations(rule, document)


def _check_page_number_position_rule(
    rule: PageNumberPositionRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет положение номера страницы и дополнительные требования к нему.

    Без дополнительных параметров поведение прежнее: сверяется преобладающее
    положение номера. Если заданы ``skip_first_page``, ``require_arabic_digits``,
    ``forbid_trailing_dot``, ``require_continuous`` или ``include_appendices``,
    к оценке добавляются проверки свойств номера, а находки адресуются странице
    или секции. Свойства, видимые только в PDF, в DOCX не оцениваются и
    перечисляются в ``details``.
    """
    violations, number_count, assessment = _page_number_position_basics(
        rule, document, contexts
    )
    params = rule.params
    property_flags = (
        params.skip_first_page
        or params.require_arabic_digits
        or params.forbid_trailing_dot
        or params.require_continuous
        or params.include_appendices
    )
    if not property_flags:
        return violations, number_count, assessment

    property_violations, property_checks, property_details = (
        _page_number_property_result(rule, document)
    )
    details: Dict[str, Any] = {
        **(assessment.details or {}),
        **property_details,
        "property_checks": property_checks,
    }
    labels = [PAGE_NUMBER_PROPERTY_LABELS[name] for name in PAGE_NUMBER_PROPERTY_LABELS if getattr(params, name)]
    expected_text = assessment.expected_text or rule.message
    if property_violations:
        return (
            [*violations, *property_violations],
            max(number_count, property_checks),
            _build_assessment(
                rule,
                "fail",
                expected_text=expected_text,
                observed_text=(
                    f"{assessment.observed_text or ''}; не выполнено: "
                    + ", ".join(labels)
                ).strip("; "),
                confidence="medium",
                details=details,
            ),
        )
    if property_checks == 0:
        # В DOCX часть требований к отрисованному номеру не видна: честно
        # сообщаем, что нужен PDF-режим, вместо оценки «нарушений нет».
        skipped_docx = property_details.get("skipped_docx") or []
        if skipped_docx and assessment.status != "fail":
            return (
                violations,
                number_count,
                _build_assessment(
                    rule,
                    "not_assessed",
                    expected_text=expected_text,
                    observed_text=(
                        "нужен PDF-режим: в DOCX не видно отрисованного номера "
                        "страницы и его оформления"
                    ),
                    confidence="low",
                    details=details,
                ),
            )
        return violations, number_count, assessment
    if assessment.status in ("fail", "not_assessed"):
        # Мало распознанных номеров — оценка положения остаётся неполной,
        # даже если проверки свойств номера прошли.
        return violations, number_count, assessment.model_copy(update={"details": details})
    return (
        violations,
        number_count,
        _build_assessment(
            rule,
            "pass",
            expected_text=expected_text,
            observed_text=(
                f"{assessment.observed_text or ''}; проверены свойства номера: "
                + ", ".join(labels)
            ).strip("; "),
            confidence="medium",
            details=details,
        ),
    )
