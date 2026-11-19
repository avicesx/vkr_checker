"""Правила подписей и выравнивания: нумерация, формат, ссылки, alignment.

Документы собираются синтетически: минимальный ParsedDocument с абзацами,
таблицами и ``cross_ref_index`` (подписи рисунков, таблиц и приложений), без
DOCX/PDF. Тесты фиксируют контракты новых видов правил:

* нумерация: дубли, пропуски (с ограничением перечня) и нарушение порядка,
  отдельно для рисунков и таблиц;
* формат подписи: префикс, разделитель, точка и регистр по тексту абзаца;
* ссылка на объект: неупомянутая таблица находится по ``is_mentioned_in_text``;
* выравнивание: подписи, ячейки таблиц и основной текст, ``justify`` = ``both``.

Каждый набор правил валидируется через ``RuleSet``: проверяется и движок, и
загрузка новых видов правил с их областями.
"""
from __future__ import annotations

from typing import Any, Dict, Sequence

import pytest

from parser.core.schema import (
    CrossReferenceIndex,
    DocumentMeta,
    GlobalSettings,
    NumberedCaptionEntity,
    PageCountInfo,
    ParagraphBlock,
    ParagraphPropertiesEffective,
    ParsedDocxDocument,
    TableBlock,
    TableCell,
    TableRow,
    ThemeData,
)
from rule_engine import RuleEngine, RuleSet
from rule_engine.engine import _caption_separator_kind
from rule_engine.rules import (
    AlignmentRule,
    CaptionFormatRule,
    CaptionNumberingRule,
    CaptionReferenceRule,
    SCOPE_LABELS,
)


# ---------------------------------------------------------------------
# Сборка документов
# ---------------------------------------------------------------------


def _paragraph(
    block_id: str,
    text: str,
    *,
    alignment: str = "left",
    alignment_source: str = "direct",
    container_type: str = "body",
    page: int = 2,
) -> ParagraphBlock:
    """Создает абзац с заданным выравниванием и типом контейнера."""
    return ParagraphBlock(
        block_id=block_id,
        section_idx=0,
        container_type=container_type,  # type: ignore[arg-type]
        page_start_est=page,
        page_end_est=page,
        text_raw=text,
        text_visible=text,
        effective_props=ParagraphPropertiesEffective(
            alignment=alignment,  # type: ignore[arg-type]
            alignment_source=alignment_source,  # type: ignore[arg-type]
        ),
    )


def _caption(
    entity_id: str,
    entity_type: str,
    number: str,
    *,
    prefix: str = "Рисунок",
    separator: str | None = "—",
    caption_block_id: str | None = None,
    page: int = 2,
    scheme: str = "continuous_arabic",
    title: str = "Схема",
    trailing_dot: bool = False,
    mentioned: bool = False,
) -> NumberedCaptionEntity:
    """Создает пронумерованную подпись с заданными полями."""
    return NumberedCaptionEntity(
        entity_id=entity_id,
        entity_type=entity_type,  # type: ignore[arg-type]
        caption_block_id=caption_block_id or f"cap_{entity_id}",
        page_est=page,
        prefix_word=prefix,
        number_str=number,
        numbering_scheme=scheme,  # type: ignore[arg-type]
        separator=separator,
        title_text=title,
        has_trailing_dot=trailing_dot,
        is_mentioned_in_text=mentioned,
    )


def _table(block_id: str, cell_paragraphs: Sequence[ParagraphBlock]) -> TableBlock:
    """Собирает таблицу из одной строки: по абзацу в каждой ячейке."""
    cells = [
        TableCell(
            cell_id=f"{block_id}_cell{index}",
            row_idx=0,
            col_idx=index,
            blocks=[paragraph],
        )
        for index, paragraph in enumerate(cell_paragraphs)
    ]
    return TableBlock(
        block_id=block_id,
        section_idx=0,
        rows=[TableRow(row_idx=0, cells=cells)],
    )


