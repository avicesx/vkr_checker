"""Семейство подписей: нумерация, формат, ссылки на объекты."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from ..findings import (
    RuleAssessment,
    Violation,
    ViolationLocation,
)
from ..rules import (
    CaptionPlacementRule,
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
    ALIGNMENT_LABELS,
    CAPTION_ENTITY_LABELS,
    CAPTION_GAP_LIMIT,
    CAPTION_PREFIX_FAMILIES,
    CAPTION_SCHEME_LABELS,
    CAPTION_SEPARATOR_LABELS,
)
from parser.core.schema import (
    NumberedCaptionEntity,
    ParagraphBlock,
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
    expected = rule.params
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

    extra_violations: List[Violation] = []
    scheme_labels = {
        key: CAPTION_SCHEME_LABELS.get(key, key)
        for key in (
            "continuous_arabic",
            "section_based",
            "appendix_based",
            "invalid",
        )
    }
    if expected.schemes is not None:
        allowed_schemes = set(expected.schemes)
        for entity in entities:
            entity_scheme = entity.numbering_scheme or "invalid"
            if entity_scheme in allowed_schemes:
                continue
            extra_violations.append(
                _build_violation(
                    rule,
                    address=f"{entity.entity_id}:scheme",
                    location=ViolationLocation(
                        block_id=entity.caption_block_id,
                        page_start_est=_safe_page_est(entity.page_est),
                        snippet=_collapse_text(_caption_display(entity)),
                    ),
                    expected={
                        "scheme": ", ".join(
                            scheme_labels[key] for key in expected.schemes
                        )
                    },
                    found={"scheme": scheme_labels[entity_scheme]},
                    expected_text=(
                        "нумерация: "
                        + ", ".join(scheme_labels[key] for key in expected.schemes)
                    ),
                    found_text=f"нумерация: {scheme_labels[entity_scheme]}",
                )
            )

    if expected.appendix != "any":
        for entity in entities:
            if entity.entity_type != "appendix":
                continue
            entity_scheme = entity.numbering_scheme or "invalid"
            if expected.appendix == "own" and entity_scheme == "continuous_arabic":
                extra_violations.append(
                    _build_violation(
                        rule,
                        address=f"{entity.entity_id}:appendix_numbering",
                        location=ViolationLocation(
                            block_id=entity.caption_block_id,
                            page_start_est=_safe_page_est(entity.page_est),
                            snippet=_collapse_text(_caption_display(entity)),
                        ),
                        expected={"appendix_numbering": "own"},
                        found={"appendix_numbering": entity_scheme},
                        expected_text="самостоятельная нумерация в приложении",
                        found_text="нумерация продолжает основной текст",
                    )
                )
            elif expected.appendix == "continuous" and entity_scheme == "appendix_based":
                extra_violations.append(
                    _build_violation(
                        rule,
                        address=f"{entity.entity_id}:appendix_numbering",
                        location=ViolationLocation(
                            block_id=entity.caption_block_id,
                            page_start_est=_safe_page_est(entity.page_est),
                            snippet=_collapse_text(_caption_display(entity)),
                        ),
                        expected={"appendix_numbering": "continuous"},
                        found={"appendix_numbering": entity_scheme},
                        expected_text="нумерация продолжается в приложении",
                        found_text="в приложении своя нумерация",
                    )
                )

    if expected.skip_number_when_single and len(entities) == 1:
        single_entity = entities[0]
        if _caption_number(single_entity) is not None:
            extra_violations.append(
                _build_violation(
                    rule,
                    address=f"{single_entity.entity_id}:single_number",
                    location=ViolationLocation(
                        block_id=single_entity.caption_block_id,
                        page_start_est=_safe_page_est(single_entity.page_est),
                        snippet=_collapse_text(_caption_display(single_entity)),
                    ),
                    expected={"number": None, "single_object": True},
                    found={"number": single_entity.number_str},
                    expected_text="у единственного объекта номер не ставят",
                    found_text=f"единственный объект пронумерован: {single_entity.number_str}",
                )
            )

    violations: List[Violation] = list(extra_violations)
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
    schemes_checked = (
        expected.schemes is not None
        or expected.appendix != "any"
        or expected.skip_number_when_single
    )
    if arabic_numbers == 0 and not schemes_checked:
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
        f"нарушений нумерации: {len(violations)} из {len(entities)} подписей"
        if violations
        else f"нумерация {len(entities)} подписей корректна"
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
    expected = rule.params
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

        if expected.capitalize_first_letter is not None:
            title_letters = [
                char for char in (entity.title_text or "") if char.isalpha()
            ]
            if title_letters:
                measurable = True
                actual_capitalized = title_letters[0].isupper()
                if actual_capitalized != expected.capitalize_first_letter:
                    expected_values["capitalize_first_letter"] = (
                        expected.capitalize_first_letter
                    )
                    found_values["capitalize_first_letter"] = actual_capitalized
                    expected_parts.append(
                        "название с заглавной буквы"
                        if expected.capitalize_first_letter
                        else "название со строчной буквы"
                    )
                    found_parts.append(
                        "с заглавной"
                        if actual_capitalized
                        else "со строчной"
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
    сама называет объект, но ссылкой не считается. Для приложений
    проверяются записи ``cross_ref_index.Apps``: ссылка «приложение А».
    """
    expected = rule.params
    entities = (
        _caption_entities(document, expected.entity_type)
        if expected.entity_type == "appendix"
        else _referenced_caption_entities(document, expected.entity_type)
    )
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


