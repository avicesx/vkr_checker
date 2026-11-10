"""Тесты проверок движка: номер страницы, формат листа и межстрочный интервал.

Документы синтетические: собирается минимальный ParsedDocument с секциями,
абзацами и постраничным PDF-аудитом, без реальных DOCX/PDF и без PyMuPDF.
Профили вузов берутся из rules/*.json, но в набор каждого теста попадает только
проверяемое правило — остальные правила профиля проверяются своими тестами.
"""
from __future__ import annotations

from typing import List, Optional

import pytest

from parser.core.schema import (
    DocumentMeta,
    GlobalSettings,
    HeadingMetadata,
    PageCountInfo,
    ParagraphBlock,
    ParagraphPropertiesEffective,
    ParsedDocxDocument,
    PdfPageAudit,
    RunFontPropertiesEffective,
    SectionProperties,
    SourceInfo,
    TextRun,
    ThemeData,
)
from rule_engine import RuleEngine, RuleSet, load_profile_ruleset
from rule_engine.findings import RuleAssessment, RuleCheckReport
from rule_engine.rules import Rule, RuleKind

TEXT = "Основной текст выпускной квалификационной работы."


def _section(
    section_idx: int = 0,
    *,
    width_mm: float = 210.0,
    height_mm: float = 297.0,
    orientation: str = "portrait",
    is_a4_standard: bool = True,
    page_start_est: int = 1,
    page_end_est: int = 6,
    headers: Optional[dict] = None,
    footers: Optional[dict] = None,
) -> SectionProperties:
    """Собирает секцию с заданным листом и диапазоном страниц."""
    return SectionProperties(
        section_idx=section_idx,
        page_width_mm=width_mm,
        page_height_mm=height_mm,
        orientation=orientation,  # type: ignore[arg-type]
        is_a4_standard=is_a4_standard,
        page_start_est=page_start_est,
        page_end_est=page_end_est,
        headers=headers or {},
        footers=footers or {},
    )


def _audit_pages(
    count: int,
    *,
    start: int = 1,
    location: str = "footer",
    alignment: str = "center",
    leading_ratio: Optional[float] = None,
) -> List[PdfPageAudit]:
    """Создает постраничный PDF-аудит с одинаковым положением номера."""
    return [
        PdfPageAudit(
            page_num=page_num,
            width_mm=210.0,
            height_mm=297.0,
            has_rendered_page_number=location != "none",
            page_number_location=location,  # type: ignore[arg-type]
            page_number_alignment=alignment,  # type: ignore[arg-type]
            leading_ratio=leading_ratio,
        )
        for page_num in range(start, start + count)
    ]


def _paragraphs(
    count: int,
    *,
    line_spacing_value: float = 1.5,
    line_spacing_rule: str = "auto",
    line_spacing_source: str = "direct",
) -> List[ParagraphBlock]:
    """Создает абзацы основного текста с заданным межстрочным интервалом."""
    return [
        ParagraphBlock(
            block_id=f"p{index + 1}",
            section_idx=0,
            page_start_est=1,
            page_end_est=1,
            text_raw=TEXT,
            text_visible=TEXT,
            effective_props=ParagraphPropertiesEffective(
                line_spacing_value=line_spacing_value,
                line_spacing_rule=line_spacing_rule,  # type: ignore[arg-type]
                line_spacing_source=line_spacing_source,  # type: ignore[arg-type]
            ),
        )
        for index in range(count)
    ]


def _font_paragraph(family: str, size_pt: float = 14.0) -> ParagraphBlock:
    """Создает абзац с одним видимым раном заданной гарнитуры и кегля."""
    return ParagraphBlock(
        block_id="f1",
        section_idx=0,
        page_start_est=1,
        page_end_est=1,
        text_raw=TEXT,
        text_visible=TEXT,
        runs=[
            TextRun(
                run_id="r1",
                text=TEXT,
                visible_text=TEXT,
                start_char=0,
                end_char=len(TEXT),
                effective_props=RunFontPropertiesEffective(
                    font_ascii=family,
                    font_hansi=family,
                    font_cs=family,
                    size_pt=size_pt,
                ),
            )
        ],
    )