def _document(
    *,
    blocks: Sequence[Any] = (),
    figures: Sequence[NumberedCaptionEntity] = (),
    tables: Sequence[NumberedCaptionEntity] = (),
    apps: Sequence[NumberedCaptionEntity] = (),
) -> ParsedDocxDocument:
    """Собирает минимальный ParsedDocument с подписями и блоками."""
    return ParsedDocxDocument(
        meta=DocumentMeta(filename="vkr.docx", file_sha256="0" * 64),
        settings=GlobalSettings(),
        theme=ThemeData(),
        page_count=PageCountInfo(
            total_pages=3, method="docx_native_breaks", confidence="high"
        ),
        blocks=list(blocks),
        cross_ref_index=CrossReferenceIndex(
            figures=list(figures),
            tables=list(tables),
            Apps=list(apps),
        ),
    )


def _engine(*rules: Dict[str, Any], ruleset_id: str = "test-captions") -> RuleEngine:
    """Собирает движок из описаний правил, валидируя набор схемой."""
    payload = {
        "ruleset_id": ruleset_id,
        "ruleset_version": "1.0.0",
        "rules": list(rules),
    }
    return RuleEngine(RuleSet.model_validate(payload))


def _numbering_rule(expected: Dict[str, Any] | None = None, **overrides: Any) -> Dict[str, Any]:
    """Описание правила нумерации подписей."""
    rule: Dict[str, Any] = {
        "rule_id": "caption_numbering",
        "kind": "caption_numbering",
        "message": "Подписи нумеруются сквозной арабской нумерацией",
        "expected": expected or {},
    }
    rule.update(overrides)
    return rule


def _format_rule(expected: Dict[str, Any], **overrides: Any) -> Dict[str, Any]:
    """Описание правила оформления подписи."""
    rule: Dict[str, Any] = {
        "rule_id": "caption_format",
        "kind": "caption_format",
        "message": "Подпись оформлена по требованию",
        "expected": expected,
    }
    rule.update(overrides)
    return rule


def _reference_rule(expected: Dict[str, Any] | None = None, **overrides: Any) -> Dict[str, Any]:
    """Описание правила ссылки на рисунок или таблицу."""
    rule: Dict[str, Any] = {
        "rule_id": "caption_reference",
        "kind": "caption_reference",
        "message": "На объект есть ссылка в тексте",
        "expected": expected or {},
    }
    rule.update(overrides)
    return rule


def _alignment_rule(
    alignment: str = "center",
    *,
    scope: str = "captions",
    **overrides: Any,
) -> Dict[str, Any]:
    """Описание правила выравнивания для выбранной области."""
    rule: Dict[str, Any] = {
        "rule_id": f"alignment_{scope}",
        "kind": "alignment",
        "message": f"Выравнивание {alignment}",
        "target": {"scope": scope},
        "expected": {"alignment": alignment},
    }
    rule.update(overrides)
    return rule


# ---------------------------------------------------------------------
# Нумерация подписей
# ---------------------------------------------------------------------


def test_figure_duplicate_numbers_are_reported() -> None:
    """Повтор номера рисунка — нарушение с адресом второй подписи."""
    figures = [
        _caption("fig_001", "figure", "1", caption_block_id="cap_1"),
        _caption("fig_002", "figure", "2", caption_block_id="cap_2"),
        _caption("fig_003", "figure", "2", caption_block_id="cap_3", page=3),
    ]
    document = _document(figures=figures)

    report = _engine(_numbering_rule()).check(document)

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.kind == "caption_numbering"
    assert ":duplicate:2" in violation.violation_id
    assert violation.location.block_id == "cap_3"
    assert violation.location.page_start_est == 3
    assert violation.found["issue_kind"] == "duplicate"
    assert violation.found["number"] == "2"
    assert violation.found["previous_number"] == "cap_2"
    assert "уже использован" in violation.found_text
    assert report.summary.objects_checked == 3
    assert report.assessments[0].status == "fail"
    assert report.assessments[0].details["issues_total"] == 1


