"""Движок проверки документа по декларативным правилам нормоконтроля.

Движок не разбирает OOXML заново: он берет ParsedDocxDocument, где каскад
стилей уже разрешен, а единицы приведены к пунктам и миллиметрам. Каждая
группа правил (шрифты, отступы, поля) сравнивает эффективные значения с
ожидаемыми и формирует нарушение с адресом, ожиданием и фактом.

Проверяются только объекты, которые правило действительно описывает: по
умолчанию из выборки исключены заголовки, подписи рисунков и таблиц, элементы
списков, пустые абзацы и строки оглавления. Это позволяет применять правило
«основной текст» без ручных исключений в наборе правил.

Титульный лист и листинги кода — служебные зоны: они набраны по требованиям
методички к титулу и коду, а не к основному тексту. Их контекст определяет
`rule_engine.context`; `RuleTarget.exclude_contexts` перечисляет контексты,
которые правило не проверяет (по умолчанию оба).

Сверка шрифта сравнивает гарнитуры по нормализованному имени: Word пишет одну
и ту же гарнитуру как «Times New Roman», «TimesNewRoman», «TimesNewRomanPSMT»
или «ABCDEF+TimesNewRoman-BoldMT», поэтому subset-префикс и стилевые маркеры
отбрасываются (`rule_engine._fonts`). Символические раны — гарнитуры Symbol,
Wingdings и раны из одних небуквенных знаков (±, ≤, ×) — правилом шрифта не
проверяются: кегль и начертание у математических знаков не осмысленны.

Заголовки калиброваны по корпусу ВКР. Уровень нумерованного заголовка равен
глубине номера (`3.1.1` — третий уровень), поэтому расхождение с `outlineLvl`
стиля не превращается в перескок уровня; проверка глубины номера при этом
сравнивает его с местом в иерархии, а не с уровнем, вычисленным из него же.
Метки служебных страниц («ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА», «ЗАДАНИЕ»)
разбор заголовками не считает, список литературы узнается по синонимам
названий (`SECTION_TITLE_SYNONYMS`: «СПИСОК ИСТОЧНИКОВ», «БИБЛИОГРАФИЧЕСКИЙ
СПИСОК», «ИСТОЧНИКИ»), а структурные роли (`NUMBERLESS_HEADING_ROLES`) номер не
требуют.

Оглавление сверяется с заголовками и приложениями. Запись «ПРИЛОЖЕНИЕ А»
указывает на приложение по букве: правило ищет ее среди заголовков приложений,
подписей «Продолжение ПРИЛОЖЕНИЯ А» и сущностей `entity_type="appendix"`, а если
буквы в подписи нет — по названию («(обязательное) Название»). Одна запись
приложения покрывает и подзаголовки внутри него. `TocExpectation.max_depth`
задает глубину, до которой заголовки обязаны быть в оглавлении: при `max_depth:
2` подраздел «3.1.1» пропуском не считается, а пропуск главы остается
нарушением.

Правила деградируют по `DocumentCapabilities`: в `pdf_only`
(`capabilities.styles_parsed=False`) нет ни `w:pPr`, ни стилей, ни
`numbering.xml`, поэтому отступы абзаца не проверяются (источник требования
ненадежен — см. `_check_indent_rule`), complex script не сверяется, а допуск
полей страницы расширяется до геометрии PDF (±1.5 мм). Гарнитура, кегль,
начертание, заголовки и оглавление проверяются и в этом режиме: запись шрифта
PDF (`TimesNewRoman`, `ABCDEF+…`, `LiberationSans`) сравнивается по
нормализованному имени, поэтому запись шрифта не шумит, а настоящая другая
гарнитура — нарушение.

Проверки, которые могут не иметь данных, возвращают явный статус
(`RuleAssessment`): положение номера страницы требует не меньше `max(3, N // 3)`
распознанных номеров, формат листа — хотя бы одну секцию, межстрочный
интервал — измеримый множитель. Если данных нет, статус `not_assessed` с
`confidence="low"`, а не выдуманный вердикт; такие проверки считаются в
`summary.not_assessed_total` и видны в `report.not_assessed`. Типографское
отношение «шаг / кегль» из PDF приводится к множителю Word делением на
`PDF_SINGLE_LINE_FACTOR` (1,15), поэтому DOCX и PDF выносят одинаковый вердикт;
оценка структуры PDF (шаг / (кегль × 1,2)) множителем Word не считается.
"""
from __future__ import annotations