def _heading(line_spacing_value: float = 1.0) -> ParagraphBlock:
    """Создает абзац заголовка, который не входит в выборку body_text."""
    return ParagraphBlock(
        block_id="h1",
        section_idx=0,
        page_start_est=1,
        page_end_est=1,
        text_raw="1 Теоретическая часть",
        text_visible="1 Теоретическая часть",
        style_id="Heading1",
        heading_info=HeadingMetadata(
            detection_confidence="explicit_outline_or_style",
            heading_kind="numbered_section",
            level=1,
            clean_title_text="Теоретическая часть",
        ),
        effective_props=ParagraphPropertiesEffective(
            line_spacing_value=line_spacing_value,
            line_spacing_rule="auto",
            line_spacing_source="direct",
        ),
    )


def _make_document(
    *,
    sections: Optional[List[SectionProperties]] = None,
    pdf_pages_audit: Optional[List[PdfPageAudit]] = None,
    blocks: Optional[List[ParagraphBlock]] = None,
    parse_mode: str = "docx_only",
    has_pdf: bool = False,
    leading_ratio: Optional[float] = None,
    leading_sample_count: int = 0,
) -> ParsedDocxDocument:
    """Собирает минимальный валидный ParsedDocument для проверок движка."""
    section_list = sections if sections is not None else []
    audit_list = pdf_pages_audit if pdf_pages_audit is not None else []
    block_list = blocks if blocks is not None else []

    if section_list:
        total_pages = max(section.page_end_est for section in section_list)
    elif audit_list:
        total_pages = len(audit_list)
    else:
        total_pages = 1

    return ParsedDocxDocument(
        parse_mode=parse_mode,  # type: ignore[arg-type]
        source=SourceInfo(
            mode=parse_mode,  # type: ignore[arg-type]
            has_docx=parse_mode != "pdf_only",
            has_pdf=has_pdf,
        ),
        page_count=PageCountInfo(
            total_pages=total_pages,
            method="pdf_exact" if has_pdf else "geometric_estimation",
            confidence="high" if has_pdf else "low",
        ),
        meta=DocumentMeta(
            filename="synthetic.docx",
            file_sha256="0" * 64,
            source_format="docx",
            pdf_median_leading_ratio=leading_ratio,
            pdf_leading_sample_count=leading_sample_count,
        ),
        settings=GlobalSettings(),
        theme=ThemeData(),
        sections=section_list,
        pdf_pages_audit=audit_list,
        blocks=block_list,
    )


def _engine_for_kind(profile_id: str, kind: RuleKind) -> tuple[RuleEngine, Rule]:
    """Берет из профиля вуза одно правило нужного вида и создает движок."""
    ruleset = load_profile_ruleset(profile_id)
    rules = [rule for rule in ruleset.rules if rule.kind == kind]
    assert len(rules) == 1, f"в профиле {profile_id} ожидалось одно правило вида {kind}"
    rule = rules[0]
    engine = RuleEngine(
        RuleSet(
            ruleset_id=f"test-{profile_id}-{kind}",
            ruleset_version="1.0.0",
            rules=[rule],
        )
    )
    return engine, rule


def _assessment(report: RuleCheckReport, rule_id: str) -> RuleAssessment:
    """Возвращает единственный статус проверки по правилу."""
    matches = [item for item in report.assessments if item.rule_id == rule_id]
    assert len(matches) == 1, f"ожидался один статус проверки {rule_id}"
    return matches[0]


# ---------------------------------------------------------------- номер страницы


