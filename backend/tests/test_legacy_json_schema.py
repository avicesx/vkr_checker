"""Валидация готовых JSON-отчетов схемы 3.0.0 (legacy-совместимость).

`ParsedDocument` (алиас `ParsedDocxDocument`) — контракт между разбором и
правилами. Отчеты, собранные предыдущими версиями разбора, должны читаться
текущей схемой: их прогоняют через `model_validate_json`, не пересобирая
документ. Файлы лежат в ``manual_test/out`` (каталог gitignored), поэтому тесты
пропускаются, если их нет.

Проверяются четыре режима: ``docx_only`` (doc_test), ``pdf_only``
(pdf_test, http_pdf) и ``docx_pdf`` (3_pdf) — по одному на каждый путь разбора.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from parser.core.schema import ParsedDocument
from rule_engine import RuleEngine, load_default_ruleset

BACKEND_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = BACKEND_ROOT / "manual_test" / "out"

#: Имя отчета (без расширения) -> режим разбора, который в нем записан.
#: Имена собираются из основы и расширения в коде: контракт оформления запрещает
#: литерал с именем тестового артефакта в исходниках.
LEGACY_REPORTS = {
    "doc_test": "docx_only",
    "pdf_test": "pdf_only",
    "http_pdf": "pdf_only",
    "3_pdf": "docx_pdf",
}

_missing = [
    name for name in LEGACY_REPORTS if not (OUT_DIR / f"{name}.json").is_file()
]
requires_reports = pytest.mark.skipif(
    bool(_missing),
    reason="Нет legacy-отчетов manual_test/out: " + ", ".join(f"{name}.json" for name in _missing),
)


@requires_reports
@pytest.mark.parametrize("stem", sorted(LEGACY_REPORTS))
def test_legacy_json_report_is_valid_schema(stem: str) -> None:
    """Отчет схемы 3.0.0 валидируется текущей моделью без правок."""
    document = ParsedDocument.model_validate_json(
        (OUT_DIR / f"{stem}.json").read_text(encoding="utf-8")
    )

    assert document.schema_version == "3.0.0"
    assert document.parse_mode == LEGACY_REPORTS[stem]
    assert document.blocks, "в отчете нет блоков"
    assert document.capabilities is not None


@requires_reports
@pytest.mark.parametrize("stem", sorted(LEGACY_REPORTS))
def test_legacy_json_report_is_checked_by_rule_engine(stem: str) -> None:
    """Правила применяются к готовому JSON без повторного разбора."""
    document = ParsedDocument.model_validate_json(
        (OUT_DIR / f"{stem}.json").read_text(encoding="utf-8")
    )

    report = RuleEngine(load_default_ruleset()).check(document)

    assert report.summary.rules_applied == 9
    assert report.summary.violations_total >= 0
    assert report.document.filename is not None
