"""Семейство подписей: нумерация, формат, ссылки на объекты."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from ..findings import (
    RuleAssessment,
    Violation,
    ViolationLocation,
)
from ..rules import (
    CaptionFormatRule,
    CaptionNumberingRule,
    CaptionReferenceRule,
)
from .builders import (
    _build_assessment,
    _build_violation,
    _collapse_text,
)
from .common import (
    _is_uppercase_text,
    _paragraph_index,
    _safe_page_est,
)
from .constants import (
    CAPTION_ENTITY_LABELS,
    CAPTION_GAP_LIMIT,
    CAPTION_PREFIX_FAMILIES,
    CAPTION_SEPARATOR_LABELS,
)
from parser.core.schema import (
    NumberedCaptionEntity,
    ParsedDocxDocument,
)


def _caption_entities(
    document: ParsedDocxDocument,
    entity_type: str,
) -> List[NumberedCaptionEntity]:
    """Возвращает подписи выбранного вида в порядке следования в документе."""
    index = document.cross_ref_index
    if entity_type == "figure":
        return list(index.figures)
    if entity_type == "table":
        return list(index.tables)
    if entity_type == "appendix":
        return list(index.Apps)
    return [*index.figures, *index.tables, *index.Apps]


def _referenced_caption_entities(
    document: ParsedDocxDocument,
    entity_type: str,
) -> List[NumberedCaptionEntity]:
    """Возвращает рисунки и таблицы, на которые в тексте должна быть ссылка.

    Приложения в выборку не входят: ссылка «приложение А» — отдельный вид
    ссылки, и ``CaptionReferenceExpectation`` говорит только о рисунках и
    таблицах.
    """
    index = document.cross_ref_index
    if entity_type == "figure":
        return list(index.figures)
    if entity_type == "table":
        return list(index.tables)
    return [*index.figures, *index.tables]


def _caption_number(entity: NumberedCaptionEntity) -> Optional[int]:
    """Возвращает арабский номер подписи или None, если он не арабский/не сквозной."""
    if entity.numbering_scheme != "continuous_arabic":
        return None
    number_str = (entity.number_str or "").strip()
    if not number_str.isdigit():
        return None
    return int(number_str)


def _normalize_caption_prefix(word: Optional[str]) -> str:
    """Нормализует слово-префикс подписи: регистр, пробелы и завершающая точка."""
    if not word:
        return ""
    return " ".join(word.split()).casefold().rstrip(".")


def _caption_family(entity: NumberedCaptionEntity) -> str:
    """Возвращает семейство нумерации: рисунки и таблицы нумеруются отдельно."""
    normalized = _normalize_caption_prefix(entity.prefix_word)
    return CAPTION_PREFIX_FAMILIES.get(normalized, entity.entity_type)


def _caption_separator_kind(
    separator: Optional[str],
    title_text: Optional[str] = None,
) -> str:
    """Переводит фактический разделитель подписи в имя требования.

    Разбор хранит в ``separator`` только пунктуационный разделитель, а
    пробельный не хранит вовсе: «Таблица 1 Данные» дает ``separator=None`` и
    непустой ``title_text``. Поэтому ``None`` с непустым названием — это
    пробел, а ``None`` без названия — разделителя нет. Пустая строка — тоже
    «разделителя нет», строка из пробелов (включая NBSP) — «пробел».

    Порядок проверок важен: тире и точка распознаются до пробельного
    вывода, иначе «— » превратилось бы в пробел. Любой не-тире и не-точка
    разделитель (":", "|" и т.п.) считается пробельным: подпись с ним читается
    как «номер, затем название», то есть тот же случай, что и обычный пробел.
    """
    if separator is None:
        return "space" if (title_text or "").strip() else "none"
    if separator == "":
        return "none"
    normalized = separator.replace("\u00a0", " ").strip()
    if not normalized:
        return "space"
    if any(dash in normalized for dash in ("—", "–", "-")):
        return "dash"
    if "." in normalized:
        return "dot"
    return "space"


def _caption_display(entity: NumberedCaptionEntity) -> str:
    """Короткое название подписи для текста отчета: «Рисунок 2»."""
    prefix = (entity.prefix_word or CAPTION_ENTITY_LABELS.get(entity.entity_type, "объект"))
    number = entity.number_str or "?"
    return f"{prefix} {number}".strip()


def _caption_numbering_texts(
    issue_kind: str,
    number_str: str,
    previous_str: Optional[str],
) -> Tuple[str, str]:
    """Формулирует ожидание и факт для нарушения нумерации подписей."""
    if issue_kind == "duplicate":
        return (
            "уникальный номер подписи",
            f"номер {number_str} уже использован подписью в блоке {previous_str}",
        )
    if issue_kind == "gap":
        return (
            f"следующий номер подписи после {previous_str}",
            f"пропущен номер {number_str}",
        )
    return (
        f"номер подписи больше {previous_str}",
        f"номер {number_str} идет после {previous_str}",
    )


def _check_caption_numbering_rule(
    rule: CaptionNumberingRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет сквозную арабскую нумерацию подписей выбранного вида.

    Проверка реализована здесь, а не берется из
    ``parser.docx.metadata_anomalies_parser``: движок работает и с
    ``pdf_only``, где DOCX-слой аномалий не запускается. Дубли, пропуски и
    нарушение порядка считаются по ``cross_ref_index``, а перечень
    пропусков ограничен ``CAPTION_GAP_LIMIT`` номерами подряд, чтобы разрыв
    1..1000 не раздувал отчет. Буквенные номера приложений и section-based
    номера («2.1») в последовательности не участвуют: они видны в счетчике
    объектов, но сквозной арабской нумерацией не являются.
    """
    expected = rule.expected
    entities = _caption_entities(document, expected.entity_type)
    entity_label = CAPTION_ENTITY_LABELS.get(
        expected.entity_type, "объект с подписью"
    )
    expected_text = (
        "сквозная арабская нумерация подписей без дублей, пропусков "
        "и нарушения порядка"
    )
    if not entities:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    f"в документе не найдено подписей ({entity_label}): "
                    "проверять нумерацию нечего"
                ),
                confidence="low",
                details={"entities_considered": 0, "entity_type": expected.entity_type},
            ),
        )

    issues: List[Tuple[NumberedCaptionEntity, str, str, Optional[str]]] = []
    seen: Dict[Tuple[str, str], str] = {}
    last_by_family: Dict[str, Tuple[int, str]] = {}
    arabic_numbers = 0
    for entity in entities:
        number = _caption_number(entity)
        if number is None:
            continue
        arabic_numbers += 1
        family = _caption_family(entity)
        number_key = (family, entity.number_str)
        if number_key in seen:
            if not expected.allow_duplicates:
                issues.append(
                    (entity, "duplicate", entity.number_str, seen[number_key])
                )
            continue
        seen[number_key] = entity.caption_block_id

        previous = last_by_family.get(family)
        if previous is not None:
            previous_number, previous_str = previous
            if number > previous_number + 1:
                if not expected.allow_gaps:
                    for missing in range(
                        previous_number + 1,
                        min(
                            number,
                            previous_number + 1 + CAPTION_GAP_LIMIT,
                        ),
                    ):
                        issues.append(
                            (entity, "gap", str(missing), previous_str)
                        )
            elif number < previous_number:
                if not expected.allow_out_of_order:
                    issues.append(
                        (entity, "out_of_order", entity.number_str, previous_str)
                    )
        last_by_family[family] = (number, entity.number_str)

    violations: List[Violation] = []
    for entity, issue_kind, number_str, previous_str in issues:
        expected_issue_text, found_issue_text = _caption_numbering_texts(
            issue_kind, number_str, previous_str
        )
        violations.append(
            _build_violation(
                rule,
                address=f"{entity.entity_id}:{issue_kind}:{number_str}",
                location=ViolationLocation(
                    block_id=entity.caption_block_id,
                    page_start_est=_safe_page_est(entity.page_est),
                    snippet=_collapse_text(_caption_display(entity)),
                ),
                expected={
                    "scheme": expected.scheme,
                    "numbering": "без дублей, пропусков и нарушения порядка",
                },
                found={
                    "issue_kind": issue_kind,
                    "number": number_str,
                    "previous_number": previous_str,
                    "entity_id": entity.entity_id,
                },
                expected_text=expected_issue_text,
                found_text=found_issue_text,
            )
        )

    details = {
        "entity_type": expected.entity_type,
        "entities_considered": len(entities),
        "numbered_arabic": arabic_numbers,
        "issues_total": len(issues),
    }
    if arabic_numbers == 0:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    "среди подписей нет сквозных арабских номеров "
                    "(буквенные или section-based номера): сверять последовательность не с чем"
                ),
                confidence="low",
                details=details,
            ),
        )
    status = "fail" if violations else "pass"
    observed_text = (
        f"нарушений нумерации: {len(violations)} из {arabic_numbers} нумерованных подписей"
        if violations
        else f"нумерация {arabic_numbers} подписей корректна"
    )
    return (
        violations,
        len(entities),
        _build_assessment(
            rule,
            status,
            expected_text=expected_text,
            observed_text=observed_text,
            confidence="high",
            details=details,
        ),
    )