def _block_order(document: ParsedDocxDocument) -> Dict[str, int]:
    """Возвращает позиции блоков в порядке документа."""
    return {
        block.block_id: index for index, block in enumerate(document.blocks)
    }


def _caption_target_block_id(
    document: ParsedDocxDocument,
    entity: NumberedCaptionEntity,
) -> Optional[str]:
    """Находит блок объекта, к которому относится подпись.

    У рисунка подпись привязана к визуальному объекту, у таблицы
    ``target_object_id`` — это блок таблицы, у приложения отдельного
    объекта нет.
    """
    if entity.entity_type == "figure":
        for visual in document.visuals.values():
            if visual.visual_id == entity.target_object_id:
                return visual.parent_block_id
        return None
    if entity.entity_type == "table":
        return entity.target_object_id
    return None


def _title_block_after(
    document: ParsedDocxDocument,
    caption_block_id: Optional[str],
) -> Optional[ParagraphBlock]:
    """Возвращает первый непустой абзац после блока подписи."""
    order = _block_order(document)
    if caption_block_id is None or caption_block_id not in order:
        return None
    start = order[caption_block_id] + 1
    for block in document.blocks[start:]:
        if not isinstance(block, ParagraphBlock):
            continue
        if block.text_visible.strip():
            return block
    return None


