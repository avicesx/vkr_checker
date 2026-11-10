"""Нормализация имени гарнитуры и exemption символических ранов в правиле шрифта.

Корпус ВКР давал 1055 ложных font-нарушений из 1109: Word пишет ту же гарнитуру
как «TimesNewRoman», «TimesNewRomanPSMT», «ABCDEF+TimesNewRoman-BoldMT», а
математические знаки (±, ≤, ×) набирает гарнитурой SymbolMT. Тесты фиксируют,
что такие записи считаются ожидаемым Times New Roman, символьные раны не
проверяются, а реальное расхождение (Arial 12 pt, Consolas) по-прежнему
попадает в отчет.
"""
from __future__ import annotations

from typing import Dict

import pytest
from conftest import DocxPackageBuilder
from parser.core.schema import ParsedDocxDocument
from parser.docx.pipeline import DocxParserPipeline
from rule_engine import RuleEngine, RuleSet
from rule_engine._fonts import family_keys as _family_keys
from rule_engine._fonts import is_symbolic_family as _is_symbolic_family
from rule_engine._fonts import same_family as _same_family
from rule_engine.rules import load_default_ruleset

BODY_TEXT = "Основной текст документа для проверки шрифта"


def _run_xml(
    family: str,
    text: str,
    size_half_points: int = 28,
    bold: bool = False,
) -> str:
    """Собирает ран с прямым форматированием гарнитуры и кегля."""
    bold_xml = "<w:b/>" if bold else ""
    return f"""
  <w:r>
    <w:rPr>
      <w:rFonts w:ascii="{family}" w:hAnsi="{family}"/>
      <w:sz w:val="{size_half_points}"/>{bold_xml}
    </w:rPr>
    <w:t xml:space="preserve">{text}</w:t>
  </w:r>
"""


def _paragraph(*runs: str) -> str:
    """Собирает абзац из готовых ранов."""
    return "<w:p>" + "".join(runs) + "</w:p>"


def _parse(builder: DocxPackageBuilder, body: str) -> ParsedDocxDocument:
    """Разбирает DOCX, собранный из переданного тела документа."""
    builder.set_body(body)
    return DocxParserPipeline(builder.build()).parse()


def _font_engine(**expected: object) -> RuleEngine:
    """Собирает движок с единственным правилом шрифта Times New Roman 14 pt."""
    font_expected: Dict[str, object] = {"family": "Times New Roman", "size_pt": 14.0}
    font_expected.update(expected)
    payload = {
        "ruleset_id": "font-normalization-test",
        "ruleset_version": "1.0.0",
        "rules": [
            {
                "rule_id": "font_body_text",
                "kind": "font",
                "message": "Основной текст набирается Times New Roman, 14 pt",
                "target": {"scope": "body_text"},
                "expected": font_expected,
            }
        ],
    }
    return RuleEngine(RuleSet.model_validate(payload))


# ---------------------------------------------------------------------------
# Нормализация записи гарнитуры
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ("TimesNewRoman", "Times New Roman"),
        ("times new roman", "Times New Roman"),
        ("TimesNewRomanPSMT", "Times New Roman"),
        ("ABCDEF+TimesNewRoman-BoldMT", "Times New Roman"),
        ("ArialMT", "Arial"),
        ("Arial-BoldItalic", "Arial"),
        ("Arial_Bold", "Arial"),
        ("Calibri-Bold", "Calibri"),
        ("Wingdings-Regular", "Wingdings"),
    ],
)
def test_family_spellings_are_equivalent(actual: str, expected: str) -> None:
    """Одна гарнитура в разных записях Word считается одной и той же."""
    assert _same_family(actual, expected) is True
    assert _same_family(expected, actual) is True


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ("Consolas", "Times New Roman"),
        ("Arial", "Times New Roman"),
        ("Times", "Times New Roman"),
        ("Calibri", "Times New Roman"),
        ("", "Times New Roman"),
    ],
)
def test_different_families_are_not_equivalent(actual: str, expected: str) -> None:
    """Разные гарнитуры не совпадают даже после нормализации."""
    assert _same_family(actual, expected) is False


def test_family_keys_keep_original_and_normalized_spelling() -> None:
    """Ключи включают исходную запись и вариант без стилевых маркеров."""
    keys = _family_keys("ABCDEF+TimesNewRoman-BoldMT")
    assert keys[0] == "timesnewromanboldmt"
    assert "timesnewroman" in keys


def test_compact_spelling_passes_font_rule(docx_builder: DocxPackageBuilder) -> None:
    """Ран с гарнитурой 'TimesNewRoman' соответствует ожиданию 'Times New Roman'."""
    document = _parse(docx_builder, _paragraph(_run_xml("TimesNewRoman", BODY_TEXT)))

    report = _font_engine().check(document)

    assert report.passed is True
    assert report.violations == []