def test_figure_numbering_gap_is_reported() -> None:
    """Пропуск номера рисунка перечисляется по одному номеру."""
    figures = [
        _caption("fig_001", "figure", "1"),
        _caption("fig_002", "figure", "2"),
        _caption("fig_003", "figure", "4"),
    ]

    report = _engine(_numbering_rule()).check(_document(figures=figures))

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert ":gap:3" in violation.violation_id
    assert violation.found["issue_kind"] == "gap"
    assert violation.found["number"] == "3"
    assert violation.found["previous_number"] == "2"
    assert "пропущен номер 3" in violation.found_text


def test_gap_list_is_limited_to_ten_numbers() -> None:
    """Разрыв 1..100 не раздувает отчет: перечисляются только 10 пропусков."""
    figures = [
        _caption("fig_001", "figure", "1"),
        _caption("fig_002", "figure", "100"),
    ]

    report = _engine(_numbering_rule()).check(_document(figures=figures))

    gaps = {
        violation.found["number"]
        for violation in report.violations
        if violation.found["issue_kind"] == "gap"
    }
    assert gaps == {str(number) for number in range(2, 12)}
    assert report.summary.violations_total == 10


def test_table_duplicate_numbers_are_reported() -> None:
    """Повтор номера таблицы — нарушение, симметричное рисункам."""
    tables = [
        _caption("tbl_001", "table", "1", prefix="Таблица", caption_block_id="cap_t1"),
        _caption("tbl_002", "table", "1", prefix="Таблица", caption_block_id="cap_t2"),
    ]
    rule = _numbering_rule({"entity_type": "table"})

    report = _engine(rule).check(_document(tables=tables))

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert ":duplicate:1" in violation.violation_id
    assert violation.location.block_id == "cap_t2"
    assert violation.found["issue_kind"] == "duplicate"


def test_table_entity_type_checks_only_tables() -> None:
    """Правило для таблиц не проверяет рисунки и наоборот."""
    document = _document(
        figures=[
            _caption("fig_001", "figure", "1"),
            _caption("fig_002", "figure", "5"),
        ],
        tables=[
            _caption("tbl_001", "table", "1", prefix="Таблица", caption_block_id="cap_t1"),
            _caption("tbl_002", "table", "3", prefix="Таблица", caption_block_id="cap_t2"),
        ],
    )

    report = _engine(
        _numbering_rule({"entity_type": "table"})
    ).check(document)

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.location.block_id == "cap_t2"
    assert violation.found["number"] == "2"
    assert report.summary.objects_checked == 2


def test_figure_and_table_numbering_do_not_mix_in_any_mode() -> None:
    """В режиме any рисунки и таблицы нумеруются независимо: «таблица 1» не дубль."""
    document = _document(
        figures=[
            _caption("fig_001", "figure", "1"),
            _caption("fig_002", "figure", "2"),
        ],
        tables=[
            _caption("tbl_001", "table", "1", prefix="Таблица"),
            _caption("tbl_002", "table", "2", prefix="Таблица"),
        ],
    )

    report = _engine(_numbering_rule({"entity_type": "any"})).check(document)

    assert report.summary.violations_total == 0
    assert report.assessments[0].status == "pass"
    assert report.summary.objects_checked == 4


def test_out_of_order_number_is_reported() -> None:
    """Номер меньше предыдущего — нарушение порядка, когда пропуски разрешены."""
    figures = [
        _caption("fig_001", "figure", "1"),
        _caption("fig_002", "figure", "4"),
        _caption("fig_003", "figure", "2", caption_block_id="cap_back"),
    ]

    report = _engine(
        _numbering_rule({"allow_gaps": True})
    ).check(_document(figures=figures))

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert ":out_of_order:2" in violation.violation_id
    assert violation.location.block_id == "cap_back"
    assert violation.found["issue_kind"] == "out_of_order"
    assert violation.found["previous_number"] == "4"
    assert "идет после 4" in violation.found_text


