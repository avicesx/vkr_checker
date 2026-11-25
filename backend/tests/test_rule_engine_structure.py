"""Тесты структурных проверок: висячие строки, неотрывность заголовка,
начало раздела с новой страницы, шрифт заголовков и объём работы.

Документы синтетические: собирается минимальный ParsedDocument с деревом
заголовков, абзацами и свойствами абзаца, без реальных DOCX/PDF. В набор
каждого теста попадает одно проверяемое правило из штатного набора 2.3.0.
"""
from __future__ import annotations

from typing import List, Optional

from parser.core.schema import (
    DocumentMeta,
    DocumentTreeNode,
    GlobalSettings,
    HeadingMetadata,
    PageCountInfo,
    ParagraphBlock,
    ParagraphPropertiesEffective,
    ParsedDocxDocument,
    RunFontPropertiesEffective,
    SectionProperties,
    SourceInfo,
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

TEXT = "Основной текст выпускной квалификационной работы. " * 4


def _run(
    text: str = TEXT,
    *,
    family: str = "Times New Roman",
    size_pt: float = 14.0,
    bold: bool = False,
) -> TextRun:
    """Создает видимый ран с заданными параметрами шрифта."""
    return TextRun(
        run_id="r1",
        text=text,
        visible_text=text,
        start_char=0,
        end_char=len(text),
        effective_props=RunFontPropertiesEffective(
            font_ascii=family,
            font_hansi=family,
            font_cs=family,
            size_pt=size_pt,
            bold=bold,
        ),
    )


def _body(
    block_id: str,
    *,
    page_start: int = 1,
    page_end: int = 1,
    breaks: Optional[List[int]] = None,
    widow_control: bool = True,
) -> ParagraphBlock:
    """Создает абзац основного текста с заданными страницами и разрывами."""
    return ParagraphBlock(
        block_id=block_id,
        section_idx=0,
        page_start_est=page_start,
        page_end_est=page_end,
        rendered_page_breaks_offsets=breaks or [],
        text_raw=TEXT,
        text_visible=TEXT,
        runs=[_run()],
        effective_props=ParagraphPropertiesEffective(widow_control=widow_control),
    )


def _heading(
    block_id: str,
    *,
    level: int = 1,
    page_start: int = 5,
    page_end: int = 5,
    keep_next: bool = True,
    page_break_before: bool = False,
    breaks: Optional[List[int]] = None,
    family: str = "Times New Roman",
    size_pt: float = 14.0,
    bold: bool = True,
) -> ParagraphBlock:
    """Создает абзац заголовка с заданным шрифтом и свойствами абзаца."""
    title = "1 Теоретическая часть"
    return ParagraphBlock(
        block_id=block_id,
        section_idx=0,
        page_start_est=page_start,
        page_end_est=page_end,
        rendered_page_breaks_offsets=breaks or [],
        text_raw=title,
        text_visible=title,
        style_id="Heading1",
        runs=[_run(title, family=family, size_pt=size_pt, bold=bold)],
        effective_props=ParagraphPropertiesEffective(
            keep_next=keep_next,
            page_break_before=page_break_before,
        ),
        para_mark_font_effective=RunFontPropertiesEffective(
            font_ascii=family, font_hansi=family, font_cs=family, size_pt=size_pt, bold=bold
        ),
        heading_info=HeadingMetadata(
            detection_confidence="explicit_outline_or_style",
            heading_kind="numbered_section",
            level=level,
            clean_title_text=title,
        ),
    )


def _node(
    block_id: str,
    *,
    level: int = 1,
    role: str = "main_chapter",
    page_start: int = 5,
    page_end: int = 8,
) -> DocumentTreeNode:
    """Создает узел дерева документа для заголовка."""
    return DocumentTreeNode(
        node_id=f"n_{block_id}",
        heading_block_id=block_id,
        title="1 Теоретическая часть",
        level=level,
        semantic_role=role,  # type: ignore[arg-type]
        page_start_est=page_start,
        page_end_est=page_end,
    )


def _make_document(
    *,
    blocks: List[ParagraphBlock],
    nodes: Optional[List[DocumentTreeNode]] = None,
    total_pages: int = 1,
    words: Optional[int] = None,
    parse_mode: str = "docx_only",
    has_pdf: bool = False,
) -> ParsedDocxDocument:
    """Собирает минимальный валидный ParsedDocument для структурных проверок."""
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
            word_stat_words=words,
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
                page_end_est=max(total_pages, 1),
            )
        ],
        document_tree=nodes or [],
        blocks=blocks,
    )


