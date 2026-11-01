"""Контракт оформления кода: докстринги и отсутствие истории правок.

Проверяются модули пакетов docx_parser и rule_engine, классы и публичные
функции: у каждого символа есть докстринг, первая строка модульного докстринга
короткая и заканчивается точкой. Комментарии и докстринги не должны содержать
отсылок к прошлым версиям, номерам абзацев конкретного документа или названиям
тестовых файлов.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_MODULES = sorted(
    [
        *list((PROJECT_ROOT / "docx_parser").glob("*.py")),
        *list((PROJECT_ROOT / "rule_engine").glob("*.py")),
    ]
)
SOURCE_FILES = (
    PACKAGE_MODULES
    + sorted((PROJECT_ROOT / "tests").glob("*.py"))
    + sorted((PROJECT_ROOT / "manual_test").glob("*.py"))
)

FORBIDDEN_MARKERS = (
    re.compile(r"\bWP\d\b"),
    re.compile(r"ИСПРАВЛЕНО"),
    re.compile(r"\bраньше\b"),
    re.compile(r"test\.json"),
    re.compile(r"\bp_\d{5}\b"),
    re.compile(r"\bTODO\b"),
    re.compile(r"\bFIXME\b"),
)

MAX_FIRST_LINE = 100


def _documented_definitions(tree: ast.Module) -> list[ast.AST]:
    """Возвращает классы и публичные функции/методы без докстрингов."""
    undocumented: list[ast.AST] = []

    def walk_body(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                if ast.get_docstring(node) is None:
                    undocumented.append(node)
                walk_body(node.body)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                is_public = not node.name.startswith("_") or node.name == "__init__"
                if is_public and ast.get_docstring(node) is None:
                    undocumented.append(node)

    walk_body(tree.body)
    return undocumented


@pytest.mark.parametrize("module_path", PACKAGE_MODULES, ids=lambda p: p.name)
def test_module_has_short_docstring(module_path: Path) -> None:
    """Модуль начинается с докстринга, его первая строка короткая и с точкой."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    docstring = ast.get_docstring(tree)

    assert docstring, f"{module_path.name}: нет модульного докстринга"
    first_line = docstring.strip().splitlines()[0].strip()
    assert first_line.endswith("."), f"{module_path.name}: первая строка без точки"
    assert len(first_line) <= MAX_FIRST_LINE, (
        f"{module_path.name}: первая строка длиннее {MAX_FIRST_LINE} символов"
    )


@pytest.mark.parametrize("module_path", PACKAGE_MODULES, ids=lambda p: p.name)
def test_classes_and_public_methods_are_documented(module_path: Path) -> None:
    """У каждого класса и публичного метода есть докстринг."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    undocumented = _documented_definitions(tree)

    names = [node.name for node in undocumented]
    assert not names, f"{module_path.name}: без докстринга остались {names}"


@pytest.mark.parametrize(
    "source_path", SOURCE_FILES, ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_source_has_no_history_markers(source_path: Path) -> None:
    """В исходниках нет отсылок к прошлым версиям и тестовым артефактам."""
    if source_path.name == Path(__file__).name and source_path.parent.name == "tests":
        pytest.skip("файл содержит сами шаблоны запрещенных маркеров")

    source = source_path.read_text(encoding="utf-8")
    found = [pattern.pattern for pattern in FORBIDDEN_MARKERS if pattern.search(source)]
    assert not found, f"{source_path.name}: найдены маркеры {found}"
