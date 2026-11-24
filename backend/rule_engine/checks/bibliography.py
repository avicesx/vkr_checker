"""Семейство списка литературы: ссылки, цитирование, формат записи."""
from __future__ import annotations

from datetime import date
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from ..findings import (
    RuleAssessment,
    Violation,
    ViolationLocation,
)
from ..rules import (
    BibliographyCitedRule,
    BibliographyCountRule,
    BibliographyFormatRule,
    BibliographyLinkRule,
    BibliographyOrderRule,
    BibliographyRecencyRule,
)
from .builders import (
    _build_assessment,
    _build_violation,
    _collapse_text,
    _counted_assessment,
)
from .common import (
    _mention_targets_text,
    _paragraph_index,
    _safe_page_est,
)
from .constants import BIBLIOGRAPHY_FORMAT_LABELS
from parser.core.schema import (
    BibliographyEntry,
    ParagraphBlock,
    ParsedDocxDocument,
    TextCrossReferenceMention,
)


def _unresolved_bibliography_targets(
    mention: TextCrossReferenceMention,
    available_orders: Set[str],
) -> List[str]:
    """Номера ссылки, для которых в списке литературы нет записи.

    Числовые цели ссылки сравниваются с ``order_index`` записей: перечисление
    «[1, 7]» может разрешиться частично, и отсутствие седьмого источника —
    такое же нарушение, как полностью неразрешенная ссылка. Цели, разбор
    которых не дал числа (авторско-годовая ссылка «Иванов, 2020»), здесь не
    проверяются: их разрешение видно по ``resolved_entity_ids``.
    """
    unresolved: List[str] = []
    seen: Set[str] = set()
    for target in mention.extracted_targets:
        normalized = (target or "").strip()
        if not normalized.isdigit():
            continue
        number = str(int(normalized))
        if number in available_orders or number in seen:
            continue
        seen.add(number)
        unresolved.append(number)
    return unresolved


def _bibliography_location(
    entry: BibliographyEntry,
    blocks: Dict[str, ParagraphBlock],
) -> ViolationLocation:
    """Строит локацию записи списка литературы: блок, страницу и фрагмент записи.

    У самой записи страницы нет, поэтому она берется у абзаца записи, если
    абзац найден в разборе; иначе страница остается неизвестной.
    """
    block = blocks.get(entry.block_id)
    return ViolationLocation(
        block_id=entry.block_id,
        page_start_est=(
            _safe_page_est(block.page_start_est) if block is not None else None
        ),
        page_end_est=(
            _safe_page_est(block.page_end_est) if block is not None else None
        ),
        container_type=block.container_type if block is not None else None,
        style_id=block.style_id if block is not None else None,
        snippet=_collapse_text(entry.raw_text),
    )


def _bibliography_field_present(value: Any) -> bool:
    """Считает поле записи списка литературы заполненным.

    Отсутствие элемента — это ``None``, пустая строка, пустой список и
    ``False`` (признак, который требуется: «есть разделитель //»). Числовые
    значения, включая ноль, считаются заполненными: смысл конкретного поля
    проверка не угадывает.
    """
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


#: Фраза даты обращения, в том числе с мягким переносом внутри слова.
RE_ACCESS_DATE_PHRASE = re.compile(r"дата\s+(?:обращения|доступа)", re.IGNORECASE)
#: Значение даты вида 10.04.2026 или 1.4.26.
RE_ACCESS_DATE_VALUE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}")
#: Мягкий перенос/дефис между буквами: «обра-щения» → «обращения».
RE_HYPHENATED_BREAK = re.compile(r"(?<=[А-Яа-яЁё])[-\u00ad]\s*(?=[А-Яа-яЁё])")


def _hyperlink_urls(block: Optional[ParagraphBlock]) -> List[str]:
    """Собирает адреса гиперссылок из ранов абзаца записи.

    В DOCX адрес электронного ресурса часто оформлен гиперссылкой, а в
    ``text_visible`` остается только ее текст («Allure Report Docs»). Парсер
    уже сохраняет ``TextRun.hyperlink_target``, поэтому URL не теряется.
    """
    if block is None:
        return []
    urls: List[str] = []
    for run in block.runs:
        target = (run.hyperlink_target or "").strip()
        if target and target not in urls:
            urls.append(target)
    return urls


def _access_date_from_raw_text(raw_text: str) -> Optional[str]:
    """Ищет дату обращения в тексте записи с мягким переносом.

    Обогащение ищет дату регулярным выражением по исходной строке и не видит
    «(дата обра-щения: 10.04.2026)»: перенос внутри слова рвет фразу. Здесь
    дефис между буквами снимается только для поиска даты, сам текст записи не
    меняется. Дата принимается лишь вместе с фразой «дата обращения/доступа»,
    чтобы случайные числа в описании не считались датой.
    """
    normalized = RE_HYPHENATED_BREAK.sub("", raw_text.replace("\u00ad", ""))
    phrase = RE_ACCESS_DATE_PHRASE.search(normalized)
    if phrase is None:
        return None
    value = RE_ACCESS_DATE_VALUE.search(normalized, phrase.end())
    if value is None:
        return None
    return f"дата обращения: {value.group(0)}"