def _engine(rule_id: str) -> tuple[RuleEngine, Rule]:
    """Создает движок с единственным правилом из штатного набора."""
    rule = load_default_ruleset().get_rule(rule_id)
    engine = RuleEngine(
        RuleSet(
            ruleset_id=f"test-{rule_id}",
            ruleset_version="1.0.0",
            rules=[rule],
        )
    )
    return engine, rule


def _engine_with_params(rule_id: str, **params: object) -> tuple[RuleEngine, Rule]:
    """Создает движок с правилом из набора и заменой отдельных ``params``."""
    rule = load_default_ruleset().get_rule(rule_id)
    updated = rule.params.model_copy(update=params)
    rule = rule.model_copy(update={"params": updated})
    engine = RuleEngine(
        RuleSet(
            ruleset_id=f"test-{rule_id}",
            ruleset_version="1.0.0",
            rules=[rule],
        )
    )
    return engine, rule


def _assessment(report: RuleCheckReport, rule_id: str) -> RuleAssessment:
    """Возвращает единственную оценку проверки по правилу."""
    matches = [item for item in report.assessments if item.rule_id == rule_id]
    assert len(matches) == 1, f"ожидалась одна оценка {rule_id}"
    return matches[0]


# ---------------------------------------------------------------- висячие строки


def test_widow_control_passes_for_intact_paragraphs() -> None:
    """Целый абзац проходит проверку висячих строк даже без переноса."""
    engine, rule = _engine("widow_control_body")
    document = _make_document(blocks=[_body("p_1")])

    report = engine.check(document)

    assert report.violations == []
    assert _assessment(report, rule.rule_id).status == "pass"


