"""Правила списка литературы: ссылки, цитирование и формат записи.

Документы собираются синтетически: минимальный ParsedDocument с
``cross_ref_index``, абзацами и записями списка литературы, без DOCX/PDF и без
слоя обогащения. Тесты фиксируют четыре контракта:

* неразрешенная ссылка ``[7]`` — нарушение с адресом упоминания;
* пустой список литературы — ``not_assessed``, а не выдуманное нарушение;
* нецитируемый источник находится по ``is_cited_in_text``;
* формат записи проверяется по разобранным полям с фильтром категорий.

Набор правил каждый раз валидируется через ``RuleSet``: тесты проверяют не
только движок, но и то, что новые виды правил и области проходят загрузку.
"""
from __future__ import annotations

from typing import Any, Dict, Sequence

import pytest

from parser.core.schema import (
    BibliographyEntry,
    CrossReferenceIndex,
    DocumentMeta,
    GlobalSettings,
    PageCountInfo,
    ParagraphBlock,
    ParsedDocxDocument,
    RunFontPropertiesEffective,
    TextCrossReferenceMention,
    TextRun,
    ThemeData,
)
from rule_engine import RuleEngine, RuleSet
from rule_engine.checks.bibliography import (
    _check_bibliography_format_rule,
    _check_bibliography_link_rule,
)
from rule_engine.rules import (
    BibliographyCitedRule,
    BibliographyFormatRule,
    BibliographyLinkRule,
)


# ---------------------------------------------------------------------
# Сборка документов и наборов правил
# ---------------------------------------------------------------------


def _paragraph(
    block_id: str,
    text: str,
    page: int = 2,
    runs: Sequence[TextRun] = (),
) -> ParagraphBlock:
    """Создает абзац основного текста с заданной страницей."""
    return ParagraphBlock(
        block_id=block_id,
        section_idx=0,
        page_start_est=page,
        page_end_est=page,
        text_raw=text,
        text_visible=text,
        runs=list(runs),
    )


def _hyperlink_run(text: str, target: str) -> TextRun:
    """Ран с гиперссылкой: видимый текст ссылки и ее адрес из rels."""
    return TextRun(
        run_id="r_0001",
        text=text,
        visible_text=text,
        start_char=0,
        end_char=len(text),
        effective_props=RunFontPropertiesEffective(
            font_ascii="Times New Roman",
            font_hansi="Times New Roman",
            font_cs="Times New Roman",
            size_pt=14.0,
        ),
        is_hyperlink=True,
        hyperlink_target=target,
    )


def _entry(
    bib_id: str = "bib_001",
    block_id: str = "p_0002",
    order: int = 1,
    *,
    raw_text: str = "Иванов И. И. Теория алгоритмов. — М. : Наука, 2018. — 320 с.",
    category: str = "unknown",
    cited: bool = True,
    **fields: Any,
) -> BibliographyEntry:
    """Создает запись списка литературы; поля записи задаются по необходимости."""
    payload: Dict[str, Any] = {
        "bib_id": bib_id,
        "block_id": block_id,
        "order_index": order,
        "raw_text": raw_text,
        "detected_category": category,
        "is_cited_in_text": cited,
    }
    payload.update(fields)
    return BibliographyEntry(**payload)


def _mention(
    mention_id: str = "men_001",
    block_id: str = "p_0001",
    page: int = 2,
    targets: Sequence[str] = ("1",),
    resolved: Sequence[str] = ("bib_001",),
    snippet: str = "[1]",
) -> TextCrossReferenceMention:
    """Создает упоминание источника в тексте с результатом разрешения."""
    return TextCrossReferenceMention(
        mention_id=mention_id,
        block_id=block_id,
        page_est=page,
        start_char=0,
        end_char=len(snippet),
        raw_snippet=snippet,
        ref_type="bibliography",
        extracted_targets=list(targets),
        resolved_entity_ids=list(resolved),
    )


