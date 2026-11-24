"""Семейство микротипографики: кавычки, тире, лишние и неразрывные пробелы.

Все нарушения уже размечены парсером DOCX в ``ParagraphBlock.typography``
(``MicroTypographyReport``): правило только отбирает виды ошибок и отсеивает
заведомо ложные срабатывания разметки. PDF не сохраняет различия кавычек,
пробелов и мягких переносов, поэтому для PDF правило честно не оценивается.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from ..findings import RuleAssessment, Violation
from ..rules import MicroTypographyCheck, MicroTypographyRule
from .builders import (
    _build_violation,
    _counted_assessment,
    _paragraph_location,
)
from .common import _is_pdf_only, _paragraph_targets
from parser.core.schema import (
    DocumentTreeNode,
    ParagraphBlock,
    ParsedDocxDocument,
    SpanLocation,
)

CHECK_LABELS: Dict[str, str] = {
    "wrong_quotes": "неверные кавычки",
    "wrong_dashes": "дефис вместо тире",
    "multiple_spaces": "лишние пробелы",
    "space_before_punctuation": "пробел перед знаком препинания",
    "missing_nbsp": "отсутствует неразрывный пробел",
}
"""Подписи видов микротипографических ошибок для отчёта."""

CODE_HINTS = set("=()[]{}<>;\\|")
"""Символы, выдающие программный код: внутри них кавычки и пробелы не норма."""

EXAMPLE_LIMIT = 3
"""Сколько фрагментов нарушения показывать в тексте найденного значения."""

SKIPPED_TREE_ROLES = frozenset({"bibliography", "toc"})
"""Разделы, где пробелы и кавычки задаёт вёрстка, а не автор прозы."""


def _structural_block_ids(
    nodes: List[DocumentTreeNode],
    roles: frozenset,
) -> set:
    """Собирает абзацы разделов с указанными структурными ролями."""
    block_ids = set()
    for node in nodes:
        if node.semantic_role in roles:
            block_ids.update(node.direct_block_ids)
        block_ids.update(_structural_block_ids(node.children, roles))
    return block_ids


def _is_code_span(text: str, start: int, end: int) -> bool:
    """Считает фрагмент частью программного кода, а не прозы.

    Разметка контекста относит абзац к листингу целиком, но код встречается и
    внутри обычного абзаца (``se.json()['id']``). Вокруг найденного фрагмента
    ищутся символы, которых в прозе почти не бывает; кавычка или пробел рядом
    с ними — свойство кода, а не нарушение нормоконтроля.
    """
    window = text[max(0, start - 6) : min(len(text), end + 6)]
    return any(char in CODE_HINTS for char in window)


def _quote_is_false_positive(text: str, start: int, end: int) -> bool:
    """Отсеивает апостроф внутри латинского слова и кавычки в коде."""
    quote = text[start:end]
    if quote not in {"'", '"'}:
        return False
    before = text[start - 1] if start else ""
    after = text[end] if end < len(text) else ""
    if (
        quote == "'"
        and before.isascii()
        and before.isalpha()
        and after.isascii()
        and after.isalpha()
    ):
        return True
    return _is_code_span(text, start, end)


def _space_before_punctuation_is_false_positive(
    text: str, start: int, end: int
) -> bool:
    """Отсеивает пробел перед точкой в имени продукта или домене.

    Регулярное выражение разметки видит пробел перед точкой в ``.NET`` и
    считает его ошибкой; в прозе после точки идёт пробел, конец строки или
    другой знак, а буква или цифра означают продолжение имени.
    """
    if end + 1 < len(text) and text[end + 1].isalnum():
        return True
    return _is_code_span(text, start, end)


def _dash_is_false_positive(text: str, start: int, end: int) -> bool:
    """Отсеивает дефис в библиографической записи перед URL.

    В описании электронного ресурса разделитель ``]. - URL:`` — часть
    принятого формата, а не тире в предложении.
    """
    before = text[:start].rstrip()
    after = text[end:].lstrip().lower()
    if before.endswith("]") or before.endswith("."):
        return after.startswith(("url", "http", "doi"))
    return False


_FALSE_POSITIVE_FILTERS = {
    "wrong_quotes": _quote_is_false_positive,
    "space_before_punctuation": _space_before_punctuation_is_false_positive,
    "wrong_dashes": _dash_is_false_positive,
}
"""Фильтры ложных срабатываний разметки по видам ошибок."""


def _span_spans(typo: object, check: MicroTypographyCheck) -> List[SpanLocation]:
    """Возвращает размеченные фрагменты указанного вида ошибки."""
    spans = getattr(typo, check, None)
    return list(spans or [])


def _collect_issues(
    paragraph: ParagraphBlock,
    checks: List[MicroTypographyCheck],
) -> Dict[str, List[SpanLocation]]:
    """Собирает отфильтрованные фрагменты ошибок абзаца по видам проверки."""
    typo = paragraph.typography
    if typo is None:
        return {}
    text = paragraph.text_visible or ""
    collected: Dict[str, List[SpanLocation]] = {}
    for check in checks:
        spans = _span_spans(typo, check)
        false_positive = _FALSE_POSITIVE_FILTERS.get(check)
        if false_positive is not None:
            spans = [
                span
                for span in spans
                if not false_positive(text, span.start_char, span.end_char)
            ]
        if spans:
            collected[check] = spans
    return collected


def _describe_issues(issues: Dict[str, List[SpanLocation]]) -> str:
    """Собирает короткое описание найденных ошибок для отчёта."""
    parts = [
        f"{CHECK_LABELS[check]}: {len(spans)}"
        for check, spans in issues.items()
    ]
    return "; ".join(parts)


def _examples(issues: Dict[str, List[SpanLocation]]) -> List[str]:
    """Возвращает несколько фрагментов текста с ошибками для отчёта."""
    examples: List[str] = []
    for spans in issues.values():
        for span in spans:
            snippet = " ".join((span.snippet or "").split())
            if snippet and snippet not in examples:
                examples.append(snippet)
            if len(examples) >= EXAMPLE_LIMIT:
                return examples
    return examples


def _check_microtypography_rule(
    rule: MicroTypographyRule,
    document: ParsedDocxDocument,
    contexts: Dict[str, str],
) -> Tuple[List[Violation], int, RuleAssessment]:
    """Ищет микротипографические ошибки в абзацах выбранной области.

    Область ``body_paragraphs`` берёт абзацы основного текста и подписей, а
    титульный лист, листинги и структурные названия отсекаются контекстом
    правила: там лишние пробелы и кавычки создаёт вёрстка, а не автор. Записи
    списка литературы пропускаются: инициалы и названия в них оформляет
    ``bibliography_format``, а не правило прозы. PDF не хранит
    микротипографику, поэтому проверка для PDF не оценивается.
    """
    checks = list(rule.params.checks)
    expected_text = "; ".join(CHECK_LABELS[check] for check in checks)
    if _is_pdf_only(document):
        return ([], 0, _counted_assessment(
            rule,
            [],
            0,
            expected_text=expected_text,
            confidence="low",
            empty_observed=(
                "PDF не сохраняет микротипографику: кавычки, пробелы и тире "
                "не измеряются"
            ),
            note="проверка микротипографики доступна только для DOCX",
        ))

    bibliography_ids = {
        entry.block_id for entry in document.cross_ref_index.bibliography
    }
    bibliography_ids.update(
        _structural_block_ids(document.document_tree, SKIPPED_TREE_ROLES)
    )
    targets = [
        paragraph
        for paragraph in _paragraph_targets(document, rule.target, contexts)
        if paragraph.block_id not in bibliography_ids
    ]
    violations: List[Violation] = []
    total_issues = 0
    issue_totals: Dict[str, int] = {check: 0 for check in checks}
    footnotes_scope = rule.target.scope == "footnotes"
    for paragraph in targets:
        issues = _collect_issues(paragraph, checks)
        if footnotes_scope:
            # Хвостовые пробелы сноски — след вёрстки, а не авторская ошибка:
            # в основном тексте их отсекает разбор абзаца, в сносках он их оставляет.
            text = paragraph.text_visible or ""
            trimmed = len(text.rstrip())
            issues = {
                check: [
                    span for span in spans
                    if not (
                        span.start_char >= trimmed
                        and not text[span.start_char:span.end_char].strip()
                    )
                ]
                for check, spans in issues.items()
            }
            issues = {check: spans for check, spans in issues.items() if spans}
        if not issues:
            continue
        issue_count = sum(len(spans) for spans in issues.values())
        total_issues += issue_count
        for check, spans in issues.items():
            issue_totals[check] += len(spans)
        examples = _examples(issues)
        found_text = _describe_issues(issues)
        if examples:
            found_text = f"{found_text} ({'; '.join(examples)})"
        violations.append(
            _build_violation(
                rule,
                address=paragraph.block_id,
                location=_paragraph_location(paragraph),
                expected={check: True for check in checks},
                found={
                    check: len(issues[check]) if check in issues else 0
                    for check in checks
                },
                expected_text=expected_text,
                found_text=found_text,
                occurrences=issue_count,
                confidence="high",
            )
        )

    assessment = _counted_assessment(
        rule,
        violations,
        len(targets),
        expected_text=expected_text,
        confidence="high",
        empty_observed="абзацы основного текста не найдены",
        details={
            "checks": checks,
            "by_check": issue_totals,
            "paragraphs_with_issues": len(violations),
            "issues": total_issues,
        },
    )
    return violations, len(targets), assessment
