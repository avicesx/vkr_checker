from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Optional

from pydantic import BaseModel, Field

from docx_parser.pdf_norm_control_parser import PdfRuleFinding
from docx_parser.schema import (
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


class ReportCategory(str, Enum):
    """категории для группировки нарушений в отчёте"""

    FORMATTING = "formatting"
    STRUCTURE = "structure"
    REVIEW_ARTIFACTS = "review_artifacts"
    DOCUMENT_INTEGRITY = "document_integrity"
    FIGURES = "figures"
    PAGE_LAYOUT = "page_layout"


class ReportSeverity(str, Enum):
    """уровни критичности, назначаемые адаптером отчёта"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ViolationLocation(BaseModel):
    """наиболее точное доступное место обнаруженного нарушения"""

    block_id: Optional[str] = None
    page_start: Optional[int] = Field(default=None, ge=1)
    page_end: Optional[int] = Field(default=None, ge=1)
    text_excerpt: Optional[str] = None
    scope: str = "document"


class Violation(BaseModel):
    """одно обнаруженное нарушение с рекомендацией по исправлению"""

    id: str
    category: ReportCategory
    severity: ReportSeverity
    location: ViolationLocation
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


class ViolationReport(BaseModel):
    """отчёт для страницы результата или скачивания в Markdown"""

    schema_version: str = "1.0"
    document_name: str
    generated_at: datetime
    summary: ViolationSummary
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
        ]
        if not self.groups:
            lines.extend(["", "Нарушений не найдено."])
            return "\n".join(lines) + "\n"

        for group in self.groups:
            lines.extend(["", f"## {_category_title(group.category)}"])
            for violation in group.violations:
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
}

_PDF_RULE_DETAILS = {
    "body_font_family": (
        ReportCategory.FORMATTING,
        ReportSeverity.MEDIUM,
        "Преобладающий шрифт отличается от Times New Roman.",
        "Проверьте гарнитуру основного текста по методическим требованиям.",
    ),
    "body_font_size": (
        ReportCategory.FORMATTING,
        ReportSeverity.MEDIUM,
        "Доминирующий размер шрифта отличается от требуемого.",
        "Проверьте размер шрифта основного текста в исходном документе.",
    ),
    "line_spacing": (
        ReportCategory.FORMATTING,
        ReportSeverity.MEDIUM,
        "Оценочный межстрочный интервал не соответствует требованию.",
        "Проверьте межстрочный интервал в исходном документе.",
    ),
    "first_line_indent": (
        ReportCategory.FORMATTING,
        ReportSeverity.LOW,
        "Оценка абзацного отступа не соответствует требованию.",
        "Проверьте отступ первой строки абзаца в исходном документе.",
    ),
    "page_text_area_position": (
        ReportCategory.PAGE_LAYOUT,
        ReportSeverity.MEDIUM,
        "Положение текстовой области отличается от заданного профиля.",
        "Проверьте поля и размещение основного текста по методичке.",
    ),
    "page_size_a4": (
        ReportCategory.PAGE_LAYOUT,
        ReportSeverity.MEDIUM,
        "Размер страницы отличается от A4.",
        "Установите формат страницы A4, если он требуется выбранной методичкой.",
    ),
    "page_number_position": (
        ReportCategory.PAGE_LAYOUT,
        ReportSeverity.LOW,
        "Номер страницы расположен не в ожидаемой зоне.",
        "Проверьте расположение нумерации страниц по выбранной методичке.",
    ),
}


def build_violation_report(
    document: ParsedDocxDocument,
    pdf_findings: Iterable[PdfRuleFinding] = (),
    *,
    generated_at: Optional[datetime] = None,
) -> ViolationReport:
    """преобразует аномалии DOCX и ошибки PDF-проверок в сгруппированный отчёт"""
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
        source: str = "docx_parser",
    ) -> None:
        violations.append(
            Violation(
                id=violation_id,
                category=category,
                severity=severity,
                location=_location(blocks, block_id, page, excerpt),
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
    for issue in anomalies.figure_numbering_issues:
        _add_figure_numbering_issue(add, issue)

    if anomalies.pdf_docx_mismatch_warning:
        add(
            "pdf-docx-mismatch",
            ReportCategory.DOCUMENT_INTEGRITY,
            ReportSeverity.HIGH,
            anomalies.pdf_docx_mismatch_warning,
            "Убедитесь, что PDF и DOCX получены из одной и той же версии документа.",
            source="docx_parser",
        )

    for finding in pdf_findings:
        if finding.status != "fail":
            continue
        details = _PDF_RULE_DETAILS.get(finding.rule_id)
        if details is None:
            category, severity = ReportCategory.FORMATTING, ReportSeverity.MEDIUM
            description = f"Не выполнено требование оформления: {finding.rule_id}."
            recommendation = "Сверьте оформление с выбранной методичкой."
        else:
            category, severity, description, recommendation = details
        add(
            f"pdf:{finding.rule_id}",
            category,
            severity,
            f"{description} Ожидалось: {finding.expected}. Обнаружено: {finding.observed}.",
            recommendation,
            source="pdf_norm_control",
        )

    return _create_report(
        document.meta.filename,
        violations,
        generated_at or datetime.now(timezone.utc),
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


def _add_figure_numbering_issue(add, issue: FigureNumberingIssue) -> None:
    """добавляет нарушение нумерации подписи рисунка"""
    issue_descriptions = {
        "duplicate": "Обнаружен повтор номера подписи рисунка.",
        "gap": "В последовательности номеров рисунков обнаружен пропуск.",
        "out_of_order": "Номер подписи рисунка нарушает порядок нумерации.",
    }
    add(
        f"figure-numbering:{issue.issue_kind}:{issue.caption_block_id or issue.number_str}",
        ReportCategory.FIGURES,
        ReportSeverity.MEDIUM,
        (f"{issue_descriptions[issue.issue_kind]} Номер: {issue.number_str}."),
        "Проверьте сквозную нумерацию подписей рисунков.",
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
    severity_counts = Counter(item.severity for item in violations)
    return ViolationReport(
        document_name=document_name,
        generated_at=generated_at,
        summary=ViolationSummary(
            total_violations=len(violations),
            critical=severity_counts[ReportSeverity.CRITICAL],
            high=severity_counts[ReportSeverity.HIGH],
            medium=severity_counts[ReportSeverity.MEDIUM],
            low=severity_counts[ReportSeverity.LOW],
        ),
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
    if location.scope == "document":
        return "весь документ"
    parts = []
    if location.page_start is not None:
        if location.page_end and location.page_end != location.page_start:
            parts.append(f"стр. {location.page_start}–{location.page_end}")
        else:
            parts.append(f"стр. {location.page_start}")
    if location.block_id:
        parts.append(f"блок `{_escape_markdown(location.block_id)}`")
    if location.text_excerpt:
        parts.append(f"«{_escape_markdown(location.text_excerpt)}»")
    return ", ".join(parts) or "место не определено парсером"


def _escape_markdown(value: str) -> str:
    """экранирует спецсимволы Markdown и переносы строк в извлечённом тексте"""
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")