def _check_bibliography_link_rule(
    rule: BibliographyLinkRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Ищет ссылки на источники, которых нет в списке литературы.

    Упоминание ``ref_type="bibliography"`` с непустыми
    ``extracted_targets`` — это ссылка вида ``[3]``. Разрешенная ссылка
    несет непустой ``resolved_entity_ids``; пустой список означает, что
    записи с таким номером в списке нет. Перечисление может разрешиться
    частично («[1, 7]»): отсутствующие номера сравниваются с
    ``order_index`` записей и тоже дают нарушение. Если список литературы
    пуст, проверять нечего: статус ``not_assessed``, а не выдуманные
    нарушения.
    """
    index = document.cross_ref_index
    entries = index.bibliography
    expected_text = "ссылка указывает на источник, который есть в списке литературы"
    if not entries:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text="список литературы пуст: разрешать ссылки не с чем",
                confidence="low",
                details={"bibliography_entries": 0, "mentions_considered": 0},
            ),
        )

    mentions = [
        mention
        for mention in index.mentions
        if mention.ref_type == "bibliography" and mention.extracted_targets
    ]
    # Квадратные скобки в листинге кода — индексы массивов, а не ссылки на
    # источники. Контексты уже посчитаны один раз на документ; упоминания в
    # code_listing из проверки выкидываются, но в отчете видно, сколько их.
    mentions_in_code = sum(
        1 for mention in mentions if contexts.get(mention.block_id) == "code_listing"
    )
    mentions = [
        mention
        for mention in mentions
        if contexts.get(mention.block_id) != "code_listing"
    ]
    if not mentions:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    "в тексте не найдено ссылок на источники списка литературы"
                    if not mentions_in_code
                    else (
                        "ссылки на источники найдены только в листингах кода: "
                        "квадратные скобки там — индексы, а не ссылки"
                    )
                ),
                confidence="low",
                details={
                    "bibliography_entries": len(entries),
                    "mentions_considered": 0,
                    "mentions_in_code": mentions_in_code,
                },
                note="Ссылки на источники ищет слой обогащения parser.enrichment",
            ),
        )

    violations: List[Violation] = []
    available_orders = {str(entry.order_index) for entry in entries}
    for mention in mentions:
        unresolved_targets = _unresolved_bibliography_targets(
            mention, available_orders
        )
        if mention.resolved_entity_ids and not unresolved_targets:
            continue
        found_text = (
            "в списке литературы нет источников: " + ", ".join(unresolved_targets)
            if unresolved_targets
            else (
                f"ссылка «{_mention_targets_text(mention)}» не разрешена "
                "в записи списка литературы"
            )
        )
        violations.append(
            _build_violation(
                rule,
                address=mention.mention_id,
                location=ViolationLocation(
                    block_id=mention.block_id,
                    page_start_est=_safe_page_est(mention.page_est),
                    snippet=_collapse_text(mention.raw_snippet),
                ),
                expected={
                    "resolved_into_bibliography": True,
                    "targets": list(mention.extracted_targets),
                },
                found={
                    "resolved_into_bibliography": bool(
                        mention.resolved_entity_ids
                    ),
                    "unresolved_targets": unresolved_targets,
                },
                expected_text=expected_text,
                found_text=found_text,
            )
        )

    status = "fail" if violations else "pass"
    observed_text = (
        f"неразрешенных ссылок: {len(violations)} из {len(mentions)}"
        if violations
        else (
            f"все ссылки ({len(mentions)}) разрешены "
            "в записях списка литературы"
        )
    )
    return (
        violations,
        len(mentions),
        _build_assessment(
            rule,
            status,
            expected_text=expected_text,
            observed_text=observed_text,
            confidence="high",
            details={
                "bibliography_entries": len(entries),
                "mentions_considered": len(mentions),
                "unresolved_mentions": len(violations),
                "mentions_in_code": mentions_in_code,
            },
        ),
    )


def _check_bibliography_cited_rule(
    rule: BibliographyCitedRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Ищет источники списка литературы, на которые нет ссылок в тексте.

    Признак ``is_cited_in_text`` вычисляет слой обогащения: запись, у
    которой он False, — источник, не упомянутый в работе. Если записей нет,
    проверять нечего: статус ``not_assessed``.
    """
    entries = document.cross_ref_index.bibliography
    expected_text = "каждый источник списка литературы цитируется в тексте работы"
    if not entries:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text="список литературы пуст: проверять цитирование нечего",
                confidence="low",
                details={"entries_considered": 0},
            ),
        )
    if not rule.params.require_all_cited:
        # Модель отвергает такую конфигурацию; защита от копирования правила.
        return (
            [],
            len(entries),
            _build_assessment(
                rule,
                "pass",
                expected_text=expected_text,
                observed_text="проверка цитирования отключена требованием правила",
                confidence="high",
                details={"entries_considered": len(entries)},
            ),
        )

    violations: List[Violation] = []
    blocks = _paragraph_index(document)
    for entry in entries:
        if entry.is_cited_in_text:
            continue
        violations.append(
            _build_violation(
                rule,
                address=entry.bib_id,
                location=_bibliography_location(entry, blocks),
                expected={"is_cited_in_text": True},
                found={
                    "is_cited_in_text": False,
                    "bib_id": entry.bib_id,
                    "category": entry.detected_category,
                },
                expected_text=expected_text,
                found_text=(
                    f"на источник «{_collapse_text(entry.raw_text, 60)}» "
                    "нет ссылки в тексте"
                ),
            )
        )

    status = "fail" if violations else "pass"
    observed_text = (
        f"без ссылок в тексте: {len(violations)} из {len(entries)} источников"
        if violations
        else f"все источники списка ({len(entries)}) процитированы"
    )
    return (
        violations,
        len(entries),
        _build_assessment(
            rule,
            status,
            expected_text=expected_text,
            observed_text=observed_text,
            confidence="high",
            details={
                "entries_considered": len(entries),
                "uncited_entries": len(violations),
            },
        ),
    )