def _document(
    *,
    entries: Sequence[BibliographyEntry] = (),
    mentions: Sequence[TextCrossReferenceMention] = (),
    blocks: Sequence[ParagraphBlock] = (),
) -> ParsedDocxDocument:
    """Собирает минимальный ParsedDocument с индексом перекрестных ссылок."""
    return ParsedDocxDocument(
        meta=DocumentMeta(filename="vkr.docx", file_sha256="0" * 64),
        settings=GlobalSettings(),
        theme=ThemeData(),
        page_count=PageCountInfo(
            total_pages=1, method="docx_native_breaks", confidence="high"
        ),
        blocks=list(blocks),
        cross_ref_index=CrossReferenceIndex(
            bibliography=list(entries),
            mentions=list(mentions),
        ),
    )


def _engine(*rules: Dict[str, Any], ruleset_id: str = "test-bibliography") -> RuleEngine:
    """Собирает движок из описаний правил, валидируя набор схемой."""
    payload = {
        "ruleset_id": ruleset_id,
        "ruleset_version": "1.0.0",
        "rules": list(rules),
    }
    return RuleEngine(RuleSet.model_validate(payload))


def _link_rule(**overrides: Any) -> Dict[str, Any]:
    """Описание правила разрешения ссылок на источники."""
    rule: Dict[str, Any] = {
        "rule_id": "bibliography_links",
        "code": "bibliography_link",
        "message": "Ссылки указывают на источники из списка литературы",
        "params": {},
    }
    rule.update(overrides)
    return rule


def _cited_rule(**overrides: Any) -> Dict[str, Any]:
    """Описание правила цитирования источников."""
    rule: Dict[str, Any] = {
        "rule_id": "bibliography_cited",
        "code": "bibliography_cited",
        "message": "Каждый источник списка литературы цитируется в тексте",
        "params": {},
    }
    rule.update(overrides)
    return rule


def _format_rule(params: Dict[str, Any], **overrides: Any) -> Dict[str, Any]:
    """Описание правила оформления записи списка литературы."""
    rule: Dict[str, Any] = {
        "rule_id": "bibliography_format",
        "code": "bibliography_format",
        "message": "Запись списка литературы оформлена по требованию",
        "params": params,
    }
    rule.update(overrides)
    return rule


def _mismatched_field(violation: Any) -> str:
    """Возвращает единственное поле, которого не хватает записи в нарушении."""
    fields = list(violation.expected)
    assert len(fields) == 1, f"ожидалось одно проверяемое поле: {fields}"
    return fields[0]


# ---------------------------------------------------------------------
# Ссылки на источники
# ---------------------------------------------------------------------


def test_unresolved_citation_is_violation() -> None:
    """Ссылка на номер, которого нет в списке, дает нарушение с адресом."""
    document = _document(
        entries=[_entry()],
        mentions=[
            _mention("men_001", "p_0002", 2, ("1",), ("bib_001",), "[1]"),
            _mention("men_002", "p_0003", 3, ("7",), (), "[7]"),
        ],
        blocks=[
            _paragraph("p_0002", "Обзор дан в работе [1].", page=2),
            _paragraph("p_0003", "Уточнение взято из [7].", page=3),
        ],
    )

    report = _engine(_link_rule()).check(document)

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.rule_id == "bibliography_links"
    assert violation.code == "bibliography_link"
    assert violation.location.block_id == "p_0003"
    assert violation.location.page_start_est == 3
    assert violation.expected["resolved_into_bibliography"] is True
    assert violation.found["resolved_into_bibliography"] is False
    assert violation.found["unresolved_targets"] == ["7"]
    assert "нет источников: 7" in violation.found_text
    assert report.summary.objects_checked == 2
    assert report.summary.not_assessed_total == 0
    assert [assessment.status for assessment in report.assessments] == ["fail"]
    assert report.assessments[0].details["unresolved_mentions"] == 1


def test_all_citations_resolved_pass() -> None:
    """Разрешенные ссылки не дают нарушений: проверка завершается статусом pass."""
    document = _document(
        entries=[_entry("bib_001", "p_0002", 1), _entry("bib_002", "p_0003", 2)],
        mentions=[
            _mention("men_001", "p_0001", 1, ("1",), ("bib_001",), "[1]"),
            _mention("men_002", "p_0001", 1, ("2",), ("bib_002",), "[2]"),
        ],
    )

    report = _engine(_link_rule()).check(document)

    assert report.summary.violations_total == 0
    assert report.summary.objects_checked == 2
    assert report.assessments[0].status == "pass"
    assert "все ссылки (2) разрешены" in report.assessments[0].observed_text