def _check_caption_format_rule(
    rule: CaptionFormatRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет оформление подписи: префикс, разделитель, точка, регистр.

    Все расхождения одной подписи собираются в одно нарушение (как у
    правила шрифта): нормоконтролеру нужен один адрес, а ``occurrences``
    показывает, сколько свойств не совпало. Регистр проверяется по тексту
    самого абзаца подписи; если абзаца нет в разборе, свойство
    пропускается, а не считается нарушением.
    """
    expected = rule.expected
    entities = _caption_entities(document, expected.entity_type)
    entity_label = CAPTION_ENTITY_LABELS.get(
        expected.entity_type, "объект с подписью"
    )
    if not entities:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text="подпись оформлена по требованию",
                observed_text=(
                    f"в документе не найдено подписей ({entity_label}): "
                    "проверять оформление нечего"
                ),
                confidence="low",
                details={"entities_considered": 0, "entity_type": expected.entity_type},
            ),
        )

    blocks = _paragraph_index(document)
    allowed_prefixes: Optional[Set[str]] = None
    if expected.prefix_words is not None:
        allowed_prefixes = {
            _normalize_caption_prefix(word) for word in expected.prefix_words
        }
    violations: List[Violation] = []
    assessed_entities = 0
    skipped_uppercase = 0
    for entity in entities:
        block = blocks.get(entity.caption_block_id)
        expected_values: Dict[str, Any] = {}
        found_values: Dict[str, Any] = {}
        expected_parts: List[str] = []
        found_parts: List[str] = []
        measurable = False

        if allowed_prefixes is not None:
            measurable = True
            actual_prefix = _normalize_caption_prefix(entity.prefix_word)
            if actual_prefix not in allowed_prefixes:
                expected_values["prefix_word"] = list(expected.prefix_words or [])
                found_values["prefix_word"] = entity.prefix_word
                expected_parts.append(
                    "подпись начинается с "
                    + " / ".join(f"«{word}»" for word in expected.prefix_words or [])
                )
                found_parts.append(f"«{entity.prefix_word}»")

        if expected.separator is not None:
            measurable = True
            actual_separator = _caption_separator_kind(
                entity.separator, entity.title_text
            )
            if actual_separator != expected.separator:
                expected_values["separator"] = expected.separator
                found_values["separator"] = actual_separator
                expected_parts.append(
                    f"разделитель: {CAPTION_SEPARATOR_LABELS[expected.separator]}"
                )
                found_parts.append(
                    "разделитель: "
                    f"{CAPTION_SEPARATOR_LABELS.get(actual_separator, actual_separator)}"
                )

        if expected.require_trailing_dot is not None:
            measurable = True
            if entity.has_trailing_dot != expected.require_trailing_dot:
                expected_values["has_trailing_dot"] = expected.require_trailing_dot
                found_values["has_trailing_dot"] = entity.has_trailing_dot
                expected_parts.append(
                    "точка в конце подписи"
                    if expected.require_trailing_dot
                    else "без точки в конце подписи"
                )
                found_parts.append(
                    "точка есть" if entity.has_trailing_dot else "точки нет"
                )

        if expected.uppercase is not None:
            actual_case = _is_uppercase_text(
                block.text_visible if block is not None else None
            )
            if actual_case is None:
                skipped_uppercase += 1
            else:
                measurable = True
                if actual_case != expected.uppercase:
                    expected_values["uppercase"] = expected.uppercase
                    found_values["uppercase"] = actual_case
                    expected_parts.append(
                        "подпись прописными буквами"
                        if expected.uppercase
                        else "подпись не прописными буквами"
                    )
                    found_parts.append(
                        "прописными" if actual_case else "не прописными"
                    )

        if measurable:
            assessed_entities += 1
        if not expected_values:
            continue
        violations.append(
            _build_violation(
                rule,
                address=entity.entity_id,
                location=ViolationLocation(
                    block_id=entity.caption_block_id,
                    page_start_est=_safe_page_est(entity.page_est),
                    snippet=(
                        _collapse_text(block.text_visible)
                        if block is not None
                        else _collapse_text(_caption_display(entity))
                    ),
                ),
                expected=expected_values,
                found={**found_values, "entity_id": entity.entity_id},
                expected_text=", ".join(expected_parts),
                found_text=", ".join(found_parts),
                occurrences=len(expected_parts),
            )
        )

    details = {
        "entity_type": expected.entity_type,
        "entities_considered": len(entities),
        "entities_assessed": assessed_entities,
        "skipped_uppercase": skipped_uppercase,
        "mismatches_total": len(violations),
    }
    if assessed_entities == 0:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text="подпись оформлена по требованию",
                observed_text=(
                    "оформление подписей не измерено: абзацы подписей "
                    "отсутствуют в разборе, проверялся только регистр"
                ),
                confidence="low",
                details=details,
            ),
        )
    status = "fail" if violations else "pass"
    observed_text = (
        f"подписей с нарушением оформления: {len(violations)} из {assessed_entities}"
        if violations
        else f"оформление {assessed_entities} подписей соответствует требованиям"
    )
    return (
        violations,
        len(entities),
        _build_assessment(
            rule,
            status,
            expected_text="подпись оформлена по требованию",
            observed_text=observed_text,
            confidence="high",
            details=details,
        ),
    )


def _check_caption_reference_rule(
    rule: CaptionReferenceRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Ищет рисунки и таблицы, на которые нет ссылки в тексте работы.

    Признак ``is_mentioned_in_text`` вычисляет слой обогащения: подпись
    сама называет объект, но ссылкой не считается. Приложения в выборку не
    входят — ссылка «приложение А» проверяется отдельно.
    """
    expected = rule.expected
    entities = _referenced_caption_entities(document, expected.entity_type)
    entity_label = CAPTION_ENTITY_LABELS.get(expected.entity_type, "объект")
    expected_text = "объект упомянут в тексте работы"
    if not entities:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    f"в документе не найдено подписей ({entity_label}): "
                    "проверять ссылки нечего"
                ),
                confidence="low",
                details={"entities_considered": 0, "entity_type": expected.entity_type},
            ),
        )
    if not expected.required:
        # Модель отвергает такую конфигурацию; защита от копирования правила.
        return (
            [],
            len(entities),
            _build_assessment(
                rule,
                "pass",
                expected_text=expected_text,
                observed_text="проверка ссылок отключена требованием правила",
                confidence="high",
                details={"entities_considered": len(entities)},
            ),
        )

    violations: List[Violation] = []
    for entity in entities:
        if entity.is_mentioned_in_text:
            continue
        violations.append(
            _build_violation(
                rule,
                address=entity.entity_id,
                location=ViolationLocation(
                    block_id=entity.caption_block_id,
                    page_start_est=_safe_page_est(entity.page_est),
                    snippet=_collapse_text(_caption_display(entity)),
                ),
                expected={"is_mentioned_in_text": True},
                found={
                    "is_mentioned_in_text": False,
                    "entity_id": entity.entity_id,
                    "number": entity.number_str,
                },
                expected_text=expected_text,
                found_text=(
                    f"на «{_caption_display(entity)}» нет ссылки в тексте"
                ),
            )
        )

    status = "fail" if violations else "pass"
    observed_text = (
        f"без ссылки в тексте: {len(violations)} из {len(entities)} объектов"
        if violations
        else f"все {len(entities)} объектов упомянуты в тексте"
    )
    return (
        violations,
        len(entities),
        _build_assessment(
            rule,
            status,
            expected_text=expected_text,
            observed_text=observed_text,
            confidence="high",
            details={
                "entity_type": expected.entity_type,
                "entities_considered": len(entities),
                "unreferenced_entities": len(violations),
            },
        ),
    )