def test_allow_gaps_suppresses_gap_violations() -> None:
    """Разрешение пропусков отключает только проверку пропусков."""
    figures = [
        _caption("fig_001", "figure", "1"),
        _caption("fig_002", "figure", "4"),
    ]

    report = _engine(
        _numbering_rule({"allow_gaps": True})
    ).check(_document(figures=figures))

    assert report.summary.violations_total == 0
    assert report.assessments[0].status == "pass"


def test_allow_duplicates_suppresses_duplicate_violations() -> None:
    """Разрешение дублей отключает только проверку повторов."""
    figures = [
        _caption("fig_001", "figure", "1"),
        _caption("fig_002", "figure", "1"),
    ]

    report = _engine(
        _numbering_rule({"allow_duplicates": True})
    ).check(_document(figures=figures))

    assert report.summary.violations_total == 0


def test_caption_numbering_without_entities_is_not_assessed() -> None:
    """Нет подписей — проверять нумерацию нечего."""
    report = _engine(_numbering_rule()).check(_document())

    assert report.summary.violations_total == 0
    assert report.summary.objects_checked == 0
    assert report.summary.not_assessed_total == 1
    assert "проверять нумерацию нечего" in report.not_assessed[0].observed_text


def test_letter_numbering_is_not_assessed_as_continuous() -> None:
    """Буквенные номера приложений не участвуют в сквозной арабской нумерации."""
    apps = [
        _caption("app_001", "appendix", "А", prefix="Приложение", scheme="appendix_based"),
        _caption("app_002", "appendix", "Б", prefix="Приложение", scheme="appendix_based"),
    ]

    report = _engine(
        _numbering_rule({"entity_type": "appendix"})
    ).check(_document(apps=apps))

    assert report.summary.violations_total == 0
    assert report.summary.not_assessed_total == 1
    assert "нет сквозных арабских номеров" in report.not_assessed[0].observed_text


# ---------------------------------------------------------------------
# Формат подписи
# ---------------------------------------------------------------------


def test_caption_format_reports_separator_and_trailing_dot() -> None:
    """Расхождения одной подписи собираются в одно нарушение с обоими полями."""
    blocks = [
        _paragraph("cap_1", "Рис. 1. Схема."),
        _paragraph("cap_2", "Рисунок 2 — Схема"),
    ]
    figures = [
        _caption(
            "fig_001",
            "figure",
            "1",
            prefix="Рис.",
            separator=".",
            caption_block_id="cap_1",
            trailing_dot=True,
        ),
        _caption("fig_002", "figure", "2", caption_block_id="cap_2"),
    ]
    rule = _format_rule({"separator": "dash", "require_trailing_dot": False})

    report = _engine(rule).check(_document(blocks=blocks, figures=figures))

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.violation_id == "caption_format:fig_001"
    assert violation.location.block_id == "cap_1"
    assert violation.expected == {"separator": "dash", "has_trailing_dot": False}
    assert violation.found["separator"] == "dot"
    assert violation.found["has_trailing_dot"] is True
    assert violation.occurrences == 2
    assert "тире" in violation.expected_text
    assert "без точки" in violation.expected_text
    assert report.summary.objects_checked == 2


def test_caption_format_prefix_words_are_normalized() -> None:
    """Префикс сравнивается без регистра и завершающей точки."""
    blocks = [
        _paragraph("cap_1", "Рис. 1 — Схема"),
        _paragraph("cap_2", "Иллюстрация 2 — Схема"),
    ]
    figures = [
        _caption(
            "fig_001", "figure", "1", prefix="Рис.", caption_block_id="cap_1"
        ),
        _caption(
            "fig_002", "figure", "2", prefix="Иллюстрация", caption_block_id="cap_2"
        ),
    ]
    rule = _format_rule({"prefix_words": ["Рисунок", "Рис."]})

    report = _engine(rule).check(_document(blocks=blocks, figures=figures))

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.violation_id == "caption_format:fig_002"
    assert violation.found["prefix_word"] == "Иллюстрация"
    assert "Рисунок" in violation.expected_text
    # Вариант «Рис.» входит в перечень и нарушением не считается.
    assert "cap_1" not in {item.location.block_id for item in report.violations}


