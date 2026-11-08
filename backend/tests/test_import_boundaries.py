"""Границы импорта: `parser.pdf` не поднимает DOCX-конвейер.

Проверки выполняются в отдельных процессах: в процессе pytest тяжелые модули
уже могут быть импортированы другими тестами, поэтому sys.modules ничего не
докажет без чистого интерпретатора.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

#: Модули DOCX-конвейера, которые не должны появляться от `import parser.pdf`.
HEAVY_DOCX_MODULES = {"parser.docx.pipeline", "parser.docx.converter"}

#: Зависимости DOCX-разбора: их не должно быть после `import parser.docx`.
HEAVY_DOCX_DEPENDENCIES = {"lxml", "lxml.etree", "pymupdf", "pydantic"}

#: Зависимости, не нужные автономному PDF-парсеру.
PDF_FORBIDDEN_DEPENDENCIES = {"lxml", "lxml.etree"}


def _run_python(code: str) -> str:
    """Выполняет код в чистом процессе из каталога backend и возвращает stdout."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_import_pdf_package_does_not_load_docx_pipeline() -> None:
    """`import parser.pdf` не импортирует DOCX-конвейер и lxml."""
    forbidden = sorted(HEAVY_DOCX_MODULES | PDF_FORBIDDEN_DEPENDENCIES)
    loaded = _run_python(
        "import sys, parser.pdf; "
        "print(sorted(m for m in sys.modules if m in "
        f"{forbidden!r}))"
    )

    assert loaded == "[]"


def test_import_docx_package_is_lazy() -> None:
    """`import parser.docx` не загружает pipeline/converter и их зависимости."""
    forbidden = sorted(HEAVY_DOCX_MODULES | HEAVY_DOCX_DEPENDENCIES)
    loaded = _run_python(
        "import sys, parser.docx; "
        "print(sorted(m for m in sys.modules if m in "
        f"{forbidden!r}))"
    )

    assert loaded == "[]"


def test_lazy_attributes_still_resolve() -> None:
    """Ленивый `__getattr__` сохраняет публичный API пакета."""
    output = _run_python(
        "import parser.docx; "
        "print("
        "parser.docx.ParsedDocument.model_fields['schema_version'].default, "
        "parser.docx.parse_docx_to_json_file.__name__, "
        "parser.docx.DocxParserPipeline.__name__, "
        "parser.docx.convert_to_docx.__name__)"
    )

    assert output == "3.0.0 parse_docx_to_json_file DocxParserPipeline convert_to_docx"


def test_import_core_does_not_load_parsers() -> None:
    """`import parser.core` не поднимает DOCX/PDF-конвейеры и их зависимости."""
    loaded = _run_python(
        "import sys, parser.core; "
        "print(sorted(m for m in sys.modules if m.startswith('parser.') "
        "or m in {'lxml', 'lxml.etree', 'pymupdf', 'pydantic'}))"
    )

    assert loaded == "['parser.core']"
