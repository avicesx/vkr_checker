"""Дополнительные правила оформления таблиц: единицы измерения и период."""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from parser.core.schema import ParsedDocument, TableBlock

from ..findings import RuleAssessment, Violation
from ..rules import Rule, TableUnitsRule
from .builders import _build_assessment, _build_violation
from .tables import (
    _caption_by_id,
    _cell_text,
    _iter_cells,
    _table_blocks,
    _table_location,
    _table_snippet,
)

RE_PERIOD = re.compile(r"\b(?:19|20)\d{2}\b|период", re.IGNORECASE)


def _header_text(table: TableBlock) -> str:
    """Текст строки-шапки или первой строки таблицы."""
    header_rows = [row for row in table.rows if row.is_tbl_header]
    rows = header_rows or table.rows[:1]
    parts: List[str] = []
    for row in rows:
        for cell in row.cells:
            if cell.is_merged_shadow:
                continue
            parts.append(_cell_text(cell))
    return " ".join(part for part in parts if part).strip()


def _check_table_units_rule(
    rule: TableUnitsRule,
    document: ParsedDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """В шапке таблицы указаны единицы измерения и период."""
    expected = rule.params
    tables = _table_blocks(document)
    if not tables:
        return [], 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="таблицы в разборе не найдены",
            details={"tables": 0},
        )
    captions = _caption_by_id(document)
    patterns = [re.compile(pattern, re.IGNORECASE) for pattern in expected.unit_patterns]
    violations: List[Violation] = []
    checked = 0
    for table in tables:
        header = _header_text(table)
        if not header:
            continue
        checked += 1
        location = _table_location(table, _table_snippet(table, captions))
        has_units = any(pattern.search(header) for pattern in patterns)
        if expected.require_units and not has_units:
            violations.append(
                _build_violation(
                    rule,
                    address=f"{table.block_id}:units",
                    location=location,
                    expected={"units": True},
                    found={"units": False},
                    expected_text="в шапке таблицы указаны единицы измерения",
                    found_text="единицы измерения в шапке не найдены",
                )
            )
        if expected.require_period and not RE_PERIOD.search(header):
            violations.append(
                _build_violation(
                    rule,
                    address=f"{table.block_id}:period",
                    location=location,
                    expected={"period": True},
                    found={"period": False},
                    expected_text="в шапке таблицы указан период",
                    found_text="период в шапке не найден",
                )
            )
    details = {"tables": len(tables), "headers_checked": checked, "violations": len(violations)}
    if checked == 0:
        return violations, 0, _build_assessment(
            rule,
            "not_assessed",
            expected_text=rule.message,
            observed_text="шапки таблиц пусты: единицы проверить нельзя",
            details=details,
        )
    return violations, checked, _build_assessment(
        rule,
        "fail" if violations else "pass",
        expected_text=rule.message,
        observed_text=(
            f"нарушений в шапках: {len(violations)} из {checked} таблиц"
            if violations
            else f"единицы и период указаны в {checked} шапках"
        ),
        confidence="medium",
        details=details,
    )