def test_caption_format_uppercase_reads_caption_block() -> None:
    """Регистр проверяется по тексту абзаца подписи, а не по полям сущности."""
    blocks = [
        _paragraph("cap_1", "Приложение А. Стенд"),
        _paragraph("cap_2", "ПРИЛОЖЕНИЕ Б. СТЕНД"),
    ]
    apps = [
        _caption(
            "app_001",
            "appendix",
            "А",
            prefix="Приложение",
            separator=".",
            caption_block_id="cap_1",
        ),
        _caption(
            "app_002",
            "appendix",
            "Б",
            prefix="ПРИЛОЖЕНИЕ",
            separator=".",
            caption_block_id="cap_2",
        ),
    ]
    rule = _format_rule({"entity_type": "appendix", "uppercase": True})

    report = _engine(rule).check(_document(blocks=blocks, apps=apps))

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.violation_id == "caption_format:app_001"
    assert violation.expected["uppercase"] is True
    assert violation.found["uppercase"] is False
    assert "не прописными" in violation.found_text


def test_caption_format_skips_uppercase_without_caption_block() -> None:
    """Абзаца подписи нет в разборе: регистр не измерен, нарушение не выдумано."""
    apps = [
        _caption(
            "app_001",
            "appendix",
            "А",
            prefix="Приложение",
            caption_block_id="cap_missing",
        )
    ]
    rule = _format_rule({"uppercase": True})

    report = _engine(rule).check(_document(apps=apps))

    assert report.summary.violations_total == 0
    assert report.summary.not_assessed_total == 1
    assessment = report.not_assessed[0]
    assert assessment.details["skipped_uppercase"] == 1
    assert "не измерено" in assessment.observed_text


def test_caption_format_without_entities_is_not_assessed() -> None:
    """Нет подписей — проверять формат нечего."""
    report = _engine(_format_rule({"separator": "dash"})).check(_document())

    assert report.summary.violations_total == 0
    assert report.summary.not_assessed_total == 1
    assert "проверять оформление нечего" in report.not_assessed[0].observed_text


@pytest.mark.parametrize(
    ("separator", "title_text", "expected_kind"),
    [
        # Разделителя нет: без названия и с явной пустой строкой.
        (None, "", "none"),
        (None, "   ", "none"),
        ("", "Данные", "none"),
        # Пробельный разделитель, в том числе NBSP и строка из пробелов.
        (" ", "Данные", "space"),
        ("\u00a0", "Данные", "space"),
        ("   ", "", "space"),
        ("\t", "Данные", "space"),
        # Тире: проверяется до пробельного вывода, чтобы «— » не стал space.
        ("—", "Данные", "dash"),
        ("–", "Данные", "dash"),
        ("-", "Данные", "dash"),
        ("  —  ", "Данные", "dash"),
        # Точка, в том числе с пробелом перед ней.
        (".", "Данные", "dot"),
        (" .", "Данные", "dot"),
        # Прочие разделители считаются пробельными: «номер, затем название».
        (":", "Данные", "space"),
        ("|", "Данные", "space"),
    ],
)
def test_caption_separator_kind_mapping(
    separator: str | None,
    title_text: str,
    expected_kind: str,
) -> None:
    """Классификатор разделителя покрывает все документированные значения."""
    assert _caption_separator_kind(separator, title_text) == expected_kind


def test_caption_separator_kind_none_without_title_is_none() -> None:
    """None без названия — разделителя нет; название не передано — тоже none."""
    assert _caption_separator_kind(None) == "none"


