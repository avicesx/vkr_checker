"""Детерминированные смоук-тесты PDF-нормоконтроля на сгенерированных файлах.

PDF рисуется на лету через PyMuPDF: A4, Times New Roman 14 pt, полуторный
межстрочный интервал Word (типографский шаг 1,72 кегля) и номер страницы в
нижнем колонтитуле по центру. Каждый тест меняет ровно одну характеристику
вёрстки, поэтому проверяется адресный статус правила профиля УрФУ: ``pass``
для эталонного документа, ``fail`` для осознанной модификации и
``not_assessed``, когда данных для вывода нет.

Особенность PDF-слоя: гарнитура приходит из шрифтовой программы в
PostScript-виде (``TimesNewRomanPSMT`` -> ``TimesNewRoman``). Движок сравнивает
семейства в компактной форме — без регистра, пробелов, дефисов, подчёркиваний и
точек, — поэтому ``TimesNewRoman`` соответствует требованию ``Times New Roman``,
а чужие семейства (``Arial``) дают нарушение. Межстрочный интервал для pdf_only
берётся из оценки ``parser.pdf`` (``meta.pdf_median_leading_ratio``, шкала
шаг / кегль) и переводится движком в множитель Word делением на
``PDF_SINGLE_LINE_FACTOR`` = 1,15; поэтому эталонный PDF с шагом 1,72 кегля
получает множитель 1,5.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import pytest

pymupdf = pytest.importorskip(
    "pymupdf",
    reason="Для генерации PDF-образцов требуется PyMuPDF (pip install pymupdf)",
)

from parser.core.schema import ParsedDocument
from parser.pdf import parse_pdf
from rule_engine import RuleCheckReport, RuleEngine, load_profile_ruleset

MM_TO_PT = 72.0 / 25.4

A4_MM = (210.0, 297.0)
LETTER_MM = (215.9, 279.4)
PROFILE_ID = "urfu"

BODY_SIZE_PT = 14.0
MODIFIED_SIZE_PT = 12.0
PAGE_NUMBER_SIZE_PT = 12.0
#: Одиночная строка Word занимает примерно 1,15 кегля, поэтому полуторному
#: интервалу Word (1,5) соответствует типографский шаг 1,725 кегля.
PDF_SINGLE_LINE_FACTOR = 1.15
WORD_BODY_LINE_SPACING = 1.5
BODY_LINE_HEIGHT = WORD_BODY_LINE_SPACING * PDF_SINGLE_LINE_FACTOR
WORD_SINGLE_LINE_SPACING = 1.0
SINGLE_LINE_HEIGHT = WORD_SINGLE_LINE_SPACING * PDF_SINGLE_LINE_FACTOR
WIDE_LINE_HEIGHT = 3.2
BODY_LINES = 14
LEFT_MARGIN_MM = 30.0
FIRST_BASELINE_MM = 40.0
HEADER_BASELINE_MM = 25.0
FOOTER_BASELINE_OFFSET_MM = 9.0
PAGE_COUNT = 4

#: Кандидаты файлов шрифтов (с кириллицей) для синтетических PDF по семействам.
FONT_DIRS = (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/msttcorefonts"))
FONT_FILES = {
    "Times New Roman": (
        "times.ttf",
        "Times New Roman.ttf",
        "LiberationSerif-Regular.ttf",
    ),
    "Arial": ("arial.ttf", "Arial.ttf", "LiberationSans-Regular.ttf"),
}


def _find_font(family: str = "Times New Roman") -> Path:
    """Ищет файл шрифта семейства и пропускает тест, если шрифта нет в системе."""
    for directory in FONT_DIRS:
        for name in FONT_FILES[family]:
            candidate = directory / name
            if candidate.exists():
                return candidate
    pytest.skip(f"Не найден шрифт {family} с кириллицей для синтетического PDF")


def _pt(value_mm: float) -> float:
    """Переводит миллиметры в пункты PDF."""
    return value_mm * MM_TO_PT


def _build_pdf(
    path: Path,
    *,
    page_size_mm: Tuple[float, float] = A4_MM,
    font_size_pt: float = BODY_SIZE_PT,
    number_zone: Optional[str] = "footer",
    font_family: str = "Times New Roman",
    line_height: float = BODY_LINE_HEIGHT,
) -> Path:
    """Собирает PDF с заданным форматом, шрифтом, кеглем, интервалом и номером.

    ``number_zone``: ``footer`` — номер снизу по центру, ``header`` — сверху по
    центру, ``None`` — страницы без номеров. ``font_family`` выбирает файл
    шрифта, ``line_height`` задаёт типографский множитель шага строк
    (шаг / кегль).
    """
    width_mm, height_mm = page_size_mm
    font_path = _find_font(font_family)
    #: Текст образца не должен задевать маркеры титульного листа из
    #: ``rule_engine.context``: «нормоконтрол…» — титульный реквизит, и при кегле
    #: <= 12 pt классификатор пометил бы страницы титульными и скрыл нарушение
    #: шрифта. Образец обязан читаться как основной текст при любом кегле.
    body_text = "\n".join(
        f"Строка основного текста номер {row + 1} для проверки оформления"
        for row in range(BODY_LINES)
    )

    document = pymupdf.open()
    try:
        for page_number in range(1, PAGE_COUNT + 1):
            page = document.new_page(width=_pt(width_mm), height=_pt(height_mm))
            page.insert_font(fontname="body", fontfile=str(font_path))
            page.insert_text(
                pymupdf.Point(_pt(LEFT_MARGIN_MM), _pt(FIRST_BASELINE_MM)),
                body_text,
                fontname="body",
                fontsize=font_size_pt,
                lineheight=line_height,
                color=(0, 0, 0),
            )
            if number_zone is None:
                continue
            baseline_mm = (
                HEADER_BASELINE_MM
                if number_zone == "header"
                else height_mm - FOOTER_BASELINE_OFFSET_MM
            )
            page.insert_text(
                pymupdf.Point(_pt(width_mm / 2.0 - 1.5), _pt(baseline_mm)),
                str(page_number),
                fontname="body",
                fontsize=PAGE_NUMBER_SIZE_PT,
                color=(0, 0, 0),
            )
        document.save(path)
    finally:
        document.close()
    return path


def _check(
    path: Path,
    profile_id: str = PROFILE_ID,
) -> Tuple[ParsedDocument, RuleCheckReport]:
    """Разбирает PDF конвейером parser.pdf и прогоняет движок с профилем."""
    document = parse_pdf(path)
    report = RuleEngine(load_profile_ruleset(profile_id)).check(document)
    return document, report


def _assessment(report: RuleCheckReport, rule_id: str):
    """Возвращает assessment правила, падая с понятным сообщением при его отсутствии."""
    for assessment in report.assessments:
        if assessment.rule_id == rule_id:
            return assessment
    raise AssertionError(
        f"В отчёте нет assessment для правила {rule_id!r}: "
        f"{[item.rule_id for item in report.assessments]}"
    )


def _violations(report: RuleCheckReport, rule_id: str) -> List:
    """Возвращает нарушения конкретного правила."""
    return [
        violation for violation in report.violations if violation.rule_id == rule_id
    ]


def _compact_family(family: str) -> str:
    """Приводит имя гарнитуры к компактной форме так же, как это делает движок."""
    name = (family or "").casefold()
    return "".join(char for char in name if char not in " .-_")


def test_ideal_pdf_passes_page_number_format_and_line_spacing(tmp_path: Path) -> None:
    """Эталонный PDF: A4, 14 pt, полуторный интервал Word, номер снизу по центру."""
    pdf_path = _build_pdf(tmp_path / "ideal.pdf")
    document, report = _check(pdf_path)

    assert document.parse_mode == "pdf_only"
    assert document.meta.filename == pdf_path.name
    assert document.page_count.total_pages == PAGE_COUNT
    assert document.meta.pdf_dominant_font_size_pt == pytest.approx(BODY_SIZE_PT)
    assert document.meta.pdf_median_leading_ratio == pytest.approx(
        BODY_LINE_HEIGHT, abs=0.02
    )
    assert document.meta.pdf_median_line_pitch_mm == pytest.approx(
        BODY_SIZE_PT * BODY_LINE_HEIGHT * 25.4 / 72.0, abs=0.2
    )
    assert document.meta.pdf_leading_sample_count > 0

    number_check = _assessment(report, "page_number_position")
    assert number_check.status == "pass"
    assert number_check.confidence == "medium"
    assert number_check.details["distribution"] == {"footer:center": PAGE_COUNT}

    format_check = _assessment(report, "page_format")
    assert format_check.status == "pass"
    assert format_check.details["sections_checked"] == 1

    spacing_check = _assessment(report, "line_spacing_body")
    assert spacing_check.status == "pass"
    # Для pdf_only шкала одна: meta.pdf_median_leading_ratio (шаг / кегль).
    # Абзацы PDF-структуры с line_spacing_source == "pdf_estimate" исключены,
    # а измеренный шаг движок переводит в множитель Word делением на 1,15.
    assert spacing_check.details["source"] == "pdf_leading"
    assert spacing_check.details["median_ratio"] == pytest.approx(
        BODY_LINE_HEIGHT, abs=0.02
    )
    assert spacing_check.details["pdf_single_line_factor"] == pytest.approx(
        PDF_SINGLE_LINE_FACTOR
    )
    assert spacing_check.details["word_multiplier"] == pytest.approx(
        WORD_BODY_LINE_SPACING, abs=0.02
    )
    assert spacing_check.details["pdf_estimate_paragraphs"] > 0

    # PostScript-имя гарнитуры (TimesNewRoman) считается тем же семейством,
    # что и "Times New Roman": нарушений правила шрифта быть не должно.
    assert _violations(report, "font_body_text") == []


def test_header_page_number_fails_position_check(tmp_path: Path) -> None:
    """Номер страницы сверху по центру нарушает профиль УрФУ (ожидается снизу)."""
    pdf_path = _build_pdf(tmp_path / "header_number.pdf", number_zone="header")
    document, report = _check(pdf_path)

    assert document.meta.pdf_dominant_font_size_pt == pytest.approx(BODY_SIZE_PT)

    number_check = _assessment(report, "page_number_position")
    assert number_check.status == "fail"
    assert number_check.details["distribution"] == {"header:center": PAGE_COUNT}

    violations = _violations(report, "page_number_position")
    assert violations
    assert violations[0].found["zone"] == "header"
    assert violations[0].found["alignment"] == "center"

    # Остальные проверки не должны страдать от перенесённого номера.
    assert _assessment(report, "page_format").status == "pass"
    assert _assessment(report, "line_spacing_body").status == "pass"


def test_letter_page_size_fails_format_check(tmp_path: Path) -> None:
    """Лист Letter (215,9 × 279,4 мм) не проходит проверку формата A4."""
    pdf_path = _build_pdf(tmp_path / "letter.pdf", page_size_mm=LETTER_MM)
    document, report = _check(pdf_path)

    assert document.page_count.total_pages == PAGE_COUNT

    format_check = _assessment(report, "page_format")
    assert format_check.status == "fail"
    assert "215" in format_check.observed_text

    violations = _violations(report, "page_format")
    assert violations
    assert violations[0].found["width_mm"] == pytest.approx(LETTER_MM[0], abs=0.5)
    assert violations[0].found["height_mm"] == pytest.approx(LETTER_MM[1], abs=0.5)
    assert violations[0].found["is_a4_standard"] is False

    # Формат листа не мешает проверке номера и интервала.
    assert _assessment(report, "page_number_position").status == "pass"
    assert _assessment(report, "line_spacing_body").status == "pass"


def test_small_font_size_fails_font_check(tmp_path: Path) -> None:
    """Кегль 12 pt вместо 14 pt даёт нарушение правила шрифта по размеру."""
    pdf_path = _build_pdf(tmp_path / "font12.pdf", font_size_pt=MODIFIED_SIZE_PT)
    document, report = _check(pdf_path)

    assert document.meta.pdf_dominant_font_size_pt == pytest.approx(MODIFIED_SIZE_PT)

    size_violations = [
        violation
        for violation in _violations(report, "font_body_text")
        if "size_pt" in violation.found
    ]
    assert size_violations
    assert {violation.expected["size_pt"] for violation in size_violations} == {
        BODY_SIZE_PT
    }
    for violation in size_violations:
        assert violation.found["size_pt"] == pytest.approx(MODIFIED_SIZE_PT, abs=0.1)

    # Гарнитура та же (TimesNewRoman), поэтому других нарушений шрифта нет.
    assert len(_violations(report, "font_body_text")) == len(size_violations)

    # Интервал остался полуторным (Word 1,5): шаг 1,725 кегля при кегле 12 pt.
    assert _assessment(report, "line_spacing_body").status == "pass"
    assert _assessment(report, "page_format").status == "pass"


def test_postscript_times_name_passes_font_family_check(tmp_path: Path) -> None:
    """PDF с PostScript-именем TimesNewRoman проходит правило семейства TNR."""
    pdf_path = _build_pdf(tmp_path / "postscript_name.pdf")
    document, report = _check(pdf_path)

    # Документ действительно содержит имя без пробелов — это и есть проверяемый случай.
    families = {
        run.effective_props.font_ascii
        for block in document.blocks
        for run in block.runs
    }
    assert families == {"TimesNewRoman"}
    assert _violations(report, "font_body_text") == []
    assert _assessment(report, "line_spacing_body").status == "pass"


def test_foreign_font_family_fails_font_check(tmp_path: Path) -> None:
    """Чужое семейство (Arial) при верном кегле нарушает требование TNR."""
    pdf_path = _build_pdf(tmp_path / "arial.pdf", font_family="Arial")
    document, report = _check(pdf_path)

    assert document.meta.pdf_dominant_font_size_pt == pytest.approx(BODY_SIZE_PT)

    family_violations = [
        violation
        for violation in _violations(report, "font_body_text")
        if "family" in violation.found
    ]
    assert family_violations
    for violation in family_violations:
        assert violation.expected["family"] == "Times New Roman"
        assert _compact_family(violation.found["family"]) != "timesnewroman"
        # Кегль верный: нарушение именно по гарнитуре.
        assert "size_pt" not in violation.found

    # Проверки, не зависящие от гарнитуры, остаются в норме.
    assert _assessment(report, "line_spacing_body").status == "pass"
    assert _assessment(report, "page_format").status == "pass"


def test_single_line_spacing_fails_line_spacing_check(tmp_path: Path) -> None:
    """Одинарный интервал Word (1,0) вместо полуторного даёт fail и нарушение."""
    pdf_path = _build_pdf(
        tmp_path / "spacing_single.pdf", line_height=SINGLE_LINE_HEIGHT
    )
    document, report = _check(pdf_path)

    assert document.meta.pdf_median_leading_ratio == pytest.approx(
        SINGLE_LINE_HEIGHT, abs=0.02
    )

    spacing_check = _assessment(report, "line_spacing_body")
    assert spacing_check.status == "fail"
    assert spacing_check.details["source"] == "pdf_leading"
    assert spacing_check.details["median_ratio"] == pytest.approx(
        SINGLE_LINE_HEIGHT, abs=0.02
    )
    assert spacing_check.details["word_multiplier"] == pytest.approx(
        WORD_SINGLE_LINE_SPACING, abs=0.02
    )

    violations = _violations(report, "line_spacing_body")
    assert violations
    assert violations[0].found["ratio"] == pytest.approx(
        SINGLE_LINE_HEIGHT, abs=0.02
    )
    assert violations[0].found["word_multiplier"] == pytest.approx(
        WORD_SINGLE_LINE_SPACING, abs=0.02
    )
    assert violations[0].found["source"] == "pdf_leading"
    assert violations[0].expected["ratio"] == pytest.approx(WORD_BODY_LINE_SPACING)

    # Номер страницы и формат листа от интервала не зависят.
    assert _assessment(report, "page_number_position").status == "pass"
    assert _assessment(report, "page_format").status == "pass"


def test_out_of_window_line_spacing_is_not_assessed(tmp_path: Path) -> None:
    """Интервал вне окна оценки (0,8–2,5) не измеряется: not_assessed, а не fail."""
    pdf_path = _build_pdf(
        tmp_path / "spacing_wide.pdf", line_height=WIDE_LINE_HEIGHT
    )
    document, report = _check(pdf_path)

    assert document.meta.pdf_median_leading_ratio is None
    assert document.meta.pdf_leading_sample_count == 0
    assert all(page.leading_ratio is None for page in document.pdf_pages_audit)

    spacing_check = _assessment(report, "line_spacing_body")
    assert spacing_check.status == "not_assessed"
    assert spacing_check.confidence == "low"
    assert spacing_check.details["source"] == "none"
    assert "line_spacing_body" in {item.rule_id for item in report.not_assessed}
    assert _violations(report, "line_spacing_body") == []

    # Остальные проверки по-прежнему оцениваются.
    assert _assessment(report, "page_number_position").status == "pass"
    assert _assessment(report, "page_format").status == "pass"


def test_pdf_without_page_numbers_is_not_assessed(tmp_path: Path) -> None:
    """Без номеров страниц положение не оценивается: not_assessed, а не fail."""
    pdf_path = _build_pdf(tmp_path / "no_numbers.pdf", number_zone=None)
    document, report = _check(pdf_path)

    assert document.page_count.total_pages == PAGE_COUNT
    assert all(page.page_number_location == "none" for page in document.pdf_pages_audit)

    number_check = _assessment(report, "page_number_position")
    assert number_check.status == "not_assessed"
    assert number_check.confidence == "low"
    assert number_check.details["pages_with_number"] == 0
    assert number_check.details["threshold"] == max(3, PAGE_COUNT // 3)
    assert [item.rule_id for item in report.not_assessed] == ["page_number_position"]
    assert report.summary.not_assessed_total == 1


@pytest.mark.parametrize("profile_id", ["mpgu", "urgpu", "urfu"])
def test_ideal_pdf_report_built_for_every_profile(
    tmp_path: Path,
    profile_id: str,
) -> None:
    """Эталонный PDF даёт собранный отчёт и валидные статусы для всех профилей."""
    pdf_path = _build_pdf(tmp_path / f"ideal_{profile_id}.pdf")
    _, report = _check(pdf_path, profile_id)

    assert report.ruleset_id == load_profile_ruleset(profile_id).ruleset_id
    assert {assessment.status for assessment in report.assessments} <= {
        "pass",
        "fail",
        "not_assessed",
    }
    assessed_ids = {assessment.rule_id for assessment in report.assessments}
    assert {"page_number_position", "line_spacing_body"} <= assessed_ids
    assert ("page_format" in assessed_ids) == (profile_id == "urfu")
    assert report.summary.not_assessed_total == len(report.not_assessed)


def test_page_number_position_expectation_differs_between_profiles(
    tmp_path: Path,
) -> None:
    """Один PDF: нижний номер проходит у УрГПУ и нарушает требование МПГУ (сверху)."""
    pdf_path = _build_pdf(tmp_path / "profiles.pdf")
    document = parse_pdf(pdf_path)

    mpgu_report = RuleEngine(load_profile_ruleset("mpgu")).check(document)
    urgpu_report = RuleEngine(load_profile_ruleset("urgpu")).check(document)

    assert _assessment(mpgu_report, "page_number_position").status == "fail"
    assert _assessment(urgpu_report, "page_number_position").status == "pass"
