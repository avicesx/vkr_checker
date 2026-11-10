"""Нормализация имён гарнитур: одна гарнитура записывается по-разному.

Word пишет «Times New Roman», «TimesNewRoman», «TimesNewRomanPSMT»,
«ABCDEF+TimesNewRoman-BoldMT», а PDF отдает запись шрифта без пробелов и с
subset-префиксом. Ключ сравнения — имя в нижнем регистре без пробелов, дефисов
и подчеркиваний; subset-префикс `XXXXXX+` и стилевые маркеры PostScript
(`MT`, `PS`, `PSMT`, `Bold`, `Italic`, `Regular`, `Roman`) относятся к
начертанию, а не к имени, поэтому отбрасываются.

Модуль общий для правила шрифта (`rule_engine.engine`) и классификатора
листингов (`rule_engine.context`): моноширинную гарнитуру в PDF тоже нужно
узнавать по склеенному имени (`CourierNew`, `ABCDEF+CourierNew`), иначе строки
кода проверяются как основной текст.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

RE_SUBSET_PREFIX = re.compile(r"^(?:[A-Za-z]{6}\+)+")
"""Subset-префикс записи шрифта: «ABCDEF+TimesNewRoman»."""

RE_FAMILY_SEPARATOR = re.compile(r"[\s\-–—_,.;:/]+")
"""Разделители, которые Word и PostScript-имена ставят внутри названия."""

STYLE_SUFFIX_TOKENS: frozenset[str] = frozenset(
    {
        "mt",
        "ps",
        "psmt",
        "bold",
        "boldmt",
        "boldps",
        "italic",
        "italicmt",
        "bolditalic",
        "bolditalicmt",
        "regular",
        "regularmt",
        "roman",
        "romanmt",
        "normal",
        "oblique",
    }
)
"""Стилевые маркеры PostScript-записи гарнитуры: начертание, а не часть имени."""

GLUED_STYLE_SUFFIXES: Tuple[str, ...] = (
    "bolditalicmt",
    "bolditalicps",
    "bolditalic",
    "boldmt",
    "boldps",
    "italicmt",
    "italicps",
    "italic",
    "regularmt",
    "regular",
    "romanmt",
    "roman",
    "psmt",
    "ps",
    "mt",
)
"""Маркеры, склеенные с именем без разделителя: TimesNewRomanPSMT, ArialMT."""

GLUED_REMAINDER_MIN = 3
"""Минимальная длина имени, остающегося после отбрасывания склеенного маркера."""

SYMBOLIC_FAMILIES: frozenset[str] = frozenset(
    {
        "symbol",
        "symbolmt",
        "symbolps",
        "wingdings",
        "wingdings2",
        "wingdings3",
        "webdings",
        "zapfdingbats",
        "dingbats",
        "marlett",
    }
)
"""Гарнитуры, которые Word подставляет математическим и символьным знакам."""


def strip_glued_style_suffix(key: str) -> Optional[str]:
    """Отбрасывает стилевой маркер, склеенный с именем гарнитуры (без разделителя)."""
    for suffix in GLUED_STYLE_SUFFIXES:
        if not key.endswith(suffix):
            continue
        remainder = key[: -len(suffix)]
        if len(remainder) >= GLUED_REMAINDER_MIN:
            return remainder
    return None


def family_keys(value: Optional[str]) -> Tuple[str, ...]:
    """Собирает ключи сравнения гарнитуры: имя без подмножества и стилевых маркеров.

    Word записывает одну и ту же гарнитуру по-разному: «Times New Roman»,
    «TimesNewRoman», «TimesNewRomanPSMT», «ABCDEF+TimesNewRoman-BoldMT».
    Ключ — имя в нижнем регистре без пробелов, дефисов и подчеркиваний, поэтому
    перечисленные записи совпадают. Стилевые маркеры (MT/PS/PSMT/Bold/Italic/
    Regular/Roman) относятся к начертанию, а не к имени, и отбрасываются; для
    «Times New Roman» сохраняется и вариант с «Roman» в составе имени, поэтому
    сравнение идет по пересечению ключей.
    """
    if not value:
        return ()
    raw = RE_SUBSET_PREFIX.sub("", value.strip())
    tokens = [token for token in RE_FAMILY_SEPARATOR.split(raw) if token]
    if not tokens:
        return ()

    keys: List[str] = []
    key = "".join(tokens).casefold()
    keys.append(key)

    glued = key
    while True:
        stripped = strip_glued_style_suffix(glued)
        if stripped is None:
            break
        if stripped not in keys:
            keys.append(stripped)
        glued = stripped

    separated = tokens
    while len(separated) > 1 and separated[-1].casefold() in STYLE_SUFFIX_TOKENS:
        separated = separated[:-1]
        tail = "".join(separated).casefold()
        if tail not in keys:
            keys.append(tail)

    return tuple(keys)


def same_family(
    actual: Optional[str],
    expected: Optional[str],
    normalize: bool = True,
) -> bool:
    """Сравнивает гарнитуры по нормализованным ключам имени.

    Без `normalize` остается прежнее строгое сравнение: регистр и лишние пробелы
    не важны, но «TimesNewRoman» и «Times New Roman» — разные записи.
    """
    if actual is None or expected is None:
        return False
    if not normalize:
        return " ".join(actual.split()).casefold() == " ".join(expected.split()).casefold()
    return bool(set(family_keys(actual)) & set(family_keys(expected)))


def is_symbolic_family(value: Optional[str]) -> bool:
    """Проверяет, что гарнитура символьная (Symbol, Wingdings, Webdings и т. п.)."""
    return any(key in SYMBOLIC_FAMILIES for key in family_keys(value))


__all__ = [
    "GLUED_REMAINDER_MIN",
    "GLUED_STYLE_SUFFIXES",
    "STYLE_SUFFIX_TOKENS",
    "SYMBOLIC_FAMILIES",
    "family_keys",
    "is_symbolic_family",
    "same_family",
    "strip_glued_style_suffix",
]