def test_caption_format_space_separator_accepts_space_caption() -> None:
    """separator=space: «Таблица 1 Данные» проходит, «Таблица 1—Данные» — нет.

    Пробельную форму разбор хранит как ``separator=None`` при непустом
    названии, поэтому проверяется именно этот случай, а не строка из пробелов.
    """
    blocks = [
        _paragraph("cap_t1", "Таблица 1 Данные"),
        _paragraph("cap_t2", "Таблица 2—Данные", page=3),
    ]
    tables = [
        _caption(
            "tbl_001",
            "table",
            "1",
            prefix="Таблица",
            separator=None,
            caption_block_id="cap_t1",
            title="Данные",
        ),
        _caption(
            "tbl_002",
            "table",
            "2",
            prefix="Таблица",
            separator="—",
            caption_block_id="cap_t2",
            title="Данные",
            page=3,
        ),
    ]
    rule = _format_rule({"entity_type": "table", "separator": "space"})

    report = _engine(rule).check(_document(blocks=blocks, tables=tables))

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.violation_id == "caption_format:tbl_002"
    assert violation.expected["separator"] == "space"
    assert violation.found["separator"] == "dash"
    assert "пробел" in violation.expected_text
    assert "тире" in violation.found_text


def test_caption_format_colon_counts_as_space_separator() -> None:
    """Двоеточие документировано как пробельный разделитель, а не как «нет»."""
    blocks = [_paragraph("cap_1", "Рис. 1: Схема")]
    figures = [
        _caption(
            "fig_001",
            "figure",
            "1",
            prefix="Рис.",
            separator=":",
            caption_block_id="cap_1",
            title="Схема",
        )
    ]

    space_rule = _engine(
        _format_rule({"separator": "space"})
    ).check(_document(blocks=blocks, figures=figures))
    dash_rule = _engine(
        _format_rule({"separator": "dash"})
    ).check(_document(blocks=blocks, figures=figures))

    assert space_rule.summary.violations_total == 0
    assert dash_rule.summary.violations_total == 1
    assert dash_rule.violations[0].found["separator"] == "space"


def test_caption_format_none_separator_allows_caption_without_title() -> None:
    """Подпись без названия («Приложение А») проходит требование separator=none."""
    blocks = [_paragraph("cap_1", "Приложение А")]
    apps = [
        _caption(
            "app_001",
            "appendix",
            "А",
            prefix="Приложение",
            separator=None,
            caption_block_id="cap_1",
            scheme="appendix_based",
            title="",
        )
    ]
    rule = _format_rule({"entity_type": "appendix", "separator": "none"})

    report = _engine(rule).check(_document(blocks=blocks, apps=apps))

    assert report.summary.violations_total == 0
    assert report.assessments[0].status == "pass"


# ---------------------------------------------------------------------
# Ссылки на рисунки и таблицы
# ---------------------------------------------------------------------


def test_unmentioned_table_is_reported() -> None:
    """Таблица без ссылки в тексте — нарушение; рисунки правилом не проверяются."""
    document = _document(
        figures=[_caption("fig_001", "figure", "1", mentioned=False)],
        tables=[
            _caption("tbl_001", "table", "1", prefix="Таблица", mentioned=True),
            _caption(
                "tbl_002",
                "table",
                "2",
                prefix="Таблица",
                caption_block_id="cap_t2",
                mentioned=False,
            ),
        ],
    )
    rule = _reference_rule({"entity_type": "table"})

    report = _engine(rule).check(document)

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.violation_id == "caption_reference:tbl_002"
    assert violation.location.block_id == "cap_t2"
    assert violation.expected["is_mentioned_in_text"] is True
    assert violation.found["is_mentioned_in_text"] is False
    assert "Таблица 2" in violation.found_text
    assert report.summary.objects_checked == 2
    assert report.assessments[0].status == "fail"


def test_all_objects_mentioned_passes() -> None:
    """Все рисунки упомянуты — нарушений нет."""
    figures = [
        _caption("fig_001", "figure", "1", mentioned=True),
        _caption("fig_002", "figure", "2", mentioned=True),
    ]

    report = _engine(_reference_rule()).check(_document(figures=figures))

    assert report.summary.violations_total == 0
    assert report.assessments[0].status == "pass"
    assert report.summary.objects_checked == 2