RE_NORMATIVE_ACT = re.compile(
    r"^\s*(?:"
    r"закон|постановлени|распоряжени|письмо|приказ|указ|решение|"
    r"инструкци|типов\w*\s+инструкци|форма\s|санпин|"
    r"гост|ост\b|снип|правила|конвенци"
    r")",
    re.IGNORECASE,
)
"""Начало описания нормативного акта или стандарта.

Парсер не всегда узнает такие записи: «Закон РФ», «Письмо комитета»,
«Типовая инструкция» попадают в категорию ``unknown``. Страниц у них не
бывает, поэтому для требований страниц и года они приравниваются к
нормативным актам.
"""


def _is_normative_entry(entry: BibliographyEntry) -> bool:
    """True, если запись описывает нормативный акт или стандарт.

    Категория парсера остается основным признаком; текстовый признак нужен
    для актов, которые разбор не распознал и пометил как ``unknown``.
    """
    return bool(RE_NORMATIVE_ACT.match(entry.raw_text or ""))


#: Автор в описании: фамилия с инициалами или организация-автор.
RE_AUTHOR = re.compile(
    r"(?:[А-ЯЁ][а-яё\-]{2,}\s+[А-ЯЁ]\.\s*(?:[А-ЯЁ]\.)?)"
    r"|(?:[A-Z][a-z\-]{2,},?\s+[A-Z]\.)"
    r"|(?:государственн\w*|российск\w*|институт|университет|академи|министерств"
    r"|комитет|центр|центр\w*|научн\w*)",
)

#: Название журнала или сборника после «//».
RE_JOURNAL_TITLE = re.compile(r"//\s*([^/]{2,})")

#: Место издания: «М.», «СПб.», «Москва:» перед издательством.
RE_PUBLICATION_PLACE = re.compile(
    r"(?<![А-Яа-яЁё])(?:М|СПб|Л|К|Новосибирск|Екатеринбург|Москва|"
    r"Санкт-Петербург|Казань|Томск|Омск|Ростов-на-Дону|Нижний Новгород|"
    r"Саратов|Самара|Уфа|Пермь|Челябинск)\s*[.:]",
)

#: Издательство или издающая организация.
RE_PUBLICATION_SOURCE = re.compile(
    r"(?:изд-во|издательств|издательск|publisher|press|"
    r"https?://|www\.)",
    re.IGNORECASE,
)

#: Пометки диссертации и автореферата.
RE_ARCHIVE_DETAILS = re.compile(
    r"(?:дис\.|диссертац|автореф|защит\w*\s+на\s+соискание)",
    re.IGNORECASE,
)


def _entry_has_author(entry: BibliographyEntry) -> bool:
    """True, если в описании есть автор или ответственное лицо."""
    return bool(RE_AUTHOR.search(entry.raw_text))


def _entry_journal_title(entry: BibliographyEntry) -> bool:
    """True, если после «//» указано непустое название источника."""
    match = RE_JOURNAL_TITLE.search(entry.raw_text)
    return bool(match and sum(ch.isalpha() for ch in match.group(1)) >= 2)


def _entry_publication_place(entry: BibliographyEntry) -> bool:
    """True, если в описании указано место издания."""
    return bool(RE_PUBLICATION_PLACE.search(entry.raw_text))


def _entry_publication_source(entry: BibliographyEntry) -> bool:
    """True, если указан источник публикации."""
    return bool(_entry_journal_title(entry) or RE_PUBLICATION_SOURCE.search(entry.raw_text))


def _entry_archive_details(entry: BibliographyEntry) -> bool:
    """True, если у диссертации или автореферата есть архивные реквизиты."""
    return bool(RE_ARCHIVE_DETAILS.search(entry.raw_text))


