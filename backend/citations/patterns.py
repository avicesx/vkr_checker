"""Регулярные выражения и разбор четырёх форм ссылок на источники.

Формы из постановки задачи:

* ``[3]`` — числовая ссылка на источник №3;
* ``[3, с. 15]`` — та же ссылка с указанием страницы источника;
* ``[1–4]`` (и ``[3, 5-7]``) — диапазон номеров, раскрывается в отдельные цели;
* ``(Иванов, 2020)`` — авторско-годовая ссылка, в том числе «Иванов и др.»,
  «Smith et al.» и вариант со страницей «(Иванов, 2020, с. 15)».

Модуль не знает о ``ParsedDocument`` и не зависит от схемы отчёта: на входе
строка абзаца, на выходе разобранные цели. Это позволяет переиспользовать его
и в отчёте, и позже — в общем слое обогащения документа для PDF.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

#: Максимальная длина раскрываемого диапазона номеров. Более длинный диапазон
#: считается опечаткой и раскрывается только по краям.
MAX_RANGE_SPAN = 50

#: Числовая ссылка в квадратных скобках. Поддерживает перечисления через запятую
#: и точку с запятой, страницы источника («, с. 15»), диапазоны («[1–4]»,
#: «[3, 5-7]») и страницы-диапазоны («[3, с. 15-17]»).
RE_BRACKET_CITATION = re.compile(
    r"\[\s*\d+(?:\s*[—–-]\s*\d+)?"
    r"(?:\s*[,;]\s*(?:[сpстр]+\.?\s*)?\d+(?:\s*[—–-]\s*\d+)?)*"
    r"\s*\]",
    re.IGNORECASE,
)

#: Фамилия: заглавная буква и строчные, допускается дефис (Петров-Водкин).
_SURNAME = r"[А-ЯЁA-Z][а-яёa-z]+(?:-[А-ЯЁA-Z][а-яёa-z]+)?"

#: «и др.» / «et al.» после фамилии.
_SUFFIX = r"(?:\s+(?:и\s+др\.?|et\s+al\.?))?"

#: Одна авторско-годовая запись: фамилия(и), запятая, год, необязательная страница.
_ENTRY = (
    rf"(?P<names>{_SURNAME}{_SUFFIX}(?:\s*[,;]\s*{_SURNAME}{_SUFFIX})*)"
    r"\s*,\s*(?P<year>(?:1[89]|20)\d{2})"
    r"(?:\s*,\s*(?:[сp]\.?|стр\.?)\s*(?P<page>\d+))?"
)

#: Авторско-годовая ссылка в круглых скобках. Допускает вводное «см.» и
#: продолжение «; …» с другими источниками: в отчёт попадает первая запись,
#: остальные разбираются следующим проходом, если встретятся отдельно.
RE_AUTHOR_YEAR_CITATION = re.compile(
    r"\(\s*(?:(?:см|see)\.?\s+)?" + _ENTRY + r"(?:\s*;[\s\S]*?)?\s*\)",
    re.UNICODE,
)

#: Часть ссылки «с. 15» / «стр. 15-17» — это страница источника, а не его номер.
_PAGE_PART = re.compile(r"^(?:[сpстр]+\.?)\s*(\d+)(?:\s*[—–-]\s*(\d+))?$", re.IGNORECASE)

#: Часть ссылки — диапазон номеров «5-7».
_RANGE_PART = re.compile(r"^(\d+)\s*[—–-]\s*(\d+)$")

#: Хвост «и др.» / «et al.» у фамилии.
_AUTHOR_SUFFIX = re.compile(r"\s+(?:и\s+др\.?|et\s+al\.?)$", re.IGNORECASE)


@dataclass(frozen=True)
class BracketCitation:
    """Разобранная числовая ссылка: цели, страница источника и стиль."""

    raw: str
    targets: List[str] = field(default_factory=list)
    page_in_source: Optional[str] = None
    style: str = "numeric"


@dataclass(frozen=True)
class AuthorYearCitation:
    """Разобранная авторско-годовая ссылка."""

    raw: str
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    page_in_source: Optional[str] = None
    style: str = "author_year"

    @property
    def targets(self) -> List[str]:
        """Цели в едином виде «Фамилия, год» — так же, как их видит потребитель."""
        if self.year is None:
            return []
        return [f"{author}, {self.year}" for author in self.authors]


def expand_numeric_range(left: int, right: int) -> List[str]:
    """Раскрывает диапазон номеров, защищаясь от опечаток и огромных интервалов."""
    if left > right:
        left, right = right, left
    if right - left > MAX_RANGE_SPAN:
        return [str(left), str(right)]
    return [str(number) for number in range(left, right + 1)]


def parse_bracket_citation(raw: str) -> Optional[BracketCitation]:
    """Разбирает содержимое квадратных скобок в цели и страницу источника.

    ``[3, с. 15]`` даёт цель ``3`` и страницу ``15``; ``[1–4]`` раскрывается в
    четыре цели; нераспознанные части (например, «гл. 2») игнорируются, чтобы
    ссылка не превращалась в мусорную цель.
    """
    inner = raw.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1].strip()

    targets: List[str] = []
    page: Optional[str] = None
    has_range = False

    for part in re.split(r"[,;]", inner):
        piece = part.strip()
        if not piece:
            continue

        page_match = _PAGE_PART.match(piece)
        if page_match:
            page = page_match.group(1)
            if page_match.group(2):
                page = f"{page}-{page_match.group(2)}"
            continue

        range_match = _RANGE_PART.match(piece)
        if range_match:
            targets.extend(
                expand_numeric_range(int(range_match.group(1)), int(range_match.group(2)))
            )
            has_range = True
            continue

        if piece.isdigit():
            targets.append(piece)

    if not targets:
        return None

    if page is not None:
        style = "numeric_with_page"
    elif has_range:
        style = "numeric_range"
    else:
        style = "numeric"
    return BracketCitation(raw=raw, targets=targets, page_in_source=page, style=style)


def parse_author_year_citation(raw: str) -> Optional[AuthorYearCitation]:
    """Разбирает авторско-годовую ссылку на фамилии, год и страницу."""
    match = RE_AUTHOR_YEAR_CITATION.fullmatch(raw.strip())
    if match is None:
        return None

    authors: List[str] = []
    for part in re.split(r"[,;]", match.group("names")):
        surname = _AUTHOR_SUFFIX.sub("", part.strip()).strip()
        if surname:
            authors.append(surname)
    if not authors:
        return None

    return AuthorYearCitation(
        raw=raw,
        authors=authors,
        year=int(match.group("year")),
        page_in_source=match.group("page"),
    )
