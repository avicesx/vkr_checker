from __future__ import annotations
from pathlib import Path
import pytest
from docx_parser import PdfNormControlParser

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VKR_DIR = PROJECT_ROOT.parent / "documents" / "vkr"
REAL_VKR_PDFS = sorted(
    path for path in VKR_DIR.glob("*.pdf") if any(ord(char) > 127 for char in path.name)
)

pymupdf = pytest.importorskip(
    "pymupdf",
    reason="Для прямого анализа PDF требуется PyMuPDF (pip install pymupdf)",
)


@pytest.mark.integration
@pytest.mark.parametrize(
    "pdf_path",
    REAL_VKR_PDFS,
    ids=lambda path: path.name,
)
def test_real_vkr_pdfs_are_analyzed_without_conversion(pdf_path: Path) -> None:
    report = PdfNormControlParser(pdf_path).parse("urfu")

    assert report.filename == pdf_path.name
    assert report.page_count > 1
    assert report.dominant_font_size_pt == 14.0
    assert report.times_new_roman_char_ratio is not None
    assert report.times_new_roman_char_ratio > 0.9
    assert report.findings


def test_vkr_pdf_profiles_apply_their_own_conflicting_requirements(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "layout_sample.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=595.28, height=841.89)
    page.insert_text(
        pymupdf.Point(85.0, 100.0),
        "\n".join(f"Строка основного текста номер {row + 1}" for row in range(10)),
        fontname="Times-Roman",
        fontsize=14,
        lineheight=1.5,
    )
    page.insert_text(
        pymupdf.Point(292.0, 820.0),
        "1",
        fontname="Times-Roman",
        fontsize=10,
    )
    pdf.save(pdf_path)
    pdf.close()

    mpgu = PdfNormControlParser(pdf_path).parse("mpgu")
    urgpu = PdfNormControlParser(pdf_path).parse("urgpu")
    urfu = PdfNormControlParser(pdf_path).parse("urfu")

    def page_number_finding(report):
        return next(f for f in report.findings if f.rule_id == "page_number_position")

    assert page_number_finding(mpgu).expected == "top_center"
    assert page_number_finding(urgpu).expected == "bottom_center"
    assert page_number_finding(urfu).expected == "bottom_center"
    assert any(f.rule_id == "page_size_a4" for f in urfu.findings)
    assert not any(f.rule_id == "page_size_a4" for f in mpgu.findings)


@pytest.mark.parametrize(
    ("fault", "expected_rule"),
    [
        ("font_size_12", "body_font_size"),
        ("wrong_font_family", "body_font_family"),
        ("single_spacing", "line_spacing"),
        ("wrong_page_number_position", "page_number_position"),
    ],
)
def test_pdf_format_checker_reports_one_deliberate_format_fault(
    tmp_path: Path,
    fault: str,
    expected_rule: str,
) -> None:
    pdf_path = tmp_path / f"{fault}.pdf"
    pdf = pymupdf.open()
    for page_index in range(4):
        page = pdf.new_page(width=595.28, height=841.89)
        fontname = "helv" if fault == "wrong_font_family" else "Times-Roman"
        fontsize = 12 if fault == "font_size_12" else 14
        lineheight = 1.0 if fault == "single_spacing" else 1.5
        page.insert_text(
            pymupdf.Point(85.0, 100.0),
            "\n".join(f"Строка основного текста номер {row + 1}" for row in range(12)),
            fontname=fontname,
            fontsize=fontsize,
            lineheight=lineheight,
        )
        number_y = 55.0 if fault == "wrong_page_number_position" else 820.0
        page.insert_text(
            pymupdf.Point(292.0, number_y),
            str(page_index + 1),
            fontname=fontname,
            fontsize=9,
        )
    pdf.save(pdf_path)
    pdf.close()

    report = PdfNormControlParser(pdf_path).parse("urfu")
    findings = {finding.rule_id: finding for finding in report.findings}

    assert findings[expected_rule].status == "fail"
    if fault == "font_size_12":
        assert report.dominant_font_size_pt == 12.0
    elif fault == "single_spacing":
        assert report.median_line_leading_ratio is not None
        assert report.median_line_leading_ratio == pytest.approx(1.0)


@pytest.mark.parametrize("profile_id", ["mpgu", "urgpu", "urfu"])
def test_all_guide_profiles_are_independently_available(
    profile_id: str, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "one_page.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=595.28, height=841.89)
    page.insert_text(pymupdf.Point(85, 100), "Проверка формата PDF", fontsize=14)
    pdf.save(pdf_path)
    pdf.close()

    result = PdfNormControlParser(pdf_path).parse(profile_id)
    assert result.profile_id == profile_id
    assert result.profile_title