#: Области описания для проверки порядка: шаблон и подпись.
DESCRIPTION_SCHEMA_PATTERNS: Dict[str, Tuple[str, str]] = {
    "author": (RE_AUTHOR.pattern, "автор"),
    "title": (r"^\s*[^/.:]{4,}", "название"),
    "publication": (RE_JOURNAL_TITLE.pattern, "источник публикации"),
    "edition": (r"(?:изд-во|издательств|издательск)", "издательство"),
    "year": (r"(?<!\d)(?:19\d{2}|20\d{2})(?!\d)", "год"),
    "pages": (r"(?:\d+\s*с\.|С\.\s*\d+)", "страницы"),
}


def _description_schema_positions(
    entry: BibliographyEntry,
    schema: List[str],
) -> List[Tuple[str, int]]:
    """Возвращает позиции областей описания в порядке схемы."""
    positions: List[Tuple[str, int]] = []
    for area in schema:
        pattern_data = DESCRIPTION_SCHEMA_PATTERNS.get(area)
        if pattern_data is None:
            continue
        pattern, _label = pattern_data
        match = re.search(pattern, entry.raw_text, re.IGNORECASE | re.MULTILINE)
        if match is not None:
            positions.append((area, match.start()))
    return positions


def _check_bibliography_format_rule(

    rule: BibliographyFormatRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет разобранные поля записей списка литературы.

    Каждое включенное требование — отдельная проверка: запись без URL и без
    года дает два нарушения, чтобы нормоконтролер видел оба пропуска.
    Фильтр ``categories`` ограничивает выборку категориями источников:
    URL и дата обращения обычно обязательны для электронного ресурса, но
    не для книги. Категории из ``exempt_categories`` не проверяются на год
    и страницы: у сайта страниц не бывает, у нормативного акта тоже.

    Два поля восстанавливаются из того, что парсер уже сохранил, если
    обогащение их не увидело: URL гиперссылки (``TextRun.hyperlink_target``)
    при пустом ``entry.urls`` и дата обращения с мягким переносом внутри
    слова («дата обра-щения»). Иначе запись с настоящим URL за текстом
    ссылки и настоящей датой считалась бы неполной.
    """
    expected = rule.params
    entries = list(document.cross_ref_index.bibliography)
    expected_text = "запись списка литературы содержит обязательные элементы"
    if expected.categories is not None:
        entries = [
            entry
            for entry in entries
            if entry.detected_category in expected.categories
        ]
    if not entries:
        category_text = (
            " выбранных категорий" if expected.categories is not None else ""
        )
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    f"в списке литературы нет записей{category_text}: "
                    "проверять формат нечего"
                ),
                confidence="low",
                details={
                    "entries_considered": 0,
                    "categories": (
                        list(expected.categories)
                        if expected.categories is not None
                        else None
                    ),
                },
            ),
        )

    violations: List[Violation] = []
    blocks = _paragraph_index(document)
    exempt_entries = 0
    for entry in entries:
        block = blocks.get(entry.block_id)
        urls_value = entry.urls or _hyperlink_urls(block)
        access_value = entry.access_date_raw or _access_date_from_raw_text(
            entry.raw_text
        )
        # У электронных ресурсов и нормативных актов нет страниц, а год
        # выпуска сайта часто не указан: эти категории исключены из
        # требований страниц и года, как и из запрета точки-тире.
        category_exempt = entry.detected_category in expected.exempt_categories or (
            entry.detected_category == "unknown"
            and "normative_act_law" in expected.exempt_categories
            and _is_normative_entry(entry)
        )
        if category_exempt:
            exempt_entries += 1
        checks: List[Tuple[str, Any]] = []
        if expected.require_url:
            checks.append(("urls", urls_value))
        if expected.require_access_date:
            checks.append(("access_date_raw", access_value))
        if expected.require_year and not category_exempt:
            checks.append(("year_extracted", entry.year_extracted))
        if expected.require_pages and not category_exempt:
            checks.append(("pages_extracted", entry.pages_extracted))
        if expected.require_isbn_or_issn:
            checks.append(("isbn_or_issn", entry.isbn_or_issn))
        if expected.require_double_slash:
            checks.append(("has_double_slash_source", entry.has_double_slash_source))
        if expected.require_text_type_designation:
            checks.append(
                ("has_text_type_designation", entry.has_text_type_designation)
            )
        if expected.require_author:
            checks.append(("author", _entry_has_author(entry)))
        if expected.require_journal_title:
            checks.append(("journal_title", _entry_journal_title(entry)))
        if expected.require_publication_place:
            checks.append(("publication_place", _entry_publication_place(entry)))
        if expected.require_publication_source:
            checks.append(("publication_source", _entry_publication_source(entry)))
        if expected.require_archive_details:
            checks.append(("archive_details", _entry_archive_details(entry)))

        if (
            expected.forbid_dot_dash
            and entry.dot_dash_count
            and entry.detected_category not in expected.exempt_categories
        ):
            label = BIBLIOGRAPHY_FORMAT_LABELS["dot_dash_count"]
            violations.append(
                _build_violation(
                    rule,
                    address=f"{entry.bib_id}:dot_dash_count",
                    location=_bibliography_location(entry, blocks),
                    expected={"dot_dash_count": 0},
                    found={
                        "dot_dash_count": entry.dot_dash_count,
                        "bib_id": entry.bib_id,
                        "category": entry.detected_category,
                    },
                    expected_text=f"{label}: не допускается",
                    found_text=f"{label}: {entry.dot_dash_count}",
                )
            )

        if expected.description_schema:
            positions = _description_schema_positions(entry, expected.description_schema)
            for (previous_area, previous_at), (area, at) in zip(
                positions, positions[1:]
            ):
                if at >= previous_at:
                    continue
                label = BIBLIOGRAPHY_FORMAT_LABELS["description_schema"]
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{entry.bib_id}:description_schema",
                        location=_bibliography_location(entry, blocks),
                        expected={"description_schema": list(expected.description_schema)},
                        found={
                            "previous": previous_area,
                            "current": area,
                            "bib_id": entry.bib_id,
                        },
                        expected_text=f"{label}: {' → '.join(expected.description_schema)}",
                        found_text=(
                            f"область «{area}» стоит раньше «{previous_area}»"
                        ),
                    )
                )
                break

        for field, actual in checks:
            if _bibliography_field_present(actual):
                continue
            label = BIBLIOGRAPHY_FORMAT_LABELS[field]
            violations.append(
                _build_violation(
                    rule,
                    address=f"{entry.bib_id}:{field}",
                    location=_bibliography_location(entry, blocks),
                    expected={field: True},
                    found={
                        field: actual,
                        "bib_id": entry.bib_id,
                        "category": entry.detected_category,
                    },
                    expected_text=f"{label}: обязательно",
                    found_text=f"{label}: отсутствует",
                )
            )

    status = "fail" if violations else "pass"
    observed_text = (
        f"несоответствий формата: {len(violations)} у {len(entries)} записей"
        if violations
        else f"все {len(entries)} записей списка соответствуют требованиям"
    )
    return (
        violations,
        len(entries),
        _build_assessment(
            rule,
            status,
            expected_text=expected_text,
            observed_text=observed_text,
            confidence="high",
            details={
                "entries_considered": len(entries),
                "mismatches_total": len(violations),
                "exempt_entries": exempt_entries,
                "categories": (
                    list(expected.categories)
                    if expected.categories is not None
                    else None
                ),
            },
        ),
    )


def _bibliography_summary_location(
    entries: List[BibliographyEntry],
    blocks: Dict[str, ParagraphBlock],
) -> ViolationLocation:
    """Строит локацию нарушения, относящегося ко всему списку литературы.

    Кроме первой записи заполняется диапазон страниц: без него замечание о
    количестве или свежести источников нельзя найти в документе.
    """
    first = entries[0] if entries else None
    pages = [
        block.page_start_est
        for entry in entries
        if (block := blocks.get(entry.block_id)) is not None
        and block.page_start_est is not None
    ]
    return ViolationLocation(
        block_id=first.block_id if first is not None else None,
        page_start_est=min(pages) if pages else None,
        page_end_est=max(pages) if pages else None,
        snippet=f"список литературы: {len(entries)} записей",
    )


def _check_bibliography_count_rule(
    rule: BibliographyCountRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет количество источников в списке литературы.

    Считаются все разобранные записи списка: методические указания задают
    нижнюю границу, а верхняя обычно не ограничена. Пустой список — это не
    «ноль источников и нарушение», а отсутствие измерения: правило не
    оценивается, потому что список мог не распознаться.
    """
    index = document.cross_ref_index
    entries = list(index.bibliography) if index is not None else []
    expected = rule.params
    expected_text = (
        f"источников не меньше {expected.min_sources}"
        if expected.max_sources is None
        else f"источников от {expected.min_sources} до {expected.max_sources}"
    )
    if not entries:
        return ([], 0, _counted_assessment(
            rule,
            [],
            0,
            expected_text=expected_text,
            confidence="medium",
            empty_observed="список литературы не найден",
        ))

    count = len(entries)
    blocks = _paragraph_index(document)
    violations: List[Violation] = []
    if expected.min_sources is not None and count < expected.min_sources:
        violations.append(
            _build_violation(
                rule,
                address="bibliography",
                location=_bibliography_summary_location(entries, blocks),
                expected={"min_sources": expected.min_sources},
                found={"sources": count},
                expected_text=expected_text,
                found_text=f"источников в списке: {count}",
                occurrences=expected.min_sources - count,
                confidence="high",
            )
        )
    if expected.max_sources is not None and count > expected.max_sources:
        violations.append(
            _build_violation(
                rule,
                address="bibliography",
                location=_bibliography_summary_location(entries, blocks),
                expected={"max_sources": expected.max_sources},
                found={"sources": count},
                expected_text=expected_text,
                found_text=f"источников в списке: {count}",
                occurrences=count - expected.max_sources,
                confidence="high",
            )
        )

    assessment = _counted_assessment(
        rule,
        violations,
        count,
        expected_text=expected_text,
        confidence="high",
        empty_observed="список литературы не найден",
        details={
            "sources": count,
            "min_sources": expected.min_sources,
            "max_sources": expected.max_sources,
        },
    )
    return violations, count, assessment


#: Заголовок группы внутри списка литературы: «Нормативные акты:».
RE_GROUP_HEADER = re.compile(r"^\s*[А-ЯЁ][А-ЯЁа-яё\s,\-]{2,60}:\s*$")

#: Слова-маркеры, по которым распознаётся тема группы.
GROUP_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "normative_act_law": ("норматив", "закон", "кодекс"),
    "book_monograph": ("книг", "монограф", "учебник", "пособ"),
    "journal_article": ("стать", "журнал"),
    "conference_paper": ("конференц", "сборник"),
    "electronic_resource": ("электрон", "интернет", "ресурс"),
    "dissertation_autoref": ("диссертац", "автореф"),
    "standard_gost": ("гост", "стандарт"),
    "patent": ("патент",),
}