def test_subset_prefixed_bold_family_passes_font_rule(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Подмножество 'ABCDEF+TimesNewRoman-BoldMT' не дает нарушения гарнитуры."""
    body = _paragraph(
        _run_xml("ABCDEF+TimesNewRoman-BoldMT", BODY_TEXT, bold=True),
    )
    document = _parse(docx_builder, body)

    report = _font_engine().check(document)

    assert report.violations == []


def test_ps_suffixed_family_passes_font_rule(docx_builder: DocxPackageBuilder) -> None:
    """PostScript-суффикс PSMT не считается частью имени гарнитуры."""
    document = _parse(docx_builder, _paragraph(_run_xml("TimesNewRomanPSMT", BODY_TEXT)))

    report = _font_engine().check(document)

    assert report.violations == []


def test_other_family_is_still_reported(docx_builder: DocxPackageBuilder) -> None:
    """Georgia 14 pt остается нарушением с фактической гарнитурой и кеглем.

    Consolas здесь не годится: моноширинный шрифт классификатор контекста
    помечает как листинг кода, и правило в основной текст его не берет.
    """
    document = _parse(docx_builder, _paragraph(_run_xml("Georgia", BODY_TEXT)))

    report = _font_engine().check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.found["family"] == "Georgia"
    assert violation.expected["family"] == "Times New Roman"
    assert violation.found_text == "Georgia"
    assert violation.occurrences == 1


def test_arial_12pt_is_still_reported(docx_builder: DocxPackageBuilder) -> None:
    """Реальное расхождение Arial 12 pt по-прежнему падает: и гарнитура, и кегль."""
    body = _paragraph(_run_xml("Arial", BODY_TEXT, size_half_points=24))
    document = _parse(docx_builder, body)

    report = _font_engine().check(document)

    assert report.passed is False
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.found["family"] == "Arial"
    assert violation.found["size_pt"] == pytest.approx(12.0)
    assert violation.found_text == "Arial, 12 pt"


def test_strict_mode_keeps_legacy_comparison(docx_builder: DocxPackageBuilder) -> None:
    """normalize_family=false возвращает строгое сравнение записи имени."""
    document = _parse(docx_builder, _paragraph(_run_xml("TimesNewRoman", BODY_TEXT)))

    report = _font_engine(normalize_family=False).check(document)

    assert len(report.violations) == 1
    assert report.violations[0].found["family"] == "TimesNewRoman"


def test_normalize_family_defaults_to_true() -> None:
    """Нормализация гарнитуры включена по умолчанию и остается в схеме правила."""
    ruleset = load_default_ruleset()
    font_rule = ruleset.get_rule("font_body_text")
    assert font_rule.expected.normalize_family is True
    assert font_rule.expected.family == "Times New Roman"


def test_default_ruleset_still_loads() -> None:
    """Поставляемый набор правил загружается и проверяет документ."""
    ruleset = load_default_ruleset()
    assert ruleset.ruleset_id == "vkr-rule-engine-test"
    assert len(ruleset.rules) >= 4


# ---------------------------------------------------------------------------
# Символические раны
# ---------------------------------------------------------------------------


def test_symbol_family_run_is_exempt(docx_builder: DocxPackageBuilder) -> None:
    """Ран гарнитурой SymbolMT (знак ±) не проверяется правилом шрифта."""
    body = _paragraph(
        _run_xml("Times New Roman", BODY_TEXT),
        _run_xml("SymbolMT", "±"),
    )
    document = _parse(docx_builder, body)

    report = _font_engine().check(document)

    assert report.violations == []


def test_symbol_family_run_with_letters_is_exempt(docx_builder: DocxPackageBuilder) -> None:
    """Символьная гарнитура исключается независимо от текста рана."""
    body = _paragraph(_run_xml("Wingdings", "ab", size_half_points=20))
    document = _parse(docx_builder, body)

    report = _font_engine().check(document)

    assert report.violations == []


def test_run_of_math_signs_only_is_exempt(docx_builder: DocxPackageBuilder) -> None:
    """Ран из одних небуквенноцифровых знаков не проверяется даже в Times New Roman."""
    body = _paragraph(
        _run_xml("Times New Roman", BODY_TEXT),
        _run_xml("Times New Roman", "± ≤ ×", size_half_points=20),
    )
    document = _parse(docx_builder, body)

    report = _font_engine().check(document)

    assert report.violations == []


def test_symbolic_run_does_not_mask_textual_mismatch(
    docx_builder: DocxPackageBuilder,
) -> None:
    """В абзаце с Calibri и знаком ± нарушением остается только текстовый ран."""
    body = _paragraph(
        _run_xml("Calibri", BODY_TEXT, size_half_points=22),
        _run_xml("SymbolMT", "±"),
    )
    document = _parse(docx_builder, body)

    report = _font_engine().check(document)

    assert len(report.violations) == 1
    assert report.violations[0].found["family"] == "Calibri"
    assert report.violations[0].occurrences == 1


@pytest.mark.parametrize(
    ("family", "expected"),
    [
        ("Symbol", True),
        ("SymbolMT", True),
        ("Wingdings", True),
        ("Wingdings-Regular", True),
        ("Webdings", True),
        ("Arial", False),
        ("TimesNewRoman", False),
        ("", False),
    ],
)
def test_symbolic_family_detection(family: str, expected: bool) -> None:
    """Символьные гарнитуры распознаются по нормализованному имени."""
    assert _is_symbolic_family(family) is expected


def test_digits_are_not_symbolic(docx_builder: DocxPackageBuilder) -> None:
    """Числовой ран — не символ: расхождение кегля в нем видно."""
    body = _paragraph(
        _run_xml("Times New Roman", BODY_TEXT),
        _run_xml("Times New Roman", "12", size_half_points=22),
    )
    document = _parse(docx_builder, body)

    report = _font_engine().check(document)

    assert len(report.violations) == 1
    assert report.violations[0].found["size_pt"] == pytest.approx(11.0)