def test_caption_reference_any_covers_figures_and_tables_without_appendices() -> None:
    """Режим any проверяет рисунки и таблицы; приложения в выборку не входят."""
    document = _document(
        figures=[_caption("fig_001", "figure", "1", mentioned=False)],
        tables=[
            _caption("tbl_001", "table", "1", prefix="Таблица", mentioned=False)
        ],
        apps=[
            _caption(
                "app_001",
                "appendix",
                "А",
                prefix="Приложение",
                scheme="appendix_based",
                mentioned=False,
            )
        ],
    )

    report = _engine(_reference_rule({"entity_type": "any"})).check(document)

    assert report.summary.violations_total == 2
    assert {violation.violation_id for violation in report.violations} == {
        "caption_reference:fig_001",
        "caption_reference:tbl_001",
    }
    assert report.summary.objects_checked == 2


def test_caption_reference_without_entities_is_not_assessed() -> None:
    """Нет рисунков и таблиц — проверять ссылки нечего."""
    report = _engine(_reference_rule()).check(_document())

    assert report.summary.violations_total == 0
    assert report.summary.not_assessed_total == 1
    assert "проверять ссылки нечего" in report.not_assessed[0].observed_text


# ---------------------------------------------------------------------
# Выравнивание
# ---------------------------------------------------------------------


def test_alignment_captions_reports_mismatch() -> None:
    """Выравнивание подписи сверяется с ожиданием по абзацу подписи."""
    blocks = [
        _paragraph("cap_1", "Рисунок 1 — Схема", alignment="left"),
        _paragraph("cap_2", "Рисунок 2 — Схема", alignment="center"),
    ]
    figures = [
        _caption("fig_001", "figure", "1", caption_block_id="cap_1"),
        _caption("fig_002", "figure", "2", caption_block_id="cap_2"),
    ]

    report = _engine(_alignment_rule("center")).check(
        _document(blocks=blocks, figures=figures)
    )

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.kind == "alignment"
    assert violation.location.block_id == "cap_1"
    assert violation.expected["alignment"] == "center"
    assert violation.found["alignment"] == "left"
    assert "по центру" in violation.expected_text
    assert "по левому краю" in violation.found_text
    assert report.summary.objects_checked == 2


def test_alignment_table_cells_checks_cell_paragraphs() -> None:
    """Область table_cells проверяет абзацы ячеек и не трогает текст вне таблиц."""
    cell_ok = _paragraph(
        "cell_ok", "Данные", alignment="center", container_type="table_cell"
    )
    cell_bad = _paragraph(
        "cell_bad", "Значение", alignment="left", container_type="table_cell"
    )
    body = _paragraph("body_1", "Основной текст работы.", alignment="left")
    document = _document(
        blocks=[body, _table("tbl_block", [cell_ok, cell_bad])]
    )

    report = _engine(_alignment_rule("center", scope="table_cells")).check(document)

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.location.block_id == "cell_bad"
    assert violation.location.container_type == "table_cell"
    assert report.summary.objects_checked == 2


def test_alignment_body_text_maps_justify_to_both() -> None:
    """Требование justify выполняется выравниванием both (по ширине)."""
    blocks = [
        _paragraph("p_1", "Текст выровнен по ширине.", alignment="both"),
        _paragraph("p_2", "Текст прижат влево.", alignment="left"),
    ]

    report = _engine(_alignment_rule("justify", scope="body_text")).check(
        _document(blocks=blocks)
    )

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.location.block_id == "p_2"
    assert violation.found["alignment"] == "left"
    assert "по ширине" in violation.expected_text


def test_alignment_without_objects_is_not_assessed() -> None:
    """Пустая область: выравнивание не измерено, статус not_assessed."""
    report = _engine(_alignment_rule("center", scope="body_text")).check(_document())

    assert report.summary.violations_total == 0
    assert report.summary.objects_checked == 0
    assert report.summary.not_assessed_total == 1
    assert "выравнивание не измерено" in report.not_assessed[0].observed_text