import json
from pathlib import Path

from typing import Callable, Dict, List, Tuple

from .checks.bibliography import (
    _check_bibliography_cited_rule,
    _check_bibliography_count_rule,
    _check_bibliography_format_rule,
    _check_bibliography_link_rule,
    _check_bibliography_order_rule,
    _check_bibliography_recency_rule,
)
from .checks.captions import (
    _check_caption_format_rule,
    _check_caption_numbering_rule,
    _check_caption_placement_rule,
    _check_caption_reference_rule,
)
from .checks.common import (
    _caption_block_ids,
    _document_contexts,
)
from .checks.equations import _check_equation_numbering_rule
from .checks.headings import (
    _check_heading_format_rule,
    _check_heading_hyphenation_rule,
    _check_heading_levels_rule,
    _check_heading_numbering_rule,
)
from .checks.page import (
    _check_line_spacing_rule,
    _check_margin_rule,
    _check_page_format_rule,
    _check_page_number_position_rule,
)
from .checks.pagination import (
    _check_heading_page_break_rule,
    _check_keep_next_rule,
    _check_widow_control_rule,
)
from .checks.microtypography import _check_microtypography_rule
from .checks.sections import _check_required_sections_rule
from .checks.text import (
    _check_alignment_rule,
    _check_font_rule,
    _check_indent_rule,
)
from .checks.inline_refs import _check_inline_reference_format_rule
from .checks.lists import _check_list_format_rule
from .checks.structure_extra import (
    _check_appendix_order_rule,
    _check_title_page_content_rule,
)
from .checks.tables import (
    _check_table_continuation_rule,
    _check_table_header_format_rule,
    _check_table_layout_rule,
)
from .checks.toc import _check_toc_rule
from .checks.volume import _check_document_volume_rule
from .findings import (
    DocumentRef,
    RuleAssessment,
    RuleCheckReport,
    Violation,
    build_summary,
)
from .rules import (
    AlignmentRule,
    AppendixOrderRule,
    BibliographyCitedRule,
    BibliographyCountRule,
    BibliographyFormatRule,
    BibliographyLinkRule,
    BibliographyOrderRule,
    BibliographyRecencyRule,
    CaptionFormatRule,
    CaptionNumberingRule,
    CaptionPlacementRule,
    CaptionReferenceRule,
    DocumentVolumeRule,
    EquationNumberingRule,
    FontRule,
    HeadingFormatRule,
    HeadingHyphenationRule,
    HeadingLevelsRule,
    HeadingNumberingRule,
    HeadingPageBreakRule,
    IndentRule,
    InlineReferenceFormatRule,
    KeepNextRule,
    ListFormatRule,
    LineSpacingRule,
    MarginRule,
    MicroTypographyRule,
    PageFormatRule,
    PageNumberPositionRule,
    RequiredSectionsRule,
    Rule,
    RuleSet,
    TableContinuationRule,
    TableHeaderFormatRule,
    TableLayoutRule,
    TitlePageContentRule,
    TocRule,
    WidowControlRule,
    load_ruleset,
)
from parser.core.schema import ParsedDocxDocument
# Совместимость: приватные помощники, которые импортируют тесты.
from .checks.captions import _caption_separator_kind  # noqa: F401
from .checks.common import _compare_font  # noqa: F401

_CheckerHandler = Callable[
    [Rule, ParsedDocxDocument, Dict[str, str]],
    Tuple[List[Violation], int, RuleAssessment],
]
"""Единый контракт чекера: (правило, документ, контексты) -> нарушения,
число проверенных объектов, оценка правила. Оценка возвращается всегда."""