@pytest.mark.parametrize("profile_id", ["urgpu", "urfu"])
def test_page_number_bottom_center_passes_for_footer_profiles(profile_id: str) -> None:
    """Нижний колонтитул по центру соответствует профилям УрГПУ и УрФУ."""
    engine, rule = _engine_for_kind(profile_id, "page_number_position")
    document = _make_document(
        sections=[_section()],
        pdf_pages_audit=_audit_pages(6),
        parse_mode="pdf_only",
        has_pdf=True,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "pass"
    assert assessment.confidence == "medium"
    assert assessment.expected_text == "нижний колонтитул, по центру"
    assert assessment.details["source"] == "pdf_pages_audit"
    assert assessment.details["pages_with_number"] == 6
    assert report.violations == []
    assert report.not_assessed == []
    assert report.summary.not_assessed_total == 0
    assert report.passed is True


def test_page_number_bottom_center_fails_for_mpgu() -> None:
    """МПГУ требует номер в верхнем колонтитуле: нижний номер — нарушение."""
    engine, rule = _engine_for_kind("mpgu", "page_number_position")
    document = _make_document(
        sections=[_section()],
        pdf_pages_audit=_audit_pages(6),
        parse_mode="pdf_only",
        has_pdf=True,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "fail"
    assert assessment.observed_text.startswith("нижний колонтитул, по центру")
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.violation_id == f"{rule.rule_id}:document"
    assert violation.kind == "page_number_position"
    assert violation.expected == {"zone": "header", "alignment": "center"}
    assert violation.found["zone"] == "footer"
    assert violation.found["alignment"] == "center"
    assert violation.found["pages_with_number"] == 6
    assert violation.location.snippet == "положение номера страницы"
    assert violation.occurrences == 6
    # severity=warning: нарушение видно в отчете, но не блокирует документ
    assert violation.severity == "warning"
    assert report.passed is True


def test_page_number_header_center_passes_for_mpgu() -> None:
    """Верхний колонтитул по центру соответствует профилю МПГУ."""
    engine, rule = _engine_for_kind("mpgu", "page_number_position")
    document = _make_document(
        sections=[_section()],
        pdf_pages_audit=_audit_pages(6, location="header"),
        parse_mode="pdf_only",
        has_pdf=True,
    )

    report = engine.check(document)

    assert _assessment(report, rule.rule_id).status == "pass"
    assert report.violations == []


def test_page_number_not_assessed_when_too_few_numbers() -> None:
    """Порог max(3, N // 3): двух номеров на шесть страниц недостаточно."""
    engine, rule = _engine_for_kind("urgpu", "page_number_position")
    document = _make_document(
        sections=[_section()],
        pdf_pages_audit=(
            _audit_pages(2, start=1)
            + _audit_pages(4, start=3, location="none", alignment="none")
        ),
        parse_mode="pdf_only",
        has_pdf=True,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "not_assessed"
    assert assessment.confidence == "low"
    assert assessment.details["pages_with_number"] == 2
    assert assessment.details["pages_total"] == 6
    assert assessment.details["threshold"] == 3
    assert "распознано номеров страниц: 2 из 6" in assessment.observed_text
    assert report.violations == []
    assert report.summary.not_assessed_total == 1
    assert [item.rule_id for item in report.not_assessed] == [rule.rule_id]


def test_page_number_assessed_at_exact_threshold() -> None:
    """Ровно max(3, N // 3) распознанных номеров уже достаточно для оценки."""
    engine, rule = _engine_for_kind("urfu", "page_number_position")
    document = _make_document(
        sections=[_section()],
        pdf_pages_audit=(
            _audit_pages(3, start=1)
            + _audit_pages(3, start=4, location="none", alignment="none")
        ),
        parse_mode="pdf_only",
        has_pdf=True,
    )

    assessment = _assessment(engine.check(document), rule.rule_id)

    assert assessment.status == "pass"


def test_page_number_not_assessed_without_pdf_or_docx_data() -> None:
    """Без PDF-аудита и без номера в колонтитулах DOCX проверка не выполняется."""
    engine, rule = _engine_for_kind("urfu", "page_number_position")
    document = _make_document(sections=[_section()], blocks=[])

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "not_assessed"
    assert assessment.confidence == "low"
    assert "не найден номер страницы" in assessment.observed_text
    assert report.violations == []
    assert report.summary.not_assessed_total == 1


def test_page_number_docx_footer_field_passes_for_urfu() -> None:
    """DOCX-ветка: поле номера в нижнем колонтитуле по центру — соответствие."""
    engine, rule = _engine_for_kind("urfu", "page_number_position")
    footer = {
        "default": {
            "hf_type": "default",
            "is_linked_to_previous": False,
            "is_empty": False,
            "has_page_field": True,
            "page_number_alignment": "center",
        }
    }
    document = _make_document(sections=[_section(footers=footer)])

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "pass"
    assert assessment.confidence == "high"
    assert assessment.details["source"] == "docx_headers_footers"
    assert report.violations == []


# ------------------------------------------------------------------ формат листа


def test_page_format_passes_for_a4_portrait() -> None:
    """Секция 210 × 297 мм книжной ориентации соответствует профилю УрФУ."""
    engine, rule = _engine_for_kind("urfu", "page_format")
    document = _make_document(sections=[_section()])

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "pass"
    assert assessment.confidence == "high"
    assert assessment.details["sections_checked"] == 1
    assert "210 × 297 мм" in assessment.expected_text
    assert report.violations == []
    assert report.passed is True


@pytest.mark.parametrize(
    ("width_mm", "height_mm", "expected_status"),
    [
        (210.9, 297.9, "pass"),
        (211.5, 297.0, "fail"),
        (210.0, 295.5, "fail"),
    ],
)
def test_page_format_uses_one_millimeter_tolerance(
    width_mm: float,
    height_mm: float,
    expected_status: str,
) -> None:
    """Допуск ±1 мм: отклонение 0,9 мм проходит, 1,5 мм — уже нарушение."""
    engine, rule = _engine_for_kind("urfu", "page_format")
    document = _make_document(
        sections=[
            _section(
                width_mm=width_mm,
                height_mm=height_mm,
                is_a4_standard=abs(width_mm - 210.0) <= 2.0
                and abs(height_mm - 297.0) <= 2.0,
            )
        ]
    )

    report = engine.check(document)

    assert _assessment(report, rule.rule_id).status == expected_status
    assert (report.violations == []) is (expected_status == "pass")


def test_page_format_fails_for_non_a4_section() -> None:
    """Секция 215 × 297 мм не проходит проверку A4 с допуском ±1 мм."""
    engine, rule = _engine_for_kind("urfu", "page_format")
    document = _make_document(
        sections=[_section(width_mm=215.0, height_mm=297.0, is_a4_standard=False)]
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "fail"
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.violation_id == f"{rule.rule_id}:section:0"
    assert violation.kind == "page_format"
    assert violation.severity == "error"
    assert violation.expected == {"width_mm": 210.0}
    assert violation.found["width_mm"] == 215.0
    assert violation.found["is_a4_standard"] is False
    assert "допуск ±1 мм" in violation.expected_text
    assert report.passed is False


def test_page_format_fails_for_landscape_section() -> None:
    """Альбомная секция не соответствует книжной ориентации правила."""
    engine, rule = _engine_for_kind("urfu", "page_format")
    document = _make_document(
        sections=[
            _section(
                width_mm=297.0,
                height_mm=210.0,
                orientation="landscape",
                is_a4_standard=False,
            )
        ]
    )

    report = engine.check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.expected["orientation"] == "portrait"
    assert violation.found["orientation"] == "landscape"
    assert "книжная ориентация" in violation.expected_text
    assert "альбомная ориентация" in violation.found_text
    assert report.passed is False


def test_page_format_not_assessed_without_sections() -> None:
    """Без секций формат листа не проверяется."""
    engine, rule = _engine_for_kind("urfu", "page_format")
    document = _make_document(sections=[])

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "not_assessed"
    assert assessment.confidence == "low"
    assert assessment.observed_text == "секции в документе не найдены"
    assert report.summary.not_assessed_total == 1


# ------------------------------------------------------------ межстрочный интервал


@pytest.mark.parametrize("profile_id", ["urgpu", "urfu"])
def test_line_spacing_docx_half_spacing_passes(profile_id: str) -> None:
    """Полуторный интервал абзацев DOCX соответствует профилю."""
    engine, rule = _engine_for_kind(profile_id, "line_spacing")
    document = _make_document(blocks=_paragraphs(12, line_spacing_value=1.5))

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "pass"
    assert assessment.confidence == "high"
    assert assessment.details["source"] == "docx_paragraphs"
    assert assessment.details["sample_count"] == 12
    assert assessment.details["median_ratio"] == 1.5
    assert report.violations == []


def test_line_spacing_docx_single_spacing_fails() -> None:
    """Одинарный интервал абзацев DOCX нарушает требование 1,5."""
    engine, rule = _engine_for_kind("urfu", "line_spacing")
    document = _make_document(blocks=_paragraphs(10, line_spacing_value=1.0))

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "fail"
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.violation_id == f"{rule.rule_id}:document"
    assert violation.kind == "line_spacing"
    assert violation.expected == {"ratio": 1.5, "tolerance": 0.25}
    assert violation.found["ratio"] == 1.0
    assert violation.found["sample_count"] == 10
    assert violation.location.snippet == "межстрочный интервал основного текста"
    assert report.passed is False


def test_line_spacing_median_is_stable_with_outliers() -> None:
    """Медиана не смещается от одиночного абзаца с другим интервалом."""
    engine, rule = _engine_for_kind("urfu", "line_spacing")
    blocks = _paragraphs(8, line_spacing_value=1.5) + _paragraphs(
        1, line_spacing_value=2.0
    )
    blocks[-1] = blocks[-1].model_copy(update={"block_id": "p9"})
    document = _make_document(blocks=blocks)

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "pass"
    assert assessment.details["sample_count"] == 9
    assert assessment.details["median_ratio"] == 1.5


def test_line_spacing_excludes_headings_from_docx_sample() -> None:
    """Заголовок с другим интервалом не входит в выборку body_text."""
    engine, rule = _engine_for_kind("urfu", "line_spacing")
    document = _make_document(
        blocks=_paragraphs(8, line_spacing_value=1.5) + [_heading(line_spacing_value=1.0)]
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "pass"
    assert assessment.details["sample_count"] == 8
    assert assessment.details["paragraphs_considered"] == 8


def test_line_spacing_not_assessed_without_docx_or_pdf_data() -> None:
    """Без абзацев и без PDF-оценки интервал не проверяется."""
    engine, rule = _engine_for_kind("urfu", "line_spacing")
    document = _make_document(blocks=[])

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "not_assessed"
    assert assessment.confidence == "low"
    assert assessment.details["source"] == "none"
    assert "нет данных об интервале" in assessment.observed_text
    assert report.violations == []
    assert report.summary.not_assessed_total == 1


def test_line_spacing_exact_value_is_not_converted_to_ratio() -> None:
    """Интервал в пунктах (exact) не переводится в множитель: not_assessed."""
    engine, rule = _engine_for_kind("urfu", "line_spacing")
    document = _make_document(
        blocks=_paragraphs(3, line_spacing_value=18.0, line_spacing_rule="exact")
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "not_assessed"
    assert assessment.details["fixed_spacing_paragraphs"] == 3
    assert assessment.details["paragraphs_considered"] == 3
    assert report.violations == []


def test_line_spacing_pdf_only_uses_leading_ratio() -> None:
    """В pdf_only интервал берется из агрегата meta.pdf_median_leading_ratio."""
    engine, rule = _engine_for_kind("urfu", "line_spacing")
    document = _make_document(
        sections=[_section()],
        pdf_pages_audit=_audit_pages(6, leading_ratio=1.52),
        parse_mode="pdf_only",
        has_pdf=True,
        leading_ratio=1.52,
        leading_sample_count=240,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "pass"
    assert assessment.confidence == "medium"
    assert assessment.details["source"] == "pdf_leading"
    assert assessment.details["sample_count"] == 240
    assert assessment.details["median_ratio"] == 1.52
    assert report.violations == []


def test_line_spacing_pdf_only_single_spacing_fails() -> None:
    """Одинарный интервал PDF (1,0) нарушает требование 1,5."""
    engine, rule = _engine_for_kind("urfu", "line_spacing")
    document = _make_document(
        sections=[_section()],
        pdf_pages_audit=_audit_pages(6, leading_ratio=1.0),
        parse_mode="pdf_only",
        has_pdf=True,
        leading_ratio=1.0,
        leading_sample_count=180,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "fail"
    assert len(report.violations) == 1
    assert report.violations[0].found["source"] == "pdf_leading"
    assert report.violations[0].found["ratio"] == 1.0
    assert report.passed is False


def test_line_spacing_pdf_falls_back_to_page_ratios() -> None:
    """Если агрегата в meta нет, берется медиана постраничных оценок."""
    engine, rule = _engine_for_kind("urfu", "line_spacing")
    document = _make_document(
        sections=[_section()],
        pdf_pages_audit=_audit_pages(4, leading_ratio=1.5),
        parse_mode="pdf_only",
        has_pdf=True,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "pass"
    assert assessment.details["source"] == "pdf_leading"
    assert assessment.details["sample_count"] == 4
    assert report.violations == []


# ------------------------------------------------------------------- шрифт


def test_font_family_accepts_postscript_spelling() -> None:
    """PDF-гарнитура без пробелов (``TimesNewRoman``) равна ``Times New Roman``."""
    engine, _ = _engine_for_kind("urfu", "font")
    document = _make_document(
        sections=[_section()],
        blocks=[_font_paragraph("TimesNewRoman")],
        parse_mode="pdf_only",
        has_pdf=True,
    )

    report = engine.check(document)

    assert report.violations == []


def test_font_family_still_rejects_other_families() -> None:
    """Терпимость к записи имени не приравнивает Arial к Times New Roman."""
    engine, rule = _engine_for_kind("urfu", "font")
    document = _make_document(
        sections=[_section()],
        blocks=[_font_paragraph("Arial")],
        parse_mode="pdf_only",
        has_pdf=True,
    )

    report = engine.check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.rule_id == rule.rule_id
    assert violation.expected["family"] == "Times New Roman"
    assert violation.found["family"] == "Arial"


def test_line_spacing_pdf_only_ignores_paragraph_estimate_scale() -> None:
    """Шкала абзацев PDF (шаг / (кегль × 1,2)) не подменяет leading ratio."""
    engine, rule = _engine_for_kind("urfu", "line_spacing")
    document = _make_document(
        sections=[_section()],
        pdf_pages_audit=_audit_pages(6, leading_ratio=1.5),
        blocks=_paragraphs(
            5,
            line_spacing_value=1.25,
            line_spacing_source="pdf_estimate",
        ),
        parse_mode="pdf_only",
        has_pdf=True,
        leading_ratio=1.5,
        leading_sample_count=240,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "pass"
    assert assessment.details["source"] == "pdf_leading"
    assert assessment.details["median_ratio"] == 1.5
    assert assessment.details["pdf_estimate_paragraphs"] == 5
    assert report.violations == []


def test_line_spacing_not_assessed_when_only_pdf_paragraph_scale_exists() -> None:
    """Без агрегата t2 шкала абзацев PDF не выдается за множитель Word."""
    engine, rule = _engine_for_kind("urfu", "line_spacing")
    document = _make_document(
        sections=[_section()],
        blocks=_paragraphs(
            4,
            line_spacing_value=1.25,
            line_spacing_source="pdf_estimate",
        ),
        parse_mode="pdf_only",
        has_pdf=True,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "not_assessed"
    assert assessment.details["source"] == "none"
    assert assessment.details["pdf_estimate_paragraphs"] == 4
    assert report.violations == []


def test_line_spacing_prefers_docx_paragraphs_over_pdf_estimate() -> None:
    """В DOCX-документе множитель абзацев важнее оценки PDF."""
    engine, rule = _engine_for_kind("urfu", "line_spacing")
    document = _make_document(
        sections=[_section()],
        blocks=_paragraphs(6, line_spacing_value=1.5),
        parse_mode="docx_pdf",
        has_pdf=True,
        leading_ratio=1.0,
        leading_sample_count=100,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == "pass"
    assert assessment.details["source"] == "docx_paragraphs"
    assert report.violations == []


# --------------------------------------------- приведение PDF-шкалы к Word


@pytest.mark.parametrize(
    ("typographic_ratio", "expected_status"),
    [
        (1.72, "pass"),  # Word 1,5 -> 1,50
        (1.49, "pass"),  # Word 1,3 -> 1,30
        (1.84, "pass"),  # Word 1,6 -> 1,60, в пределах допуска ±0,25
        (2.05, "fail"),  # Word 1,78 -> 1,78, за допуском
        (1.27, "fail"),  # Word 1,1 -> 1,10
        (1.15, "fail"),  # Word 1,0 -> 1,00
    ],
)
def test_line_spacing_pdf_converts_to_word_multiplier(
    typographic_ratio: float,
    expected_status: str,
) -> None:
    """Типографское шаг/кегль приводится к множителю Word делением на 1,15."""
    engine, rule = _engine_for_kind("urfu", "line_spacing")
    document = _make_document(
        sections=[_section()],
        pdf_pages_audit=_audit_pages(6, leading_ratio=typographic_ratio),
        parse_mode="pdf_only",
        has_pdf=True,
        leading_ratio=typographic_ratio,
        leading_sample_count=240,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert assessment.status == expected_status
    assert assessment.details["source"] == "pdf_leading"
    # Исходное типографское отношение сохраняется, конвертированное идет рядом.
    assert assessment.details["median_ratio"] == pytest.approx(typographic_ratio)
    assert assessment.details["pdf_single_line_factor"] == pytest.approx(1.15)
    assert assessment.details["word_multiplier"] == pytest.approx(
        typographic_ratio / 1.15,
        abs=1e-4,
    )
    if expected_status == "pass":
        assert report.violations == []
    else:
        assert len(report.violations) == 1
        assert report.violations[0].found["word_multiplier"] == pytest.approx(
            typographic_ratio / 1.15,
            abs=1e-4,
        )
        assert report.passed is False


@pytest.mark.parametrize(
    ("docx_ratio", "typographic_ratio", "expected_status"),
    [
        (1.6, 1.84, "pass"),
        (1.1, 1.27, "fail"),
    ],
)
def test_line_spacing_modes_agree_on_word_multiplier(
    docx_ratio: float,
    typographic_ratio: float,
    expected_status: str,
) -> None:
    """DOCX и PDF выносят одинаковый вердикт для одного множителя Word."""
    engine, rule = _engine_for_kind("urfu", "line_spacing")
    docx_document = _make_document(
        blocks=_paragraphs(10, line_spacing_value=docx_ratio),
    )
    pdf_document = _make_document(
        sections=[_section()],
        pdf_pages_audit=_audit_pages(6, leading_ratio=typographic_ratio),
        parse_mode="pdf_only",
        has_pdf=True,
        leading_ratio=typographic_ratio,
        leading_sample_count=240,
    )

    docx_assessment = _assessment(engine.check(docx_document), rule.rule_id)
    pdf_assessment = _assessment(engine.check(pdf_document), rule.rule_id)

    assert docx_assessment.status == expected_status
    assert pdf_assessment.status == expected_status
    if expected_status == "pass":
        assert docx_assessment.details["median_ratio"] == pytest.approx(docx_ratio)
    else:
        assert pdf_assessment.details["word_multiplier"] == pytest.approx(
            typographic_ratio / 1.15,
            abs=1e-4,
        )


# --------------------------------------------------------------------- отчет


def test_report_assessments_are_deterministic() -> None:
    """Повторный прогон на том же документе дает идентичный JSON отчета."""
    ruleset = load_profile_ruleset("urfu")
    rules = [
        rule
        for rule in ruleset.rules
        if rule.kind in {"page_number_position", "page_format", "line_spacing"}
    ]
    assert len(rules) == 3
    engine = RuleEngine(
        RuleSet(ruleset_id="test-three-checks", ruleset_version="1.0.0", rules=rules)
    )
    document = _make_document(
        sections=[_section()],
        pdf_pages_audit=_audit_pages(6),
        blocks=_paragraphs(6, line_spacing_value=1.5),
        parse_mode="pdf_only",
        has_pdf=True,
        leading_ratio=1.5,
        leading_sample_count=120,
    )

    first = engine.check(document)
    second = engine.check(document)

    assert first.model_dump_json() == second.model_dump_json()
    assert [item.kind for item in first.assessments] == [
        "line_spacing",
        "page_format",
        "page_number_position",
    ]
    assert all(item.status == "pass" for item in first.assessments)
