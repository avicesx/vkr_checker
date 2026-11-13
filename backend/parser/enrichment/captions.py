"""Извлечение подписей рисунков, таблиц и приложений из ``ParsedDocument``.

Модуль — общий слой обогащения для DOCX- и PDF-веток разбора. Подпись
распознается по тексту абзаца, а не по стилям и структуре, поэтому одинаково
работает в ``pdf_only``, где стили и нумерация DOCX недоступны.

Поддерживаемые формы подписей:

* ``Рисунок 1 — Схема`` / ``Рис. 1. Схема`` — рисунок;
* ``Таблица 2 — Данные`` / ``Табл. 2. Данные`` — таблица;
* ``Приложение А. Текст`` / ``Приложение Б Текст`` — приложение.

Строка без номера (``Рисунок не найден``) подписью не считается. Для рисунка
дополнительно подбирается иллюстрация из ``document.visuals``: кандидаты
ограничены той же страницей и еще не привязанной подписью, ближайший
определяется по вертикальным координатам ``pdf_y_*``, если они известны.
Поле ``is_mentioned_in_text`` намеренно не заполняется — упоминания в тексте
разрешает отдельный этап обогащения.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from parser.core.schema import (
    BlockUnion,
    NumberedCaptionEntity,
    ParagraphBlock,
    ParsedDocument,
    TableBlock,
    VisualObject,
)

#: Подпись рисунка: «Рисунок 1 — Схема», «Рис. 1. Схема», «Рис 1.Схема».
#: После номера допустимы тире/точка/двоеточие ИЛИ просто пробелы: в PDF
#: подпись часто набрана без разделителя («Рис 1.Структура управления»).
RE_FIGURE_CAPTION = re.compile(
    r"^\s*(?P<prefix>рисунок|рис\.?)"
    r"\s*(?P<num>[А-ЯЁA-Z]?\s*\d+(?:\.\d+)*)"
    r"(?:(?:\s*(?P<sep>[—–.:\-])\s*|\s+)(?P<title>.*))?$",
    re.IGNORECASE,
)

#: Подпись таблицы: «Таблица 2 — Данные», «Табл. 2. Данные», «Таблица 2  Данные».
#: Пробельный разделитель так же допустим, как и пунктуационный; склейка
#: «Таблица 2Данные» подписью не считается.
RE_TABLE_CAPTION = re.compile(
    r"^\s*(?P<prefix>таблица|табл\.?)"
    r"\s*(?P<num>[А-ЯЁA-Z]?\s*\d+(?:\.\d+)*)"
    r"(?:(?:\s*(?P<sep>[—–.:\-])\s*|\s+)(?P<title>.*))?$",
    re.IGNORECASE,
)

#: Заголовок приложения: «Приложение А. Текст», «Приложение Б Текст», «Приложение 1».
RE_APPENDIX_CAPTION = re.compile(
    r"^\s*(?P<prefix>приложение)\s+"
    r"(?P<num>[А-ЯЁA-Z0-9]+(?:\.[0-9]+)*)"
    r"(?:\s*(?P<sep>[—–.:\-])?\s*(?P<title>.*))?$",
    re.IGNORECASE,
)


def _iter_paragraphs(blocks: Iterable[BlockUnion]) -> Iterable[ParagraphBlock]:
    """Обходит абзацы в порядке чтения, включая абзацы внутри ячеек таблиц.

    Оглавление (``TocContainerBlock``) не раскрывается: его строки похожи на
    подписи, но описывают структуру, а не сам рисунок или таблицу.
    """
    for block in blocks:
        if isinstance(block, ParagraphBlock):
            yield block
        elif isinstance(block, TableBlock):
            for row in block.rows:
                for cell in row.cells:
                    yield from _iter_paragraphs(cell.blocks)


def _normalize_text(text: str) -> str:
    """Сводит переводы строк и повторные пробелы к одному пробелу."""
    normalized = (text or "").replace("\u00a0", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", normalized).strip()


def _is_heading(block: ParagraphBlock) -> bool:
    """True, если абзац размечен как заголовок (подписью он быть не может)."""
    return block.heading_info is not None and block.heading_info.is_heading


def _is_appendix_heading(block: ParagraphBlock) -> bool:
    """True, если абзац размечен как заголовок приложения."""
    return (
        block.heading_info is not None
        and block.heading_info.heading_kind == "appendix_heading"
    )


def _classify_numbering_scheme(number_str: str) -> str:
    """Определяет схему нумерации подписи по ее номеру."""
    clean = number_str.strip(".")
    if re.fullmatch(r"\d+", clean):
        return "continuous_arabic"
    if re.fullmatch(r"\d+\.\d+(?:\.\d+)*", clean):
        return "section_based"
    if re.fullmatch(r"[А-ЯЁA-Z]\.\d+", clean, re.IGNORECASE):
        return "appendix_based"
    return "invalid"


def _split_title(title_raw: str) -> Tuple[str, bool]:
    """Отделяет текст подписи от завершающей точки.

    Возвращает очищенный текст и признак того, что исходный текст заканчивался
    точкой.
    """
    title = title_raw.strip()
    has_trailing_dot = title.endswith(".")
    return title.rstrip(".").strip(), has_trailing_dot


def _vertical_distance(
    caption_block: ParagraphBlock, visual: VisualObject
) -> Optional[float]:
    """Оценивает вертикальное расстояние между подписью и иллюстрацией.

    Используются центры объектов, если известны обе границы; иначе — ближайшая
    доступная пара координат ``pdf_y_*``. ``None`` означает, что координат нет
    и сравнивать объекты по геометрии нельзя.
    """
    cap_top, cap_bottom = caption_block.pdf_y_top_mm, caption_block.pdf_y_bottom_mm
    vis_top, vis_bottom = visual.pdf_y_top_mm, visual.pdf_y_bottom_mm

    if None not in (cap_top, cap_bottom, vis_top, vis_bottom):
        cap_center = (cap_top + cap_bottom) / 2.0  # type: ignore[operator]
        vis_center = (vis_top + vis_bottom) / 2.0  # type: ignore[operator]
        return abs(cap_center - vis_center)

    if cap_top is not None and vis_bottom is not None:
        return abs(cap_top - vis_bottom)
    if cap_bottom is not None and vis_top is not None:
        return abs(vis_top - cap_bottom)

    cap_y = cap_top if cap_top is not None else cap_bottom
    vis_y = vis_top if vis_top is not None else vis_bottom
    if cap_y is not None and vis_y is not None:
        return abs(cap_y - vis_y)
    return None


def _link_figure_visual(
    caption_block: ParagraphBlock, visuals: Dict[str, VisualObject]
) -> Optional[str]:
    """Находит ближайшую иллюстрацию на странице подписи и связывает ее.

    Кандидаты — ``VisualObject`` с тем же ``page_est``, у которых еще нет
    ``caption_block_id`` (либо он уже указывает на эту же подпись — повторный
    вызов слоя не должен терять связь). При известных координатах выбирается
    ближайший по вертикали, иначе — первый в порядке документа. При привязке
    заполняется ``target_object_id`` сущности и ``caption_block_id`` визуала,
    чтобы одну иллюстрацию не забрали две подписи.
    """
    page = caption_block.page_start_est
    candidates: List[Tuple[int, str, VisualObject]] = [
        (order, visual_id, visual)
        for order, (visual_id, visual) in enumerate(visuals.items())
        if visual.page_est == page
        and (
            not visual.caption_block_id
            or visual.caption_block_id == caption_block.block_id
        )
    ]
    if not candidates:
        return None

    best_visual_id: Optional[str] = None
    best_key: Optional[Tuple[int, float, int]] = None
    for order, visual_id, visual in candidates:
        distance = _vertical_distance(caption_block, visual)
        # Кандидаты с координатами предпочтительнее: (0, расстояние, порядок).
        key = (0, distance, order) if distance is not None else (1, 0.0, order)
        if best_key is None or key < best_key:
            best_key = key
            best_visual_id = visual_id

    if best_visual_id is not None:
        visuals[best_visual_id].caption_block_id = caption_block.block_id
    return best_visual_id


def _build_entity(
    entity_id: str,
    entity_type: str,
    caption_block: ParagraphBlock,
    prefix_word: str,
    number_str: str,
    separator: Optional[str],
    title_text: str,
    has_trailing_dot: bool,
    numbering_scheme: str,
    target_object_id: Optional[str] = None,
) -> NumberedCaptionEntity:
    """Собирает сущность подписи с общими для всех типов полями."""
    return NumberedCaptionEntity(
        entity_id=entity_id,
        entity_type=entity_type,  # type: ignore[arg-type]
        target_object_id=target_object_id,
        caption_block_id=caption_block.block_id,
        page_est=caption_block.page_start_est,
        prefix_word=prefix_word,
        number_str=number_str,
        numbering_scheme=numbering_scheme,  # type: ignore[arg-type]
        separator=separator,
        title_text=title_text,
        has_trailing_dot=has_trailing_dot,
    )


@dataclass(frozen=True)
class _CaptionCandidate:
    """Кандидат в подпись до дедупликации и присвоения идентификатора."""

    order: int
    entity_type: str
    block: ParagraphBlock
    prefix_word: str
    number_str: str
    separator: Optional[str]
    title_text: str
    has_trailing_dot: bool


#: Глаголы, с которых начинается предложение основного текста, а не название
#: объекта. Нужны для пробельной формы подписи без явного разделителя.
_SENTENCE_VERBS = re.compile(
    r"^(?:показывает|содержит|отражает|представляет|демонстрирует|"
    r"приведен|приведён|описан|изображен|изображён)\b",
    re.IGNORECASE,
)


def _looks_like_sentence(title: str) -> bool:
    """True, если продолжение строки похоже на предложение, а не на заголовок.

    Проверяется только форма без явного разделителя: «Таблица 1 показывает
    динамику…» — это текст, а «Таблица 1 Анализ рентабельности» — подпись.
    """
    if not title:
        return False
    if title[:1].islower():
        return True
    return bool(_SENTENCE_VERBS.match(title))


def _deduplicate_candidates(
    candidates: List[_CaptionCandidate],
) -> List[_CaptionCandidate]:
    """Убирает дубли номеров, оставляя кандидата с явным разделителем.

    Дубли появляются из-за ложных пробельных форм («Таблица 1 показывает…»
    рядом с настоящей «Таблица 1 — …»). Если разделитель есть у нескольких
    кандидатов, сохраняется первый по порядку в документе.
    """
    chosen: Dict[Tuple[str, str], _CaptionCandidate] = {}
    for candidate in candidates:
        key = (candidate.entity_type, candidate.number_str)
        previous = chosen.get(key)
        if previous is None or (
            previous.separator is None and candidate.separator is not None
        ):
            chosen[key] = candidate
    return sorted(chosen.values(), key=lambda item: item.order)


def extract_captions(document: ParsedDocument) -> List[NumberedCaptionEntity]:
    """Извлекает подписи рисунков, таблиц и приложений из документа.

    Возвращает сущности в порядке появления в тексте; идентификаторы сквозные
    внутри типа (``fig_001``, ``tbl_001``, ``app_001``). Для рисунков
    дополнительно заполняется ``target_object_id`` — ближайшая иллюстрация на
    той же странице. Поле ``is_mentioned_in_text`` не заполняется.

    Кандидаты без явного разделителя отбрасываются, если продолжение похоже на
    предложение, а повторные номера одного типа дедуплицируются.
    """
    candidates: List[_CaptionCandidate] = []

    for block in _iter_paragraphs(document.blocks):
        text = _normalize_text(block.text_visible or block.text_raw)
        if not text:
            continue

        m_fig = RE_FIGURE_CAPTION.match(text)
        if m_fig and not _is_heading(block):
            separator = m_fig.group("sep")
            title_text, has_trailing_dot = _split_title(m_fig.group("title") or "")
            if separator is None and _looks_like_sentence(title_text):
                continue
            candidates.append(
                _CaptionCandidate(
                    order=len(candidates),
                    entity_type="figure",
                    block=block,
                    prefix_word=m_fig.group("prefix").strip(),
                    number_str=re.sub(r"\s+", "", m_fig.group("num")).upper(),
                    separator=separator,
                    title_text=title_text,
                    has_trailing_dot=has_trailing_dot,
                )
            )
            continue

        m_tbl = RE_TABLE_CAPTION.match(text)
        if m_tbl and not _is_heading(block):
            separator = m_tbl.group("sep")
            title_text, has_trailing_dot = _split_title(m_tbl.group("title") or "")
            if separator is None and _looks_like_sentence(title_text):
                continue
            candidates.append(
                _CaptionCandidate(
                    order=len(candidates),
                    entity_type="table",
                    block=block,
                    prefix_word=m_tbl.group("prefix").strip(),
                    number_str=re.sub(r"\s+", "", m_tbl.group("num")).upper(),
                    separator=separator,
                    title_text=title_text,
                    has_trailing_dot=has_trailing_dot,
                )
            )
            continue

        m_app = RE_APPENDIX_CAPTION.match(text)
        if m_app:
            title_raw = (m_app.group("title") or "").strip()
            separator = m_app.group("sep")
            # Без разделителя заголовком приложения считаем только разметку
            # заголовка или текст с прописной буквы: «Приложение А содержит…» —
            # это предложение основного текста, а не заголовок приложения.
            if (
                not _is_appendix_heading(block)
                and separator is None
                and title_raw
                and title_raw[:1].islower()
            ):
                continue

            title_text, has_trailing_dot = _split_title(title_raw)
            candidates.append(
                _CaptionCandidate(
                    order=len(candidates),
                    entity_type="appendix",
                    block=block,
                    prefix_word=m_app.group("prefix").strip(),
                    number_str=m_app.group("num").upper(),
                    separator=separator,
                    title_text=title_text,
                    has_trailing_dot=has_trailing_dot,
                )
            )

    entities: List[NumberedCaptionEntity] = []
    sequences = {"figure": 0, "table": 0, "appendix": 0}
    id_prefixes = {"figure": "fig", "table": "tbl", "appendix": "app"}
    for candidate in _deduplicate_candidates(candidates):
        sequences[candidate.entity_type] += 1
        target_object_id = (
            _link_figure_visual(candidate.block, document.visuals)
            if candidate.entity_type == "figure"
            else None
        )
        entities.append(
            _build_entity(
                entity_id=(
                    f"{id_prefixes[candidate.entity_type]}_"
                    f"{sequences[candidate.entity_type]:03d}"
                ),
                entity_type=candidate.entity_type,
                caption_block=candidate.block,
                prefix_word=candidate.prefix_word,
                number_str=candidate.number_str,
                separator=candidate.separator,
                title_text=candidate.title_text,
                has_trailing_dot=candidate.has_trailing_dot,
                numbering_scheme=(
                    "appendix_based"
                    if candidate.entity_type == "appendix"
                    else _classify_numbering_scheme(candidate.number_str)
                ),
                target_object_id=target_object_id,
            )
        )

    return entities


__all__ = ["extract_captions"]