class RuleEngine:
    """Проверяет разобранный документ по набору правил и формирует отчет."""

    def __init__(self, ruleset: RuleSet) -> None:
        """Сохраняет набор правил для последующих проверок."""
        self.ruleset = ruleset

    @classmethod
    def from_json_file(cls, ruleset_path: str | Path) -> "RuleEngine":
        """Создает движок из JSON-файла набора правил."""
        return cls(load_ruleset(ruleset_path))

    def check(self, document: ParsedDocxDocument) -> RuleCheckReport:
        """Проверяет документ по всем правилам набора и возвращает отчет."""
        violations: List[Violation] = []
        assessments: List[RuleAssessment] = []
        objects_checked = 0
        caption_ids = _caption_block_ids(document)
        contexts = _document_contexts(document, caption_ids)

        for rule in self.ruleset.rules:
            handler = self._CHECKERS.get(type(rule))
            if handler is None:
                continue
            rule_violations, checked, assessment = handler(rule, document, contexts)
            violations.extend(rule_violations)
            objects_checked += checked
            assessments.append(assessment)

        meta = document.meta
        total_pages = document.sections[-1].page_end_est if document.sections else 0
        return RuleCheckReport(
            ruleset_id=self.ruleset.ruleset_id,
            ruleset_version=self.ruleset.ruleset_version,
            parser_schema_version=document.schema_version,
            document=DocumentRef(
                filename=meta.filename,
                file_sha256=meta.file_sha256,
                source_format=meta.source_format,
                pagination_source=meta.pagination_source,
                total_pages_est=total_pages,
            ),
            summary=build_summary(
                violations,
                len(self.ruleset.rules),
                objects_checked,
                not_assessed_total=sum(
                    1 for assessment in assessments if assessment.status == "not_assessed"
                ),
            ),
            violations=violations,
            assessments=assessments,
            passed=not any(violation.severity == "error" for violation in violations),
        )

    def check_json_file(self, document_json_path: str | Path) -> RuleCheckReport:
        """Проверяет документ, сохраненный ранее в JSON парсера."""
        raw_text = Path(document_json_path).read_text(encoding="utf-8")
        return self.check(ParsedDocxDocument.model_validate(json.loads(raw_text)))

    # Реестр чекеров: тип правила -> обработчик с единым контрактом.
    _CHECKERS: Dict[type, _CheckerHandler] = {
        FontRule: _check_font_rule,
        IndentRule: _check_indent_rule,
        MarginRule: _check_margin_rule,
        PageNumberPositionRule: _check_page_number_position_rule,
        PageFormatRule: _check_page_format_rule,
        LineSpacingRule: _check_line_spacing_rule,
        HeadingLevelsRule: _check_heading_levels_rule,
        HeadingNumberingRule: _check_heading_numbering_rule,
        HeadingFormatRule: _check_heading_format_rule,
        RequiredSectionsRule: _check_required_sections_rule,
        TocRule: _check_toc_rule,
        BibliographyLinkRule: _check_bibliography_link_rule,
        BibliographyCitedRule: _check_bibliography_cited_rule,
        BibliographyFormatRule: _check_bibliography_format_rule,
        CaptionNumberingRule: _check_caption_numbering_rule,
        CaptionFormatRule: _check_caption_format_rule,
        CaptionReferenceRule: _check_caption_reference_rule,
        CaptionPlacementRule: _check_caption_placement_rule,
        InlineReferenceFormatRule: _check_inline_reference_format_rule,
        TableLayoutRule: _check_table_layout_rule,
        TableContinuationRule: _check_table_continuation_rule,
        TableHeaderFormatRule: _check_table_header_format_rule,
        ListFormatRule: _check_list_format_rule,
        HeadingHyphenationRule: _check_heading_hyphenation_rule,
        AppendixOrderRule: _check_appendix_order_rule,
        TitlePageContentRule: _check_title_page_content_rule,
        AlignmentRule: _check_alignment_rule,
        WidowControlRule: _check_widow_control_rule,
        KeepNextRule: _check_keep_next_rule,
        HeadingPageBreakRule: _check_heading_page_break_rule,
        DocumentVolumeRule: _check_document_volume_rule,
        MicroTypographyRule: _check_microtypography_rule,
        EquationNumberingRule: _check_equation_numbering_rule,
        BibliographyCountRule: _check_bibliography_count_rule,
        BibliographyOrderRule: _check_bibliography_order_rule,
        BibliographyRecencyRule: _check_bibliography_recency_rule,
    }
