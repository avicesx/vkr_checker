from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Optional

from pydantic import BaseModel, Field

from parser.core.schema import (
    BlockUnion,
    CommentArtifact,
    ConsecutiveEmptyParagraphs,
    FigureNumberingIssue,
    ParagraphBlock,
    ParsedDocxDocument,
    TableBlock,
    TocPageNumberWithoutRenderedNumber,
    TrackChangeArtifact,
    VisualObject,
)
from rule_engine.findings import (
    Confidence,
    RuleAssessment,
    RuleCheckReport,
    Violation as RuleViolation,
)


class ReportCategory(str, Enum):
    """категории для группировки нарушений в отчёте"""

    FORMATTING = "formatting"
    STRUCTURE = "structure"
    REVIEW_ARTIFACTS = "review_artifacts"
    DOCUMENT_INTEGRITY = "document_integrity"
    FIGURES = "figures"
    PAGE_LAYOUT = "page_layout"
    BIBLIOGRAPHY = "bibliography"
    CAPTIONS = "captions"


class ReportSeverity(str, Enum):
    """уровни критичности, назначаемые адаптером отчёта"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ViolationLocation(BaseModel):
    """наиболее точное доступное место обнаруженного нарушения"""

    block_id: Optional[str] = None
    section_idx: Optional[int] = Field(default=None, ge=0)
    page_start: Optional[int] = Field(default=None, ge=1)
    page_end: Optional[int] = Field(default=None, ge=1)
    text_excerpt: Optional[str] = None
    scope: str = "document"


class Violation(BaseModel):
    """обнаруженное нарушение или группа однотипных нарушений

    ``count`` — сколько находок движка представляет запись, ``locations`` —
    полный список адресов группы (без обрезки; в Markdown показываются примеры).
    Для одиночного нарушения ``count`` равен единице, а адрес лежит в ``location``.
    ``occurrences`` — число дефектных фрагментов внутри адресов, ``found`` —
    сводка по подтипам (для микротипографики: кавычки, тире, пробелы).
    """

    id: str
    rule_id: Optional[str] = None
    count: int = Field(default=1, ge=1)
    occurrences: int = Field(
        default=1,
        ge=1,
        description="Сколько дефектных фрагментов внутри адреса (для группы — сумма)",
    )
    found: dict[str, int] = Field(
        default_factory=dict,
        description="Сводка находок по подтипам (для микротипографики — суммы)",
    )
    category: ReportCategory
    severity: ReportSeverity
    location: ViolationLocation
    locations: list[ViolationLocation] = Field(default_factory=list)
    description: str
    recommendation: str
    source: str


class ViolationGroup(BaseModel):
    """нарушения одной категории отчёта"""

    category: ReportCategory
    title: str
    violations: list[Violation] = Field(default_factory=list)


class ViolationSummary(BaseModel):
    """общее число нарушений и их количество по критичности"""

    total_violations: int = Field(ge=0)
    critical: int = Field(ge=0)
    high: int = Field(ge=0)
    medium: int = Field(ge=0)
    low: int = Field(ge=0)


class NotAssessedRule(BaseModel):
    """правило, которое не удалось проверить из-за нехватки данных"""

    rule_id: str
    code: str
    observed_text: str = ""
    confidence: Confidence = "medium"


class ReportCoverage(BaseModel):
    """сводка покрытия: сколько правил проверено, выполнено и не оценено

    Нарушения в отчёт попадают из ``RuleCheckReport.violations``, а оценки
    отвечают только за этот блок: ``not_assessed`` не превращается в нарушение.
    """

    rules_assessed: int = Field(default=0, ge=0)
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    not_assessed: int = Field(default=0, ge=0)
    not_assessed_rules: list[NotAssessedRule] = Field(default_factory=list)


class ViolationReport(BaseModel):
    """отчёт для страницы результата или скачивания в Markdown"""

    schema_version: str = "1.0"
    document_name: str
    generated_at: datetime
    summary: ViolationSummary
    coverage: ReportCoverage = Field(default_factory=ReportCoverage)
    groups: list[ViolationGroup] = Field(default_factory=list)

    def to_markdown(self) -> str:
        """формирует отчёт в Markdown для скачивания"""
        lines = [
            f"# Отчёт о нарушениях: {_escape_markdown(self.document_name)}",
            "",
            f"- Дата формирования: {self.generated_at.isoformat()}",
            f"- Всего нарушений: {self.summary.total_violations}",
            (
                "- Критичность: "
                f"критические — {self.summary.critical}, "
                f"высокие — {self.summary.high}, "
                f"средние — {self.summary.medium}, "
                f"низкие — {self.summary.low}"
            ),
            (
                "- Покрытие правил: "
                f"проверено — {self.coverage.rules_assessed}, "
                f"выполнено — {self.coverage.passed}, "
                f"не оценено — {self.coverage.not_assessed}"
            ),
        ]
        if self.coverage.not_assessed_rules:
            lines.append(
                "- Не оценены: "
                + ", ".join(
                    _escape_markdown(item.rule_id)
                    for item in self.coverage.not_assessed_rules
                )
            )
        if not self.groups:
            lines.extend(["", "Нарушений не найдено."])
            return "\n".join(lines) + "\n"

        for group in self.groups:
            lines.extend(["", f"## {_category_title(group.category)}"])
            for violation in group.violations:
                if violation.count > 1 and violation.rule_id:
                    lines.extend(_format_grouped_violation(violation))
                    continue
                location = _format_location(violation.location)
                lines.extend(
                    [
                        "",
                        (
                            f"### [{violation.severity.value.upper()}] "
                            f"{_escape_markdown(violation.id)}"
                        ),
                        f"- Место: {location}",
                        f"- Описание: {_escape_markdown(violation.description)}",
                        f"- Рекомендация: {_escape_markdown(violation.recommendation)}",
                    ]
                )
        return "\n".join(lines) + "\n"


_CATEGORY_TITLES = {
    ReportCategory.FORMATTING: "Оформление",
    ReportCategory.STRUCTURE: "Структура документа",
    ReportCategory.REVIEW_ARTIFACTS: "Артефакты рецензирования",
    ReportCategory.DOCUMENT_INTEGRITY: "Целостность документа",
    ReportCategory.FIGURES: "Рисунки и иллюстрации",
    ReportCategory.PAGE_LAYOUT: "Разметка страниц",
    ReportCategory.BIBLIOGRAPHY: "Список литературы и ссылки на источники",
    ReportCategory.CAPTIONS: "Подписи рисунков и таблиц",
}

_CODE_DETAILS: dict[
    str, tuple[ReportCategory, ReportSeverity, str]
] = {
    "font": (
        ReportCategory.FORMATTING,
        ReportSeverity.MEDIUM,
        "Проверьте гарнитуру, кегль и начертание текста по методичке.",
    ),
    "indent": (
        ReportCategory.FORMATTING,
        ReportSeverity.LOW,
        "Проверьте отступ первой строки абзаца в исходном документе.",
    ),
    "line_spacing": (
        ReportCategory.FORMATTING,
        ReportSeverity.MEDIUM,
        "Проверьте межстрочный интервал основного текста по методичке.",
    ),
    "alignment": (
        ReportCategory.FORMATTING,
        ReportSeverity.MEDIUM,
        "Проверьте выравнивание абзаца по методичке.",
    ),
    "margin": (
        ReportCategory.PAGE_LAYOUT,
        ReportSeverity.MEDIUM,
        "Проверьте размеры полей страницы по методичке.",
    ),
    "page_format": (
        ReportCategory.PAGE_LAYOUT,
        ReportSeverity.MEDIUM,
        "Проверьте формат страницы по методичке.",
    ),
    "page_number_position": (
        ReportCategory.PAGE_LAYOUT,
        ReportSeverity.LOW,
        "Проверьте расположение нумерации страниц по выбранной методичке.",
    ),
    "widow_control": (
        ReportCategory.PAGE_LAYOUT,
        ReportSeverity.LOW,
        "Включите контроль висячих строк, чтобы одиночные строки не оставались на соседней странице.",
    ),
    "heading_levels": (
        ReportCategory.STRUCTURE,
        ReportSeverity.MEDIUM,
        "Приведите иерархию заголовков к непрерывной: старт с первого уровня, без перескоков.",
    ),
    "heading_numbering": (
        ReportCategory.STRUCTURE,
        ReportSeverity.MEDIUM,
        "Проверьте последовательность и глубину нумерации разделов.",
    ),
    "heading_format": (
        ReportCategory.STRUCTURE,
        ReportSeverity.LOW,
        "Приведите оформление заголовка к требуемому виду.",
    ),
    "keep_next": (
        ReportCategory.STRUCTURE,
        ReportSeverity.LOW,
        "Включите неотрывность заголовка от следующего абзаца.",
    ),
    "heading_page_break": (
        ReportCategory.STRUCTURE,
        ReportSeverity.MEDIUM,
        "Начинайте структурный раздел с новой страницы.",
    ),
    "required_sections": (
        ReportCategory.STRUCTURE,
        ReportSeverity.HIGH,
        "Добавьте обязательные разделы и расположите их в требуемом порядке.",
    ),
    "toc": (
        ReportCategory.STRUCTURE,
        ReportSeverity.MEDIUM,
        "Сверьте оглавление с заголовками и нумерацией страниц документа.",
    ),
    "document_volume": (
        ReportCategory.STRUCTURE,
        ReportSeverity.MEDIUM,
        "Сверьте объём работы с методическими указаниями.",
    ),
    "microtypography": (
        ReportCategory.FORMATTING,
        ReportSeverity.LOW,
        "Приведите кавычки, тире и пробелы к типографской норме: «ёлочки», длинное тире, одиночные пробелы.",
    ),
    "equation_numbering": (
        ReportCategory.STRUCTURE,
        ReportSeverity.MEDIUM,
        "Пронумеруйте вынесенные формулы и проверьте ссылки на них в тексте.",
    ),
    "bibliography_count": (
        ReportCategory.BIBLIOGRAPHY,
        ReportSeverity.MEDIUM,
        "Дополните список литературы до требуемого числа источников.",
    ),
    "bibliography_order": (
        ReportCategory.BIBLIOGRAPHY,
        ReportSeverity.HIGH,
        "Перенумеруйте источники подряд, без пропусков и повторяющихся номеров.",
    ),
    "bibliography_recency": (
        ReportCategory.BIBLIOGRAPHY,
        ReportSeverity.LOW,
        "Добавьте свежие источники: не менее трети списка должны составлять издания последних лет.",
    ),
    "bibliography_link": (
        ReportCategory.BIBLIOGRAPHY,
        ReportSeverity.HIGH,
        "Проверьте, что ссылка указывает на существующий источник и оформлена по методичке.",
    ),
    "bibliography_cited": (
        ReportCategory.BIBLIOGRAPHY,
        ReportSeverity.MEDIUM,
        "Добавьте ссылку на источник в тексте или удалите неиспользуемую запись списка.",
    ),
    "bibliography_format": (
        ReportCategory.BIBLIOGRAPHY,
        ReportSeverity.MEDIUM,
        "Оформите библиографическую запись по образцу из методички.",
    ),
    "caption_numbering": (
        ReportCategory.CAPTIONS,
        ReportSeverity.MEDIUM,
        "Проверьте сквозную нумерацию подписей и устраните дубли, пропуски и нарушения порядка.",
    ),
    "caption_format": (
        ReportCategory.CAPTIONS,
        ReportSeverity.MEDIUM,
        "Приведите подпись к требуемому виду: положение, шрифт, завершающие знаки.",
    ),
    "caption_reference": (
        ReportCategory.CAPTIONS,
        ReportSeverity.MEDIUM,
        "Добавьте в текст корректную ссылку на рисунок или таблицу.",
    ),
    "caption_placement": (
        ReportCategory.CAPTIONS,
        ReportSeverity.MEDIUM,
        "Расположите подпись относительно объекта, как требует методичка, и проверьте её выравнивание.",
    ),
    "inline_reference_format": (
        ReportCategory.BIBLIOGRAPHY,
        ReportSeverity.MEDIUM,
        "Оформите внутритекстовую ссылку по образцу: тип ссылки, номер и страница.",
    ),
    "table_layout": (
        ReportCategory.FORMATTING,
        ReportSeverity.MEDIUM,
        "Обведите таблицу видимыми рамками и впишите её в полосу набора.",
    ),
    "table_continuation": (
        ReportCategory.FORMATTING,
        ReportSeverity.MEDIUM,
        "Обозначьте продолжение таблицы, повторите шапку и уберите нижнюю рамку на переносе.",
    ),
    "table_header_format": (
        ReportCategory.FORMATTING,
        ReportSeverity.LOW,
        "Приведите шапку таблицы к требуемому регистру и уберите точку в конце.",
    ),
    "list_format": (
        ReportCategory.FORMATTING,
        ReportSeverity.LOW,
        "Приведите маркеры перечисления к требуемым для каждого уровня вложенности.",
    ),
    "heading_hyphenation": (
        ReportCategory.STRUCTURE,
        ReportSeverity.LOW,
        "Уберите переносы и сокращения из заголовка раздела.",
    ),
    "appendix_order": (
        ReportCategory.STRUCTURE,
        ReportSeverity.MEDIUM,
        "Расположите приложения в порядке первой ссылки и начинайте каждое с новой страницы.",
    ),
    "title_page_content": (
        ReportCategory.STRUCTURE,
        ReportSeverity.HIGH,
        "Добавьте на титульный лист обязательные реквизиты по методичке.",
    ),
}

# резервные категории для семейств правил, которых нет в _CODE_DETAILS:
# новые правила не должны оставаться без описания, даже если их code
# ещё не добавлен в точный маппинг
_RULE_PREFIX_FALLBACKS: tuple[
    tuple[str, tuple[ReportCategory, ReportSeverity, str]], ...
] = (
    (
        "bibliography_",
        (
            ReportCategory.BIBLIOGRAPHY,
            ReportSeverity.MEDIUM,
            "Сверьте ссылки и записи списка литературы с требованиями методички.",
        ),
    ),
    (
        "caption_",
        (
            ReportCategory.CAPTIONS,
            ReportSeverity.MEDIUM,
            "Сверьте оформление и нумерацию подписи с требованиями методички.",
        ),
    ),
    (
        "table_",
        (
            ReportCategory.FORMATTING,
            ReportSeverity.MEDIUM,
            "Проверьте шрифт и оформление текста в таблице по методичке.",
        ),
    ),
    (
        "heading_",
        (
            ReportCategory.STRUCTURE,
            ReportSeverity.MEDIUM,
            "Сверьте заголовки с требованиями методички.",
        ),
    ),
    (
        "page_",
        (
            ReportCategory.PAGE_LAYOUT,
            ReportSeverity.MEDIUM,
            "Сверьте разметку страницы с требованиями методички.",
        ),
    ),
    (
        "font_",
        (
            ReportCategory.FORMATTING,
            ReportSeverity.MEDIUM,
            "Проверьте гарнитуру, кегль и начертание текста по методичке.",
        ),
    ),
)

_GROUP_MIN_SIZE = 4
_GROUP_EXAMPLES_LIMIT = 10
_GROUP_TYPOGRAPHY_EXAMPLES_LIMIT = 3
_MICROTYPOGRAPHY_LABELS = {
    "wrong_quotes": "кавычки",
    "wrong_dashes": "тире",
    "multiple_spaces": "лишние пробелы",
    "space_before_punctuation": "пробел перед знаком препинания",
    "missing_nbsp": "неразрывные пробелы",
}
_MICROTYPOGRAPHY_ORDER = tuple(_MICROTYPOGRAPHY_LABELS)
_GROUP_EXAMPLE_EXCERPT = 80
_TABLE_BLOCK_RE = re.compile(r"^tbl_(\d+)_")

_DEFAULT_DETAILS = (
    ReportCategory.FORMATTING,
    ReportSeverity.MEDIUM,
    "Сверьте оформление с выбранной методичкой.",
)


def _code_details(
    code: str,
    rule_id: str,
) -> tuple[ReportCategory, ReportSeverity, str]:
    """находит категорию, критичность и рекомендацию по code правила

    Маппинг общий для семейств правил: новое правило с уже известным ``code``
    попадает в свою категорию автоматически. Для незнакомого ``code`` работает
    резервный поиск по префиксу семейства, чтобы нарушение не осталось без
    описания.
    """
    details = _CODE_DETAILS.get(code)
    if details is not None:
        return details
    for prefix, fallback_details in _RULE_PREFIX_FALLBACKS:
        if code.startswith(prefix) or rule_id.startswith(prefix):
            return fallback_details
    return _DEFAULT_DETAILS


def _engine_description(violation: RuleViolation) -> str:
    """собирает описание нарушения движка из сообщения правила и ожиданий"""
    parts = [violation.message.strip().rstrip(".")]
    expected = violation.expected_text.strip().rstrip(".")
    found = violation.found_text.strip().rstrip(".")
    if expected:
        parts.append(f"Ожидалось: {expected}")
    if found:
        parts.append(f"Обнаружено: {found}")
    return ". ".join(parts) + "."


def build_violation_report(
    document: ParsedDocxDocument,
    rule_report: Optional[RuleCheckReport] = None,
    *,
    generated_at: Optional[datetime] = None,
) -> ViolationReport:
    """преобразует аномалии парсера и нарушения движка правил в отчёт.

    Каждое нарушение ``RuleCheckReport`` попадает в отчёт с адресом (блок,
    страница, фрагмент текста). Однотипные находки одного правила (четыре и
    более) сворачиваются в одну запись с полным списком адресов в ``locations``;
    ``summary`` при этом считает исходные находки, а не записи. Оценки правил
    нужны только для сводки покрытия ``pass``/``not_assessed``.
    """
    violations: list[Violation] = []
    blocks = _index_blocks(document.blocks)

    def add(
        violation_id: str,
        category: ReportCategory,
        severity: ReportSeverity,
        description: str,
        recommendation: str,
        *,
        block_id: Optional[str] = None,
        page: Optional[int] = None,
        excerpt: Optional[str] = None,
        source: str = "parser",
        location: Optional[ViolationLocation] = None,
    ) -> None:
        violations.append(
            Violation(
                id=violation_id,
                category=category,
                severity=severity,
                location=location or _location(blocks, block_id, page, excerpt),
                description=description,
                recommendation=recommendation,
                source=source,
            )
        )

    anomalies = document.anomalies
    for artifact in anomalies.unaccepted_track_changes:
        add(
            f"track-change:{artifact.block_id}:{artifact.artifact_type}",
            ReportCategory.REVIEW_ARTIFACTS,
            ReportSeverity.HIGH,
            f"В документе осталась непринятая правка ({artifact.artifact_type}).",
            "Примите или отклоните правку и сохраните чистовую версию документа.",
            block_id=artifact.block_id,
            page=artifact.page_est,
            excerpt=artifact.text_snippet,
        )
    for comment in anomalies.leftover_comments:
        _add_comment(add, comment)
    for cluster in anomalies.empty_paragraph_clusters:
        add(
            f"empty-paragraphs:{cluster.start_block_id}:{cluster.end_block_id}",
            ReportCategory.FORMATTING,
            ReportSeverity.LOW,
            f"Обнаружена серия из {cluster.count} пустых абзацев.",
            "Удалите пустые абзацы и задайте интервалы средствами форматирования.",
            block_id=cluster.start_block_id,
            page=cluster.page_est,
        )

    _add_block_id_issues(
        add,
        anomalies.hidden_text_block_ids,
        "hidden-text",
        ReportCategory.DOCUMENT_INTEGRITY,
        ReportSeverity.HIGH,
        "Обнаружен скрытый текст.",
        "Проверьте содержимое и снимите скрытие текста, если оно не требуется.",
    )
    _add_block_id_issues(
        add,
        anomalies.embedded_ole_block_ids,
        "embedded-object",
        ReportCategory.DOCUMENT_INTEGRITY,
        ReportSeverity.MEDIUM,
        "В абзаце обнаружен внедрённый OLE-объект, который требует ручной проверки.",
        "Проверьте объект в редакторе и убедитесь, что его содержимое отображается корректно.",
    )
    _add_block_id_issues(
        add,
        anomalies.broken_field_block_ids,
        "broken-field",
        ReportCategory.DOCUMENT_INTEGRITY,
        ReportSeverity.MEDIUM,
        "Обнаружено поле или ссылка с некорректным текстом.",
        "Обновите поле в редакторе документа и проверьте его отображение.",
    )
    _add_block_id_issues(
        add,
        anomalies.shaded_background_block_ids,
        "shaded-background",
        ReportCategory.FORMATTING,
        ReportSeverity.LOW,
        "У абзаца или его фрагмента обнаружена заливка фона.",
        "Проверьте, соответствует ли заливка требованиям оформления.",
    )
    _add_block_id_issues(
        add,
        anomalies.non_black_text_block_ids,
        "non-black-text",
        ReportCategory.FORMATTING,
        ReportSeverity.MEDIUM,
        "В абзаце обнаружен цветной текст.",
        "Проверьте цвет текста; основной текст обычно должен быть чёрным.",
    )

    for entry in anomalies.toc_page_numbers_without_rendered_numbers:
        add(
            f"toc-page-number:{entry.entry_block_id}",
            ReportCategory.STRUCTURE,
            ReportSeverity.MEDIUM,
            (
                "Номер страницы в оглавлении не подтверждён номером, "
                f"отрисованным в PDF (указано: {entry.extracted_page_str})."
            ),
            "Сверьте номер страницы в оглавлении с итоговым документом.",
            block_id=entry.entry_block_id,
        )

    for visual_id in anomalies.uncaptioned_visual_ids:
        _add_visual_issue(
            add,
            document.visuals.get(visual_id),
            visual_id,
            "uncaptioned-visual",
            ReportSeverity.MEDIUM,
            "Иллюстрация не имеет распознанной подписи.",
            "Добавьте подпись к иллюстрации, если этого требуют правила оформления.",
        )
    for block_id in anomalies.figure_captions_without_visual_ids:
        add(
            f"caption-without-visual:{block_id}",
            ReportCategory.FIGURES,
            ReportSeverity.MEDIUM,
            "Подпись рисунка не связана с найденным графическим объектом.",
            "Проверьте, что рисунок присутствует рядом с подписью и корректно распознаётся.",
            block_id=block_id,
        )
    for entity_id in anomalies.unreferenced_figure_ids:
        entity = next(
            (
                item
                for item in document.cross_ref_index.figures
                if item.entity_id == entity_id
            ),
            None,
        )
        add(
            f"unreferenced-figure:{entity_id}",
            ReportCategory.STRUCTURE,
            ReportSeverity.LOW,
            "На рисунок нет ссылки в основном тексте.",
            "Добавьте ссылку на рисунок в тексте или проверьте необходимость рисунка.",
            block_id=entity.caption_block_id if entity else None,
            page=entity.page_est if entity else None,
        )
    # поле может отсутствовать в промежуточных версиях схемы парсера
    for entity_id in getattr(anomalies, "unreferenced_table_ids", ()):
        entity = next(
            (
                item
                for item in document.cross_ref_index.tables
                if item.entity_id == entity_id
            ),
            None,
        )
        add(
            f"unreferenced-table:{entity_id}",
            ReportCategory.STRUCTURE,
            ReportSeverity.LOW,
            "На таблицу нет ссылки в основном тексте.",
            "Добавьте ссылку на таблицу в тексте или проверьте необходимость таблицы.",
            block_id=entity.caption_block_id if entity else None,
            page=entity.page_est if entity else None,
        )
    for issue in anomalies.figure_numbering_issues:
        _add_numbering_issue(add, issue)

    if anomalies.pdf_docx_mismatch_warning:
        add(
            "pdf-docx-mismatch",
            ReportCategory.DOCUMENT_INTEGRITY,
            ReportSeverity.HIGH,
            anomalies.pdf_docx_mismatch_warning,
            "Убедитесь, что PDF и DOCX получены из одной и той же версии документа.",
            source="parser",
        )

    assessments: list[RuleAssessment] = []
    if rule_report is not None:
        assessments = list(rule_report.assessments)
        engine_violations = [
            _build_rule_violation(blocks, rule_violation)
            for rule_violation in rule_report.violations
        ]
        violations.extend(_group_rule_violations(engine_violations))

    return _create_report(
        document.meta.filename,
        violations,
        generated_at or datetime.now(timezone.utc),
        _build_coverage(assessments),
    )


def _build_rule_violation(
    blocks: dict[str, BlockUnion],
    rule_violation: RuleViolation,
) -> Violation:
    """создаёт запись отчёта для одного нарушения движка"""
    category, severity, recommendation = _code_details(
        rule_violation.code, rule_violation.rule_id
    )
    found = {}
    if rule_violation.code == "microtypography":
        found = {
            key: value
            for key, value in rule_violation.found.items()
            if isinstance(value, int) and value > 0
        }
    return Violation(
        id=rule_violation.violation_id,
        rule_id=rule_violation.rule_id,
        occurrences=rule_violation.occurrences,
        found=found,
        category=category,
        severity=severity,
        location=_engine_location(blocks, rule_violation),
        description=_engine_description(rule_violation),
        recommendation=recommendation,
        source="rule_engine",
    )


def _group_rule_violations(violations: list[Violation]) -> list[Violation]:
    """сворачивает однотипные нарушения одного правила в одну запись.

    Группировка — только представление: ``count`` хранит число исходных
    находок, ``locations`` — их полный список. Правила с числом находок
    меньше ``_GROUP_MIN_SIZE`` остаются отдельными записями, чтобы одиночные
    замечания не терялись в сводке.
    """
    by_rule: dict[str, list[Violation]] = {}
    order: list[str] = []
    for violation in violations:
        rule_id = violation.rule_id or violation.id
        if rule_id not in by_rule:
            by_rule[rule_id] = []
            order.append(rule_id)
        by_rule[rule_id].append(violation)

    result: list[Violation] = []
    for rule_id in order:
        items = by_rule[rule_id]
        if len(items) >= _GROUP_MIN_SIZE:
            result.append(_merge_rule_group(rule_id, items))
        else:
            result.extend(items)
    return result


def _microtypography_summary(found: dict[str, int]) -> str:
    """печатает суммы подтипов микротипографики в устойчивом порядке"""
    parts = [
        f"{_MICROTYPOGRAPHY_LABELS[key]} {found[key]}"
        for key in _MICROTYPOGRAPHY_ORDER
        if found.get(key, 0) > 0
    ]
    known = set(_MICROTYPOGRAPHY_ORDER)
    parts.extend(
        f"{key} {found[key]}"
        for key in sorted(found)
        if key not in known and isinstance(found[key], int) and found[key] > 0
    )
    return ", ".join(parts)


def _microtypography_span_example(found_text: str) -> Optional[str]:
    """извлекает первый span-пример из текста находки микротипографики.

    Чекер печатает примеры в конце ``found_text`` как ``(фрагмент; фрагмент)``;
    это реальные куски текста с ошибкой, в отличие от начала абзаца в location.
    """
    text = (found_text or "").rstrip()
    if not text.endswith(")"):
        return None
    start = text.rfind(" (")
    if start < 0:
        return None
    first = text[start + 2 : -1].split("; ", 1)[0].strip()
    return first or None


def _merge_rule_group(rule_id: str, items: list[Violation]) -> Violation:
    """собирает группу однотипных нарушений с полным списком адресов"""
    first = items[0]
    occurrences = sum(item.occurrences for item in items)
    found: dict[str, int] = {}
    if first.found:
        for item in items:
            for key, value in item.found.items():
                if isinstance(value, int):
                    found[key] = found.get(key, 0) + value
    description = (
        f"{first.description} Всего однотипных нарушений: {len(items)}."
    )
    subtype_summary = _microtypography_summary(found)
    if subtype_summary:
        base = first.description.split(". Обнаружено:", 1)[0].rstrip().rstrip(".")
        description = (
            f"{base}. Обнаружено: {subtype_summary}. "
            f"Всего однотипных нарушений: {len(items)}."
        )
    return first.model_copy(
        update={
            "id": f"group:{rule_id}",
            "count": len(items),
            "occurrences": occurrences,
            "found": found,
            "locations": [item.location for item in items],
            "description": description,
        }
    )


def _table_numbers(locations: Iterable[ViolationLocation]) -> list[int]:
    """извлекает номера таблиц из block_id вида ``tbl_0001_r0_c0_p0``"""
    numbers = {
        int(match.group(1))
        for location in locations
        if (match := _TABLE_BLOCK_RE.match(location.block_id or ""))
    }
    return sorted(numbers)


def _compact_number_ranges(numbers: list[int]) -> str:
    """сжимает последовательность номеров в диапазоны: 1, 3–6"""
    parts: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        parts.append(str(start) if start == previous else f"{start}–{previous}")
        start = previous = number
    parts.append(str(start) if start == previous else f"{start}–{previous}")
    return ", ".join(parts)


def _table_range_label(locations: Iterable[ViolationLocation]) -> Optional[str]:
    """подпись с номерами таблиц, к которым относятся адреса группы"""
    numbers = _table_numbers(locations)
    if not numbers:
        return None
    if len(numbers) == 1:
        return f"таблица {numbers[0]}"
    return f"таблицы {_compact_number_ranges(numbers)}"


def _format_group_example(location: ViolationLocation) -> str:
    """форматирует один адрес группы для строки примеров в Markdown"""
    if location.block_id:
        example = location.block_id
        if location.page_start is not None:
            example += f" (стр. {location.page_start})"
    elif location.section_idx is not None:
        example = f"секция {location.section_idx}"
    elif location.page_start is not None:
        example = f"стр. {location.page_start}"
    else:
        example = "весь документ"
    if location.text_excerpt:
        excerpt = location.text_excerpt
        if len(excerpt) > _GROUP_EXAMPLE_EXCERPT:
            excerpt = f"{excerpt[: _GROUP_EXAMPLE_EXCERPT - 3]}..."
        example += f": «{_escape_markdown(excerpt)}»"
    return example


def _format_grouped_violation(violation: Violation) -> list[str]:
    """формирует компактную Markdown-запись для группы однотипных нарушений"""
    limit = (
        _GROUP_TYPOGRAPHY_EXAMPLES_LIMIT
        if violation.found
        else _GROUP_EXAMPLES_LIMIT
    )
    examples = violation.locations[:limit]
    example_text = "; ".join(_format_group_example(item) for item in examples)
    if len(violation.locations) > limit:
        example_text += f"; … и ещё {len(violation.locations) - limit}"
    table_label = _table_range_label(violation.locations)
    subtype_summary = _microtypography_summary(violation.found)
    details = [item for item in (table_label, subtype_summary) if item]
    summary = f"{violation.rule_id} — {violation.count} нарушений"
    if details:
        summary += f" ({', '.join(details)})"
    if example_text:
        summary += f", напр. {example_text}"
    return [
        "",
        (
            f"### [{violation.severity.value.upper()}] "
            f"{_escape_markdown(violation.rule_id or violation.id)}"
        ),
        f"- {summary}",
        f"- Рекомендация: {_escape_markdown(violation.recommendation)}",
    ]


def _engine_location(
    blocks: dict[str, BlockUnion],
    rule_violation: RuleViolation,
) -> ViolationLocation:
    """переносит адрес нарушения движка в модель адреса отчёта"""
    source = rule_violation.location
    excerpt = source.snippet or None
    if rule_violation.code == "microtypography":
        excerpt = _microtypography_span_example(rule_violation.found_text) or excerpt
    location = _location(
        blocks,
        source.block_id,
        source.page_start_est,
        excerpt,
    )
    page_end = source.page_end_est or location.page_end
    if source.block_id is not None:
        scope = "block"
    elif location.page_start is not None:
        scope = "page"
    elif source.section_idx is not None:
        scope = "section"
    else:
        scope = "document"
    return location.model_copy(
        update={
            "page_end": page_end,
            "section_idx": source.section_idx,
            "scope": scope,
        }
    )


def _build_coverage(assessments: Iterable[RuleAssessment]) -> ReportCoverage:
    """считает покрытие правил по оценкам движка"""
    items = list(assessments)
    status_counts = Counter(item.status for item in items)
    return ReportCoverage(
        rules_assessed=len(items),
        passed=status_counts["pass"],
        failed=status_counts["fail"],
        not_assessed=status_counts["not_assessed"],
        not_assessed_rules=[
            NotAssessedRule(
                rule_id=item.rule_id,
                code=item.code,
                observed_text=item.observed_text,
                confidence=item.confidence,
            )
            for item in items
            if item.status == "not_assessed"
        ],
    )


def _add_comment(add, comment: CommentArtifact) -> None:
    """добавляет в отчёт оставленный комментарий"""
    add(
        f"comment:{comment.comment_id}",
        ReportCategory.REVIEW_ARTIFACTS,
        ReportSeverity.MEDIUM,
        f"В документе остался комментарий: {comment.comment_body}",
        "Разрешите или удалите комментарий перед передачей чистовой версии.",
        block_id=comment.block_id,
        page=comment.page_est,
        excerpt=comment.commented_text,
    )


def _add_block_id_issues(
    add,
    block_ids: list[str],
    rule_id: str,
    category: ReportCategory,
    severity: ReportSeverity,
    description: str,
    recommendation: str,
) -> None:
    """добавляет в отчёт отдельный пункт для каждой блочной аномалии"""
    for block_id in block_ids:
        add(
            f"{rule_id}:{block_id}",
            category,
            severity,
            description,
            recommendation,
            block_id=block_id,
        )


def _add_visual_issue(
    add,
    visual: Optional[VisualObject],
    visual_id: str,
    rule_id: str,
    severity: ReportSeverity,
    description: str,
    recommendation: str,
) -> None:
    """добавляет аномалию объекта и определяет место содержащего его блока"""
    add(
        f"{rule_id}:{visual_id}",
        ReportCategory.FIGURES,
        severity,
        description,
        recommendation,
        block_id=visual.parent_block_id if visual else None,
        page=visual.page_est if visual else None,
        excerpt=f"Объект {visual_id}",
    )


_NUMBERING_ENTITY_FORMS = {
    "figure": ("рисунка", "рисунков"),
    "table": ("таблицы", "таблиц"),
    "appendix": ("приложения", "приложений"),
}


def _add_numbering_issue(add, issue: FigureNumberingIssue) -> None:
    """добавляет нарушение нумерации подписи рисунка, таблицы или приложения"""
    # поле entity_type может отсутствовать в промежуточных версиях схемы парсера
    entity_type = getattr(issue, "entity_type", "figure") or "figure"
    entity_single, entity_plural = _NUMBERING_ENTITY_FORMS.get(
        entity_type, _NUMBERING_ENTITY_FORMS["figure"]
    )
    is_table = entity_type == "table"
    issue_descriptions = {
        "duplicate": f"Обнаружен повтор номера подписи {entity_single}.",
        "gap": f"В последовательности номеров {entity_plural} обнаружен пропуск.",
        "out_of_order": (
            f"Номер подписи {entity_single} нарушает порядок нумерации."
        ),
    }
    add(
        (
            f"{'table' if is_table else 'figure'}-numbering:"
            f"{issue.issue_kind}:{issue.caption_block_id or issue.number_str}"
        ),
        ReportCategory.CAPTIONS if is_table else ReportCategory.FIGURES,
        ReportSeverity.MEDIUM,
        (f"{issue_descriptions[issue.issue_kind]} Номер: {issue.number_str}."),
        f"Проверьте сквозную нумерацию подписей {entity_plural}.",
        block_id=issue.caption_block_id,
        excerpt=(
            f"Предыдущий номер: {issue.previous_number_str}"
            if issue.previous_number_str
            else None
        ),
    )


def _create_report(
    document_name: str,
    violations: list[Violation],
    generated_at: datetime,
    coverage: ReportCoverage,
) -> ViolationReport:
    """группирует нарушения и рассчитывает сводные показатели"""
    grouped: dict[ReportCategory, list[Violation]] = {}
    for violation in violations:
        grouped.setdefault(violation.category, []).append(violation)
    groups = [
        ViolationGroup(
            category=category,
            title=_category_title(category),
            violations=items,
        )
        for category, items in grouped.items()
    ]
    severity_counts: Counter[ReportSeverity] = Counter()
    for item in violations:
        severity_counts[item.severity] += item.count
    total_violations = sum(item.count for item in violations)
    return ViolationReport(
        document_name=document_name,
        generated_at=generated_at,
        summary=ViolationSummary(
            total_violations=total_violations,
            critical=severity_counts[ReportSeverity.CRITICAL],
            high=severity_counts[ReportSeverity.HIGH],
            medium=severity_counts[ReportSeverity.MEDIUM],
            low=severity_counts[ReportSeverity.LOW],
        ),
        coverage=coverage,
        groups=groups,
    )


def _index_blocks(blocks: list[BlockUnion]) -> dict[str, BlockUnion]:
    """индексирует блоки верхнего уровня и содержимое ячеек таблиц по ID"""
    result: dict[str, BlockUnion] = {}
    pending = list(blocks)
    while pending:
        block = pending.pop()
        result[block.block_id] = block
        if isinstance(block, TableBlock):
            for row in block.rows:
                for cell in row.cells:
                    pending.extend(cell.blocks)
    return result


def _location(
    blocks: dict[str, BlockUnion],
    block_id: Optional[str],
    page: Optional[int],
    excerpt: Optional[str],
) -> ViolationLocation:
    """определяет блок, оценочную страницу и фрагмент текста для нарушения"""
    block = blocks.get(block_id) if block_id else None
    if isinstance(block, ParagraphBlock):
        page_start = page or block.page_start_est
        page_end = block.page_end_est if page is None else page
        text_excerpt = excerpt or block.text_visible.strip() or None
    elif isinstance(block, TableBlock):
        page_start = page or block.page_start_est
        page_end = block.page_end_est if page is None else page
        text_excerpt = excerpt or None
    else:
        page_start = page
        page_end = page
        text_excerpt = excerpt
    if text_excerpt:
        text_excerpt = text_excerpt.replace("\n", " ").strip()
        if len(text_excerpt) > 240:
            text_excerpt = f"{text_excerpt[:237]}..."
    return ViolationLocation(
        block_id=block_id,
        page_start=page_start,
        page_end=page_end,
        text_excerpt=text_excerpt,
        scope="block" if block_id else "document",
    )


def _category_title(category: ReportCategory) -> str:
    """возвращает отображаемое название категории"""
    return _CATEGORY_TITLES[category]


def _format_location(location: ViolationLocation) -> str:
    """форматирует место нарушения для вывода в Markdown"""
    if (
        location.scope == "document"
        and location.page_start is None
        and not location.text_excerpt
    ):
        return "весь документ"
    parts = []
    if location.page_start is not None:
        if location.page_end and location.page_end != location.page_start:
            parts.append(f"стр. {location.page_start}–{location.page_end}")
        else:
            parts.append(f"стр. {location.page_start}")
    if location.block_id:
        parts.append(f"блок `{_escape_markdown(location.block_id)}`")
    if location.section_idx is not None:
        parts.append(f"секция {location.section_idx}")
    if location.text_excerpt:
        parts.append(f"«{_escape_markdown(location.text_excerpt)}»")
    return ", ".join(parts) or "место не определено парсером"


def _escape_markdown(value: str) -> str:
    """экранирует спецсимволы Markdown и переносы строк в извлечённом тексте"""
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")
