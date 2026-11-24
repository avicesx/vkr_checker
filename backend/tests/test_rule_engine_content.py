"""Тесты контентных проверок: микротипографика, нумерация формул и список литературы.

Документы синтетические: минимальный ParsedDocument с абзацами, разметкой
микротипографики, индексом формул и записей списка литературы. В набор
каждого теста попадает одно правило из штатного набора 2.4.0.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from parser.core.schema import (
    BibliographyEntry,
    CrossReferenceIndex,
    DocumentMeta,
    EquationObject,
    GlobalSettings,
    MicroTypographyReport,
    PageCountInfo,
    ParagraphBlock,
    ParsedDocxDocument,
    RunFontPropertiesEffective,
    SectionProperties,
    SourceInfo,
    SpanLocation,
    TextRun,
    ThemeData,
)
from rule_engine import (
    RuleEngine,
    RuleSet,
    load_default_ruleset,
    load_profile_ruleset,
)
from rule_engine.findings import RuleAssessment, RuleCheckReport
from rule_engine.rules import Rule

TEXT = "Основной текст выпускной квалификационной работы."


def _run(text: str = TEXT) -> TextRun:
    """Создает видимый ран с параметрами шрифта по умолчанию."""
    return TextRun(
        run_id="r1",
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
    )


def _span(text: str, start: int, end: int, subtype: str = "") -> SpanLocation:
    """Создает размеченный фрагмент текста."""
    return SpanLocation(
        start_char=start,
        end_char=end,
        snippet=text[start:end],
        issue_subtype=subtype,
    )


def _paragraph(
    block_id: str,
    text: str = TEXT,
    *,
    typography: Optional[MicroTypographyReport] = None,
    page_start: int = 1,
) -> ParagraphBlock:
    """Создает абзац основного текста с необязательной разметкой типографики."""
    return ParagraphBlock(
        block_id=block_id,
        section_idx=0,
        page_start_est=page_start,
        page_end_est=page_start,
        text_raw=text,
        text_visible=text,
        runs=[_run(text)],
        typography=typography or MicroTypographyReport(),
    )


def _entry(bib_id: str, order: int, year: Optional[int]) -> BibliographyEntry:
    """Создает запись списка литературы."""
    return BibliographyEntry(
        bib_id=bib_id,
        block_id=f"p_{bib_id}",
        order_index=order,
        raw_text=f"{order}. Источник {bib_id}",
        year_extracted=year,
    )


def _equation(equation_id: str, *, numbered: bool, standalone: bool = True) -> EquationObject:
    """Создает формулу с номером или без него."""
    return EquationObject(
        equation_id=equation_id,
        parent_block_id="p_eq",
        section_idx=0,
        representation_kind="omml_para",
        is_standalone_line=standalone,
        text_linearized="x = y + z",
        has_number=numbered,
        number_raw="(1)" if numbered else None,
        number_clean="1" if numbered else None,
    )


def _make_document(
    blocks: List[ParagraphBlock],
    *,
    equations: Optional[List[EquationObject]] = None,
    bibliography: Optional[List[BibliographyEntry]] = None,
    parse_mode: str = "docx_only",
) -> ParsedDocxDocument:
    """Собирает минимальный ParsedDocument для контентных проверок."""
    return ParsedDocxDocument(
        parse_mode=parse_mode,  # type: ignore[arg-type]
        source=SourceInfo(
            mode=parse_mode,  # type: ignore[arg-type]
            has_docx=parse_mode != "pdf_only",
            has_pdf=parse_mode == "pdf_only",
        ),
        page_count=PageCountInfo(
            total_pages=1,
            method="pdf_exact" if parse_mode == "pdf_only" else "geometric_estimation",
            confidence="high" if parse_mode == "pdf_only" else "low",
        ),
        meta=DocumentMeta(
            filename="synthetic.docx",
            file_sha256="0" * 64,
            source_format="docx",
        ),
        settings=GlobalSettings(),
        theme=ThemeData(),
        sections=[
            SectionProperties(
                section_idx=0,
                page_width_mm=210.0,
                page_height_mm=297.0,
                orientation="portrait",
                is_a4_standard=True,
                page_start_est=1,
                page_end_est=1,
            )
        ],
        blocks=blocks,
        cross_ref_index=CrossReferenceIndex(
            equations=equations or [],
            bibliography=bibliography or [],
        ),
    )


def _engine(rule_id: str, params: Optional[Dict[str, object]] = None) -> tuple[RuleEngine, Rule]:
    """Создает движок с одним правилом набора, при необходимости с новыми ожиданиями."""
    try:
        rule = load_default_ruleset().get_rule(rule_id)
    except KeyError:
        # Число источников задаёт только методичка УрГПУ, правило живёт
        # в профиле вуза, а не в базовом наборе.
        rule = load_profile_ruleset("urgpu").get_rule(rule_id)
    payload = rule.model_dump(mode="json")
    if params is not None:
        payload["params"] = params
    ruleset = RuleSet.model_validate(
        {
            "ruleset_id": f"test-{rule_id}",
            "ruleset_version": "1.0.0",
            "rules": [payload],
        }
    )
    return RuleEngine(ruleset), ruleset.rules[0]


def _assessment(report: RuleCheckReport, rule_id: str) -> RuleAssessment:
    """Возвращает единственную оценку проверки по правилу."""
    matches = [item for item in report.assessments if item.rule_id == rule_id]
    assert len(matches) == 1, f"ожидалась одна оценка {rule_id}"
    return matches[0]


# ------------------------------------------------------------ микротипографика


def test_microtypography_aggregates_quotes_per_paragraph() -> None:
    """Прямые кавычки собираются в одно нарушение абзаца с числом вхождений."""
    text = 'Он сказал "привет" и "пока" коллегам.'
    quotes = [_span(text, i, i + 1) for i, char in enumerate(text) if char == '"']
    typo = MicroTypographyReport(wrong_quotes=quotes)
    engine, rule = _engine("microtypography_prose")

    report = engine.check(_make_document([_paragraph("p_1", text, typography=typo)]))

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.location.block_id == "p_1"
    assert violation.found["wrong_quotes"] == 4
    assert violation.occurrences == 4
    assert _assessment(report, rule.rule_id).status == "fail"


def test_microtypography_skips_code_quotes() -> None:
    """Кавычки рядом с символами кода не считаются нарушением прозы."""
    text = "se.json()['id'] == value"
    quote_positions = [i for i, char in enumerate(text) if char in {'"', "'"}]
    quotes = [_span(text, i, i + 1) for i in quote_positions]
    typo = MicroTypographyReport(wrong_quotes=quotes)
    engine, rule = _engine("microtypography_prose")

    report = engine.check(_make_document([_paragraph("p_1", text, typography=typo)]))

    assert report.violations == []
    assert _assessment(report, rule.rule_id).status == "pass"


def test_microtypography_skips_english_apostrophe() -> None:
    """Апостроф внутри латинского слова — часть названия, а не кавычка."""
    text = "backstabber's Knife Co"
    index = text.index("'")
    typo = MicroTypographyReport(wrong_quotes=[_span(text, index, index + 1)])
    engine, rule = _engine("microtypography_prose")

    report = engine.check(_make_document([_paragraph("p_1", text, typography=typo)]))

    assert report.violations == []
    assert _assessment(report, rule.rule_id).status == "pass"


def test_microtypography_skips_space_before_dot_in_product_name() -> None:
    """Пробел перед точкой в .NET — имя платформы, а не ошибка пунктуации."""
    text = "Платформа .NET удобна для разработки."
    index = text.index(" .")
    typo = MicroTypographyReport(
        space_before_punctuation=[_span(text, index, index + 1)]
    )
    engine, rule = _engine("microtypography_prose")

    report = engine.check(_make_document([_paragraph("p_1", text, typography=typo)]))

    assert report.violations == []
    assert _assessment(report, rule.rule_id).status == "pass"


def test_microtypography_flags_hyphen_as_dash() -> None:
    """Дефис с пробелами вместо тире остаётся настоящим нарушением."""
    text = "Маркетинг - вид человеческой деятельности."
    index = text.index(" - ")
    typo = MicroTypographyReport(wrong_dashes=[_span(text, index + 1, index + 2)])
    engine, rule = _engine("microtypography_prose")

    report = engine.check(_make_document([_paragraph("p_1", text, typography=typo)]))

    assert len(report.violations) == 1
    assert report.violations[0].found["wrong_dashes"] == 1
    assert _assessment(report, rule.rule_id).status == "fail"


def test_microtypography_nbsp_reports_only_missing_nbsp() -> None:
    """Правило неразрывных пробелов не смешивает другие виды типографики."""
    text = "Результаты представлены в таблице 1."
    index = text.index("таблице")
    typo = MicroTypographyReport(
        missing_nbsp=[_span(text, index, index + len("таблице 1"))],
        wrong_quotes=[_span(text, 0, 1)],
    )
    engine, rule = _engine("microtypography_nbsp")

    report = engine.check(_make_document([_paragraph("p_1", text, typography=typo)]))

    assert len(report.violations) == 1
    assert report.violations[0].found == {
        "missing_nbsp": 1,
    }
    assessment = _assessment(report, rule.rule_id)
    assert assessment.status == "fail"
    assert assessment.details["by_check"] == {"missing_nbsp": 1}


def test_microtypography_is_not_assessed_for_pdf() -> None:
    """PDF не хранит микротипографику, поэтому правило честно не оценивается."""
    engine, rule = _engine("microtypography_prose")
    document = _make_document([_paragraph("p_1")], parse_mode="pdf_only")

    report = engine.check(document)

    assert report.violations == []
    assessment = _assessment(report, rule.rule_id)
    assert assessment.status == "not_assessed"
    assert "PDF" in assessment.observed_text


# -------------------------------------------------------------------- формулы


def test_equation_numbering_not_assessed_without_equations() -> None:
    """Без вынесенных формул правило не оценивается и не падает."""
    engine, rule = _engine("equation_numbering")

    report = engine.check(_make_document([_paragraph("p_1")]))

    assert report.violations == []
    assessment = _assessment(report, rule.rule_id)
    assert assessment.status == "not_assessed"
    assert "формулы не найдены" in assessment.observed_text


def test_equation_numbering_flags_unnumbered_standalone_equation() -> None:
    """Вынесенная формула без номера — нарушение с адресом формулы."""
    engine, rule = _engine("equation_numbering")
    equation = _equation("eq_1", numbered=False)
    document = _make_document([_paragraph("p_1")], equations=[equation])

    report = engine.check(document)

    assert [violation.location.block_id for violation in report.violations] == ["p_eq"]
    assert report.violations[0].found["has_number"] is False
    assert _assessment(report, rule.rule_id).status == "fail"


def test_equation_numbering_passes_for_numbered_equation() -> None:
    """Пронумерованная вынесенная формула проходит проверку."""
    engine, rule = _engine("equation_numbering")
    document = _make_document(
        [_paragraph("p_1")],
        equations=[_equation("eq_1", numbered=True)],
    )

    report = engine.check(document)

    assert report.violations == []
    assert _assessment(report, rule.rule_id).status == "pass"


def test_equation_numbering_ignores_inline_equations() -> None:
    """Внутристрочные формулы не нумеруются и не проверяются."""
    engine, rule = _engine("equation_numbering")
    document = _make_document(
        [_paragraph("p_1")],
        equations=[_equation("eq_1", numbered=False, standalone=False)],
    )

    report = engine.check(document)

    assert report.violations == []
    assert _assessment(report, rule.rule_id).status == "not_assessed"


# --------------------------------------------------------------- библиография


def test_bibliography_count_flags_short_list() -> None:
    """Список короче нормы — одно нарушение с числом источников."""
    engine, rule = _engine("bibliography_count")
    entries = [_entry(f"b{i}", i, 2024) for i in range(1, 5)]
    document = _make_document([_paragraph("p_1")], bibliography=entries)

    report = engine.check(document)

    assert len(report.violations) == 1
    assert report.violations[0].found == {"sources": 4}
    assert _assessment(report, rule.rule_id).status == "fail"


def test_bibliography_count_not_assessed_for_empty_list() -> None:
    """Нераспознанный список — отсутствие измерения, а не ноль источников."""
    engine, rule = _engine("bibliography_count")

    report = engine.check(_make_document([_paragraph("p_1")]))

    assert report.violations == []
    assessment = _assessment(report, rule.rule_id)
    assert assessment.status == "not_assessed"
    assert "не найден" in assessment.observed_text


def test_bibliography_order_flags_gap_and_duplicate() -> None:
    """Пропуск и повтор номера в списке литературы — нарушения порядка."""
    engine, rule = _engine("bibliography_order")
    entries = [_entry("b1", 1, 2024), _entry("b3", 3, 2023), _entry("b3", 3, 2022)]
    document = _make_document([_paragraph("p_1")], bibliography=entries)

    report = engine.check(document)

    addresses = sorted(violation.location.block_id for violation in report.violations)
    assert addresses == ["p_b3", "p_b3"]
    assert _assessment(report, rule.rule_id).status == "fail"
    assert report.violations[0].found["order_index"] in {2, 3}


def test_bibliography_order_passes_for_sequential_list() -> None:
    """Последовательная нумерация списка проходит проверку."""
    engine, rule = _engine("bibliography_order")
    entries = [_entry(f"b{i}", i, 2024) for i in range(1, 4)]
    document = _make_document([_paragraph("p_1")], bibliography=entries)

    report = engine.check(document)

    assert report.violations == []
    assert _assessment(report, rule.rule_id).status == "pass"


def test_bibliography_recency_flags_old_list() -> None:
    """Список из старых источников не проходит проверку свежести."""
    engine, rule = _engine(
        "bibliography_recency",
        {
            "min_recent_share": 0.3,
            "recent_years": 5,
            "reference_year": 2025,
            "min_year_coverage": 0.5,
        },
    )
    entries = [_entry(f"b{i}", i, year) for i, year in enumerate([2005, 2008, 2010, 2012], 1)]
    document = _make_document([_paragraph("p_1")], bibliography=entries)

    report = engine.check(document)

    assert len(report.violations) == 1
    assert report.violations[0].found["recent_sources"] == 0
    assert _assessment(report, rule.rule_id).status == "fail"


def test_bibliography_recency_passes_with_fresh_sources() -> None:
    """Достаточная доля свежих источников проходит проверку."""
    engine, rule = _engine(
        "bibliography_recency",
        {
            "min_recent_share": 0.3,
            "recent_years": 5,
            "reference_year": 2025,
            "min_year_coverage": 0.5,
        },
    )
    entries = [_entry(f"b{i}", i, year) for i, year in enumerate([2024, 2023, 2005], 1)]
    document = _make_document([_paragraph("p_1")], bibliography=entries)

    report = engine.check(document)

    assert report.violations == []
    assessment = _assessment(report, rule.rule_id)
    assert assessment.status == "pass"
    assert assessment.details["recent_sources"] == 2


def test_bibliography_recency_not_assessed_without_years() -> None:
    """Если годы известны у меньшинства записей, правило не оценивается."""
    engine, rule = _engine("bibliography_recency")
    entries = [_entry(f"b{i}", i, None) for i in range(1, 5)]
    document = _make_document([_paragraph("p_1")], bibliography=entries)

    report = engine.check(document)

    assert report.violations == []
    assessment = _assessment(report, rule.rule_id)
    assert assessment.status == "not_assessed"
