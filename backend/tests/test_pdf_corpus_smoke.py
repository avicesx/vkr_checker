"""Смоук-тесты PDF-нормоконтроля на реальном корпусе ВКР.

Файл заменил тест старого анализатора ``PdfNormControlParser``: документ
разбирается конвейером ``parser.pdf``, а требования и статусы проверок даёт
``rule_engine`` с профильными ruleset (mpgu/urgpu/urfu из ``rule_engine/rules``).
Корпус берётся из соседнего с репозиторием каталога ``documents/vkr``. Если
корпуса или PyMuPDF нет, пропускаются только тесты, которым нужен реальный
PDF, — профильный тест без корпуса выполняется всегда.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set

import pytest

from rule_engine import (
    LineSpacingRule,
    PageFormatRule,
    PageNumberPositionRule,
    RuleEngine,
    load_profile_ruleset,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VKR_DIR = PROJECT_ROOT.parent / "documents" / "vkr"
PROFILES = ("mpgu", "urgpu", "urfu")
CHECK_STATUSES = {"pass", "fail", "not_assessed"}
CONFIDENCE_LEVELS = {"high", "medium", "low"}
NEW_CHECK_KINDS = (PageNumberPositionRule, PageFormatRule, LineSpacingRule)

REAL_VKR_PDFS = sorted(
    path for path in VKR_DIR.glob("*.pdf") if any(ord(char) > 127 for char in path.name)
)
CORPUS_REASON = (
    f"Корпус ВКР не найден: {VKR_DIR} (нужны PDF с кириллицей в имени файла)"
)
#: Сентинел None даёт по одному явному skip на профиль, когда корпуса нет.
CORPUS_CASES: List[Optional[Path]] = list(REAL_VKR_PDFS) or [None]


def _parse_pdf(path: Path):
    """Лениво подключает parser.pdf и PyMuPDF: без них модуль не падает на сборе."""
    try:
        import pymupdf  # noqa: F401  (проверка доступности до импорта parser.pdf)
    except ImportError:
        pytest.skip("Для прямого анализа PDF требуется PyMuPDF (pip install pymupdf)")
    from parser.pdf import parse_pdf

    return parse_pdf(path)


def _profiled_check_ids(profile_id: str) -> Set[str]:
    """Возвращает id правил профиля, для которых движок пишет assessment."""
    ruleset = load_profile_ruleset(profile_id)
    return {rule.rule_id for rule in ruleset.rules if isinstance(rule, NEW_CHECK_KINDS)}


@pytest.mark.integration
@pytest.mark.parametrize("profile_id", PROFILES)
@pytest.mark.parametrize(
    "pdf_path",
    CORPUS_CASES,
    ids=lambda path: path.name if isinstance(path, Path) else "no-corpus",
)
def test_real_vkr_pdf_is_checked_with_profile_ruleset(
    pdf_path: Optional[Path],
    profile_id: str,
) -> None:
    """Отчёт по реальному PDF собирается для профиля и содержит валидные статусы."""
    if pdf_path is None:
        pytest.skip(CORPUS_REASON)

    document = _parse_pdf(pdf_path)

    assert document.parse_mode == "pdf_only"
    assert document.meta.filename == pdf_path.name
    assert document.page_count.total_pages > 1

    ruleset = load_profile_ruleset(profile_id)
    report = RuleEngine(ruleset).check(document)

    assert report.document.filename == pdf_path.name
    assert report.ruleset_id == ruleset.ruleset_id
    assert report.parser_schema_version == document.schema_version
    assert report.summary.rules_applied == len(ruleset.rules)

    assessments = {assessment.rule_id: assessment for assessment in report.assessments}
    assert set(assessments) == _profiled_check_ids(profile_id), (
        "движок должен оценивать ровно те новые правила, которые есть в профиле"
    )
    assert report.summary.not_assessed_total == len(report.not_assessed)

    rule_ids = {rule.rule_id for rule in ruleset.rules}
    for assessment in report.assessments:
        assert assessment.status in CHECK_STATUSES
        assert assessment.confidence in CONFIDENCE_LEVELS
        assert assessment.expected_text
        assert assessment.observed_text

    for violation in report.violations:
        assert violation.rule_id in rule_ids
        assert violation.severity in {"error", "warning", "info"}
        assert violation.message
        assert violation.found_text

    failed_ids = {
        assessment.rule_id
        for assessment in report.assessments
        if assessment.status == "fail"
    }
    assert failed_ids <= {violation.rule_id for violation in report.violations}
    assert report.passed == (
        not any(violation.severity == "error" for violation in report.violations)
    )

    number_check = assessments["page_number_position"]
    pages_with_number = number_check.details["pages_with_number"]
    threshold = number_check.details["threshold"]
    if number_check.status == "not_assessed":
        assert pages_with_number < threshold
        assert number_check.confidence == "low"
    else:
        assert pages_with_number >= threshold
        assert number_check.confidence == "medium"

    spacing_check = assessments["line_spacing_body"]
    if spacing_check.status == "not_assessed":
        assert spacing_check.details["source"] == "none"
        assert spacing_check.confidence == "low"
    else:
        # Для pdf_only интервал берётся из агрегата parser.pdf
        # (meta.pdf_median_leading_ratio либо медиана постраничных оценок),
        # а абзацы PDF-структуры со шкалой Word (шаг / (кегль × 1,2)) исключены.
        assert spacing_check.details["source"] == "pdf_leading"
        assert spacing_check.details["sample_count"] > 0
        assert spacing_check.details["median_ratio"] is not None
        # Типографское шаг/кегль движок переводит в множитель Word.
        assert spacing_check.details["pdf_single_line_factor"] == pytest.approx(
            1.15
        )
        assert spacing_check.details["word_multiplier"] == pytest.approx(
            spacing_check.details["median_ratio"] / 1.15, abs=1e-3
        )


def test_profile_rulesets_configure_different_new_checks() -> None:
    """Профили различаются набором новых проверок: формат листа задан только у УрФУ."""
    urfu_ids = _profiled_check_ids("urfu")
    mpgu_ids = _profiled_check_ids("mpgu")
    urgpu_ids = _profiled_check_ids("urgpu")

    assert "page_format" in urfu_ids
    assert "page_format" not in mpgu_ids
    assert "page_format" not in urgpu_ids
    common = {"page_number_position", "line_spacing_body"}
    assert common <= urfu_ids
    assert common <= mpgu_ids
    assert common <= urgpu_ids