def test_empty_bibliography_is_not_assessed() -> None:
    """Пустой список литературы: движок не выдумывает нарушение, а не оценивает."""
    document = _document(
        mentions=[_mention("men_001", "p_0001", 1, ("3",), (), "[3]")],
        blocks=[_paragraph("p_0001", "Ссылка на источник [3].", page=1)],
    )

    report = _engine(_link_rule()).check(document)

    assert report.summary.violations_total == 0
    assert report.summary.objects_checked == 0
    assert report.summary.not_assessed_total == 1
    assessment = report.not_assessed[0]
    assert assessment.code == "bibliography_link"
    assert "список литературы пуст" in assessment.observed_text
    assert assessment.confidence == "low"
    assert assessment.details["bibliography_entries"] == 0


def test_missing_mentions_are_not_assessed() -> None:
    """Список есть, но ссылок в тексте нет: разрешать нечего, статус not_assessed."""
    document = _document(entries=[_entry()])

    report = _engine(_link_rule()).check(document)

    assert report.summary.violations_total == 0
    assert report.summary.objects_checked == 0
    assessment = report.not_assessed[0]
    assert "не найдено ссылок" in assessment.observed_text
    assert assessment.details["bibliography_entries"] == 1


def test_partially_resolved_citation_is_violation() -> None:
    """Перечисление «[1, 7]» разрешается частично: отсутствующий номер — нарушение."""
    document = _document(
        entries=[_entry("bib_001", "p_0002", 1)],
        mentions=[
            _mention("men_001", "p_0001", 1, ("1", "7"), ("bib_001",), "[1, 7]"),
        ],
        blocks=[_paragraph("p_0001", "Ссылки [1, 7].", page=1)],
    )

    report = _engine(_link_rule()).check(document)

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.found["resolved_into_bibliography"] is True
    assert violation.found["unresolved_targets"] == ["7"]
    assert "нет источников: 7" in violation.found_text
    assert report.assessments[0].details["unresolved_mentions"] == 1


def test_link_rule_ignores_non_bibliography_mentions() -> None:
    """Упоминание рисунка не считается ссылкой на источник, даже если не разрешено."""
    figure_mention = _mention("men_001", "p_0001", 1, ("2",), (), "рис. 2")
    figure_mention = figure_mention.model_copy(update={"ref_type": "figure"})
    document = _document(entries=[_entry()], mentions=[figure_mention])

    report = _engine(_link_rule()).check(document)

    # Ссылка на рисунок отброшена фильтром ref_type, поэтому ссылок на источники нет.
    assert report.summary.violations_total == 0
    assert report.summary.not_assessed_total == 1


# ---------------------------------------------------------------------
# Цитирование источников
# ---------------------------------------------------------------------


def test_uncited_sources_are_violations() -> None:
    """Источник без ссылки в тексте получает отдельное нарушение."""
    document = _document(
        entries=[
            _entry("bib_001", "p_0002", 1, cited=True),
            _entry(
                "bib_002",
                "p_0003",
                2,
                raw_text="Петров П. П. Обзор методов // Вестник науки. — 2020. — С. 12-15.",
                category="journal_article",
                cited=False,
            ),
        ],
        blocks=[
            _paragraph("p_0002", "Первый источник списка.", page=2),
            _paragraph("p_0003", "Второй источник списка.", page=3),
        ],
    )

    report = _engine(_cited_rule()).check(document)

    assert report.summary.violations_total == 1
    violation = report.violations[0]
    assert violation.violation_id == "bibliography_cited:bib_002"
    assert violation.code == "bibliography_cited"
    assert violation.location.block_id == "p_0003"
    assert violation.location.page_start_est == 3
    assert violation.expected["is_cited_in_text"] is True
    assert violation.found["is_cited_in_text"] is False
    assert violation.found["bib_id"] == "bib_002"
    assert "нет ссылки в тексте" in violation.found_text
    assert report.summary.objects_checked == 2
    assert report.assessments[0].status == "fail"
    assert report.assessments[0].details["uncited_entries"] == 1


