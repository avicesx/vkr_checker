# -*- coding: utf-8 -*-
"""Машинная сверка контракта формата правил с кодом движка.

``docs/RULES_CONTRACT.md`` — стык с ИИ-командой: по его таблицам команда пишет
наборы правил. Расхождение документа и кода означает, что команда напишет
набор, который движок отвергнет, поэтому тест читает документ как данные:
таблицу кодов, таблицы ``params``, области, роли, поля правила и набора,
сценарии валидации, примеры — и падает при любом расхождении с реестром
``rule_engine.rules``. Падение теста — сигнал править документ вместе с кодом.
"""
from __future__ import annotations

import json
import re
import types
import typing
from pathlib import Path

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticUndefined

from rule_engine import rules as R

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "RULES_CONTRACT.md"

UNION = typing.get_args(R.Rule)[0]
RULE_CLASSES = list(typing.get_args(UNION))
BY_CODE = {
    typing.get_args(cls.model_fields["code"].annotation)[0]: cls
    for cls in RULE_CLASSES
}
CODES = sorted(BY_CODE)
SCOPES = list(typing.get_args(R.Scope))
ROLES = list(typing.get_args(R.HeadingRole))

SECTION_TITLES = [
    "## 1. Назначение и границы",
    "## 2. Коды и параметры правил",
    "## 3. Области проверки (`scope`)",
    "## 4. Поля правила",
    "## 5. Формат набора",
    "## 6. Валидация",
    "## 7. Неизвестный код",
    "## 8. Что не извлекать как правила",
    "## 9. Примеры",
    "## 10. Чек-лист приёмки от ИИ-команды",
    "## 11. Соответствие схеме БД",
]

CODES_HEADERS = [
    "Код",
    "Название",
    "Область по умолчанию",
    "Допустимые области",
    "strictness по умолчанию",
]
PARAM_HEADERS = [
    "Ключ",
    "Тип",
    "Обязательность",
    "По умолчанию",
    "Допустимые значения и единицы",
    "Назначение",
    "Пример",
]
MODEL_HEADERS = [
    "Поле",
    "Тип",
    "Обязательность",
    "По умолчанию",
    "Допустимые значения и единицы",
    "Назначение",
    "Пример",
]
SCOPE_HEADERS = [
    "Область",
    "Подпись в отчёте",
    "Что входит",
    "Коды, которые её допускают",
]
RULE_HEADERS = ["Поле", "Тип", "Обязательность", "По умолчанию", "Назначение", "Пример"]
TARGET_HEADERS = [
    "Поле",
    "Тип",
    "Обязательность",
    "По умолчанию",
    "Допустимые значения",
    "Назначение",
]
ROLE_HEADERS = ["Роль", "Что означает"]
CONTEXT_HEADERS = ["Контекст", "Что это", "В отсеве по умолчанию"]
SET_HEADERS = ["Поле", "Тип", "Обязательность", "По умолчанию", "Назначение"]
SCENARIO_HEADERS = [
    "Сценарий",
    "Что сломано",
    "Тип ошибки pydantic",
    "Что видит автор набора",
]
DB_HEADERS = ["Поле контракта", "Таблица и колонка", "Тип в БД", "Примечание"]


# --------------------------------------------------------------- markdown
def _is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\|(?:-{3}\|)+", line.strip()))