def _is_group_header(entry: BibliographyEntry) -> bool:
    """Определяет запись-заголовок группы, а не источник.

    Заголовок группы не имеет номера и библиографических признаков (год,
    URL, ISBN), а его текст заканчивается двоеточием.
    """
    if entry.raw_number_prefix:
        return False
    if entry.urls or entry.isbn_or_issn or entry.year_extracted is not None:
        return False
    return bool(RE_GROUP_HEADER.match(entry.raw_text.strip()))


def _group_category(entry: BibliographyEntry) -> str:
    """Определяет тему группы по словам в заголовке."""
    text = entry.raw_text.casefold()
    for category, words in GROUP_KEYWORDS.items():
        if any(word in text for word in words):
            return category
    return "unknown"


def _first_letter(text: str) -> str:
    """Возвращает первую букву текста в нижнем регистре."""
    for char in text:
        if char.isalpha():
            return char.casefold()
    return ""

#: Римская запись номера записи списка: «IV», «XII.».
RE_ROMAN_NUMBER_PREFIX = re.compile(r"^[IVXLCDM]+\s*\.?$")

#: Латиница в описании: иностранный источник или латинское название.
RE_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")

#: Юридическая сила нормативных актов: меньшее значение — выше в списке.
LEGAL_FORCE_RANKS: Tuple[Tuple[str, int, str], ...] = (
    (r"\bконституци", 1, "Конституция"),
    (r"\bкодекс", 2, "кодекс"),
    (r"^\s*федеральн\w*\s+закон|^\s*закон", 3, "федеральный закон"),
    (r"\bуказ\b", 4, "указ"),
    (r"\bпостановлени", 5, "постановление"),
    (r"\bраспоряжени", 6, "распоряжение"),
    (r"\bприказ\b", 7, "приказ"),
    (r"\bписьмо\b", 8, "письмо"),
    (r"\bинструкци", 9, "инструкция"),
    (r"\bгост\b|\bстандарт", 10, "стандарт"),
    (r"\bсанпин|\bснип", 11, "санитарные правила"),
)


