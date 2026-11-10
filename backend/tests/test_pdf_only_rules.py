"""Деградация правил по capabilities и честное измерение отступа в pdf_only.

PDF рисуется на лету через PyMuPDF: поля заданы геометрией текста, абзацы — с
красной строкой 12.5 мм и без нее, есть одиночная строка и заголовок. Тесты
фиксируют два контракта:

* ``parser.pdf`` измеряет отступ первой строки как сдвиг первой строки абзаца
  относительно второй, а где измерить нельзя — отдает ``None``, а не ``0``;
* ``rule_engine`` в pdf_only не проверяет отступы и гарнитуру (их в PDF нет),
  продолжает проверять кегль и расширяет допуск полей страницы до ±1.5 мм.

DOCX-часть проверяет, что деградация не затронула строгий режим: отступ и
допуск полей в ``docx_only`` остаются прежними.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import pytest

pymupdf = pytest.importorskip(
    "pymupdf", reason="Для тестов PDF-парсера требуется PyMuPDF (pip install pymupdf)"
)

from conftest import DocxPackageBuilder
from parser.core.schema import ParagraphBlock, ParsedDocument, RunFontPropertiesEffective
from parser.docx.pipeline import DocxParserPipeline
from parser.pdf import parse_pdf
from parser.pdf.extractor import PdfLine
from parser.pdf.structure import PdfStructureBuilder
from rule_engine import RuleEngine, RuleSet
from rule_engine.context import _is_monospace_font
from rule_engine.engine import _compare_font
from rule_engine.rules import FontExpectation

MM_TO_PT = 72.0 / 25.4

PAGE_WIDTH_MM = 210.0
PAGE_HEIGHT_MM = 297.0
LEFT_MARGIN_MM = 31.5
TEXT_RIGHT_MM = 193.5
BODY_SIZE_PT = 12.0
BODY_PITCH_MM = 8.5
PARAGRAPH_GAP_MM = 4.0

#: Шрифты Times New Roman с поддержкой кириллицы для синтетических PDF.
FONT_DIRS = (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/msttcorefonts"))
REGULAR_FONT_NAMES = ("times.ttf", "Times New Roman.ttf", "LiberationSerif-Regular.ttf")
BOLD_FONT_NAMES = ("timesbd.ttf", "Times New Roman Bold.ttf", "LiberationSerif-Bold.ttf")


def _find_font(names: Tuple[str, ...]) -> Path:
    """Ищет файл шрифта в известных каталогах и пропускает тест, если его нет."""
    for directory in FONT_DIRS:
        for name in names:
            candidate = directory / name
            if candidate.exists():
                return candidate
    pytest.skip("Не найден шрифт Times New Roman с кириллицей для синтетического PDF")


def _pt(value_mm: float) -> float:
    """Переводит миллиметры в пункты PDF."""
    return value_mm * MM_TO_PT


def _text_width_mm(text: str, size_pt: float, bold: bool = False) -> float:
    """Ширина строки в миллиметрах по метрикам реального шрифта."""
    font_file = _find_font(BOLD_FONT_NAMES if bold else REGULAR_FONT_NAMES)
    return pymupdf.Font(fontfile=str(font_file)).text_length(text, fontsize=size_pt) / MM_TO_PT


def _new_page(document: "pymupdf.Document") -> "pymupdf.Page":
    """Добавляет страницу A4 и регистрирует на ней шрифты с кириллицей."""
    page = document.new_page(width=_pt(PAGE_WIDTH_MM), height=_pt(PAGE_HEIGHT_MM))
    page.insert_font(fontname="tnr", fontfile=str(_find_font(REGULAR_FONT_NAMES)))
    page.insert_font(fontname="tnrb", fontfile=str(_find_font(BOLD_FONT_NAMES)))
    return page


def _draw_text(
    page: "pymupdf.Page",
    x_mm: float,
    baseline_mm: float,
    text: str,
    size_pt: float = BODY_SIZE_PT,
    bold: bool = False,
) -> None:
    """Рисует строку текста с указанной базовой линией."""
    page.insert_text(
        pymupdf.Point(_pt(x_mm), _pt(baseline_mm)),
        text,
        fontname="tnrb" if bold else "tnr",
        fontsize=size_pt,
        color=(0, 0, 0),
    )


def _draw_justified(
    page: "pymupdf.Page",
    x_left_mm: float,
    baseline_mm: float,
    words: Sequence[str],
    right_mm: float,
) -> None:
    """Рисует выключенную по ширине строку: слова распределены до правого края."""
    widths = [_text_width_mm(word, BODY_SIZE_PT) for word in words]
    gaps = max(1, len(words) - 1)
    gap_mm = (right_mm - x_left_mm - sum(widths)) / gaps
    x = x_left_mm
    for word, width in zip(words, widths):
        _draw_text(page, x, baseline_mm, word)
        x += width + gap_mm


def _draw_paragraph(
    page: "pymupdf.Page",
    baseline_mm: float,
    lines: Sequence[str],
    first_line_indent_mm: float = 0.0,
    justify: bool = False,
) -> float:
    """Рисует абзац и возвращает базовую линию следующего абзаца."""
    baseline = baseline_mm
    for index, line in enumerate(lines):
        x = LEFT_MARGIN_MM + (first_line_indent_mm if index == 0 else 0.0)
        if justify and index < len(lines) - 1:
            _draw_justified(page, x, baseline, line.split(), TEXT_RIGHT_MM)
        else:
            _draw_text(page, x, baseline, line)
        baseline += BODY_PITCH_MM
    return baseline + PARAGRAPH_GAP_MM


INDENTED_PARAGRAPH = (
    "Первый абзац начинается с красной строки и",
    "продолжается на второй строке текста",
    "третья строка завершает абзац.",
)

ZERO_INDENT_PARAGRAPH = (
    "Четвертый абзац проверяет нулевой отступ строки",
    "и должен измериться как ноль миллиметров.",
)

JUSTIFIED_PARAGRAPH = (
    "Второй абзац набран без красной строки и",
    "поэтому начинается у левой границы текста.",
)

SINGLE_LINE_PARAGRAPH = ("Одиночная строка не дает измерить отступ.",)

FILLER_PARAGRAPHS: Tuple[Tuple[str, ...], ...] = (
    (
        "Для проверки оценки полей страница заполняется текстом до нижнего",
        "поля как в реальном документе короткие строки чередуются с",
        "длинными а интервал между строками остается полуторным",
        "третий абзац описывает порядок подготовки исходных данных и",
        "требования к составу разделов проектной документации а также",
        "перечень согласований необходимых для начала работ",
    ),
    (
        "Заполняющий абзац посвящен контролю качества и приемке результата",
        "здесь перечислены критерии по которым заказчик оценивает работу",
        "и требования к оформлению пояснительной записки",
        "пятый абзац уточняет состав приложений и порядок их",
        "согласования с нормоконтролером выпускающей кафедры",
        "и фиксирует итоговые значения показателей проекта",
    ),
)

LAST_PARAGRAPH = (
    "Последний абзац закрывает лист и не дает оценке нижнего",
    "поля выйти за пределы разумного диапазона.",
)


def _build_synthetic_pdf(path: Path) -> Path:
    """Собирает PDF: абзацы с отступом и без, одиночная строка, заголовок и заполнение."""
    document = pymupdf.open()
    page = _new_page(document)

    baseline = _draw_paragraph(page, 23.0, INDENTED_PARAGRAPH, first_line_indent_mm=12.5)
    baseline = _draw_paragraph(page, baseline, JUSTIFIED_PARAGRAPH, justify=True)
    baseline = _draw_paragraph(page, baseline, SINGLE_LINE_PARAGRAPH)
    _draw_text(page, LEFT_MARGIN_MM, baseline, "1 Основная часть", size_pt=16.0, bold=True)
    baseline += BODY_PITCH_MM + PARAGRAPH_GAP_MM
    baseline = _draw_paragraph(page, baseline, ZERO_INDENT_PARAGRAPH)

    index = 0
    while baseline < 200.0:
        filler = FILLER_PARAGRAPHS[index % len(FILLER_PARAGRAPHS)]
        indent = 12.5 if index % 2 == 0 else 0.0
        baseline = _draw_paragraph(
            page, baseline, filler, first_line_indent_mm=indent, justify=True
        )
        index += 1
    _draw_paragraph(page, baseline, LAST_PARAGRAPH)

    document.save(str(path))
    document.close()
    return path


@pytest.fixture(scope="module")
def pdf_only_document(tmp_path_factory: pytest.TempPathFactory) -> ParsedDocument:
    """Разобранный синтетический PDF в режиме pdf_only (собирается один раз)."""
    target = tmp_path_factory.mktemp("pdf_only_rules") / "synthetic.pdf"
    return parse_pdf(_build_synthetic_pdf(target))


def _plain(text: str) -> str:
    """Нормализует текст для сравнений: PDF отдает часть пробелов как неразрывные."""
    return text.replace("\xa0", " ")


def _paragraph_by_prefix(document: ParsedDocument, prefix: str) -> ParagraphBlock:
    """Находит абзац по началу видимого текста (пробелы нормализуются)."""
    for block in document.blocks:
        if isinstance(block, ParagraphBlock) and _plain(block.text_visible).startswith(prefix):
            return block
    raise AssertionError(f"Не найден абзац, начинающийся с {prefix!r}")


def _indent_engine(first_line_mm: Optional[float] = 12.5, **expected: object) -> RuleEngine:
    """Собирает движок с единственным правилом отступов."""
    indent_expected: Dict[str, object] = {
        "first_line_mm": first_line_mm,
        "tolerance_mm": 0.5,
    }
    indent_expected.update(expected)
    payload = {
        "ruleset_id": "pdf-only-indents",
        "ruleset_version": "1.0.0",
        "rules": [
            {
                "rule_id": "indent_first_line",
                "kind": "indent",
                "message": "Абзац начинается с отступа 12,5 мм",
                "target": {"scope": "body_text"},
                "expected": indent_expected,
            }
        ],
    }
    return RuleEngine(RuleSet.model_validate(payload))


def _font_engine(**expected: object) -> RuleEngine:
    """Собирает движок с единственным правилом шрифта."""
    payload = {
        "ruleset_id": "pdf-only-fonts",
        "ruleset_version": "1.0.0",
        "rules": [
            {
                "rule_id": "font_body_text",
                "kind": "font",
                "message": "Основной текст набран Times New Roman, 14 pt",
                "target": {"scope": "body_text"},
                "expected": expected,
            }
        ],
    }
    return RuleEngine(RuleSet.model_validate(payload))


def _margin_engine(**expected: object) -> RuleEngine:
    """Собирает движок с единственным правилом полей страницы."""
    margin_expected: Dict[str, object] = {"tolerance_mm": 1.0}
    margin_expected.update(expected)
    payload = {
        "ruleset_id": "pdf-only-margins",
        "ruleset_version": "1.0.0",
        "rules": [
            {
                "rule_id": "page_margins",
                "kind": "margin",
                "message": "Поля страницы по методичке",
                "target": {"scope": "sections"},
                "expected": margin_expected,
            }
        ],
    }
    return RuleEngine(RuleSet.model_validate(payload))


def _with_margins(document: ParsedDocument, **changes: float) -> ParsedDocument:
    """Возвращает копию документа с измененными полями единственной секции."""
    section = document.sections[0]
    margins = section.margins.model_copy(update=changes)
    patched = section.model_copy(update={"margins": margins})
    return document.model_copy(update={"sections": [patched, *document.sections[1:]]})


# ---------------------------------------------------------------------
# parser/pdf: честное измерение отступа первой строки
# ---------------------------------------------------------------------


def test_pdf_measures_first_line_indent(pdf_only_document: ParsedDocument) -> None:
    """Отступ 12,5 мм измеряется как сдвиг первой строки абзаца относительно второй."""
    paragraph = _paragraph_by_prefix(pdf_only_document, "Первый абзац")

    assert paragraph.pdf_total_lines_count == 3
    assert paragraph.effective_props.indent_first_line_mm == pytest.approx(12.5, abs=0.4)
    assert paragraph.effective_props.indent_first_line_source == "pdf_estimate"


def test_pdf_measures_zero_indent_as_zero(pdf_only_document: ParsedDocument) -> None:
    """Абзац без красной строки честно измеряется как 0,0 мм, а не как None."""
    paragraph = _paragraph_by_prefix(pdf_only_document, "Четвертый абзац")

    assert paragraph.pdf_total_lines_count == 2
    assert paragraph.effective_props.indent_first_line_mm == pytest.approx(0.0, abs=0.2)


def test_pdf_single_line_paragraph_has_no_measured_indent(
    pdf_only_document: ParsedDocument,
) -> None:
    """Одна строка: отступ измерить нечем, значение None и источник pdf_unmeasured."""
    paragraph = _paragraph_by_prefix(pdf_only_document, "Одиночная строка")

    assert paragraph.pdf_total_lines_count == 1
    assert paragraph.effective_props.indent_first_line_mm is None
    assert paragraph.effective_props.indent_first_line_source == "pdf_unmeasured"


def test_pdf_heading_has_no_measured_indent(pdf_only_document: ParsedDocument) -> None:
    """У заголовка абзацного отступа нет: измерение не выдумывается."""
    headings = [
        block
        for block in pdf_only_document.blocks
        if isinstance(block, ParagraphBlock) and block.heading_info is not None
    ]

    assert headings
    for heading in headings:
        assert heading.effective_props.indent_first_line_mm is None
        assert heading.effective_props.indent_first_line_source == "pdf_unmeasured"


def _line(x0_mm: float, page_num: int = 1, y0_mm: float = 20.0) -> PdfLine:
    """Собирает строку PDF с заданным левым краем для проверки измерения отступа."""
    return PdfLine(
        bbox_pt=(_pt(x0_mm), _pt(y0_mm), _pt(x0_mm + 60.0), _pt(y0_mm + 4.0)),
        page_num=page_num,
    )


def test_pdf_indent_is_shift_between_first_and_second_line() -> None:
    """Отступ — разность x0 первой и второй строки, а не сдвиг от оцененного поля."""
    first, second = _line(44.0), _line(31.5)

    measured = PdfStructureBuilder._first_line_indent([first, second], "left", None, None)

    assert measured == pytest.approx(12.5, abs=0.01)


def test_pdf_indent_across_pages_is_not_measured() -> None:
    """Строки на разных страницах стоят в разных колонках: измерение недостоверно."""
    first, second = _line(44.0, page_num=1), _line(31.5, page_num=2)

    assert PdfStructureBuilder._first_line_indent([first, second], "left", None, None) is None


def test_pdf_implausible_shift_is_not_measured_as_indent() -> None:
    """Сдвиг на 70 мм — колонки или склейка строк, а не красная строка."""
    first, second = _line(100.0), _line(30.0)

    assert PdfStructureBuilder._first_line_indent([first, second], "left", None, None) is None


def test_pdf_centered_paragraph_has_no_measured_indent() -> None:
    """У выключенного по центру абзаца красной строки нет: значение None."""
    first, second = _line(60.0), _line(50.0)

    assert PdfStructureBuilder._first_line_indent([first, second], "center", None, None) is None


# ---------------------------------------------------------------------
# rule_engine: деградация правил в pdf_only
# ---------------------------------------------------------------------


def test_pdf_only_indent_rule_creates_no_violations(pdf_only_document: ParsedDocument) -> None:
    """pdf_only: правило отступов не применяется — ложных «0 мм» больше нет."""
    report = _indent_engine().check(pdf_only_document)

    assert report.violations == []
    assert report.summary.objects_checked == 0
    assert report.summary.by_rule == {}


def test_pdf_only_font_family_is_checked_by_normalized_name(
    pdf_only_document: ParsedDocument,
) -> None:
    """pdf_only: гарнитура сверяется, но запись шрифта PDF не дает шума.

    PDF отдает имя шрифта без пробелов («TimesNewRoman»); для требования
    «Times New Roman» это та же гарнитура, поэтому нарушений нет. Настоящая
    другая гарнитура — нарушение: имя шрифта в PDF измерено.
    """
    matched = _font_engine(family="Times New Roman").check(pdf_only_document)
    assert matched.violations == [], "запись шрифта PDF не должна считаться другой гарнитурой"

    mismatched = _font_engine(family="Arial").check(pdf_only_document)
    assert mismatched.violations, "другая гарнитура в PDF — реальное нарушение"
    for violation in mismatched.violations:
        assert set(violation.expected) == {"family"}
        assert set(violation.found) == {"family"}
        assert violation.found["family"] == "TimesNewRoman"


def test_pdf_only_font_size_is_still_checked(pdf_only_document: ParsedDocument) -> None:
    """pdf_only: кегль измерен по спанам и по-прежнему проверяется."""
    report = _font_engine(family="Times New Roman", size_pt=14.0).check(pdf_only_document)

    assert report.violations, "Ожидались нарушения кегля 12 pt при требовании 14 pt"
    assert report.summary.by_rule == {"font_body_text": len(report.violations)}
    for violation in report.violations:
        assert set(violation.expected) == {"size_pt"}
        assert set(violation.found) == {"size_pt"}
        assert violation.found["size_pt"] == pytest.approx(BODY_SIZE_PT, abs=0.5)
        assert "family" not in violation.found_text


def test_pdf_only_complex_script_is_not_compared() -> None:
    """pdf_only: complex script не измеряется, поэтому сверка font_cs отключена.

    Гарнитура рана при этом сверяется: `unless check_complex_script=False`
    отключает только `font_cs`, а не всю проверку семейства.
    """
    props = RunFontPropertiesEffective(
        font_ascii="TimesNewRoman",
        font_hansi="TimesNewRoman",
        font_cs="Arial",
        size_pt=BODY_SIZE_PT,
    )
    expected = FontExpectation(family="Times New Roman", check_complex_script=True)

    docx_view = _compare_font(props, expected, check_family=True, check_complex_script=True)
    pdf_view = _compare_font(props, expected, check_family=True, check_complex_script=False)

    assert docx_view is not None and "font_cs" in docx_view[0]
    assert pdf_view is None, "в pdf_only font_cs не сверяется"
    assert _compare_font(
        props, FontExpectation(family="Georgia"), check_family=True, check_complex_script=False
    ) is not None, "гарнитура проверяется и в pdf_only"


@pytest.mark.parametrize(
    "family,expected",
    [
        ("CourierNew", True),
        ("ABCDEF+CourierNew", True),
        ("CourierNewPSMT", True),
        ("Courier New CYR", True),
        ("Consolas", True),
        ("TimesNewRoman", False),
        ("LiberationSans", False),
        ("", False),
    ],
)
def test_pdf_monospace_family_is_recognized_in_glued_spelling(
    family: str, expected: bool
) -> None:
    """pdf_only: моноширинная гарнитура узнается по записи шрифта PDF.

    PyMuPDF отдает «CourierNew» и «ABCDEF+CourierNew» без пробелов и с
    subset-префиксом; без нормализации имени строки кода проверялись как
    основной текст (7 блоков Дистеля 12 pt).
    """
    assert _is_monospace_font(family) is expected


def test_pdf_only_margin_deviation_1_5_mm_passes(pdf_only_document: ParsedDocument) -> None:
    """pdf_only: отклонение полей ровно на 1.5 мм — погрешность геометрии, не нарушение."""
    margins = pdf_only_document.sections[0].margins
    report = _margin_engine(
        left_mm=round(margins.left_mm - 1.5, 1),
        right_mm=round(margins.right_mm + 1.5, 1),
        top_mm=round(margins.top_mm - 1.5, 1),
        bottom_mm=round(margins.bottom_mm + 1.5, 1),
    ).check(pdf_only_document)

    assert report.violations == []


def test_pdf_only_margin_deviation_13_7_mm_is_reported(
    pdf_only_document: ParsedDocument,
) -> None:
    """pdf_only: реальное расхождение 13,7 мм остается нарушением."""
    margins = pdf_only_document.sections[0].margins
    document = _with_margins(pdf_only_document, left_mm=round(margins.left_mm - 13.7, 1))

    report = _margin_engine(
        left_mm=round(margins.left_mm, 1),
        right_mm=round(margins.right_mm, 1),
        top_mm=round(margins.top_mm, 1),
        bottom_mm=round(margins.bottom_mm, 1),
    ).check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.expected == {"left_mm": round(margins.left_mm, 1)}
    assert violation.found["left_mm"] == pytest.approx(
        round(margins.left_mm - 13.7, 1), abs=0.01
    )


# ---------------------------------------------------------------------
# docx_only: строгий режим не изменился
# ---------------------------------------------------------------------


ZERO_FIRST_LINE_PARAGRAPH = """
<w:p>
  <w:pPr><w:ind w:firstLine="0"/></w:pPr>
  <w:r><w:t>Абзац без красной строки</w:t></w:r>