def test_alignment_skips_caption_without_block() -> None:
    """Подпись без абзаца в разборе не считается объектом с измеренным выравниванием."""
    figures = [
        _caption("fig_001", "figure", "1", caption_block_id="cap_missing")
    ]

    report = _engine(_alignment_rule("center")).check(_document(figures=figures))

    assert report.summary.violations_total == 0
    assert report.summary.not_assessed_total == 1
    assert report.not_assessed[0].details["skipped_unmeasured"] == 1


# ---------------------------------------------------------------------
# Схема набора правил: области, обязательность полей, обратная совместимость
# ---------------------------------------------------------------------


def test_caption_kinds_load_with_own_scope() -> None:
    """Правила подписей по умолчанию работают в области captions."""
    engine = _engine(
        _numbering_rule(),
        _format_rule({"separator": "dash"}),
        _reference_rule(),
    )

    assert [rule.target.scope for rule in engine.ruleset.rules] == [
        "captions",
        "captions",
        "captions",
    ]
    assert isinstance(engine.ruleset.rules[0], CaptionNumberingRule)
    assert isinstance(engine.ruleset.rules[1], CaptionFormatRule)
    assert isinstance(engine.ruleset.rules[2], CaptionReferenceRule)


def test_alignment_rule_defaults_to_captions_scope() -> None:
    """Правило выравнивания по умолчанию проверяет подписи."""
    engine = _engine(_alignment_rule())

    assert isinstance(engine.ruleset.rules[0], AlignmentRule)
    assert engine.ruleset.rules[0].target.scope == "captions"


@pytest.mark.parametrize("scope", ["captions", "table_cells", "body_text"])
def test_alignment_rule_accepts_supported_scopes(scope: str) -> None:
    """Выравнивание осмысленно для подписей, ячеек таблиц и основного текста."""
    engine = _engine(_alignment_rule("center", scope=scope))

    assert engine.ruleset.rules[0].target.scope == scope


def test_alignment_rule_rejects_sections_scope() -> None:
    """У секции нет выравнивания абзаца: область sections запрещена."""
    with pytest.raises(ValueError, match="scope должен быть одним из"):
        _engine(_alignment_rule("center", scope="sections"))


def test_caption_kind_rejects_foreign_scope() -> None:
    """Правило подписей не применяется к основному тексту."""
    with pytest.raises(ValueError, match="scope должен быть одним из"):
        _engine(_numbering_rule(target={"scope": "body_text"}))


def test_caption_numbering_expectation_requires_a_check() -> None:
    """Конфигурация, разрешающая всё сразу, ничего не проверяет и отвергается."""
    with pytest.raises(ValueError, match="пусто"):
        _engine(
            _numbering_rule(
                {
                    "allow_gaps": True,
                    "allow_duplicates": True,
                    "allow_out_of_order": True,
                }
            )
        )


def test_caption_format_expectation_requires_a_property() -> None:
    """Правило формата без единого свойства отвергается загрузкой набора."""
    with pytest.raises(ValueError, match="пусто"):
        _engine(_format_rule({}))


def test_caption_format_empty_prefix_words_are_rejected() -> None:
    """Пустой перечень префиксов неоднозначен и отвергается."""
    with pytest.raises(ValueError, match="prefix_words пуст"):
        _engine(_format_rule({"prefix_words": ["  ", ""]}))


def test_caption_reference_required_false_is_rejected() -> None:
    """Отключенная проверка ссылок — ошибка набора, а не «мягкий режим»."""
    with pytest.raises(ValueError, match="пусто"):
        _engine(_reference_rule({"required": False}))


def test_scope_labels_cover_new_areas() -> None:
    """Новые области имеют русские названия для сообщений об ошибках набора."""
    assert SCOPE_LABELS["bibliography"] == "список литературы"
    assert SCOPE_LABELS["captions"] == "подписи рисунков и таблиц"