def _entry_number_style(entry: BibliographyEntry) -> Optional[str]:
    """Определяет форму записи номера: «arabic», «roman» или None."""
    prefix = (entry.raw_number_prefix or "").strip()
    if not prefix:
        return None
    if prefix.isdigit():
        return "arabic"
    if RE_ROMAN_NUMBER_PREFIX.match(prefix):
        return "roman"
    return None


def _is_foreign_entry(entry: BibliographyEntry) -> bool:
    """Определяет иностранный источник по латинице в описании."""
    return bool(RE_LATIN_WORD.search(entry.raw_text))


def _legal_force_rank(entry: BibliographyEntry) -> Optional[Tuple[int, str]]:
    """Возвращает ранг юридической силы нормативного акта и его подпись."""
    text = entry.raw_text.strip()
    for pattern, rank, label in LEGAL_FORCE_RANKS:
        if re.search(pattern, text, re.IGNORECASE):
            return rank, label
    return None


def _check_bibliography_order_rule(
    rule: BibliographyOrderRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет сквозную нумерацию списка литературы.

    Номера записей обязаны идти подряд от ``start_at``: на них ссылается текст.
    Пропуск, повтор или сдвиг нумерации дают нарушение с локацией конкретной
    записи. Пустой список не оценивается — нумеровать нечего.
    """
    index = document.cross_ref_index
    entries = list(index.bibliography) if index is not None else []
    expected = rule.params
    expected_text = f"записи пронумерованы подряд от {expected.start_at}"
    if not entries:
        return ([], 0, _counted_assessment(
            rule,
            [],
            0,
            expected_text=expected_text,
            confidence="medium",
            empty_observed="список литературы не найден",
        ))

    blocks = _paragraph_index(document)
    violations: List[Violation] = []
    seen: Set[int] = set()
    expected_number = expected.start_at
    for entry in entries:
        actual = entry.order_index
        duplicate = actual in seen
        mismatch = actual != expected_number
        if (duplicate and not expected.allow_duplicates) or (
            mismatch and not expected.allow_gaps
        ):
            reason = "повтор номера" if duplicate else "нарушен порядок нумерации"
            violations.append(
                _build_violation(
                    rule,
                    address=entry.bib_id,
                    location=_bibliography_location(entry, blocks),
                    expected={"order_index": expected_number},
                    found={
                        "order_index": actual,
                        "duplicate": duplicate,
                        "reason": reason,
                    },
                    expected_text=expected_text,
                    found_text=f"номер записи: {actual} ({reason})",
                    confidence="high",
                )
            )
        seen.add(actual)
        expected_number += 1

    # Деление списка на группы: запись-заголовок группы («Нормативные акты:»).
    group_headers = [entry for entry in entries if _is_group_header(entry)]
    if not expected.allow_groups:
        for entry in group_headers:
            violations.append(
                _build_violation(
                    rule,
                    address=f"{entry.bib_id}:group",
                    location=_bibliography_location(entry, blocks),
                    expected={"group": False},
                    found={"group": True},
                    expected_text="список без деления на группы",
                    found_text=f"запись-заголовок группы: «{entry.raw_text.strip()}»",
                    confidence="high",
                )
            )

    if expected.group_order is not None and group_headers:
        allowed_groups = [
            frozenset(part.strip() for part in group.split("|"))
            for group in expected.group_order
        ]
        for position, entry in enumerate(group_headers):
            actual_category = _group_category(entry)
            if position < len(allowed_groups) and actual_category in allowed_groups[position]:
                continue
            if position < len(allowed_groups):
                expected_label = " или ".join(sorted(allowed_groups[position]))
            else:
                expected_label = f"групп не больше {len(allowed_groups)}"
            violations.append(
                _build_violation(
                    rule,
                    address=f"{entry.bib_id}:group_order",
                    location=_bibliography_location(entry, blocks),
                    expected={"group": expected_label},
                    found={"group": actual_category},
                    expected_text=f"группа: {expected_label}",
                    found_text=f"группа: {actual_category}",
                    confidence="high",
                )
            )
        if len(group_headers) < len(allowed_groups):
            missing = [
                " или ".join(sorted(group))
                for group in allowed_groups[len(group_headers):]
            ]
            violations.append(
                _build_violation(
                    rule,
                    address="bibliography:group_order",
                    location=_bibliography_summary_location(entries, blocks),
                    expected={"groups": missing},
                    found={"groups": len(group_headers)},
                    expected_text="группы: " + ", ".join(missing),
                    found_text=f"групп в списке: {len(group_headers)}",
                    confidence="high",
                )
            )

    if expected.sort == "alphabetical":
        previous_letter = ""
        previous_entry: Optional[BibliographyEntry] = None
        for entry in entries:
            if _is_group_header(entry):
                previous_letter = ""
                previous_entry = None
                continue
            letter = _first_letter(entry.raw_text)
            if not letter:
                continue
            if previous_letter and letter < previous_letter:
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{entry.bib_id}:sort",
                        location=_bibliography_location(entry, blocks),
                        expected={"sort": "alphabetical"},
                        found={
                            "previous": previous_letter,
                            "current": letter,
                            "previous_bib_id": (
                                previous_entry.bib_id
                                if previous_entry is not None
                                else None
                            ),
                        },
                        expected_text="записи упорядочены по алфавиту",
                        found_text=(
                            f"порядок нарушен: «{previous_letter}», затем «{letter}»"
                        ),
                        confidence="high",
                    )
                )
                continue
            previous_letter = letter
            previous_entry = entry

    if expected.number_style is not None:
        style_label = {"arabic": "арабские цифры", "roman": "римские цифры"}[
            expected.number_style
        ]
        for entry in entries:
            if _is_group_header(entry):
                continue
            actual_style = _entry_number_style(entry)
            if actual_style is None or actual_style == expected.number_style:
                continue
            violations.append(
                _build_violation(
                    rule,
                    address=f"{entry.bib_id}:number_style",
                    location=_bibliography_location(entry, blocks),
                    expected={"number_style": expected.number_style},
                    found={
                        "number_style": actual_style,
                        "raw_number": entry.raw_number_prefix,
                    },
                    expected_text=f"номера записей: {style_label}",
                    found_text=f"номер записи: {entry.raw_number_prefix}",
                    confidence="high",
                )
            )

    if expected.foreign_sources_last:
        foreign_seen = False
        for entry in entries:
            if _is_group_header(entry):
                continue
            if _is_foreign_entry(entry):
                foreign_seen = True
                continue
            if foreign_seen:
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{entry.bib_id}:foreign_order",
                        location=_bibliography_location(entry, blocks),
                        expected={"foreign_sources_last": True},
                        found={"foreign_sources_last": False},
                        expected_text="иностранные источники в конце списка",
                        found_text=(
                            "русскоязычная запись стоит после иностранной: "
                            f"«{_collapse_text(entry.raw_text)}»"
                        ),
                        confidence="high",
                    )
                )

    if expected.author_group_sort:
        previous_author = ""
        previous_entry: Optional[BibliographyEntry] = None
        for entry in entries:
            if _is_group_header(entry):
                previous_author = ""
                previous_entry = None
                continue
            letter = _first_letter(entry.raw_text)
            if not letter:
                continue
            if previous_author and letter < previous_author:
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{entry.bib_id}:author_order",
                        location=_bibliography_location(entry, blocks),
                        expected={"author_group_sort": True},
                        found={
                            "previous": previous_author,
                            "current": letter,
                            "previous_bib_id": (
                                previous_entry.bib_id
                                if previous_entry is not None
                                else None
                            ),
                        },
                        expected_text="записи внутри группы упорядочены по авторам",
                        found_text=(
                            f"порядок нарушен: «{previous_author}», затем «{letter}»"
                        ),
                        confidence="high",
                    )
                )
                continue
            previous_author = letter
            previous_entry = entry

    if expected.legal_force_order:
        previous_rank: Optional[int] = None
        previous_label = ""
        for entry in entries:
            if _is_group_header(entry):
                continue
            rank_data = _legal_force_rank(entry)
            if rank_data is None:
                continue
            rank, label = rank_data
            if previous_rank is not None and rank < previous_rank:
                violations.append(
                    _build_violation(
                        rule,
                        address=f"{entry.bib_id}:legal_force",
                        location=_bibliography_location(entry, blocks),
                        expected={"legal_force_rank": previous_rank},
                        found={"legal_force_rank": rank, "document": label},
                        expected_text=f"акт не выше по силе, чем {previous_label}",
                        found_text=f"после акта ниже по силе идёт {label}",
                        confidence="high",
                    )
                )
            previous_rank = rank
            previous_label = label

    assessment = _counted_assessment(
        rule,
        violations,
        len(entries),
        expected_text=expected_text,
        confidence="high",
        empty_observed="список литературы не найден",
        details={
            "sources": len(entries),
            "start_at": expected.start_at,
            "groups": (
                len(group_headers)
                if not expected.allow_groups or expected.group_order is not None
                else None
            ),
            "sorted": expected.sort,
            "foreign_sources_last": expected.foreign_sources_last,
            "number_style": expected.number_style,
            "author_group_sort": expected.author_group_sort,
            "legal_force_order": expected.legal_force_order,
        },
    )
    return violations, len(entries), assessment


def _check_bibliography_recency_rule(
    rule: BibliographyRecencyRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет долю свежих источников в списке литературы.

    Год записи разобран не у всех источников, поэтому сначала проверяется
    покрытие годами: если оно ниже порога, свежесть не оценивается. Иначе
    считается доля записей не старше ``recent_years`` лет от года отсчёта и
    сравнивается с ``min_recent_share``; нарушение одно на список, потому что
    претензия относится к подбору литературы в целом.
    """
    index = document.cross_ref_index
    entries = list(index.bibliography) if index is not None else []
    expected = rule.params
    reference_year = expected.reference_year or date.today().year
    threshold_year = reference_year - expected.recent_years
    expected_text = (
        f"не менее {expected.min_recent_share:.0%} источников не старше "
        f"{expected.recent_years} лет (с {threshold_year} года)"
    )
    if not entries:
        return ([], 0, _counted_assessment(
            rule,
            [],
            0,
            expected_text=expected_text,
            confidence="medium",
            empty_observed="список литературы не найден",
        ))

    years = [entry.year_extracted for entry in entries if entry.year_extracted is not None]
    coverage = len(years) / len(entries)
    if coverage < expected.min_year_coverage:
        return ([], 0, _counted_assessment(
            rule,
            [],
            0,
            expected_text=expected_text,
            confidence="low",
            empty_observed="год издания определён у слишком малой доли источников",
            details={
                "sources": len(entries),
                "years_known": len(years),
                "year_coverage": round(coverage, 3),
            },
            note=(
                "свежесть источников не оценена: парсер определил год только у "
                f"{len(years)} из {len(entries)} записей"
            ),
        ))

    recent = [year for year in years if year >= threshold_year]
    share = len(recent) / len(years)
    blocks = _paragraph_index(document)
    violations: List[Violation] = []
    if share < expected.min_recent_share:
        violations.append(
            _build_violation(
                rule,
                address="bibliography",
                location=_bibliography_summary_location(entries, blocks),
                expected={
                    "min_recent_share": expected.min_recent_share,
                    "recent_years": expected.recent_years,
                    "reference_year": reference_year,
                },
                found={
                    "recent_share": round(share, 3),
                    "recent_sources": len(recent),
                    "years_known": len(years),
                    "threshold_year": threshold_year,
                },
                expected_text=expected_text,
                found_text=(
                    f"свежих источников: {len(recent)} из {len(years)} "
                    f"({share:.0%})"
                ),
                occurrences=len(years) - len(recent),
                confidence="medium",
            )
        )

    assessment = _counted_assessment(
        rule,
        violations,
        len(years),
        expected_text=expected_text,
        confidence="medium",
        empty_observed="год издания не определён ни у одной записи",
        details={
            "sources": len(entries),
            "years_known": len(years),
            "recent_sources": len(recent),
            "recent_share": round(share, 3),
            "threshold_year": threshold_year,
        },
    )
    return violations, len(years), assessment