def test_widow_control_flags_split_paragraph_with_disabled_control() -> None:
    """Разорванный между страницами абзац с выключенным контролем — нарушение."""
    engine, rule = _engine("widow_control_body")
    document = _make_document(
        blocks=[_body("p_1", page_start=10, page_end=11, breaks=[68], widow_control=False)]
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert [violation.location.block_id for violation in report.violations] == ["p_1"]
    assert report.violations[0].found == {"widow_control": False}
    assert report.violations[0].occurrences == 1
    assert assessment.status == "fail"
    assert assessment.details["split_paragraphs"] == 1


def test_widow_control_ignores_unsplit_paragraph_with_disabled_control() -> None:
    """Выключенный контроль без фактического разрыва не считается нарушением."""
    engine, rule = _engine("widow_control_body")
    document = _make_document(
        blocks=[_body("p_1", page_start=10, page_end=10, widow_control=False)]
    )

    report = engine.check(document)

    assert report.violations == []
    assert _assessment(report, rule.rule_id).status == "pass"


def test_widow_control_is_not_assessed_for_pdf() -> None:
    """PDF не хранит свойства абзаца — проверка не оценивается."""
    engine, rule = _engine("widow_control_body")
    document = _make_document(
        blocks=[_body("p_1", widow_control=False)],
        parse_mode="pdf_only",
        has_pdf=True,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert report.violations == []
    assert assessment.status == "not_assessed"
    assert "DOCX" in (assessment.note or "")


# ------------------------------------------------------------ неотрывность заголовка


def test_keep_next_flags_heading_without_keep_next() -> None:
    """Заголовок раздела без keepNext отрывается от следующего текста."""
    engine, rule = _engine("heading_keep_next")
    document = _make_document(
        blocks=[_body("p_1", page_start=5, page_end=5), _heading("h_1", keep_next=False)],
        nodes=[_node("h_1")],
        total_pages=8,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert [violation.location.block_id for violation in report.violations] == ["h_1"]
    assert assessment.status == "fail"
    assert assessment.details["headings"] == 1


def test_keep_next_passes_when_enabled() -> None:
    """Заголовок с keepNext проходит проверку."""
    engine, rule = _engine("heading_keep_next")
    document = _make_document(
        blocks=[_body("p_1", page_start=5, page_end=5), _heading("h_1")],
        nodes=[_node("h_1")],
        total_pages=8,
    )

    report = engine.check(document)

    assert report.violations == []
    assert _assessment(report, rule.rule_id).status == "pass"


def test_keep_next_is_not_assessed_for_pdf() -> None:
    """PDF не хранит keepNext — проверка не оценивается."""
    engine, rule = _engine("heading_keep_next")
    document = _make_document(
        blocks=[_body("p_1"), _heading("h_1", keep_next=False)],
        nodes=[_node("h_1")],
        parse_mode="pdf_only",
        has_pdf=True,
        total_pages=8,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert report.violations == []
    assert assessment.status == "not_assessed"


# -------------------------------------------------------- раздел с новой страницы


def test_heading_page_break_flags_chapter_on_previous_text_page() -> None:
    """Заголовок главы на той же странице, что и текст, — нарушение."""
    engine, rule = _engine("heading_page_break")
    document = _make_document(
        blocks=[_body("p_1", page_start=4, page_end=4), _heading("h_1", page_start=4, page_end=6)],
        nodes=[_node("h_1")],
        total_pages=8,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert [violation.location.block_id for violation in report.violations] == ["h_1"]
    assert report.violations[0].found == {"start_page": 4, "previous_text_page": 4}
    assert assessment.status == "fail"


def test_heading_page_break_passes_when_chapter_starts_new_page() -> None:
    """Заголовок главы на новой странице проходит проверку."""
    engine, rule = _engine("heading_page_break")
    document = _make_document(
        blocks=[_body("p_1", page_start=4, page_end=4), _heading("h_1", page_start=5, page_end=7)],
        nodes=[_node("h_1")],
        total_pages=8,
    )

    report = engine.check(document)

    assert report.violations == []
    assert _assessment(report, rule.rule_id).status == "pass"


def test_heading_page_break_address_matches_heading_page() -> None:
    """Страница в адресе совпадает с найденной страницей заголовка.

    Абзац заголовка оценен 18-й страницей, но разрыв в нулевой позиции
    переносит начало на 19-ю. Адрес должен показывать 19 — то же значение,
    которое читатель видит в ``found.start_page``.
    """
    engine, rule = _engine("heading_page_break")
    document = _make_document(
        blocks=[
            _body("p_1", page_start=18, page_end=19),
            _heading("h_1", page_start=18, page_end=20, breaks=[0]),
        ],
        nodes=[_node("h_1", page_start=18, page_end=20)],
        total_pages=25,
    )

    report = engine.check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.found["start_page"] == 19
    assert violation.location.page_start_est == 19
    assert violation.location.page_end_est == 20


# ------------------------------------------- явный разрыв страницы перед главой


def test_page_break_disabled_in_default_ruleset() -> None:
    """В штатном наборе требование явного разрыва выключено."""
    rule = load_default_ruleset().get_rule("heading_page_break")

    assert rule.params.require_page_break is False


def test_page_break_flags_chapter_without_explicit_break() -> None:
    """Глава без явного разрыва — нарушение, если разрывы в документе есть."""
    engine, rule = _engine_with_params("heading_page_break", require_page_break=True)
    document = _make_document(
        blocks=[
            _body("p_1", page_start=4, page_end=4, breaks=[10]),
            _heading("h_1", page_start=5, page_end=7),
        ],
        nodes=[_node("h_1")],
        total_pages=8,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert [violation.location.block_id for violation in report.violations] == ["h_1"]
    assert report.violations[0].found == {"page_break": False, "page_start": 5}
    assert assessment.status == "fail"
    assert assessment.details["explicit_breaks_found"] == 1


def test_page_break_passes_chapter_with_explicit_break() -> None:
    """Глава с явным разрывом в нулевой позиции проходит проверку."""
    engine, rule = _engine_with_params("heading_page_break", require_page_break=True)
    document = _make_document(
        blocks=[
            _body("p_1", page_start=4, page_end=4, breaks=[10]),
            _heading("h_1", page_start=5, page_end=7, breaks=[0]),
        ],
        nodes=[_node("h_1")],
        total_pages=8,
    )

    report = engine.check(document)

    assert report.violations == []
    assert _assessment(report, rule.rule_id).status == "pass"


def test_page_break_not_assessed_without_explicit_breaks() -> None:
    """Без явных разрывов в документе положение главы не определено."""
    engine, rule = _engine_with_params("heading_page_break", require_page_break=True)
    document = _make_document(
        blocks=[
            _body("p_1", page_start=4, page_end=4),
            _heading("h_1", page_start=5, page_end=7),
        ],
        nodes=[_node("h_1")],
        total_pages=8,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert report.violations == []
    assert assessment.status == "not_assessed"
    assert "явных разрывов страницы" in assessment.observed_text


def test_page_break_uses_exact_pdf_pages_without_rendered_breaks() -> None:
    """В PDF страницы точные, поэтому явный разрыв не обязателен."""
    engine, rule = _engine_with_params("heading_page_break", require_page_break=True)
    document = _make_document(
        blocks=[
            _body("p_1", page_start=4, page_end=4),
            _heading("h_1", page_start=4, page_end=6),
        ],
        nodes=[_node("h_1")],
        total_pages=8,
        parse_mode="pdf_only",
        has_pdf=True,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert [violation.location.block_id for violation in report.violations] == ["h_1"]
    assert report.violations[0].location.page_start_est == report.violations[0].found["start_page"]
    assert assessment.status == "fail"



def test_profile_keeps_error_for_chapter_page_break() -> None:
    """В профиле глава обязана начинать страницу, и находка остаётся error."""
    rule = load_profile_ruleset("urgpu").get_rule("heading_page_break")
    engine = RuleEngine(RuleSet(ruleset_id="test-chapter-break", ruleset_version="1.0.0", rules=[rule]))
    document = _make_document(
        blocks=[
            _body("p_1", page_start=2, page_end=2, breaks=[0]),
            _heading("h_1", page_start=2, page_end=2),
        ],
        nodes=[_node("h_1")],
        total_pages=8,
    )

    report = engine.check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.rule_id == "heading_page_break"
    assert violation.severity == "error"
    assert violation.message == "Раздел должен начинаться с новой страницы"


def test_profile_warns_when_paragraph_starts_new_page() -> None:
    """Запрет разрыва перед параграфом — предупреждение с текстом про параграф."""
    rule = load_profile_ruleset("urgpu").get_rule("heading_page_break_subsection")
    engine = RuleEngine(RuleSet(ruleset_id="test-subsection-break", ruleset_version="1.0.0", rules=[rule]))
    document = _make_document(
        blocks=[
            _body("p_1", page_start=2, page_end=2),
            _heading("h_1", level=2, page_start=3, page_end=3),
        ],
        nodes=[_node("h_1", level=2, role="sub_section", page_start=3, page_end=3)],
        total_pages=8,
    )

    report = engine.check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.rule_id == "heading_page_break_subsection"
    assert violation.severity == "warning"
    assert violation.message == "Параграф начинается с новой страницы, хотя не должен"
    assert violation.expected_text == "параграф не должен начинаться с новой страницы"
    assert violation.found["start_page"] == 3


# ---------------------------------------------------------------- шрифт заголовков


def test_heading_font_flags_foreign_font_and_size() -> None:
    """Заголовок чужой гарнитурой и кеглем — нарушение шрифта заголовков."""
    engine, rule = _engine("heading_font")
    document = _make_document(
        blocks=[_heading("h_1", family="Arial", size_pt=12.0)],
        nodes=[_node("h_1")],
        total_pages=8,
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert [violation.location.block_id for violation in report.violations] == ["h_1"]
    assert report.violations[0].found["font"]["family"] == "Arial"
    assert assessment.status == "fail"


def test_heading_font_passes_for_times_new_roman_14_bold() -> None:
    """Заголовок Times New Roman 14 pt полужирным проходит проверку."""
    engine, rule = _engine("heading_font")
    document = _make_document(
        blocks=[_heading("h_1")],
        nodes=[_node("h_1")],
        total_pages=8,
    )

    report = engine.check(document)

    assert report.violations == []
    assert _assessment(report, rule.rule_id).status == "pass"


# ---------------------------------------------------------------- объём работы


def test_document_volume_passes_within_bounds() -> None:
    """Работа в границах 60–90 страниц и 8000–18000 слов проходит проверку."""
    engine, rule = _engine("document_volume")
    document = _make_document(
        blocks=[_body("p_1")], total_pages=70, words=9000
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert report.violations == []
    assert assessment.status == "pass"
    assert assessment.details["pages"] == 70


def test_document_volume_flags_both_bounds() -> None:
    """Слишком короткая работа с малым числом слов даёт два нарушения."""
    engine, rule = _engine("document_volume")
    document = _make_document(
        blocks=[_body("p_1")], total_pages=50, words=7000
    )

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    addresses = {
        violation.violation_id.split(":", 1)[1] for violation in report.violations
    }
    assert addresses == {"document:pages", "document:words"}
    assert assessment.status == "fail"
    assert assessment.details["objects_checked"] == 2


def test_document_volume_skips_words_when_not_counted() -> None:
    """Без статистики слов проверяется только число страниц."""
    engine, rule = _engine("document_volume")
    document = _make_document(blocks=[_body("p_1")], total_pages=70, words=None)

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert report.violations == []
    assert assessment.status == "pass"
    assert "слов" in (assessment.note or "")


def test_document_volume_is_not_assessed_for_short_fragment() -> None:
    """Документ короче десяти страниц считается фрагментом и не оценивается."""
    engine, rule = _engine("document_volume")
    document = _make_document(blocks=[_body("p_1")], total_pages=5, words=900)

    report = engine.check(document)
    assessment = _assessment(report, rule.rule_id)

    assert report.violations == []
    assert assessment.status == "not_assessed"
    assert "фрагмент" in (assessment.note or "")
