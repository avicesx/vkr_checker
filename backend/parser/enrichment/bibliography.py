"""Извлечение списка литературы из готовой модели ParsedDocument.

Модуль не зависит от DOCX/PDF-конвейеров и от слоя ``citations``: на вход
подается ``ParsedDocument``, на выходе — записи ``BibliographyEntry``. Обход
дерева документа повторяет прием из ``citations.extract`` (раздел литературы
определяется по ``semantic_role``), но реализован здесь независимо, чтобы
слои можно было развивать отдельно.

Порядок работы:
    1. Находим блоки раздела (``semantic_role == "bibliography"``): прямые
       блоки узла и всех его потомков; если дерево не размечено прямыми
       блоками — берем диапазон от заголовка до следующего заголовка того же
       или высшего уровня (как в citations).
    2. Собираем записи: блок с номерным префиксом ("1.", "2)", "[3]")
       начинает новую запись, блок без префикса, идущий следом, склеивается с
       предыдущей (многострочная запись). Номер записи проверяется: он должен
       расти и не быть годом («2000.» в начале предложения — не номер). Серия
       из ``MAX_BIB_NON_ENTRY_RUN`` непохожих на запись блоков или следующий
       заголовок закрывают список, поэтому текст после литературы не
       приклеивается к последней записи.
    3. Из полного текста записи извлекаем год, URL, страницы, ISBN/ISSN и
       категорию.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Sequence, Set, Tuple

from parser.core.schema import (
    BibliographyEntry,
    DocumentTreeNode,
    ParagraphBlock,
    ParsedDocument,
)

#: Номерной префикс вручную набранной записи: "1.", "2)", "[3]".
RE_NUMBER_PREFIX = re.compile(
    r"^\s*(?P<raw>\[\s*\d+\s*\]|\d+\s*[.)])\s*(?P<body>\S.*)$",
    re.DOTALL,
)
#: Числовой маркер списка в ListItemMetadata.rendered_marker ("1.", "2)", "[3]").
RE_NUMERIC_MARKER = re.compile(r"^(?:\d+[.)]|\[\s*\d+\s*\])$")
#: Номерной префикс внутри абзаца: PDF-структура иногда склеивает несколько
#: записей в один блок ("... 2001.  49.Крылов И.В. ..."). Номер — не больше
#: трёх цифр и только перед заглавной буквой, чтобы не разрезать "С. 12",
#: "2000." и десятичные номера стандартов вроде "СанПиН 2.4.1.1249-03".
RE_INLINE_NUMBER_PREFIX = re.compile(
    r"(?<!\d)(?P<raw>\d{1,3}\s*[.)])\s*(?=[А-ЯЁA-Z])"
)
#: Сколько подряд непохожих на запись блоков означают конец списка литературы
#: (тот же приём, что в DOCX-парсере: текст следующего раздела не приклеивается).
MAX_BIB_NON_ENTRY_RUN = 2
#: Запись закончена: точка, восклицательный или вопросительный знак в конце.
RE_BIB_TERMINAL_END = re.compile(r"[.!?…][»\"']?\s*$")
#: Явный обрывок фразы, которым может начинаться перенос строки записи.
RE_BIB_FRAGMENT_START = re.compile(r"^[,;:—–\-)\]»]")
#: Сильные библиографические приметы продолжения: страницы, ISBN/ISSN, URL, «//».
#: Один только год приметой не считается, иначе к списку приклеивался бы текст
#: заключения, где годы упоминаются в обычных предложениях.
RE_BIB_STRONG_SIGNAL = re.compile(
    r"//|ISBN|ISSN|https?://|\b[СсPp]\.\s*\d|\d+\s*[сp]\."
)
#: Первый год в диапазоне 19xx/20xx (по постановке — именно первый).
RE_YEAR = re.compile(r"(?<!\d)(?:19\d{2}|20\d{2})(?!\d)")
#: URL: полная схема или www-форма.
RE_URL = re.compile(r"(?:https?://|www\.)[^\s)\]>\"'«»]+", re.IGNORECASE)
#: Страницы: "С. 12-15" или "123 с." (как в DOCX-парсере).
RE_PAGES = re.compile(r"\b(?:[СсPp]\.\s*\d+(?:\s*[—–-]\s*\d+)?|\d+\s*[сp]\.)")
#: ISBN/ISSN с номером.
RE_ISBN_ISSN = re.compile(
    r"\b(?:ISBN|ISSN)\s*[№:]?\s*[0-9Xx][0-9Xx\-–\s]{8,20}",
    re.IGNORECASE,
)
#: Дата обращения к электронному ресурсу.
RE_ACCESS_DATE = re.compile(
    r"дата\s+(?:обращения|доступа)\s*:?\s*\d{1,2}\.\d{1,2}\.\d{2,4}",
    re.IGNORECASE,
)
#: Разделитель "точка — тире — пробел" между областями описания.
RE_DOT_DASH = re.compile(r"\.\s*[—–-]\s+")
#: ГОСТ и родственные стандарты.
RE_GOST = re.compile(r"\bгост(?:\s*р)?\b|\bснип\b|\bсанпин\b", re.IGNORECASE)
#: Словесные признаки электронного ресурса.
RE_ELECTRONIC_HINT = re.compile(
    r"режим\s+доступа|электронн\w*\s+ресурс",
    re.IGNORECASE,
)
#: Обозначение вида текста по ГОСТ Р 7.0.5.
RE_TEXT_TYPE = re.compile(
    r"\[\s*электронный\s+ресурс\s*\]|текст\s*:\s*непосредственный",
    re.IGNORECASE,
)


@dataclass
class _EntryDraft:
    """Накопитель одной записи до склейки многострочного продолжения."""

    block_id: str
    raw_prefix: Optional[str]
    order_index: int
    parts: List[str] = field(default_factory=list)

    def add(self, text: str) -> None:
        """Добавляет продолжение записи, сохраняя порядок строк."""
        if text:
            self.parts.append(text)

    def raw_text(self) -> str:
        """Полный текст записи (строки склеены через пробел)."""
        return " ".join(part.strip() for part in self.parts if part.strip())


def extract_bibliography(document: ParsedDocument) -> List[BibliographyEntry]:
    """Извлекает записи списка литературы из разделов ``bibliography``.

    Функция ничего не меняет в документе: возвращает новый список записей с
    полями, которые можно вычислить без внешних источников. Если раздел не
    найден или пуст, возвращается пустой список.

    :param document: разобранный документ (DOCX, PDF или смешанный режим).
    :return: записи в порядке следования в документе.
    """
    blocks = _bibliography_blocks(document)
    entries: List[BibliographyEntry] = []
    draft: Optional[_EntryDraft] = None
    order_counter = 0
    non_entry_run = 0
    last_part_text = ""

    for block in blocks:
        text = block.text_visible.strip()
        if block.is_empty_or_whitespace or not text:
            continue
        if block.heading_info is not None and block.heading_info.is_heading:
            # Заголовок после записей открывает новый раздел: список закончился.
            if draft is not None or entries:
                break
            # Заголовок до записей (например, подраздел) записями не считаем.
            continue

        prefix, body = _split_entry_prefix(block, text)
        # PDF-структура может склеить несколько записей в один абзац: делим
        # текст по внутренним номерам, сохраняя префикс самого блока.
        stop = False
        for segment_index, (segment_prefix, segment_body) in enumerate(
            _split_inline_entries(body)
        ):
            effective_prefix = prefix if segment_index == 0 else segment_prefix
            number = (
                _entry_number(effective_prefix)
                if effective_prefix is not None
                else None
            )
            # Номер должен расти: год «2000.» в начале предложения, повтор
            # номера и номер вне разумного диапазона записью не считаются.
            if number is not None and number > order_counter:
                if draft is not None:
                    entries.append(_finalize_entry(draft))
                order_counter = number
                draft = _EntryDraft(
                    block_id=block.block_id,
                    raw_prefix=effective_prefix,
                    order_index=order_counter,
                    parts=[segment_body],
                )
                last_part_text = segment_body
                non_entry_run = 0
                continue

            if draft is not None and _looks_like_continuation(
                last_part_text, segment_body
            ):
                # Продолжение многострочной записи: склеиваем с предыдущей.
                draft.add(segment_body)
                last_part_text = segment_body
                non_entry_run = 0
                continue

            # Блок не похож на запись: несколько таких подряд означают конец
            # списка, иначе в последнюю запись попадал текст следующего раздела.
            non_entry_run += 1
            if non_entry_run >= MAX_BIB_NON_ENTRY_RUN:
                stop = True
                break
        if stop:
            break

    if draft is not None:
        entries.append(_finalize_entry(draft))
    return entries


def _bibliography_blocks(document: ParsedDocument) -> List[ParagraphBlock]:
    """Возвращает абзацы разделов литературы в порядке модели документа."""
    paragraphs = [b for b in document.blocks if isinstance(b, ParagraphBlock)]
    by_id = {paragraph.block_id: paragraph for paragraph in paragraphs}

    marked_ids: Set[str] = set()
    for node in _iter_tree(document.document_tree):
        if node.semantic_role != "bibliography":
            continue
        # Записи могут лежать как в самом узле, так и в его подразделах.
        for sub_node in _iter_tree([node]):
            marked_ids.update(sub_node.direct_block_ids)

    if marked_ids:
        marked = [paragraph for paragraph in paragraphs if paragraph.block_id in marked_ids]
        if marked:
            return marked

    # Дерево без прямых блоков: определяем диапазон раздела по заголовкам.
    return _blocks_in_section_range(paragraphs, document.document_tree)


def _blocks_in_section_range(
    paragraphs: Sequence[ParagraphBlock],
    document_tree: Sequence[DocumentTreeNode],
) -> List[ParagraphBlock]:
    """Блоки от заголовка литературы до следующего заголовка того же уровня.

    Прием повторяет ``citations.extract._bibliography_block_ids``, но здесь
    возвращаются сами абзацы раздела, а не их идентификаторы.
    """
    index_by_block = {
        paragraph.block_id: index for index, paragraph in enumerate(paragraphs)
    }
    marks: List[Tuple[int, DocumentTreeNode]] = []
    for node in _iter_tree(document_tree):
        index = index_by_block.get(node.heading_block_id or "")
        if index is not None:
            marks.append((index, node))
    marks.sort(key=lambda pair: pair[0])

    selected: Set[int] = set()
    for position, (start, node) in enumerate(marks):
        if node.semantic_role != "bibliography":
            continue
        end = len(paragraphs)
        for next_index, next_node in marks[position + 1:]:
            if next_node.level <= node.level:
                end = next_index
                break

        selected.update(range(start, end))

    return [paragraph for index, paragraph in enumerate(paragraphs) if index in selected]


def _iter_tree(nodes: Sequence[DocumentTreeNode]) -> Iterator[DocumentTreeNode]:
    """Обходит дерево документа в порядке следования узлов."""
    for node in nodes:
        yield node
        yield from _iter_tree(node.children)


def _split_entry_prefix(
    block: ParagraphBlock,
    text: str,
) -> Tuple[Optional[str], str]:
    """Отделяет номерной префикс записи от ее текста.

    Для автонумерации Word берется отрендеренный маркер списка; для ручной
    нумерации — ведущий префикс вида "1.", "2)" или "[3]". Маркер без номера
    (например, "•") префиксом не считается.
    """
    list_info = block.list_info
    if (
        list_info is not None
        and list_info.mechanism == "openxml_numPr"
        and list_info.rendered_marker.strip()
    ):
        marker = list_info.rendered_marker.strip()
        body = (list_info.body_text_without_marker or text).strip()
        if RE_NUMERIC_MARKER.match(marker) and body:
            return marker, body
        return None, body or text

    match = RE_NUMBER_PREFIX.match(text)
    if match is not None:
        return match.group("raw").strip(), match.group("body").strip()
    return None, text


def _split_inline_entries(text: str) -> List[Tuple[Optional[str], str]]:
    """Делит текст абзаца на несколько записей по внутренним номерам.

    Возвращает пары ``(префикс, текст)``; у первого фрагмента префикс ``None``,
    если номер стоит не с начала текста — тогда это продолжение предыдущей
    записи, а не новая запись.
    """
    matches = list(RE_INLINE_NUMBER_PREFIX.finditer(text))
    if not matches:
        return [(None, text)]

    segments: List[Tuple[Optional[str], str]] = []
    if matches[0].start() > 0:
        head = text[: matches[0].start()].strip()
        if head:
            segments.append((None, head))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        if body:
            segments.append((match.group("raw").strip(), body))
    return segments or [(None, text)]


def _entry_number(prefix: str) -> Optional[int]:
    """Номер записи из префикса или ``None``, если это не номер списка.

    Отбрасываются годы («2000.» в начале предложения), нулевые номера и
    значения вне разумного диапазона списка литературы.
    """
    digits = re.sub(r"\D", "", prefix)
    if not digits:
        return None
    value = int(digits)
    if not 1 <= value <= 999:
        return None
    if 1900 <= value <= 2099:
        return None
    return value


def _looks_like_continuation(prev_text: str, cur_text: str) -> bool:
    """True, если блок продолжает предыдущую запись (перенос строки).

    Перенос строки узнаётся по обрывку фразы, незаконченному предложению,
    строчной букве в начале или сильным библиографическим приметам (страницы,
    ISBN, URL, «//»). Один год приметой не считается: иначе к списку
    приклеивался бы текст заключения, где годы упоминаются в предложениях.
    """
    if not prev_text or not cur_text:
        return False
    if RE_BIB_FRAGMENT_START.match(cur_text):
        return True
    if not RE_BIB_TERMINAL_END.search(prev_text):
        return True
    if RE_BIB_STRONG_SIGNAL.search(cur_text):
        return True
    return cur_text[:1].islower()


def _finalize_entry(draft: _EntryDraft) -> BibliographyEntry:
    """Считает производные поля записи по ее полному тексту."""
    raw_text = draft.raw_text()

    year_match = RE_YEAR.search(raw_text)
    year_value = int(year_match.group(0)) if year_match is not None else None

    pages_match = RE_PAGES.search(raw_text)
    pages_value = pages_match.group(0).strip() if pages_match is not None else None

    isbn_match = RE_ISBN_ISSN.search(raw_text)
    isbn_value = isbn_match.group(0).strip() if isbn_match is not None else None

    urls = [url.rstrip(".,;:") for url in RE_URL.findall(raw_text)]
    urls = [url for url in urls if url]

    access_match = RE_ACCESS_DATE.search(raw_text)
    access_value = access_match.group(0).strip() if access_match is not None else None

    # Слэш ответственности ищем вне URL: там "//" — часть схемы.
    text_without_urls = RE_URL.sub("", raw_text).replace("//", "")

    return BibliographyEntry(
        bib_id=f"bib_{draft.order_index:03d}",
        block_id=draft.block_id,
        order_index=draft.order_index,
        raw_number_prefix=draft.raw_prefix,
        raw_text=raw_text,
        detected_category=_detect_category(  # type: ignore[arg-type]
            raw_text, bool(urls), isbn_value
        ),
        has_slash_responsibility="/" in text_without_urls,
        has_double_slash_source="//" in raw_text,
        dot_dash_count=len(RE_DOT_DASH.findall(raw_text)),
        year_extracted=year_value,
        pages_extracted=pages_value,
        isbn_or_issn=isbn_value,
        urls=urls,
        access_date_raw=access_value,
        has_text_type_designation=bool(RE_TEXT_TYPE.search(raw_text)),
    )


def _detect_category(
    raw_text: str,
    has_url: bool,
    isbn_or_issn: Optional[str],
) -> str:
    """Базовая эвристика категории источника (без глубокого разбора описания).

    ГОСТ и родственные стандарты важнее ссылки; наличие URL или словесных
    признаков электронного ресурса дает ``electronic_resource``; ISBN/ISSN без
    URL трактуем как книжное издание; остальное — ``unknown``.
    """
    if RE_GOST.search(raw_text):
        return "standard_gost"
    if has_url or RE_ELECTRONIC_HINT.search(raw_text):
        return "electronic_resource"
    if isbn_or_issn:
        return "book_monograph"
    return "unknown"