def _check_caption_placement_rule(
    rule: CaptionPlacementRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Проверяет положение и оформление подписи относительно объекта.

    Положение «над объектом/под объектом» определяется порядком блоков в
    разобранном документе. Для приложений дополнительно проверяется, что
    номер стоит выше названия. Если объект подписи не найден в разборе,
    свойство пропускается и попадает в ``details.skipped``, а не выдаётся
    находкой.
    """
    expected = rule.params
    entities = _caption_entities(document, expected.entity_type)
    entity_label = CAPTION_ENTITY_LABELS.get(
        expected.entity_type, "объект с подписью"
    )
    expected_text = "подпись расположена и оформлена по требованию"
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
                    "проверять положение нечего"
                ),
                confidence="low",
                details={
                    "entities_considered": 0,
                    "entity_type": expected.entity_type,
                },
            ),
        )

    blocks = _paragraph_index(document)
    order = _block_order(document)
    violations: List[Violation] = []
    assessed = 0
    checked_position = 0
    checked_alignment = 0
    skipped: List[str] = []
    expected_prefix = (
        _normalize_caption_prefix(expected.prefix_word)
        if expected.prefix_word is not None
        else None
    )

    for entity in entities:
        block = blocks.get(entity.caption_block_id)
        expected_values: Dict[str, Any] = {}
        found_values: Dict[str, Any] = {}
        expected_parts: List[str] = []
        found_parts: List[str] = []
        measurable = False

        if expected.position is not None:
            target_block_id = _caption_target_block_id(document, entity)
            caption_index = order.get(entity.caption_block_id)
            target_index = (
                order.get(target_block_id) if target_block_id is not None else None
            )
            if caption_index is None or target_index is None:
                skipped.append(f"{entity.entity_id}:position")
            else:
                checked_position += 1
                measurable = True
                actual_position = (
                    "above_object"
                    if caption_index < target_index
                    else "below_object"
                )
                wanted_position = (
                    "above_object"
                    if expected.position == "above_table"
                    else expected.position
                )
                if actual_position != wanted_position:
                    expected_values["position"] = wanted_position
                    found_values["position"] = actual_position
                    expected_parts.append(
                        "подпись над объектом"
                        if wanted_position == "above_object"
                        else "подпись под объектом"
                    )
                    found_parts.append(
                        "подпись над объектом"
                        if actual_position == "above_object"
                        else "подпись под объектом"
                    )

        if expected.alignment is not None:
            actual_alignment = (
                block.effective_props.alignment if block is not None else None
            )
            if actual_alignment is None:
                skipped.append(f"{entity.entity_id}:alignment")
            else:
                checked_alignment += 1
                measurable = True
                if actual_alignment != expected.alignment:
                    expected_values["alignment"] = expected.alignment
                    found_values["alignment"] = actual_alignment
                    expected_parts.append(ALIGNMENT_LABELS[expected.alignment])
                    found_parts.append(
                        ALIGNMENT_LABELS.get(actual_alignment, "не задано")
                    )

        if expected.prefix_word is not None and expected_prefix is not None:
            measurable = True
            actual_prefix = _normalize_caption_prefix(entity.prefix_word)
            if actual_prefix != expected_prefix:
                expected_values["prefix_word"] = expected.prefix_word
                found_values["prefix_word"] = entity.prefix_word
                expected_parts.append(f"подпись начинается с «{expected.prefix_word}»")
                found_parts.append(f"«{entity.prefix_word}»")

        if expected.forbid_quotes:
            caption_text = (
                block.text_visible
                if block is not None
                else _caption_display(entity)
            )
            if "«" in caption_text or '"' in caption_text:
                measurable = True
                expected_values["quotes"] = False
                found_values["quotes"] = True
                expected_parts.append("без кавычек в подписи")
                found_parts.append("подпись содержит кавычки")

        if expected.require_number:
            measurable = True
            if not entity.number_str:
                expected_values["number"] = True
                found_values["number"] = False
                expected_parts.append("номер в подписи")
                found_parts.append("номер отсутствует")

        if expected.title_case is not None:
            title_letters = [
                char for char in (entity.title_text or "") if char.isalpha()
            ]
            if title_letters:
                measurable = True
                title_is_upper = all(char.isupper() for char in title_letters)
                title_is_capitalized = title_letters[0].isupper()
                if expected.title_case == "uppercase":
                    actual_title_case = "uppercase" if title_is_upper else "other"
                else:
                    actual_title_case = (
                        "capitalized" if title_is_capitalized and not title_is_upper else "other"
                    )
                if actual_title_case != expected.title_case:
                    expected_values["title_case"] = expected.title_case
                    found_values["title_case"] = actual_title_case
                    expected_parts.append(
                        "название прописными буквами"
                        if expected.title_case == "uppercase"
                        else "название с заглавной буквы"
                    )
                    found_parts.append(
                        "прописными"
                        if title_is_upper
                        else ("с заглавной" if title_is_capitalized else "со строчной")
                    )

        if expected.entity_type == "appendix":
            title_block = _title_block_after(document, entity.caption_block_id)
            caption_index = order.get(entity.caption_block_id)
            title_index = (
                order.get(title_block.block_id) if title_block is not None else None
            )
            if caption_index is not None and title_index is not None:
                measurable = True
                if caption_index > title_index:
                    expected_values["number_position"] = "above_title"
                    found_values["number_position"] = "below_title"
                    expected_parts.append("номер приложения выше названия")
                    found_parts.append("номер приложения ниже названия")

        if measurable:
            assessed += 1
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
        "entities_assessed": assessed,
        "position_checked": checked_position,
        "alignment_checked": checked_alignment,
        "skipped": skipped,
        "mismatches_total": len(violations),
    }
    if assessed == 0:
        return (
            [],
            0,
            _build_assessment(
                rule,
                "not_assessed",
                expected_text=expected_text,
                observed_text=(
                    "положение подписей не измерено: объекты или абзацы "
                    "подписей отсутствуют в разборе"
                ),
                confidence="low",
                details=details,
            ),
        )
    status = "fail" if violations else "pass"
    observed_text = (
        f"подписей с нарушением положения: {len(violations)} из {assessed}"
        if violations
        else f"положение {assessed} подписей соответствует требованиям"
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