</w:p>
"""

WIDE_LEFT_MARGIN = """
<w:sectPr>
  <w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1786"/>
</w:sectPr>
"""


def _parse_docx(builder: DocxPackageBuilder, body: str) -> ParsedDocument:
    """Собирает DOCX из тела и возвращает результат разбора."""
    builder.set_body(body)
    return DocxParserPipeline(builder.build()).parse()


def test_docx_only_indent_rule_is_still_strict(docx_builder: DocxPackageBuilder) -> None:
    """docx_only: прямое обнуление красной строки по-прежнему нарушение."""
    document = _parse_docx(docx_builder, ZERO_FIRST_LINE_PARAGRAPH)

    report = _indent_engine().check(document)

    assert len(report.violations) == 1
    assert report.violations[0].found["first_line_mm"] == pytest.approx(0.0, abs=0.01)


def test_docx_only_margin_tolerance_is_not_widened(docx_builder: DocxPackageBuilder) -> None:
    """docx_only: отклонение 1.5 мм при допуске 1.0 мм — нарушение, деградация не применена."""
    document = _parse_docx(docx_builder, WIDE_LEFT_MARGIN)

    report = _margin_engine(
        left_mm=30.0,
        right_mm=15.0,
        top_mm=20.0,
        bottom_mm=20.0,
    ).check(document)

    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.expected == {"left_mm": 30.0}
    assert violation.found["left_mm"] == pytest.approx(31.5, abs=0.02)