def test_all_sources_cited_pass() -> None:
    """Все источники процитированы: нарушений нет, статус pass."""
    document = _document(
        entries=[_entry("bib_001", "p_0002", 1), _entry("bib_002", "p_0003", 2)]
    )

    report = _engine(_cited_rule()).check(document)

    assert report.summary.violations_total == 0
    assert report.assessments[0].status == "pass"
    assert report.summary.not_assessed_total == 0


def test_empty_bibliography_is_not_assessed_for_citation() -> None:
    """Пустой список: проверять цитирование нечего — not_assessed."""
    report = _engine(_cited_rule()).check(_document())

    assert report.summary.violations_total == 0
    assert report.summary.not_assessed_total == 1
    assessment = report.not_assessed[0]
    assert "проверять цитирование нечего" in assessment.observed_text


# ---------------------------------------------------------------------
# Формат записи списка литературы
# ---------------------------------------------------------------------


def test_bibliography_format_reports_each_missing_field() -> None:
    """Каждое невыполненное требование к записи — отдельное нарушение."""
    document = _document(
        entries=[
            _entry(
                "bib_001",
                "p_0002",
                1,
                raw_text="Электронный ресурс. — URL: отсутствует.",
                category="electronic_resource",
                urls=[],
                access_date_raw=None,
                year_extracted=None,
                pages_extracted=None,
            )
        ],
        blocks=[_paragraph("p_0002", "Электронный ресурс без URL.", page=4)],
    )
    rule = _format_rule(
        {
            "require_url": True,
            "require_access_date": True,
            "require_year": True,
            "require_pages": True,
        }
    )

    report = _engine(rule).check(document)

    assert report.summary.violations_total == 4
    assert report.summary.by_code == {"bibliography_format": 4}
    violation_ids = {violation.violation_id for violation in report.violations}
    assert violation_ids == {
        "bibliography_format:bib_001:urls",
        "bibliography_format:bib_001:access_date_raw",
        "bibliography_format:bib_001:year_extracted",
        "bibliography_format:bib_001:pages_extracted",
    }
    by_field = {_mismatched_field(violation): violation for violation in report.violations}
    assert by_field["urls"].expected == {"urls": True}
    assert by_field["urls"].found["urls"] == []
    assert by_field["urls"].location.page_start_est == 4
    assert by_field["year_extracted"].found["year_extracted"] is None
    assert "обязательно" in by_field["urls"].expected_text
    assert "отсутствует" in by_field["urls"].found_text
    assert report.summary.objects_checked == 1
    assert report.assessments[0].status == "fail"


def test_bibliography_format_compliant_entry_passes() -> None:
    """Запись со всеми обязательными элементами нарушений не дает."""
    document = _document(
        entries=[
            _entry(
                "bib_001",
                "p_0002",
                1,
                category="electronic_resource",
                raw_text=(
                    "Электронный ресурс. — URL: https://example.org/article?id=7. "
                    "— Дата обращения: 01.02.2021."
                ),
                urls=["https://example.org/article?id=7"],
                access_date_raw="01.02.2021",
                year_extracted=2021,
                pages_extracted="С. 12-15",
            )
        ]
    )
    rule = _format_rule(
        {
            "require_url": True,
            "require_access_date": True,
            "require_year": True,
            "require_pages": True,
        }
    )

    report = _engine(rule).check(document)

    assert report.summary.violations_total == 0
    assert report.assessments[0].status == "pass"
    assert report.assessments[0].details["entries_considered"] == 1


def test_bibliography_format_double_slash_and_type_designation() -> None:
    """Признаки «//» и обозначение вида издания проверяются как отдельные требования."""
    document = _document(
        entries=[
            _entry(
                "bib_001",
                "p_0002",
                1,
                category="journal_article",
                raw_text="Петров П. П. Обзор методов. Вестник науки. 2020.",
                has_double_slash_source=False,
                has_text_type_designation=False,
            )
        ]
    )
    rule = _format_rule(
        {"require_double_slash": True, "require_text_type_designation": True}
    )

    report = _engine(rule).check(document)

    assert report.summary.violations_total == 2
    fields = {_mismatched_field(violation) for violation in report.violations}
    assert fields == {"has_double_slash_source", "has_text_type_designation"}
    assert all(
        violation.found[_mismatched_field(violation)] is False
        for violation in report.violations
    )


