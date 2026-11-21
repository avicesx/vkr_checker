"""Сквозные проверки новых видов правил на реальных документах и синтетике.

Модуль закрепляет два уровня проверки новых правил нормоконтроля:

* реальные образцы ``manual_test/3.docx`` (``docx_only``) и
  ``manual_test/3.pdf`` (``pdf_only``) разбираются штатным парсером и
  проверяются штатным набором ``vkr_test_rules.json``: ссылки и список
  литературы, подписи рисунков и таблиц, нумерация таблиц. Режимы сравниваются
  между собой: в ``pdf_only`` нет стилей и pPr, но общий слой обогащения
  заполняет ``cross_ref_index``, поэтому новые проверки обязаны работать и по
  одному PDF. Каталог ``manual_test/`` не хранится в git, поэтому без образцов
  тесты пропускаются (``pytest.skip``), а не падают;
* синтетические документы проверяют точность правил на том же наборе: документ
  без дефектов не дает ни одного нарушения новых видов и все новые проверки
  получают статус ``pass``, а каждый внесенный дефект дает ровно одно нарушение
  с ожидаемым ``rule_id`` и не задевает соседние проверки.

Синтетика собирается как минимальный ``ParsedDocument`` (как в модульных тестах
правил): так дефект изолирован, а штатный набор применяется целиком. Старые
текстовые правила на минимальном документе дают нарушения (нет оглавления и
обязательных разделов), поэтому тесты синтетики проверяют только новые виды.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection, Dict, List, Sequence

import pytest

from parser.core.schema import (
    BibliographyEntry,
    CrossReferenceIndex,
    DocumentMeta,
    GlobalSettings,
    NumberedCaptionEntity,
    PageCountInfo,
    ParagraphBlock,
    ParagraphPropertiesEffective,
    ParsedDocument,
    RunFontPropertiesEffective,
    TableBlock,
    TableCell,
    TableRow,
    TextCrossReferenceMention,
    TextRun,
    ThemeData,
)
from rule_engine import (
    DEFAULT_RULESET_PATH,
    RuleAssessment,
    RuleCheckReport,
    RuleEngine,
    Violation,
    load_ruleset,
)
from services.document_parsing import parse_document

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = BACKEND_ROOT / "manual_test"
DOCX_SAMPLE = SAMPLES_DIR / "3.docx"
PDF_SAMPLE = SAMPLES_DIR / "3.pdf"

#: Записей в списке литературы образца; источника без ссылки в тексте — 73.
SAMPLE_BIBLIOGRAPHY_ENTRIES = 74
SAMPLE_UNCITED_ENTRIES = 73
#: Рисунков в образце два, и оба не упомянуты в тексте работы.
SAMPLE_UNREFERENCED_FIGURES = 2
#: Подписей таблиц в образце шесть; нумерация сквозная и корректная.
SAMPLE_TABLE_CAPTIONS = 6

#: ``rule_id`` новых правил → вид правила. Порядок задает идентификаторы
#: параметризации, поэтому это обычный словарь, а не множество.
NEW_RULE_CODES: Dict[str, str] = {
    "bibliography_links_resolved": "bibliography_link",
    "bibliography_all_cited": "bibliography_cited",
    "bibliography_format_electronic": "bibliography_format",
    "bibliography_format_article": "bibliography_format",
    "caption_numbering_figures": "caption_numbering",
    "caption_numbering_tables": "caption_numbering",
    "caption_format_figures": "caption_format",
    "caption_format_tables": "caption_format",
    "caption_reference_figures": "caption_reference",
    "caption_reference_tables": "caption_reference",
    "alignment_captions": "alignment",
    "alignment_table_cells": "alignment",
    "font_table_cells": "font",
}
NEW_RULE_IDS = frozenset(NEW_RULE_CODES)

#: Текст синтетического документа одного абзаца: ссылки на источники и объекты.
SYNTHETIC_BODY_TEXT = (
    "В работе использованы источники [1] и [2], рисунки 1, 2 и таблицы 1, 2."
)
#: Гарнитура ячеек таблицы, требуемая штатным набором (Times New Roman, 12 pt).
TABLE_CELL_FONT = "Times New Roman"
TABLE_CELL_SIZE_PT = 12.0


# ---------------------------------------------------------------------
# Общие помощники отчета
# ---------------------------------------------------------------------


def _new_code_violations(report: RuleCheckReport) -> List[Violation]:
    """Возвращает нарушения только новых видов правил."""
    return [
        violation for violation in report.violations if violation.rule_id in NEW_RULE_IDS
    ]


def _assessment(report: RuleCheckReport, rule_id: str) -> RuleAssessment:
    """Возвращает оценку правила; отсутствие оценки — ошибка теста, а не пропуск."""
    for assessment in report.assessments:
        if assessment.rule_id == rule_id:
            return assessment
    raise AssertionError(f"в отчете нет оценки правила {rule_id}")


@dataclass(frozen=True)
class SampleCheck:
    """Разобранный образец вместе с отчетом штатного набора правил."""

    path: Path
    document: ParsedDocument
    report: RuleCheckReport

    def violations(self, rule_id: str) -> List[Violation]:
        """Нарушения одного правила в порядке отчета."""
        return [
            violation
            for violation in self.report.violations
            if violation.rule_id == rule_id
        ]

    def assessment(self, rule_id: str) -> RuleAssessment:
        """Оценка одного правила отчета."""
        return _assessment(self.report, rule_id)


def _check_sample(engine: RuleEngine, path: Path, mode: str) -> SampleCheck:
    """Разбирает образец и проверяет его набором; без файла — ``pytest.skip``."""
    if not path.is_file():
        pytest.skip(
            f"нет образца {path.name}: каталог manual_test не хранится в git"
        )
    document = parse_document(path, mode=mode)
    return SampleCheck(path=path, document=document, report=engine.check(document))


@pytest.fixture(scope="module")
def standard_engine() -> RuleEngine:
    """Движок со штатным набором правил ``vkr_test_rules.json``."""
    return RuleEngine(load_ruleset(DEFAULT_RULESET_PATH))


@pytest.fixture(scope="module")
def docx_sample(standard_engine: RuleEngine) -> SampleCheck:
    """Реальный образец 3.docx, разобранный в режиме ``docx_only``."""
    return _check_sample(standard_engine, DOCX_SAMPLE, "docx_only")


@pytest.fixture(scope="module")
def pdf_sample(standard_engine: RuleEngine) -> SampleCheck:
    """Реальный образец 3.pdf, разобранный в режиме ``pdf_only``."""
    pytest.importorskip(
        "pymupdf", reason="Для режима pdf_only требуется PyMuPDF (pip install pymupdf)"
    )
    return _check_sample(standard_engine, PDF_SAMPLE, "pdf_only")


# ---------------------------------------------------------------------
# Штатный набор правил
# ---------------------------------------------------------------------


def test_standard_ruleset_applies_every_new_check(standard_engine: RuleEngine) -> None:
    """Штатный набор содержит все новые виды проверок без дублей ``rule_id``."""
    applied = [rule.rule_id for rule in standard_engine.ruleset.rules]

    assert NEW_RULE_IDS <= set(applied), sorted(NEW_RULE_IDS - set(applied))
    assert len(applied) == len(set(applied)), "в наборе повторяются rule_id"


# ---------------------------------------------------------------------
# Реальный DOCX: список литературы, подписи, нумерация таблиц
# ---------------------------------------------------------------------


@pytest.mark.integration
def test_docx_sample_reports_uncited_sources(docx_sample: SampleCheck) -> None:
    """В 3.docx 73 из 74 источников не имеют ссылки в тексте."""
    entries = docx_sample.document.cross_ref_index.bibliography
    uncited = [entry for entry in entries if not entry.is_cited_in_text]

    assert len(entries) == SAMPLE_BIBLIOGRAPHY_ENTRIES
    assert len(uncited) == SAMPLE_UNCITED_ENTRIES

    violations = docx_sample.violations("bibliography_all_cited")
    assert len(violations) == SAMPLE_UNCITED_ENTRIES
    assert {violation.violation_id for violation in violations} == {
        f"bibliography_all_cited:{entry.bib_id}" for entry in uncited
    }
    assert all(violation.code == "bibliography_cited" for violation in violations)
    assert all(
        violation.location.block_id == entry.block_id
        for violation, entry in zip(violations, uncited)
    )

    assessment = docx_sample.assessment("bibliography_all_cited")
    assert assessment.status == "fail"
    assert assessment.details["entries_considered"] == SAMPLE_BIBLIOGRAPHY_ENTRIES
    assert assessment.details["uncited_entries"] == SAMPLE_UNCITED_ENTRIES


@pytest.mark.integration
def test_docx_sample_has_no_unresolved_source_links(docx_sample: SampleCheck) -> None:
    """Нецитируемые источники не делают ссылки неразрешенными: это разные проверки."""
    assessment = docx_sample.assessment("bibliography_links_resolved")

    assert assessment.status == "pass"
    assert assessment.details["mentions_considered"] >= 1
    assert assessment.details["unresolved_mentions"] == 0
    assert docx_sample.violations("bibliography_links_resolved") == []


@pytest.mark.integration
def test_docx_sample_has_two_unreferenced_figures(docx_sample: SampleCheck) -> None:
    """Оба рисунка 3.docx не упомянуты в тексте и попадают в нарушения."""
    figures = docx_sample.document.cross_ref_index.figures
    unmentioned = [figure for figure in figures if not figure.is_mentioned_in_text]

    assert len(figures) == SAMPLE_UNREFERENCED_FIGURES
    assert len(unmentioned) == SAMPLE_UNREFERENCED_FIGURES

    violations = docx_sample.violations("caption_reference_figures")
    assert len(violations) == SAMPLE_UNREFERENCED_FIGURES
    assert {violation.violation_id for violation in violations} == {
        f"caption_reference_figures:{figure.entity_id}" for figure in unmentioned
    }
    assert all(violation.code == "caption_reference" for violation in violations)
    assert all(
        "нет ссылки в тексте" in violation.found_text for violation in violations
    )
    assert docx_sample.assessment("caption_reference_figures").status == "fail"

    # Таблицы образца упомянуты в тексте, поэтому правило ссылок на таблицы молчит.
    tables = docx_sample.document.cross_ref_index.tables
    assert tables
    assert all(table.is_mentioned_in_text for table in tables)
    assert docx_sample.violations("caption_reference_tables") == []

    # Слой аномалий парсера подтверждает то же самое независимо от правил.
    anomalies = docx_sample.document.anomalies
    assert set(anomalies.unreferenced_figure_ids) == {
        figure.entity_id for figure in unmentioned
    }
    assert anomalies.unreferenced_table_ids == []


@pytest.mark.integration
def test_docx_sample_table_numbering_is_clean(docx_sample: SampleCheck) -> None:
    """Нумерация таблиц 3.docx корректна и на уровне правил, и на уровне аномалий."""
    tables = docx_sample.document.cross_ref_index.tables

    assert len(tables) == SAMPLE_TABLE_CAPTIONS
    assert all(table.numbering_scheme == "continuous_arabic" for table in tables)
    assert docx_sample.violations("caption_numbering_tables") == []

    assessment = docx_sample.assessment("caption_numbering_tables")
    assert assessment.status == "pass"
    assert assessment.details["entity_type"] == "table"
    assert assessment.details["entities_considered"] == SAMPLE_TABLE_CAPTIONS
    assert assessment.details["issues_total"] == 0

    # Рисунки нумеруются отдельной последовательностью и тоже без ошибок.
    assert docx_sample.violations("caption_numbering_figures") == []
    assert docx_sample.assessment("caption_numbering_figures").status == "pass"

    table_issues = [
        issue
        for issue in docx_sample.document.anomalies.figure_numbering_issues
        if issue.entity_type == "table"
    ]
    assert table_issues == []


# ---------------------------------------------------------------------
# Реальный PDF: те же проверки через слой обогащения (docx_only недоступен)
# ---------------------------------------------------------------------


@pytest.mark.integration
def test_pdf_sample_is_parsed_without_docx_layer(pdf_sample: SampleCheck) -> None:
    """3.pdf разобран в pdf_only: стилей нет, но индекс ссылок заполнен обогащением."""
    document = pdf_sample.document
    index = document.cross_ref_index

    assert document.parse_mode == "pdf_only"
    assert document.capabilities.styles_parsed is False
    assert document.capabilities.pdf_layout_coordinates is True

    # Флаги capabilities описывают DOCX-слой, а cross_ref_index в pdf_only
    # заполняет parser.enrichment — пустым он быть не должен.
    assert len(index.bibliography) == SAMPLE_BIBLIOGRAPHY_ENTRIES
    assert index.figures
    assert len(index.tables) == SAMPLE_TABLE_CAPTIONS
    assert index.mentions


@pytest.mark.integration
def test_pdf_sample_runs_new_checks_through_enrichment(pdf_sample: SampleCheck) -> None:
    """Новые проверки работают по одному PDF: ссылки, подписи и нумерация таблиц."""
    entries = pdf_sample.document.cross_ref_index.bibliography
    uncited = [entry for entry in entries if not entry.is_cited_in_text]

    assert len(uncited) == SAMPLE_UNCITED_ENTRIES
    assert len(pdf_sample.violations("bibliography_all_cited")) == SAMPLE_UNCITED_ENTRIES
    assert pdf_sample.assessment("bibliography_all_cited").status == "fail"

    figures = pdf_sample.document.cross_ref_index.figures
    unmentioned = [figure for figure in figures if not figure.is_mentioned_in_text]
    assert unmentioned, "в pdf_only не нашлось неупомянутых рисунков: слой ссылок пуст"
    reference_violations = pdf_sample.violations("caption_reference_figures")
    assert len(reference_violations) == len(unmentioned)
    assert {violation.violation_id for violation in reference_violations} == {
        f"caption_reference_figures:{figure.entity_id}" for figure in unmentioned
    }

    assert pdf_sample.violations("caption_numbering_tables") == []
    tables_assessment = pdf_sample.assessment("caption_numbering_tables")
    assert tables_assessment.status == "pass"
    assert tables_assessment.details["entities_considered"] == SAMPLE_TABLE_CAPTIONS


@pytest.mark.integration
def test_docx_and_pdf_samples_agree_on_new_checks(
    docx_sample: SampleCheck, pdf_sample: SampleCheck
) -> None:
    """Паритет DOCX/PDF: семантика новых проверок одна и та же в обоих режимах."""
    docx_entries = len(docx_sample.document.cross_ref_index.bibliography)
    pdf_entries = len(pdf_sample.document.cross_ref_index.bibliography)
    assert docx_entries == pdf_entries == SAMPLE_BIBLIOGRAPHY_ENTRIES

    docx_uncited = len(docx_sample.violations("bibliography_all_cited"))
    pdf_uncited = len(pdf_sample.violations("bibliography_all_cited"))
    assert docx_uncited == pdf_uncited == SAMPLE_UNCITED_ENTRIES

    docx_tables = len(docx_sample.document.cross_ref_index.tables)
    pdf_tables = len(pdf_sample.document.cross_ref_index.tables)
    assert docx_tables == pdf_tables == SAMPLE_TABLE_CAPTIONS

    for sample in (docx_sample, pdf_sample):
        assert sample.violations("caption_numbering_tables") == []
        assert sample.assessment("caption_numbering_tables").status == "pass"
        assert sample.violations("caption_numbering_figures") == []

        figure_ids = {
            figure.entity_id for figure in sample.document.cross_ref_index.figures
        }
        reference_violations = sample.violations("caption_reference_figures")
        assert reference_violations, f"{sample.path.name}: ссылки на рисунки не проверены"
        assert all(
            violation.violation_id.split(":", 1)[1] in figure_ids
            for violation in reference_violations
        )


# ---------------------------------------------------------------------
# Синтетика: минимальный документ и точечные дефекты
# ---------------------------------------------------------------------


def _run(
    run_id: str,
    text: str,
    *,
    font: str = TABLE_CELL_FONT,
    size_pt: float = TABLE_CELL_SIZE_PT,
) -> TextRun:
    """Создает ран с измеренным шрифтом и кеглем."""
    return TextRun(
        run_id=run_id,
        text=text,
        visible_text=text,
        start_char=0,
        end_char=len(text),
        effective_props=RunFontPropertiesEffective(
            font_ascii=font,
            font_hansi=font,
            font_cs=font,
            size_pt=size_pt,
        ),
    )


def _paragraph(
    block_id: str,
    text: str,
    *,
    alignment: str = "left",
    container_type: str = "body",
    page: int = 2,
    runs: Sequence[TextRun] = (),
) -> ParagraphBlock:
    """Создает абзац с заданным выравниванием, контейнером и ранами."""
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
            alignment_source="direct",
        ),
        runs=list(runs),
    )


def _cell(
    block_id: str,
    text: str,
    *,
    alignment: str = "left",
    font: str = TABLE_CELL_FONT,
) -> ParagraphBlock:
    """Создает абзац ячейки таблицы с измеренным выравниванием и шрифтом."""
    return _paragraph(
        block_id,
        text,
        alignment=alignment,
        container_type="table_cell",
        page=3,
        runs=[_run(f"{block_id}_r0", text, font=font)],
    )


def _caption(
    entity_id: str,
    entity_type: str,
    number: str,
    *,
    prefix: str,
    caption_block_id: str,
    separator: str = "—",
    mentioned: bool = True,
    page: int = 2,
) -> NumberedCaptionEntity:
    """Создает пронумерованную подпись рисунка или таблицы."""
    return NumberedCaptionEntity(
        entity_id=entity_id,
        entity_type=entity_type,  # type: ignore[arg-type]
        caption_block_id=caption_block_id,
        page_est=page,
        prefix_word=prefix,
        number_str=number,
        numbering_scheme="continuous_arabic",
        separator=separator,
        title_text="Схема",
        has_trailing_dot=False,
        is_mentioned_in_text=mentioned,
    )


def _entry(
    bib_id: str,
    block_id: str,
    order: int,
    *,
    category: str,
    raw_text: str,
    cited: bool = True,
    urls: Sequence[str] = (),
    access_date: str | None = None,
    double_slash: bool = False,
) -> BibliographyEntry:
    """Создает запись списка литературы с разобранными полями."""
    return BibliographyEntry(
        bib_id=bib_id,
        block_id=block_id,
        order_index=order,
        raw_text=raw_text,
        detected_category=category,  # type: ignore[arg-type]
        urls=list(urls),
        access_date_raw=access_date,
        has_double_slash_source=double_slash,
        is_cited_in_text=cited,
    )


def _mention(
    mention_id: str,
    block_id: str,
    target: str,
    resolved: Sequence[str],
) -> TextCrossReferenceMention:
    """Создает ссылку ``[N]`` и результат ее разрешения в записи списка."""
    snippet = f"[{target}]"
    return TextCrossReferenceMention(
        mention_id=mention_id,
        block_id=block_id,
        page_est=2,
        start_char=0,
        end_char=len(snippet),
        raw_snippet=snippet,
        ref_type="bibliography",
        extracted_targets=[target],
        resolved_entity_ids=list(resolved),
    )


def _table(block_id: str, cell_paragraphs: Sequence[ParagraphBlock]) -> TableBlock:
    """Собирает таблицу из одной строки: по абзацу в каждой ячейке."""
    cells = [
        TableCell(
            cell_id=f"{block_id}_cell{index}",
            row_idx=0,
            col_idx=index,
            blocks=[paragraph],
            text_combined=paragraph.text_visible,
        )
        for index, paragraph in enumerate(cell_paragraphs)
    ]
    return TableBlock(
        block_id=block_id,
        section_idx=0,
        page_start_est=3,
        page_end_est=3,
        rows=[TableRow(row_idx=0, page_start_est=3, page_end_est=3, cells=cells)],
    )


def _synthetic_document(defects: Collection[str] = ()) -> ParsedDocument:
    """Собирает синтетический документ; ``defects`` добавляет по одной ошибке.

    Документ без дефектов удовлетворяет всем новым правилам штатного набора:
    два источника с разрешенными ссылками, электронный ресурс с URL и датой
    обращения, статья с «//», две подписи рисунков и две подписи таблиц со
    сквозной нумерацией и тире, все объекты упомянуты, подписи по центру, текст
    ячеек таблицы по левому краю и набран Times New Roman 12 pt.

    Каждый ``rule_id`` из ``defects`` вносит ровно один точечный дефект, поэтому
    документ с одним дефектом дает одно нарушение своего правила, а документ со
    всеми дефектами — по одному нарушению на каждое новое правило.
    """
    broken = set(defects)

    entries = [
        _entry(
            "bib_001",
            "p_bib_1",
            1,
            category="electronic_resource",
            raw_text=(
                "Электронный ресурс. — URL: https://example.org/data "
                "(дата обращения: 01.03.2024)."
            ),
            urls=(
                []
                if "bibliography_format_electronic" in broken
                else ["https://example.org/data"]
            ),
            access_date="01.03.2024",
        ),
        _entry(
            "bib_002",
            "p_bib_2",
            2,
            category="journal_article",
            raw_text="Петров П. П. Методика оценки // Вестник науки. — 2021. — С. 10-15.",
            cited="bibliography_all_cited" not in broken,
            double_slash="bibliography_format_article" not in broken,
        ),
    ]

    # Дефект ссылки: вторая ссылка ведет на номер 9, которого нет в списке.
    unresolved_link = "bibliography_links_resolved" in broken
    mentions = [
        _mention("men_001", "p_body", "1", ["bib_001"]),
        _mention(
            "men_002",
            "p_body",
            "9" if unresolved_link else "2",
            [] if unresolved_link else ["bib_002"],
        ),
    ]

    figures = [
        _caption(
            "fig_001",
            "figure",
            "1",
            prefix="Рисунок",
            caption_block_id="p_cap_fig_1",
            separator="." if "caption_format_figures" in broken else "—",
            mentioned="caption_reference_figures" not in broken,
        ),
        _caption(
            "fig_002",
            "figure",
            "3" if "caption_numbering_figures" in broken else "2",
            prefix="Рисунок",
            caption_block_id="p_cap_fig_2",
        ),
    ]
    tables = [
        _caption(
            "tbl_001",
            "table",
            "1",
            prefix="Таблица",
            caption_block_id="p_cap_tbl_1",
        ),
        _caption(
            "tbl_002",
            "table",
            "1" if "caption_numbering_tables" in broken else "2",
            prefix="Таблица",
            caption_block_id="p_cap_tbl_2",
            separator="." if "caption_format_tables" in broken else "—",
            mentioned="caption_reference_tables" not in broken,
        ),
    ]

    figure_caption_alignment = (
        "left" if "alignment_captions" in broken else "center"
    )
    blocks: List[Any] = [
        _paragraph("p_body", SYNTHETIC_BODY_TEXT),
        _paragraph(
            "p_cap_fig_1",
            "Рисунок 1 — Схема",
            alignment=figure_caption_alignment,
        ),
        _paragraph("p_cap_fig_2", "Рисунок 2 — Схема", alignment="center"),
        _paragraph("p_cap_tbl_1", "Таблица 1 — Данные", alignment="center"),
        _paragraph("p_cap_tbl_2", "Таблица 2 — Данные", alignment="center"),
        _paragraph("p_bib_1", "Электронный ресурс.", page=4),
        _paragraph("p_bib_2", "Статья в журнале.", page=4),
        _table(
            "tbl_block_1",
            [
                _cell("cell_1_p0", "Данные"),
                _cell(
                    "cell_2_p0",
                    "Значение",
                    alignment=(
                        "center" if "alignment_table_cells" in broken else "left"
                    ),
                    font=(
                        "Arial" if "font_table_cells" in broken else TABLE_CELL_FONT
                    ),
                ),
            ],
        ),
    ]

    return ParsedDocument(
        parse_mode="docx_only",
        meta=DocumentMeta(filename="synthetic.docx", file_sha256="0" * 64),
        settings=GlobalSettings(),
        theme=ThemeData(),
        page_count=PageCountInfo(
            total_pages=4, method="docx_native_breaks", confidence="high"
        ),
        blocks=blocks,
        cross_ref_index=CrossReferenceIndex(
            bibliography=entries,
            mentions=mentions,
            figures=figures,
            tables=tables,
        ),
    )


def test_synthetic_clean_document_passes_every_new_check(
    standard_engine: RuleEngine,
) -> None:
    """Документ без дефектов: ни одного нарушения новых видов и все проверки pass."""
    report = standard_engine.check(_synthetic_document())

    assert _new_code_violations(report) == []

    for rule_id in sorted(NEW_RULE_IDS):
        assessment = _assessment(report, rule_id)
        assert assessment.status == "pass", (
            f"{rule_id}: {assessment.status} ({assessment.observed_text})"
        )
        assert assessment.details, f"{rule_id}: проверка не измерила ни одного объекта"

    assert report.summary.objects_checked > 0


def test_every_rule_returns_exactly_one_assessment(standard_engine: RuleEngine) -> None:
    """Каждое правило набора дает ровно одну оценку с допустимым статусом."""
    report = standard_engine.check(_synthetic_document())

    expected_ids = [rule.rule_id for rule in standard_engine.ruleset.rules]
    assessed_ids = [assessment.rule_id for assessment in report.assessments]
    assert sorted(assessed_ids) == sorted(expected_ids), "оценки не покрывают набор"
    assert len(assessed_ids) == len(set(assessed_ids)), "правило оценивалось дважды"
    for assessment in report.assessments:
        assert assessment.status in {"pass", "fail", "not_assessed"}
        assert assessment.expected_text, f"{assessment.rule_id}: пустое ожидание"
        assert assessment.observed_text, f"{assessment.rule_id}: пустое наблюдение"


@pytest.mark.parametrize("rule_id", tuple(NEW_RULE_CODES))
def test_synthetic_single_defect_gives_exactly_one_violation(
    standard_engine: RuleEngine, rule_id: str
) -> None:
    """Одна ошибка каждого вида дает ровно одно нарушение своего правила."""
    report = standard_engine.check(_synthetic_document({rule_id}))

    violations = _new_code_violations(report)
    assert len(violations) == 1, (
        f"{rule_id}: нарушений новых видов {len(violations)}, "
        f"ожидалось одно: {[v.violation_id for v in violations]}"
    )
    violation = violations[0]
    assert violation.rule_id == rule_id
    assert violation.code == NEW_RULE_CODES[rule_id]


def test_synthetic_all_defects_give_one_violation_per_rule(
    standard_engine: RuleEngine,
) -> None:
    """Документ со всеми дефектами сразу: по одному нарушению на каждое правило."""
    report = standard_engine.check(_synthetic_document(NEW_RULE_IDS))

    counts: Dict[str, int] = {}
    for violation in _new_code_violations(report):
        counts[violation.rule_id] = counts.get(violation.rule_id, 0) + 1

    assert counts == {rule_id: 1 for rule_id in NEW_RULE_CODES}
    assert sum(counts.values()) == len(NEW_RULE_CODES)
    assert {violation.code for violation in _new_code_violations(report)} == set(
        NEW_RULE_CODES.values()
    )

    for rule_id in NEW_RULE_CODES:
        assert report.summary.by_rule[rule_id] == 1, (
            f"{rule_id}: сводка отчета расходится с нарушениями"
        )
