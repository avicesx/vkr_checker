"""Семейство документа целиком: объём работы в страницах и словах."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..findings import (
    RuleAssessment,
    Violation,
)
from ..rules import DocumentVolumeRule
from .builders import _counted_assessment
from .common import _document_violation
from parser.core.schema import ParsedDocxDocument

#: Ниже этого числа страниц документ считается фрагментом или шаблоном, и объём
#: не оценивается: нормативы ВКР рассчитаны на полный текст, а не на выжимку.
VOLUME_MIN_MEASURABLE_PAGES = 10


def _check_lower_bound(
    rule: DocumentVolumeRule,
    address: str,
    measured: int,
    minimum: int,
    unit: str,
    confidence: str,
) -> Optional[Violation]:
    """Собирает нарушение нижней границы объёма, если она нарушена."""
    if measured >= minimum:
        return None
    return _document_violation(
        rule,
        address=address,
        expected_values={f"min_{unit}": minimum},
        found_values={unit: measured},
        expected_text=f"не менее {minimum} {unit}",
        found_text=f"{unit}: {measured}",
        confidence=confidence,
    )


def _check_upper_bound(
    rule: DocumentVolumeRule,
    address: str,
    measured: int,
    maximum: int,
    unit: str,
    confidence: str,
) -> Optional[Violation]:
    """Собирает нарушение верхней границы объёма, если она нарушена."""
    if measured <= maximum:
        return None
    return _document_violation(
        rule,
        address=address,
        expected_values={f"max_{unit}": maximum},
        found_values={unit: measured},
        expected_text=f"не более {maximum} {unit}",
        found_text=f"{unit}: {measured}",
        confidence=confidence,
    )


def _check_document_volume_rule(
    rule: DocumentVolumeRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Сравнивает объём работы с границами правила.

    Страницы берутся из ``page_count`` (точные для PDF и по разрывам DOCX), а
    не из ``meta.word_stat_pages``: статистика DOCX может врать (например,
    хранить 1 страницу для многостраничного файла). Число слов приходит из
    статистики DOCX или из извлеченного текста PDF; если парсер его не
    посчитал, проверка слов пропускается с пометкой в оценке.
    """
    expected = rule.params
    pages = document.page_count.total_pages
    words = document.meta.word_stat_words
    page_confidence = document.page_count.confidence

    if pages < VOLUME_MIN_MEASURABLE_PAGES:
        assessment = _counted_assessment(
            rule,
            [],
            0,
            expected_text=rule.message,
            confidence=page_confidence,
            empty_observed="объём работы не измерен: документ короче норматива проверки",
            details={"pages": pages, "words": words},
            note=(
                "документ короче "
                f"{VOLUME_MIN_MEASURABLE_PAGES} страниц — вероятно, это фрагмент или "
                "шаблон, объём работы не оценивается"
            ),
        )
        return [], 0, assessment

    violations: List[Violation] = []
    checked = 1

    if expected.min_pages is not None:
        violation = _check_lower_bound(
            rule, "document:pages", pages, expected.min_pages, "страниц", page_confidence
        )
        if violation is not None:
            violations.append(violation)
    if expected.max_pages is not None:
        violation = _check_upper_bound(
            rule, "document:pages", pages, expected.max_pages, "страниц", page_confidence
        )
        if violation is not None:
            violations.append(violation)

    if words is not None:
        checked += 1
        if expected.min_words is not None:
            violation = _check_lower_bound(
                rule, "document:words", words, expected.min_words, "слов", page_confidence
            )
            if violation is not None:
                violations.append(violation)
        if expected.max_words is not None:
            violation = _check_upper_bound(
                rule, "document:words", words, expected.max_words, "слов", page_confidence
            )
            if violation is not None:
                violations.append(violation)

    note = None
    if words is None:
        note = "число слов не определено парсером — проверка слов пропущена"
    assessment = _counted_assessment(
        rule,
        violations,
        checked,
        expected_text=rule.message,
        confidence=page_confidence,
        empty_observed="объём работы не измерен",
        details={"pages": pages, "words": words},
        note=note,
    )
    return violations, checked, assessment
