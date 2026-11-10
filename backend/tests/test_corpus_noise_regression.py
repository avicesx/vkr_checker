"""Регрессия шума rule_engine на корпусе ВКР из manual_test.

Корпус — семь реальных пояснительных записок (3 DOCX и 4 PDF). Бинарные
образцы не хранятся в репозитории (``manual_test/`` в ``.gitignore``), поэтому
тесты корпуса пропускаются, если файлов нет; синтетический положительный
контроль (кривой абзац основного текста) выполняется всегда.

Числа до фиксации сняты с отчетов ``manual_test/out/vkr_corpus_pre_fix``
(прогон до правок f1–f5):

    файл                                     font  indent  всего
    2_Бикчуркина…docx                          1       2     170
    2_Бутаков…docx                            18      55     112
    Разработка_прокси…docx                     0      16      38
    2_Смолина…pdf                            321     320     663
    Пояснительная записка Дистель (1).pdf    371     371     774
    Мартемьянова…pdf                         391     391     836
    Как_будет_To_Be…pdf                        7       7      20
    итого                                   1109    1162    2613

После правок: font 0, indent 36 (реальный ноль красной строки в DOCX), всего
184 — то есть 1,6 % от прежних 2271 нарушений шрифта и отступа и 7,0 % от
прежних 2613 нарушений.

Тесты фиксируют четыре свойства:

* в pdf_only гарнитура сверяется по нормализованному имени: запись шрифта PDF
  (``TimesNewRoman``, ``ABCDEF+…``) не шумит, а настоящая другая гарнитура
  (``LiberationSans``, ``NimbusRomNo9L``) — реальное нарушение; отступы в PDF не
  проверяются как ненадежный источник;
* служебные зоны (УРФУ-титул и лист задания Бутакова на стр. 3, подписная
  строка «(подпись)» 10 pt у Бикчуркиной, титульные строки прокси-сервера и
  Смолиной) не дают нарушений шрифта и отступа;
* реальные нарушения сохраняются: заголовки «по ширине», красная строка 0 в
  DOCX, подписи рисунков и таблиц, отклонения полей страницы больше допуска;
* суммарный бюджет шума по корпусу не выходит за оговоренные границы.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pytest
from conftest import DocxPackageBuilder
from parser.core.schema import ParagraphBlock, ParsedDocument
from parser.docx.pipeline import DocxParserPipeline
from rule_engine import RuleEngine, RuleSet, load_default_ruleset
from rule_engine._fonts import same_family as _same_family
from rule_engine.context import _is_monospace_font, classify_document_contexts
from rule_engine.findings import RuleCheckReport, Violation
from services.document_parsing import parse_document

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = BACKEND_ROOT / "manual_test"

#: DOCX-часть корпуса: разбор со стилями, pPr и numbering.
DOCX_CORPUS: Dict[str, str] = {
    "bikchurkina": "2_Бикчуркина_Р_А_РИ_420933_Пояснительная_записка_ВКР.docx",
    "butakov": "2_Бутаков_Н_С_РИ_420932_Пояснительная_записка_ВКР_1.docx",
    "proxy": "Разработка_прокси_сервера_для_фильтрации_open_source_библиотек.docx",
}

#: PDF-часть корпуса: разбор только по геометрии страниц (pdf_only).
PDF_CORPUS: Dict[str, str] = {
    "smolina": "2_Смолина_Е_П_РИ_420943_Пояснительная_записка_к_ВКР_1.pdf",
    "distel": "Пояснительная записка Дистель (1).pdf",
    "martemyanova": "Мартемьянова_Л_В_РИ_420931_Пояснительная_записка_ФИНАЛ_С_ПОДПИСЯМИ.pdf",
    "ai_service": "Как_будет_To_Be_Автоматизированный_нормоконтроль_ВКР_с_AI_сервисом.pdf",
}

CORPUS_FILES: Dict[str, str] = {**DOCX_CORPUS, **PDF_CORPUS}

_MISSING_FILES: List[str] = [
    filename for filename in CORPUS_FILES.values() if not (CORPUS_DIR / filename).is_file()
]

#: Корпус не хранится в репозитории: без файлов тесты корпуса пропускаются.
requires_corpus = pytest.mark.skipif(
    bool(_MISSING_FILES),
    reason="Корпус manual_test отсутствует (gitignored): нет " + ", ".join(_MISSING_FILES),
)

#: Нарушения до фиксации: сумма font+indent из отчетов pre_fix и общий итог.
PRE_FIX_FONT = 1109
PRE_FIX_INDENT = 1162
PRE_FIX_FONT_INDENT = PRE_FIX_FONT + PRE_FIX_INDENT  # 2271 по заданию
PRE_FIX_TOTAL = 2611  # в отчетах pre_fix 2613; по заданию — 2611

#: Бюджеты шума: не больше 20 % прежних текстовых нарушений и меньше 600 всего.
FONT_INDENT_BUDGET_SHARE = 0.20
TOTAL_NOISE_BUDGET = 600

#: Итог корпуса после правок f1–f6: font 0, indent 36, остальное — реальные находки.
TOTAL_NOISE_TARGET = 184

#: Допуск полей страницы в pdf_only: шире docx-допуска (1.0 мм) до 1.5 мм (t3).
PDF_MARGIN_TOLERANCE_MM = 1.5

FONT_RULE_ID = "font_body_text"
INDENT_RULE_ID = "indent_first_line"
MARGIN_RULE_ID = "page_margins"
ALIGN_RULE_ID = "heading_alignment"


@dataclass(frozen=True)
class CorpusDoc:
    """Разобранный документ корпуса вместе с отчетом и контекстами блоков."""

    name: str
    filename: str
    parsed: ParsedDocument
    report: RuleCheckReport
    contexts: Dict[str, str]

    def violations(self, rule_id: str) -> List[Violation]:
        """Нарушения одного правила в порядке отчета."""
        return [v for v in self.report.violations if v.rule_id == rule_id]

    def paragraphs(self) -> List[ParagraphBlock]:
        """Абзацы верхнего уровня: для корпуса достаточно тела документа."""
        return [block for block in self.parsed.blocks if isinstance(block, ParagraphBlock)]

    def title_page_ids(self) -> List[str]:
        """block_id абзацев, распознанных как титульный (служебный) лист."""
        return [
            paragraph.block_id
            for paragraph in self.paragraphs()
            if self.contexts.get(paragraph.block_id) == "title_page"
        ]


@pytest.fixture(scope="module")
def corpus() -> Dict[str, CorpusDoc]:
    """Разбирает и проверяет весь корпус один раз на модуль (≈17 с)."""
    engine = RuleEngine(load_default_ruleset())
    parsed_docs: Dict[str, CorpusDoc] = {}
    for name, filename in CORPUS_FILES.items():
        document = parse_document(CORPUS_DIR / filename)
        parsed_docs[name] = CorpusDoc(
            name=name,
            filename=filename,
            parsed=document,
            report=engine.check(document),
            contexts=classify_document_contexts(document.blocks),
        )
    return parsed_docs


def _pdf_docs(corpus: Dict[str, CorpusDoc]) -> List[CorpusDoc]:
    """Документы, разобранные только по PDF."""
    return [corpus[name] for name in PDF_CORPUS if name in corpus]


def _docx_docs(corpus: Dict[str, CorpusDoc]) -> List[CorpusDoc]:
    """Документы, разобранные по DOCX."""
    return [corpus[name] for name in DOCX_CORPUS if name in corpus]


def _run_size_pt(paragraph: ParagraphBlock) -> set:
    """Кегли ранов абзаца (с округлением до 0,1 pt)."""
    return {round(run.effective_props.size_pt, 1) for run in paragraph.runs}


# --- 1. pdf_only: имя гарнитуры и отступы больше не шумят -----------------


@requires_corpus
def test_pdf_only_parsed_in_pdf_mode(corpus: Dict[str, CorpusDoc]) -> None:
    """Все PDF корпуса разобраны в pdf_only: стилей и pPr в них нет."""
    for doc in _pdf_docs(corpus):
        assert doc.parsed.parse_mode == "pdf_only"
        assert doc.parsed.capabilities.styles_parsed is False


@requires_corpus
def test_pdf_only_has_no_font_family_noise(corpus: Dict[str, CorpusDoc]) -> None:
    """В pdf_only гарнитура сверяется, но запись шрифта PDF не шумит.

    PDF отдает имена без пробелов (``TimesNewRoman``, ``TimesNewRoman-BoldMT``),
    и правило сравнивает их с ожидаемым «Times New Roman» по нормализованному
    имени: нарушений семейства нет. Настоящая другая гарнитура в корпусе есть
    только на служебной странице (``LiberationSans`` у статьи про AI-сервис), и
    текстовые правила ее не проверяют.
    """
    for doc in _pdf_docs(corpus):
        family_noise = [
            violation
            for violation in doc.violations(FONT_RULE_ID)
            if "family" in violation.found or "font_cs" in violation.found
        ]
        assert family_noise == [], (
            f"{doc.filename}: запись шрифта PDF снова шумит гарнитурой "
            f"({len(family_noise)} нарушений)"
        )

    # Нормализация — то, что снимает шум: «TimesNewRoman-BoldMT» замена на
    # «Times New Roman» по ключам имени (см. rule_engine._fonts).
    assert _same_family("TimesNewRoman", "Times New Roman") is True
    assert _same_family("TimesNewRoman-BoldMT", "Times New Roman") is True
    assert _same_family("ABCDEF+TimesNewRomanPSMT", "Times New Roman") is True
    assert _same_family("LiberationSans", "Times New Roman") is False

    # Строки кода Дистеля (CourierNew) распознаны как листинг, поэтому 12 pt
    # вместо 14 pt в них больше не нарушение шрифта: это были 7 записей шума.
    distel = corpus["distel"]
    assert distel.violations(FONT_RULE_ID) == []
    code_ids = {
        paragraph.block_id
        for paragraph in distel.paragraphs()
        if any(
            _is_monospace_font(run.effective_props.font_ascii)
            for run in paragraph.runs
        )
    }
    assert code_ids, "в Дистеле не осталось моноширинных абзацев"
    assert all(
        distel.contexts.get(block_id) == "code_listing" for block_id in code_ids
    ), "моноширинные абзацы PDF должны быть листингами"


@requires_corpus
def test_pdf_only_font_family_check_is_enabled(corpus: Dict[str, CorpusDoc]) -> None:
    """Сверка семейства в pdf_only не отключена: другое требование ее ломает."""
    ruleset = RuleSet.model_validate(
        {
            "ruleset_id": "pdf-family",
            "ruleset_version": "1.0.0",
            "rules": [
                {
                    "rule_id": "font_body_text",
                    "kind": "font",
                    "message": "Основной текст набран Times New Roman",
                    "target": {"scope": "body_text"},
                    "expected": {"family": "Arial"},
                }
            ],
        }
    )
    report = RuleEngine(ruleset).check(corpus["smolina"].parsed)

    assert report.violations, "в pdf_only выключена сверка семейства шрифта"
    assert all("family" in violation.found for violation in report.violations)


@requires_corpus
def test_pdf_only_indent_noise_below_three_percent(corpus: Dict[str, CorpusDoc]) -> None:
    """Отступы в pdf_only дают меньше 3 % блоков: красная строка не измеряется."""
    for doc in _pdf_docs(corpus):
        total_blocks = len(doc.parsed.blocks)
        indent_noise = len(doc.violations(INDENT_RULE_ID))
        assert indent_noise < 0.03 * total_blocks, (
            f"{doc.filename}: {indent_noise} нарушений отступа на {total_blocks} "
            f"блоков — больше 3 %"
        )
        # Сейчас честное измерение отступа в PDF невозможно, поэтому шума нет.
        assert indent_noise == 0


@requires_corpus
def test_pdf_only_margin_13_7_kept_in_measurements(corpus: Dict[str, CorpusDoc]) -> None:
    """Правое поле 13,7 мм Смолиной по-прежнему измерено; шумит только крупное отклонение.

    Отчет до фиксации помечал секцию «правое поле 13,7 мм, нижнее поле 26,5 мм».
    После калибровки pdf_only (t3) допуск полей расширен до ±1.5 мм, поэтому
    отклонение 1,3 мм по правому полю больше не нарушение, а значение 13,7 мм
    остается в геометрии секции и в отчете не появляется как шум. Реальные
    отклонения — нижнее поле 26,5 мм и многомиллиметровые поля ландшафтной
    страницы — остаются нарушениями.
    """
    smolina = corpus["smolina"]
    right_margins = [
        round(section.margins.right_mm, 1) for section in smolina.parsed.sections
    ]
    assert right_margins == [13.7]
    assert abs(13.7 - 15.0) <= PDF_MARGIN_TOLERANCE_MM

    margin_violations = [
        violation for doc in _pdf_docs(corpus) for violation in doc.violations(MARGIN_RULE_ID)
    ]
    assert margin_violations, "правило полей отключилось: реальные отклонения не ловятся"
    assert any(
        violation.found.get("bottom_mm") == pytest.approx(26.5)
        for violation in smolina.violations(MARGIN_RULE_ID)
    ), "нижнее поле 26,5 мм Смолиной должно остаться нарушением"

    # Ни одно нарушение не построено по отклонению внутри допуска pdf_only:
    # 13,7/13,8 мм правого поля корпуса шумом не считаются.
    for violation in margin_violations:
        for field, actual in violation.found.items():
            expected = violation.expected[field]
            assert abs(float(actual) - float(expected)) > PDF_MARGIN_TOLERANCE_MM


# --- 2. служебные зоны: титул, лист задания, подписные строки ------------


@requires_corpus
def test_butakov_urfu_title_page_has_no_font_or_indent_noise(
    corpus: Dict[str, CorpusDoc],
) -> None:
    """УРФУ-титул и лист задания Бутакова (стр. 3) не дают нарушений шрифта и отступа.

    До фиксации документ давал 18 нарушений шрифта и 55 отступа, в том числе на
    титульных реквизитах 10–12 pt. Зона титула теперь не ограничена номером
    страницы и распознается по маркерам листа задания.
    """
    butakov = corpus["butakov"]
    assert butakov.parsed.parse_mode == "docx_only"

    page_block_ids = {
        paragraph.block_id
        for paragraph in butakov.paragraphs()
        if paragraph.page_start_est == 3
    }
    title_on_page = [
        block_id
        for block_id in page_block_ids
        if butakov.contexts.get(block_id) == "title_page"
    ]
    assert len(title_on_page) >= 5, "стр. 3 Бутакова должна быть распознана как титул"

    page_text = " ".join(
        paragraph.text_visible
        for paragraph in butakov.paragraphs()
        if paragraph.page_start_est == 3
    )
    assert "Уральский федеральный университет" in page_text
    assert "ЗАДАНИЕ" in page_text

    noise = [
        violation
        for violation in butakov.report.violations
        if violation.rule_id in {FONT_RULE_ID, INDENT_RULE_ID}
        and violation.location.page_start_est == 3
    ]
    assert noise == [], f"шум на титуле стр. 3: {[(v.rule_id, v.found) for v in noise]}"

    title_ids = set(title_on_page)
    title_noise = [
        violation
        for violation in butakov.report.violations
        if violation.rule_id in {FONT_RULE_ID, INDENT_RULE_ID}
        and violation.location.block_id in title_ids
    ]
    assert title_noise == []
    assert butakov.violations(FONT_RULE_ID) == [], (
        "в документе не должно остаться шума шрифта"
    )


@requires_corpus
def test_bikchurkina_signature_line_10pt_has_no_font_noise(
    corpus: Dict[str, CorpusDoc],
) -> None:
    """Подписная строка «(подпись)» 10 pt Бикчуркиной не дает нарушения шрифта.

    До фиксации документ давал ровно одно нарушение шрифта — этот ран: 10 pt
    вместо 14 pt на служебной подписной строке.
    """
    bikchurkina = corpus["bikchurkina"]
    signatures = [
        paragraph
        for paragraph in bikchurkina.paragraphs()
        if "подпись" in paragraph.text_visible.casefold()
        and 10.0 in _run_size_pt(paragraph)
    ]
    assert len(signatures) == 1, "ожидалась одна подписная строка 10 pt"

    signature = signatures[0]
    assert bikchurkina.contexts.get(signature.block_id) == "title_page"
    assert bikchurkina.violations(FONT_RULE_ID) == []


@requires_corpus
def test_proxy_title_lines_have_no_indent_noise(corpus: Dict[str, CorpusDoc]) -> None:
    """Титульные строки прокси-сервера не дают нарушений отступа.

    Титул здесь разобран по DOCX: firstLine у реквизитов действительно 0, но это
    служебная зона, а не основной текст. Настоящие нарушения отступа (подписи
    таблиц и рисунков приложений) остаются.
    """
    proxy = corpus["proxy"]
    title_ids = set(proxy.title_page_ids())
    assert len(title_ids) >= 10, "титульный лист прокси-сервера не распознан"

    indent_violations = proxy.violations(INDENT_RULE_ID)
    assert not title_ids & {violation.location.block_id for violation in indent_violations}

    assert indent_violations, "реальные нарушения отступа в DOCX пропали"
    assert all(
        violation.found["first_line_mm"] == pytest.approx(0.0, abs=0.01)
        for violation in indent_violations
    )


@requires_corpus
def test_smolina_12pt_title_lines_have_no_font_noise(corpus: Dict[str, CorpusDoc]) -> None:
    """Титульные строки Смолиной 11–12 pt не дают нарушений шрифта в pdf_only."""
    smolina = corpus["smolina"]
    title_blocks = [
        paragraph
        for paragraph in smolina.paragraphs()
        if smolina.contexts.get(paragraph.block_id) == "title_page"
    ]
    assert title_blocks, "титульный лист Смолиной не распознан"
    twelve_point = [
        paragraph for paragraph in title_blocks if 12.0 in _run_size_pt(paragraph)
    ]
    assert twelve_point, "ожидались титульные строки 12 pt (реквизиты вуза)"

    title_ids = {paragraph.block_id for paragraph in title_blocks}
    title_font_noise = [
        violation
        for violation in smolina.violations(FONT_RULE_ID)
        if violation.location.block_id in title_ids
    ]
    assert title_font_noise == []
    assert smolina.violations(FONT_RULE_ID) == []


# --- 3. реальные нарушения сохраняются -----------------------------------


@requires_corpus
def test_real_heading_alignment_noise_survives(corpus: Dict[str, CorpusDoc]) -> None:
    """Заголовки «по ширине» в DOCX по-прежнему ловятся правилом выравнивания."""
    for doc in _docx_docs(corpus):
        alignment = doc.violations(ALIGN_RULE_ID)
        assert alignment, f"{doc.filename}: правило выравнивания заголовков молчит"
        justify = [v for v in alignment if v.found.get("alignment") == "both"]
        assert justify, f"{doc.filename}: не осталось заголовков «по ширине»"
        assert all(violation.severity == "warning" for violation in alignment)
        assert all("по ширине" in violation.found_text for violation in justify)


#: Заведомо кривой абзац основного текста: Arial 12 pt вместо Times New Roman 14 pt.
BROKEN_BODY_TEXT = (
    "Этот абзац набран гарнитурой Arial двенадцатого кегля и без красной строки."
)


def _heading_xml(text: str) -> str:
    """Заголовок первого уровня: закрывает зону титула перед основным текстом."""
    return (
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:jc w:val="center"/></w:pPr>'
        f"<w:r><w:t>{text}</w:t></w:r></w:p>"
    )


def _broken_body_xml(text: str) -> str:
    """Абзац с прямым форматированием Arial 12 pt и нулевой красной строкой."""
    return (
        '<w:p><w:pPr><w:ind w:firstLine="0"/></w:pPr>'
        '<w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="24"/></w:rPr>'
        f'<w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
    )


def test_broken_docx_body_still_fails_font_rule(docx_builder: DocxPackageBuilder) -> None:
    """Синтетический DOCX с Arial 12 pt в основном тексте падает по шрифту.

    Положительный контроль регрессии: послабления pdf_only и служебных зон не
    должны скрывать настоящий дефект оформления в DOCX.
    """
    docx_builder.set_body(
        _heading_xml("1 Обзор предметной области") + _broken_body_xml(BROKEN_BODY_TEXT)
    )
    document = DocxParserPipeline(docx_builder.build("broken_body.docx")).parse()

    report = RuleEngine(load_default_ruleset()).check(document)

    broken = [
        paragraph
        for paragraph in document.blocks
        if isinstance(paragraph, ParagraphBlock)
        and paragraph.text_visible.startswith(BROKEN_BODY_TEXT)
    ]
    assert len(broken) == 1
    font_violations = [v for v in report.violations if v.rule_id == FONT_RULE_ID]
    assert len(font_violations) == 1, "кривой абзац основного текста не поймался"
    violation = font_violations[0]
    assert violation.location.block_id == broken[0].block_id
    assert violation.found["family"] == "Arial"
    assert violation.found["size_pt"] == pytest.approx(12.0)
    assert violation.severity == "error"


# --- 4. бюджет шума по корпусу -------------------------------------------


@requires_corpus
def test_corpus_noise_budget(corpus: Dict[str, CorpusDoc]) -> None:
    """Суммарные font+indent по корпусу — меньше 20 % прежних, всего — меньше 600.

    До фиксации: шрифт 1109 + отступ 1162 = 2271, всего 2613 нарушений.
    """
    font_total = sum(len(doc.violations(FONT_RULE_ID)) for doc in corpus.values())
    indent_total = sum(len(doc.violations(INDENT_RULE_ID)) for doc in corpus.values())
    total = sum(doc.report.summary.violations_total for doc in corpus.values())

    assert font_total + indent_total < FONT_INDENT_BUDGET_SHARE * PRE_FIX_FONT_INDENT, (
        f"шрифт+отступ: {font_total + indent_total} при бюджете "
        f"{FONT_INDENT_BUDGET_SHARE:.0%} от {PRE_FIX_FONT_INDENT}"
    )
    assert total < TOTAL_NOISE_BUDGET, (
        f"всего нарушений {total} при бюджете {TOTAL_NOISE_BUDGET} (до фиксации {PRE_FIX_TOTAL})"
    )


@requires_corpus
def test_corpus_total_noise_at_most_target(corpus: Dict[str, CorpusDoc]) -> None:
    """Итог корпуса не больше 184 нарушений, шрифтовых — ноль.

    Цель после правок f1–f6: font 0 (запись шрифта PDF нормализуется, строки
    кода распознаются листингом), indent 36 (реальная нулевая красная строка в
    DOCX), остальное — настоящие находки (выравнивание заголовков, поля,
    нумерация, состав разделов). Улучшения допустимы, рост — регрессия.
    """
    font_total = sum(len(doc.violations(FONT_RULE_ID)) for doc in corpus.values())
    total = sum(doc.report.summary.violations_total for doc in corpus.values())

    assert font_total == 0, f"шрифтовой шум корпуса {font_total} вместо нуля"
    assert total <= TOTAL_NOISE_TARGET, (
        f"всего нарушений {total} при цели {TOTAL_NOISE_TARGET} (до фиксации {PRE_FIX_TOTAL})"
    )