def test_bibliography_format_categories_filter_limits_entries() -> None:
    """Фильтр categories проверяет только выбранные категории источников."""
    document = _document(
        entries=[
            _entry("bib_001", "p_0002", 1, category="book_monograph", urls=[]),
            _entry(
                "bib_002",
                "p_0003",
                2,
                category="electronic_resource",
                urls=["https://example.org/article"],
            ),
        ]
    )
    filtered_rule = _format_rule(
        {"require_url": True, "categories": ["electronic_resource"]}
    )

    filtered = _engine(filtered_rule).check(document)

    assert filtered.summary.violations_total == 0
    assert filtered.summary.objects_checked == 1
    assert filtered.assessments[0].status == "pass"
    assert filtered.assessments[0].details["categories"] == ["electronic_resource"]

    # Без фильтра книга без URL тоже нарушение: видно, что фильтр действительно работает.
    unfiltered = _engine(_format_rule({"require_url": True})).check(document)

    assert unfiltered.summary.violations_total == 1
    assert unfiltered.violations[0].found["bib_id"] == "bib_001"


def test_bibliography_format_without_matching_category_is_not_assessed() -> None:
    """Если записей выбранной категории нет, проверка не оценивается."""
    document = _document(
        entries=[_entry("bib_001", "p_0002", 1, category="book_monograph")]
    )
    rule = _format_rule(
        {"require_url": True, "categories": ["electronic_resource"]}
    )

    report = _engine(rule).check(document)

    assert report.summary.violations_total == 0
    assert report.summary.not_assessed_total == 1
    assert report.not_assessed[0].details["entries_considered"] == 0


# ---------------------------------------------------------------------
# Схема набора правил: новые виды, области и валидаторы
# ---------------------------------------------------------------------


def test_bibliography_codes_load_with_own_scope() -> None:
    """Правила библиографии по умолчанию работают в области bibliography."""
    engine = _engine(_link_rule(), _cited_rule(), _format_rule({"require_year": True}))

    assert [rule.target.scope for rule in engine.ruleset.rules] == [
        "bibliography",
        "bibliography",
        "bibliography",
    ]
    assert isinstance(engine.ruleset.rules[0], BibliographyLinkRule)
    assert isinstance(engine.ruleset.rules[1], BibliographyCitedRule)
    assert isinstance(engine.ruleset.rules[2], BibliographyFormatRule)


def test_bibliography_kind_rejects_foreign_scope() -> None:
    """Область body_text не применима к правилу библиографии."""
    with pytest.raises(ValueError, match="scope должен быть одним из"):
        _engine(_link_rule(target={"scope": "body_text"}))


def test_existing_kind_rejects_bibliography_scope() -> None:
    """Правило шрифта не может проверять список литературы: обратная совместимость."""
    with pytest.raises(ValueError, match="не применим к абзацам"):
        _engine(
            {
                "rule_id": "font_body",
                "code": "font",
                "message": "Times New Roman",
                "target": {"scope": "bibliography"},
                "params": {"family": "Times New Roman"},
            }
        )


def test_bibliography_format_requires_a_requirement() -> None:
    """Правило формата без единого требования отвергается загрузкой набора."""
    with pytest.raises(ValueError, match="пусто"):
        _engine(_format_rule({}))


def test_bibliography_format_categories_must_not_be_empty() -> None:
    """Пустой список категорий неоднозначен и отвергается."""
    with pytest.raises(ValueError, match="categories пуст"):
        _engine(_format_rule({"require_url": True, "categories": []}))


def test_bibliography_format_categories_are_deduplicated() -> None:
    """Повторы категорий схлопываются, чтобы выборка не зависела от записи."""
    engine = _engine(
        _format_rule(
            {
                "require_url": True,
                "categories": ["electronic_resource", "electronic_resource"],
            }
        )
    )

    rule = engine.ruleset.rules[0]
    assert isinstance(rule, BibliographyFormatRule)
    assert rule.params.categories == ["electronic_resource"]


# ---------------------------------------------------------------------
# Ложные срабатывания: код, гиперссылки и мягкий перенос
# ---------------------------------------------------------------------