def _split_row(line: str, lineno: int) -> list[str]:
    stripped = line.strip()
    assert stripped.startswith("|") and stripped.endswith("|"), (
        f"строка {lineno}: ожидалась markdown-строка таблицы, получено {stripped[:80]!r}"
    )
    # Вертикальная черта внутри ячейки экранируется как `\|`: режем по неэкранированным.
    cells = [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", stripped[1:-1])]
    return cells


class Table:
    """Разобранная markdown-таблица: заголовок, строки и номера строк файла."""

    def __init__(self, header: list[str], rows: list[tuple[int, list[str]]], line: int):
        self.header = header
        self.rows = rows
        self.line = line

    @property
    def cells(self) -> list[list[str]]:
        return [cells for _, cells in self.rows]

    def column_values(self, index: int) -> list[str]:
        return [cells[index] for cells in self.cells]


def read_doc() -> str:
    assert DOC_PATH.is_file(), f"нет файла контракта: {DOC_PATH}"
    return DOC_PATH.read_text(encoding="utf-8")


def _tables(text: str) -> list[Table]:
    lines = text.split("\n")
    tables: list[Table] = []
    i = 0
    while i < len(lines) - 1:
        if not lines[i].strip().startswith("|") or not _is_separator(lines[i + 1]):
            i += 1
            continue
        header = _split_row(lines[i], i + 1)
        rows: list[tuple[int, list[str]]] = []
        j = i + 2
        while j < len(lines) and lines[j].strip().startswith("|"):
            cells = _split_row(lines[j], j + 1)
            assert len(cells) == len(header), (
                f"строка {j + 1}: {len(cells)} ячеек вместо {len(header)} — "
                f"вероятно, вертикальная черта внутри ячейки ломает таблицу: {lines[j][:120]!r}"
            )
            rows.append((j + 1, cells))
            j += 1
        tables.append(Table(header, rows, i + 1))
        i = j
    return tables


def _find_table(text: str, headers: list[str], where: str) -> Table:
    matches = [table for table in _tables(text) if table.header == headers]
    assert len(matches) == 1, (
        f"{where}: ожидалась ровно одна таблица с заголовком {headers}, "
        f"найдено {len(matches)}; заголовки в этом фрагменте: "
        f"{[table.header for table in _tables(text)]}"
    )
    return matches[0]


def section(text: str, number: int) -> str:
    match = re.search(rf"^## {number}\.\s.*$", text, re.M)
    assert match, f"в документе нет раздела «## {number}.»"
    tail = text[match.end():]
    nxt = re.search(r"^## \d+\.", tail, re.M)
    end = match.end() + nxt.start() if nxt else len(text)
    return text[match.start():end]


def subsection(text: str, number: str) -> str:
    match = re.search(rf"^### {re.escape(number)}\.\s.*$", text, re.M)
    assert match, f"в документе нет подраздела «### {number}.»"
    tail = text[match.end():]
    nxt = re.search(r"^### |^## ", tail, re.M)
    end = match.end() + nxt.start() if nxt else len(text)
    return text[match.start():end]


def unquote(cell: str) -> str:
    cell = cell.strip()
    if len(cell) >= 2 and cell[0] == "`" and cell[-1] == "`":
        return cell[1:-1]
    return cell


def code_list(cell: str) -> list[str]:
    return [unquote(part) for part in cell.split(",")]


# ------------------------------------------------------- отображение типов
def doc_type(annotation) -> str:
    """Тип так, как его печатает документ (§2.1)."""
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return " / ".join(f"`{value}`" for value in typing.get_args(annotation))
    if origin in (list, typing.List):
        inner = doc_type(typing.get_args(annotation)[0])
        return f"({inner})[]" if " / " in inner else f"{inner}[]"
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        inner = " / ".join(doc_type(a) for a in args)
        if len(args) > 1:
            inner = f"({inner})"
        return f"{inner} или `null`"
    if isinstance(annotation, type):
        if annotation is bool:
            return "boolean"
        if annotation is int:
            return "integer"
        if annotation is float:
            return "number"
        if annotation is str:
            return "string"
        if issubclass(annotation, BaseModel):
            return annotation.__name__
    return str(annotation)


def render_default(field) -> str:
    """Значение по умолчанию так, как его печатает документ."""
    if field.is_required():
        return "—"
    default = field.default
    if default is PydanticUndefined:
        default = field.default_factory() if field.default_factory is not None else None
    if default is None:
        return "`null`"
    if isinstance(default, bool):
        return "`true`" if default else "`false`"
    if isinstance(default, (int, float, str)):
        return f"`{default}`"
    if isinstance(default, (list, dict)):
        return f"`{json.dumps(default, ensure_ascii=False)}`"
    return f"`{default}`"


def params_model(code: str):
    return BY_CODE[code].model_fields["params"].annotation


def default_scope(code: str) -> str:
    cls = BY_CODE[code]
    factory = cls.model_fields["target"].default_factory
    scope = factory().scope if factory is not None else "body_text"
    allowed = R.CODE_ALLOWED_SCOPES[code]
    if scope not in allowed:
        # margin: модель по умолчанию body_text, но проверяет только секции
        assert len(allowed) == 1, (code, scope, allowed)
        scope = next(iter(allowed))
    return scope


def default_strictness(code: str) -> str:
    field = BY_CODE[code].model_fields["strictness"]
    return "error" if field.is_required() else field.default


def _check_field_row(
    cells: list[str],
    field,
    where: str,
    *,
    skip_default: bool = False,
) -> None:
    key = unquote(cells[0])
    assert cells[1] == doc_type(field.annotation), (
        f"{where}: у ключа `{key}` тип в документе {cells[1]!r}, "
        f"в коде {doc_type(field.annotation)!r}"
    )
    expected_required = "да" if field.is_required() else "нет"
    assert cells[2] == expected_required, (
        f"{where}: у ключа `{key}` обязательность в документе {cells[2]!r}, "
        f"в коде {expected_required!r}"
    )
    if not skip_default:
        assert cells[3] == render_default(field), (
            f"{where}: у ключа `{key}` значение по умолчанию в документе {cells[3]!r}, "
            f"в коде {render_default(field)!r}"
        )
    allowed = cells[4]
    assert allowed and allowed != "—", f"{where}: у ключа `{key}` не описаны допустимые значения"
    for literal in _field_literals(field.annotation):
        assert f"`{literal}`" in allowed, (
            f"{where}: у ключа `{key}` в допустимых значениях нет литерала `{literal}`: {allowed!r}"
        )
    for bound in _field_bounds(field):
        assert bound in allowed, (
            f"{where}: у ключа `{key}` в допустимых значениях нет границы {bound}: {allowed!r}"
        )


def _unwrap_optional(annotation):
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _field_literals(annotation) -> list[str]:
    annotation = _unwrap_optional(annotation)
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return [str(value) for value in typing.get_args(annotation)]
    if origin in (list, typing.List):
        inner = _unwrap_optional(typing.get_args(annotation)[0])
        if typing.get_origin(inner) is typing.Literal:
            return [str(value) for value in typing.get_args(inner)]
    return []


def _field_bounds(field) -> list[str]:
    bounds = []
    for meta in field.metadata:
        for attr in ("ge", "gt", "le", "lt", "min_length", "max_length"):
            value = getattr(meta, attr, None)
            if value is not None:
                bounds.append(str(value))
    return bounds


def _blocks(text: str, pattern: str) -> dict[str, str]:
    matches = list(re.finditer(pattern, text, re.M))
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        name = match.group(1)
        assert name not in blocks, f"раздел `{name}` встречается в документе дважды"
        blocks[name] = text[match.start():end]
    return blocks


# ---------------------------------------------------------------- документ
def test_document_has_all_sections() -> None:
    text = read_doc()
    headings = re.findall(r"^## .+$", text, re.M)
    assert headings == SECTION_TITLES, (
        f"состав разделов контракта не совпадает: в документе {headings}"
    )


def test_tables_are_well_formed() -> None:
    text = read_doc()
    tables = _tables(text)
    assert len(tables) >= 45, f"в контракте всего {len(tables)} таблиц — документ обрезан?"
    for table in tables:
        assert len(table.header) >= 2, f"таблица со строки {table.line}: меньше двух колонок"
        assert table.rows, f"таблица со строки {table.line} без строк"
        for lineno, cells in table.rows:
            assert cells[0].strip(), f"строка {lineno}: пустая первая ячейка"


def test_doc_states_registry_counts() -> None:
    text = read_doc()
    total_params = sum(len(params_model(code).model_fields) for code in CODES)
    required_params = sum(
        1
        for code in CODES
        for field in params_model(code).model_fields.values()
        if field.is_required()
    )
    assert f"Всего в реестре {len(CODES)} кодов" in text
    assert f"Всего в реестре {total_params} ключей `params`" in text
    assert f"обязательных из них — {required_params}" in text
    assert f"Областей всего {len(SCOPES)}" in text
    # числа из этого теста обязаны совпадать с ответом задачи
    assert (len(CODES), total_params, len(SCOPES)) == (49, 212, 13)


def test_code_registry_table_matches_code() -> None:
    text = read_doc()
    table = _find_table(section(text, 2), CODES_HEADERS, "§2.2")
    doc_codes = [unquote(cells[0]) for cells in table.cells]
    assert len(doc_codes) == len(set(doc_codes)), (
        "в таблице 2.2 коды повторяются: "
        f"{sorted(code for code in set(doc_codes) if doc_codes.count(code) > 1)}"
    )
    missing_in_doc = sorted(set(CODES) - set(doc_codes))
    unknown_in_doc = sorted(set(doc_codes) - set(CODES))
    assert not missing_in_doc, f"коды есть в движке, но не описаны в §2.2: {missing_in_doc}"
    assert not unknown_in_doc, f"коды описаны в §2.2, но отсутствуют в движке: {unknown_in_doc}"
    for lineno, cells in table.rows:
        code = unquote(cells[0])
        assert cells[1].strip() and cells[1] != "—", f"строка {lineno}: у кода `{code}` нет названия"
        assert unquote(cells[2]) == default_scope(code), (
            f"строка {lineno}: область по умолчанию у `{code}` в документе "
            f"{unquote(cells[2])!r}, в коде {default_scope(code)!r}"
        )
        expected_scopes = set(R.CODE_ALLOWED_SCOPES[code])
        actual_scopes = set(code_list(cells[3]))
        assert actual_scopes == expected_scopes, (
            f"строка {lineno}: допустимые области `{code}` в документе {sorted(actual_scopes)}, "
            f"в коде {sorted(expected_scopes)}"
        )
        assert unquote(cells[4]) == default_strictness(code), (
            f"строка {lineno}: strictness по умолчанию у `{code}` в документе "
            f"{unquote(cells[4])!r}, в коде {default_strictness(code)!r}"
        )


def test_params_tables_match_models() -> None:
    text = read_doc()
    blocks = _blocks(subsection(text, "2.3"), r"^#### `([a-z_]+)` — .+$")
    missing = sorted(set(CODES) - set(blocks))
    unknown = sorted(set(blocks) - set(CODES))
    assert not missing, f"нет блоков параметров для кодов: {missing}"
    assert not unknown, f"блоки параметров для неизвестных кодов: {unknown}"
    total = 0
    for code in CODES:
        model = params_model(code)
        table = _find_table(blocks[code], PARAM_HEADERS, f"§2.3, код `{code}`")
        doc_keys = [unquote(cells[0]) for cells in table.cells]
        code_keys = list(model.model_fields)
        assert doc_keys == code_keys, (
            f"код `{code}`: ключи params в документе {doc_keys}, в модели {code_keys}"
        )
        for lineno, cells in table.rows:
            key = unquote(cells[0])
            _check_field_row(cells, model.model_fields[key], f"§2.3, код `{code}`, строка {lineno}")
            assert cells[6].strip(), f"строка {lineno}: у `{code}.{key}` нет примера значения"
        total += len(doc_keys)
    assert total == 212, f"в таблицах params описано {total} ключей, ожидалось 212"


def test_nested_model_tables_match_code() -> None:
    text = read_doc()
    blocks = _blocks(subsection(text, "2.4"), r"^#### `([A-Za-z]+)`\s*$")
    expected = {"FontExpectation", "SectionSpec", "ListLevelMarkerSpec"}
    assert set(blocks) == expected, (
        f"в §2.4 описаны модели {sorted(blocks)}, ожидались {sorted(expected)}"
    )
    for name in sorted(expected):
        model = getattr(R, name)
        table = _find_table(blocks[name], MODEL_HEADERS, f"§2.4, модель `{name}`")
        doc_keys = [unquote(cells[0]) for cells in table.cells]
        assert doc_keys == list(model.model_fields), (
            f"модель `{name}`: поля в документе {doc_keys}, в коде {list(model.model_fields)}"
        )
        for lineno, cells in table.rows:
            key = unquote(cells[0])
            _check_field_row(cells, model.model_fields[key], f"§2.4, `{name}.{key}`, строка {lineno}")
            assert cells[6].strip(), f"строка {lineno}: у `{name}.{key}` нет примера значения"


def test_scope_table_matches_code() -> None:
    text = read_doc()
    table = _find_table(section(text, 3), SCOPE_HEADERS, "§3")
    doc_scopes = [unquote(cells[0]) for cells in table.cells]
    assert doc_scopes == SCOPES, (
        f"области в документе {doc_scopes} не совпадают с перечнем в коде {SCOPES}"
    )
    inverse: dict[str, list[str]] = {scope: [] for scope in SCOPES}
    for code, allowed in R.CODE_ALLOWED_SCOPES.items():
        for scope in allowed:
            inverse[scope].append(code)
    for lineno, cells in table.rows:
        scope = unquote(cells[0])
        assert cells[1] == R.SCOPE_LABELS[scope], (
            f"строка {lineno}: подпись области `{scope}` в документе {cells[1]!r}, "
            f"в коде {R.SCOPE_LABELS[scope]!r}"
        )
        assert cells[2].strip() and cells[2] != "—", f"строка {lineno}: область `{scope}` без описания"
        assert set(code_list(cells[3])) == set(inverse[scope]), (
            f"строка {lineno}: коды области `{scope}` в документе {sorted(code_list(cells[3]))}, "
            f"в коде {sorted(inverse[scope])}"
        )


def test_rule_fields_table_matches_code() -> None:
    text = read_doc()
    table = _find_table(subsection(text, "4.1"), RULE_HEADERS, "§4.1")
    expected = ["rule_id", "code", "params", "strictness", "message", "quote", "target"]
    doc_fields = [unquote(cells[0]) for cells in table.cells]
    assert doc_fields == expected, f"поля правила в документе {doc_fields}, ожидались {expected}"
    for lineno, cells in table.rows:
        name = unquote(cells[0])
        if name in ("code", "params"):
            assert cells[2] == "да", f"строка {lineno}: у поля `{name}` обязательность {cells[2]!r}"
            assert cells[3] == "—", f"строка {lineno}: у поля `{name}` есть значение по умолчанию"
            continue
        field = R._RuleBase.model_fields[name]
        if name == "target":
            assert cells[1] == "RuleTarget", f"строка {lineno}: тип `target` {cells[1]!r}"
        else:
            assert cells[1] == doc_type(field.annotation), (
                f"строка {lineno}: тип `{name}` в документе {cells[1]!r}, "
                f"в коде {doc_type(field.annotation)!r}"
            )
        expected_required = "да" if field.is_required() else "нет"
        assert cells[2] == expected_required, (
            f"строка {lineno}: обязательность `{name}` {cells[2]!r}, ожидалась {expected_required!r}"
        )


def test_target_fields_table_matches_code() -> None:
    text = read_doc()
    table = _find_table(subsection(text, "4.2"), TARGET_HEADERS, "§4.2")
    doc_fields = [unquote(cells[0]) for cells in table.cells]
    assert doc_fields == list(R.RuleTarget.model_fields), (
        f"поля target в документе {doc_fields}, в коде {list(R.RuleTarget.model_fields)}"
    )
    for lineno, cells in table.rows:
        name = unquote(cells[0])
        field = R.RuleTarget.model_fields[name]
        skip_default = name == "exclude_contexts"
        _check_field_row(cells, field, f"§4.2, `{name}`, строка {lineno}", skip_default=skip_default)
        if skip_default:
            assert "7 служебных контекстов" in cells[3], (
                f"строка {lineno}: значение `exclude_contexts` по умолчанию описано как {cells[3]!r}"
            )


def test_heading_roles_table_matches_code() -> None:
    text = read_doc()
    table = _find_table(subsection(text, "4.3"), ROLE_HEADERS, "§4.3")
    doc_roles = [unquote(cells[0]) for cells in table.cells]
    assert doc_roles == ROLES, f"роли в документе {doc_roles}, в коде {ROLES}"
    for lineno, cells in table.cells and table.rows:
        assert cells[1].strip(), f"строка {lineno}: роль `{unquote(cells[0])}` без описания"


def test_contexts_table_matches_defaults() -> None:
    text = read_doc()
    table = _find_table(subsection(text, "4.4"), CONTEXT_HEADERS, "§4.4")
    default_contexts = list(R.RuleTarget.model_fields["exclude_contexts"].default_factory())
    enabled = [
        unquote(cells[0])
        for _, cells in table.rows
        if cells[2].strip().lower() == "да"
    ]
    assert enabled == default_contexts, (
        f"контексты «в отсеве по умолчанию» в документе {enabled}, "
        f"в коде {default_contexts}"
    )
    for lineno, cells in table.rows:
        assert cells[1].strip(), f"строка {lineno}: контекст `{unquote(cells[0])}` без описания"
        assert cells[2].strip().lower() in ("да", "нет"), (
            f"строка {lineno}: в колонке отсева {cells[2]!r}"
        )


def test_ruleset_fields_table_matches_code() -> None:
    text = read_doc()
    table = _find_table(subsection(text, "5.1"), SET_HEADERS, "§5.1")
    doc_fields = [unquote(cells[0]) for cells in table.cells]
    assert doc_fields == list(R.RuleSet.model_fields), (
        f"поля набора в документе {doc_fields}, в коде {list(R.RuleSet.model_fields)}"
    )
    for lineno, cells in table.rows:
        name = unquote(cells[0])
        field = R.RuleSet.model_fields[name]
        expected_type = "Rule[]" if name == "rules" else doc_type(field.annotation)
        assert cells[1] == expected_type, (
            f"строка {lineno}: тип `{name}` в документе {cells[1]!r}, ожидался {expected_type!r}"
        )
        expected_required = "да" if field.is_required() else "нет"
        assert cells[2] == expected_required, (
            f"строка {lineno}: обязательность `{name}` {cells[2]!r}, ожидалась {expected_required!r}"
        )
        assert cells[3] == render_default(field), (
            f"строка {lineno}: значение `{name}` по умолчанию {cells[3]!r}, "
            f"ожидалось {render_default(field)!r}"
        )


def test_doc_codes_match_seed_catalogue() -> None:
    """Коды и область по умолчанию совпадают с сидом справочника `rule_type`."""
    from app.rule_types import RULE_TYPE_CATALOGUE

    text = read_doc()
    table = _find_table(section(text, 2), CODES_HEADERS, "§2.2")
    doc_codes = [unquote(cells[0]) for cells in table.cells]
    catalogue = {spec.code: spec for spec in RULE_TYPE_CATALOGUE}
    assert set(doc_codes) == set(catalogue), (
        f"коды документа не совпадают с сидом справочника: "
        f"лишние в документе {sorted(set(doc_codes) - set(catalogue))}, "
        f"пропущены {sorted(set(catalogue) - set(doc_codes))}"
    )
    for lineno, cells in table.rows:
        code = unquote(cells[0])
        assert cells[2] == f"`{catalogue[code].default_scope}`", (
            f"строка {lineno}: область по умолчанию `{code}` в документе {cells[2]!r}, "
            f"в справочнике {catalogue[code].default_scope!r}"
        )


def test_profile_section_states_current_stats() -> None:
    """§5.4 описывает профили теми числами, которые даёт код на текущей ветке."""
    text = read_doc()
    chunk = subsection(text, "5.4")
    base = R.load_default_ruleset()
    base_ids = {rule.rule_id for rule in base.rules}
    assert (
        f"Базовый набор `{base.ruleset_id}` {base.ruleset_version} — {len(base.rules)} правил."
        in chunk
    ), f"§5.4 не сообщает актуальный размер базового набора ({len(base.rules)} правил)"
    assert "backend/rule_engine/rules/profiles/" in chunk, (
        "§5.4 не указывает, где лежат файлы профилей"
    )
    for profile_id, path in sorted(R.PROFILE_RULESET_PATHS.items()):
        profile = R.RuleSet.model_validate(json.loads(path.read_text(encoding="utf-8")))
        overrides = sum(1 for rule in profile.rules if rule.rule_id in base_ids)
        added = len(profile.rules) - overrides
        merged = R.merge_ruleset(base, profile)
        expected = (
            f"`{profile_id}` — {len(profile.rules)} правил ({overrides} переопределений "
            f"и {added} новых), после слияния {len(merged.rules)}"
        )
        assert expected in chunk, f"§5.4 не описывает профиль `{profile_id}`: ожидалось «{expected}»"
        assert profile.extends == base.ruleset_id, (
            f"профиль `{profile_id}`: extends {profile.extends!r}, базовый набор {base.ruleset_id!r}"
        )


# ------------------------------------------------------------- валидация
def _rule(**overrides) -> dict:
    rule = {
        "rule_id": "r",
        "code": "font",
        "message": "m",
        "params": {"family": "Times New Roman"},
    }
    rule.update(overrides)
    return rule


def _ruleset(*rules, **overrides) -> dict:
    payload = {
        "ruleset_id": "s",
        "ruleset_version": "1.0.0",
        "rules": list(rules) if rules else [_rule()],
    }
    payload.update(overrides)
    return payload


SCENARIO_PAYLOADS = {
    "unknown_code": lambda: _ruleset(_rule(code="page_margins")),
    "extra_param": lambda: _ruleset(_rule(params={"unknown_key": 1})),
    "wrong_type": lambda: _ruleset(_rule(params={"size_pt": "14 пт"})),
    "missing_rule_id": lambda: _ruleset(
        {key: value for key, value in _rule().items() if key != "rule_id"}
    ),
    "missing_params": lambda: _ruleset(
        {key: value for key, value in _rule().items() if key != "params"}
    ),
    "missing_message": lambda: _ruleset(
        {key: value for key, value in _rule().items() if key != "message"}
    ),
    "blank_rule_id": lambda: _ruleset(_rule(rule_id="   ")),
    "empty_message": lambda: _ruleset(_rule(message="   ")),
    "bad_scope_for_code": lambda: _ruleset(_rule(target={"scope": "toc"})),
    "bad_scope_literal": lambda: _ruleset(_rule(target={"scope": "paragraphs"})),
    "exclude_roles_bad_scope": lambda: _ruleset(
        _rule(target={"scope": "body_text", "exclude_roles": ["appendix"]})
    ),
    "bad_strictness": lambda: _ruleset(_rule(strictness="fatal")),
    "unknown_field": lambda: _ruleset(_rule(name="x")),
    "unknown_ruleset_field": lambda: _ruleset(owner="x"),
    "target_extra": lambda: _ruleset(_rule(target={"scope": "body_text", "roles": []})),
    "missing_zone": lambda: _ruleset(
        {"rule_id": "p", "code": "page_number_position", "message": "m", "params": {}}
    ),
    "empty_level_markers": lambda: _ruleset(
        {"rule_id": "l", "code": "list_format", "message": "m", "params": {"level_markers": []}}
    ),
    "empty_rules": lambda: _ruleset(rules=[]),
    "duplicate_rule_id": lambda: _ruleset(_rule(), _rule()),
    "empty_ruleset_id": lambda: _ruleset(ruleset_id="  "),
    "bad_orientation": lambda: _ruleset(
        {"rule_id": "f", "code": "page_format", "message": "m", "params": {"orientation": "diagonal"}}
    ),
    "params_null": lambda: _ruleset(
        {"rule_id": "n", "code": "font", "message": "m", "params": None}
    ),
    "ruleset_version_number": lambda: _ruleset(ruleset_version=1),
}


def test_validation_scenarios_match_code() -> None:
    text = read_doc()
    table = _find_table(section(text, 6), SCENARIO_HEADERS, "§6")
    doc_slugs = [unquote(cells[0]) for cells in table.cells]
    assert len(doc_slugs) == len(set(doc_slugs)), "сценарии валидации в §6 повторяются"
    missing = sorted(set(SCENARIO_PAYLOADS) - set(doc_slugs))
    unknown = sorted(set(doc_slugs) - set(SCENARIO_PAYLOADS))
    assert not missing, f"сценарии есть в тесте, но не описаны в §6: {missing}"
    assert not unknown, f"сценарии описаны в §6, но не проверяются тестом: {unknown}"
    problems = []
    for lineno, cells in table.rows:
        slug = unquote(cells[0])
        expected_type = unquote(cells[2])
        visible = cells[3]
        fragments = re.findall(r"`([^`]+)`", visible)
        try:
            R.RuleSet.model_validate(SCENARIO_PAYLOADS[slug]())
        except ValidationError as exc:
            error = exc.errors()[0]
            if error["type"] != expected_type:
                problems.append(
                    f"строка {lineno}: `{slug}` даёт тип {error['type']!r}, "
                    f"в документе {expected_type!r}"
                )
            if not fragments:
                problems.append(f"строка {lineno}: `{slug}` — в колонке нет дословного фрагмента")
            elif not any(fragment in error["msg"] for fragment in fragments):
                problems.append(
                    f"строка {lineno}: `{slug}` — ни один фрагмент {fragments} "
                    f"не встречается в сообщении {error['msg']!r}"
                )
        else:
            problems.append(f"строка {lineno}: `{slug}` не отвергается валидатором")
    assert not problems, "расхождения документа и валидатора:\n" + "\n".join(problems)


# --------------------------------------------------------------- примеры
def _fenced_json(text: str, marker: str) -> dict:
    index = text.find(f"<!-- {marker} -->")
    assert index >= 0, f"в документе нет маркера <!-- {marker} -->"
    match = re.search(r"```json\n(.*?)\n```", text[index:], re.S)
    assert match, f"после маркера <!-- {marker} --> нет блока ```json"
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:  # pragma: no cover - защита от правки руками
        raise AssertionError(f"пример <!-- {marker} --> — некорректный JSON: {exc}") from exc


def _wrap(rule: dict) -> dict:
    return {"ruleset_id": "doc-check", "ruleset_version": "0.0.0", "rules": [rule]}


def test_rule_examples_in_section_2_match_code() -> None:
    text = read_doc()
    markers = re.findall(r"^<!-- rule: ([a-z_]+) -->$", text, re.M)
    assert len(markers) == len(set(markers)), "маркеры <!-- rule: ... --> повторяются"
    missing = sorted(set(CODES) - set(markers))
    unknown = sorted(set(markers) - set(CODES))
    assert not missing, f"нет примеров правил для кодов: {missing}"
    assert not unknown, f"примеры для неизвестных кодов: {unknown}"
    for code in CODES:
        payload = _fenced_json(text, f"rule: {code}")
        ruleset = R.RuleSet.model_validate(_wrap(payload))
        rule = ruleset.rules[0]
        assert rule.code == code, f"пример `{code}` содержит правило с кодом {rule.code!r}"
        assert payload.get("rule_id"), f"пример `{code}` без rule_id"
        assert payload.get("message"), f"пример `{code}` без message"
        assert "params" in payload, f"пример `{code}` без params"


def test_complete_examples_are_executable() -> None:
    text = read_doc()
    examples = section(text, 9)

    single = R.RuleSet.model_validate(_wrap(_fenced_json(examples, "example: single_rule")))
    assert len(single.rules) == 1 and single.rules[0].code == "font"

    three = R.RuleSet.model_validate(_fenced_json(examples, "example: ruleset_three"))
    assert three.ruleset_id == "vkr-minimal"
    assert [rule.code for rule in three.rules] == ["font", "line_spacing", "heading_page_break"]
    assert [rule.strictness for rule in three.rules].count("warning") == 1

    base = R.load_default_ruleset()
    profile_payload = _fenced_json(examples, "example: profile")
    profile = R.RuleSet.model_validate(profile_payload)
    assert profile.extends == base.ruleset_id, (
        f"пример профиля расширяет {profile.extends!r}, а набор по умолчанию — "
        f"{base.ruleset_id!r}"
    )
    merged = R.merge_ruleset(base, profile)
    assert merged.ruleset_id == profile.ruleset_id
    merged_ids = [rule.rule_id for rule in merged.rules]
    assert all(rule.rule_id in merged_ids for rule in base.rules), "профиль потерял правила базы"
    assert len(merged.rules) == len(base.rules) + 2, (
        "пример профиля должен добавлять два новых правила: "
        f"получено {len(merged.rules) - len(base.rules)}"
    )
    base_by_id = {rule.rule_id: rule for rule in base.rules}
    for rule in merged.rules:
        original = base_by_id.get(rule.rule_id)
        if original is not None and rule.rule_id in {r.rule_id for r in profile.rules}:
            assert rule.target.scope in R.CODE_ALLOWED_SCOPES[rule.code], (
                f"правило профиля `{rule.rule_id}` проверяет область {rule.target.scope!r}"
            )


def test_acceptance_checklist_is_actionable() -> None:
    text = read_doc()
    checklist = section(text, 10)
    items = re.findall(r"^(\d+)\. (.+)$", checklist, re.M)
    assert len(items) == 8, f"в чек-листе {len(items)} пунктов, ожидалось 8"
    assert [int(number) for number, _ in items] == list(range(1, 9))
    for number, body in items:
        assert "`" in body, f"пункт чек-листа {number} не ссылается на поле контракта: {body!r}"


def test_db_mapping_covers_all_exchange_fields() -> None:
    text = read_doc()
    table = _find_table(section(text, 11), DB_HEADERS, "§11")
    whole = "\n".join(" ".join(cells) for _, cells in table.rows)
    for expected in (
        "rule_type.code",
        "rule.params",
        "rule.scope",
        "rule.quote",
        "rule.quote_location",
        "rule.strictness",
        "rule.id",
        "ruleset.id",
        "ruleset.version",
    ):
        assert expected in whole, f"в §11 нет строки про `{expected}`"
    assert "не хранится" in whole, "§11 не отмечает поля, которых нет в базе"
