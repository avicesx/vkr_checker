"""Единая сборка нарушений, оценок и адресов: единственное место, где
создаются `Violation` и `RuleAssessment` для всех семейств правил."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..findings import (
    CheckStatus,
    Confidence,
    RuleAssessment,
    Violation,
    ViolationLocation,
)
from ..rules import Rule
from .constants import SNIPPET_LIMIT
from parser.core.schema import (
    ParagraphBlock,
    TextRun,
)


def _collapse_text(text: str, limit: int = SNIPPET_LIMIT) -> str:
    """Сжимает пробелы и обрезает фрагмент текста для локации нарушения."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


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


def _build_violation(
    rule: Rule,
    *,
    address: str,
    location: ViolationLocation,
    expected: Dict[str, Any],
    found: Dict[str, Any],
    expected_text: str,
    found_text: str,
    occurrences: int = 1,
    confidence: Optional[Confidence] = None,
) -> Violation:
    """Единая сборка нарушения: поля правила и идентификатор — в одном месте.

    ``address`` — суффикс идентификатора после ``rule_id:`` (block_id абзаца,
    ``section:{idx}``, ``toc:{block_id}:entry{n}`` и т. п.); префикс правила
    добавляется здесь, чтобы чекеры не дублировали его вручную.
    """
    return Violation(
        violation_id=f"{rule.rule_id}:{address}",
        rule_id=rule.rule_id,
        code=rule.code,
        severity=rule.strictness,
        message=rule.message,
        quote=rule.quote,
        location=location,
        expected=expected,
        found=found,
        expected_text=expected_text,
        found_text=found_text,
        occurrences=occurrences,
        confidence=confidence,
    )


def _build_assessment(
    rule: Rule,
    status: CheckStatus,
    *,
    expected_text: str,
    observed_text: str,
    confidence: Confidence = "medium",
    details: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None,
) -> RuleAssessment:
    """Единая сборка оценки правила: каждое правило дает ровно одну оценку."""
    return RuleAssessment(
        rule_id=rule.rule_id,
        code=rule.code,
        status=status,
        expected_text=expected_text,
        observed_text=observed_text,
        confidence=confidence,
        details=details if details is not None else {},
        note=note,
    )


def _counted_assessment(
    rule: Rule,
    violations: List[Violation],
    checked: int,
    *,
    expected_text: str,
    confidence: Confidence,
    empty_observed: str,
    details: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None,
) -> RuleAssessment:
    """Оценка правила по числу проверенных объектов и нарушений.

    Общий итог для чекеров-счетов: есть нарушения — ``fail``; проверенных
    объектов нет — ``not_assessed`` с причиной ``empty_observed``; иначе
    ``pass``. Счетчики ``objects_checked``/``violations`` добавляются в
    ``details`` автоматически.
    """
    merged: Dict[str, Any] = {"objects_checked": checked, "violations": len(violations)}
    if details:
        merged.update(details)
    if violations:
        return _build_assessment(
            rule,
            "fail",
            expected_text=expected_text,
            observed_text=f"нарушений: {len(violations)}; проверено объектов: {checked}",
            confidence=confidence,
            details=merged,
            note=note,
        )
    if checked == 0:
        return _build_assessment(
            rule,
            "not_assessed",
            expected_text=expected_text,
            observed_text=empty_observed,
            confidence="low",
            details=merged,
            note=note,
        )
    return _build_assessment(
        rule,
        "pass",
        expected_text=expected_text,
        observed_text=f"проверено объектов: {checked}, нарушений нет",
        confidence=confidence,
        details=merged,
        note=note,
    )