def test_code_listing_brackets_are_not_bibliography_links() -> None:
    """Квадратные скобки листинга кода не считаются ссылками на источники.

    Бикчуркина хранит в листинге Python ``data[1800]`` и ``data[2400]``:
    обогащение видит в них ссылки, но в code_listing это индексы массивов.
    Правило пропускает такие упоминания, а настоящую ссылку в прозе проверяет.
    """
    document = _document(
        entries=[_entry()],
        mentions=[
            _mention("men_code", "p_0001", targets=("1800",), resolved=(), snippet="[1800]"),
            _mention("men_text", "p_0002", targets=("1",), resolved=("bib_001",)),
        ],
        blocks=[
            _paragraph("p_0001", "values = data[1800]"),
            _paragraph("p_0002", "Источник [1] описывает метод."),
        ],
    )
    rule = BibliographyLinkRule.model_validate(_link_rule())

    violations, checked, assessment = _check_bibliography_link_rule(
        rule, document, {"p_0001": "code_listing", "p_0002": "body"}
    )

    assert violations == []
    assert checked == 1
    assert assessment.status == "pass"
    assert assessment.details["mentions_in_code"] == 1


def test_only_code_listing_mentions_are_not_assessed() -> None:
    """Если все скобки нашлись только в коде, правило не оценивается."""
    document = _document(
        entries=[_entry()],
        mentions=[_mention("men_code", "p_0001", targets=("123",), resolved=())],
        blocks=[_paragraph("p_0001", "return data[123]")],
    )
    rule = BibliographyLinkRule.model_validate(_link_rule())

    violations, checked, assessment = _check_bibliography_link_rule(
        rule, document, {"p_0001": "code_listing"}
    )

    assert violations == []
    assert checked == 0
    assert assessment.status == "not_assessed"
    assert "листингах кода" in assessment.observed_text


def _electronic_format_rule() -> BibliographyFormatRule:
    """Правило формата электронного источника: URL и дата обращения."""
    return BibliographyFormatRule.model_validate(
        _format_rule(
            {
                "categories": ["electronic_resource"],
                "require_url": True,
                "require_access_date": True,
            }
        )
    )


def test_hyperlink_target_counts_as_entry_url() -> None:
    """URL за текстом гиперссылки засчитывается как адрес электронного ресурса."""
    entry = _entry(
        category="electronic_resource",
        raw_text="Allure Report Docs. – URL: Allure Report Docs (дата обращения: 15.03.2026).",
        access_date_raw="дата обращения: 15.03.2026",
    )
    document = _document(
        entries=[entry],
        blocks=[
            _paragraph(
                "p_0002",
                "Allure Report Docs. – URL: Allure Report Docs (дата обращения: 15.03.2026).",
                runs=[_hyperlink_run("Allure Report Docs", "https://allurereport.org/docs/")],
            )
        ],
    )

    violations, checked, assessment = _check_bibliography_format_rule(
        _electronic_format_rule(), document, {}
    )

    assert violations == []
    assert checked == 1
    assert assessment.status == "pass"


def test_hyphenated_access_date_is_recognized() -> None:
    """Дата обращения с мягким переносом внутри слова не теряется."""
    entry = _entry(
        category="electronic_resource",
        raw_text=(
            "Overview of WPF : сайт. – URL: https://learn.microsoft.com/wpf "
            "(дата обра-щения: 10.04.2026)."
        ),
        urls=["https://learn.microsoft.com/wpf"],
        access_date_raw=None,
    )
    document = _document(entries=[entry], blocks=[_paragraph("p_0002", entry.raw_text)])

    violations, checked, assessment = _check_bibliography_format_rule(
        _electronic_format_rule(), document, {}
    )

    assert violations == []
    assert checked == 1
    assert assessment.status == "pass"


def test_arbitrary_date_is_not_access_date() -> None:
    """Дата без фразы «дата обращения» не засчитывается как дата обращения."""
    entry = _entry(
        category="electronic_resource",
        raw_text="Отчет. – 10.04.2026. – URL: https://example.org/report",
        urls=["https://example.org/report"],
        access_date_raw=None,
    )
    document = _document(entries=[entry], blocks=[_paragraph("p_0002", entry.raw_text)])

    violations, checked, assessment = _check_bibliography_format_rule(
        _electronic_format_rule(), document, {}
    )

    assert len(violations) == 1
    assert _mismatched_field(violations[0]) == "access_date_raw"
    assert assessment.status == "fail"
