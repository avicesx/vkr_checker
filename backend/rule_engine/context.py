"""Детерминированный контекст блоков: титульный лист, листинг кода и обычный текст.

Прогон `rule_engine` по реальной ВКР дал 978 «нарушений», которые относятся не к
оформлению основного текста, а к служебным зонам документа:

* титульный лист — реквизиты вуза (11 pt, Normal, маркеры «Министерство»,
  «кафедра», «дипломная») и подписные строки (10 pt, ``(М.П.) (Подпись)``);
* листинги C# — стиль 6112, Courier New 12 pt, строки ``public class``,
  ``private const``, ``//``, ``{``.

Правила «основной текст» не должны их проверять, но и выключать правила для
всего документа нельзя. Поэтому каждый блок получает контекст:

``body``
    Обычный текст: правила применяются без изменений.
``title_page``
    Титульный лист, сервисные листы (задание, календарный план, акт внедрения,
    аннотация, реферат, отзыв) и подписные строки: шрифт, кегль и отступы здесь
    заданы методичкой на служебные страницы, а не требованиями к основному
    тексту. Титулом блок считается только при наличии реквизита (министерство,
    кафедра, студент, «(М.П.)», подпись): мелкий кегль и короткая строка без
    точки в конце сами по себе встречаются в обычном тексте.
``code_listing``
    Фрагмент листинга: моноширинные шрифты и отступы кода проверять не нужно.

Классификация полностью детерминирована: используются только поля
`ParagraphBlock` (выравнивание, раны, текст) и позиция блока в документе. Нет
внешних моделей, обращений к сети, времени или случайности, поэтому один и тот
же документ всегда дает один и тот же результат, а каждое решение объясняется
набором сработавших сигналов.

Модуль намеренно консервативен: сомнительный блок остается ``body``, потому что
пропустить нарушение дешевле, чем скрыть его. Заголовки (``heading_info``) и
подписи рисунков, таблиц и приложений не классифицируются никогда — у них свои
правила и свои требования к оформлению.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Literal, Optional, Sequence

from parser.core.schema import BlockUnion, ParagraphBlock, TocContainerBlock

from ._fonts import family_keys


BlockContext = Literal["body", "title_page", "code_listing"]
"""Контекст блока: обычный текст, титульный лист или листинг кода."""

# =====================================================================
# 1. ЛИСТИНГИ КОДА
# =====================================================================

#: Гарнитуры, которыми Word набирает листинги. Сравнение идет по нормализованному
#: имени и допускает хвостовые уточнения вроде «Courier New CYR».
MONOSPACE_FONTS: frozenset[str] = frozenset(
    {
        "courier",
        "courier new",
        "consolas",
        "lucida console",
        "lucida sans typewriter",
        "pt mono",
        "jetbrains mono",
        "source code pro",
        "cascadia",
        "cascadia code",
        "cascadia mono",
        "fira code",
        "fira mono",
        "ibm plex mono",
        "roboto mono",
        "ubuntu mono",
        "dejavu sans mono",
        "liberation mono",
        "noto sans mono",
        "anonymous pro",
        "inconsolata",
        "hack",
        "menlo",
        "monaco",
        "sfmono-regular",
    }
)

#: Доля видимых символов абзаца, набранных моноширинной гарнитурой, при которой
#: блок считается листингом. Моноширинный шрифт — главный признак кода в ВКР:
#: в отчете все листинги набраны Courier New 12 pt.
MONOSPACE_CHAR_SHARE_THRESHOLD = 0.80

#: Доля строк с код-синтаксисом, при которой абзац в пропорциональной гарнитуре
#: считается листингом (запасной путь, если стиль листинга не задан).
CODE_LINE_SHARE_THRESHOLD = 0.60

#: Сколько «средних» строк (оператор с вызовом или присваиванием) нужно, чтобы
#: классифицировать абзац без сильных признаков кода: одна такая строка слишком
#: похожа на обычное предложение с точкой с запятой в конце.
CODE_MIN_MEDIUM_LINES = 2

#: Доля моноширинных символов, при которой абзац внутри региона листингов
#: считается его продолжением (моноширинная строка, разорванная на два абзаца).
CODE_REGION_FILLER_MONOSPACE_SHARE = 0.50

#: Комментарий или строка блочного комментария в начале строки.
RE_CODE_COMMENT = re.compile(r"^\s*(?://|/\*|\*/)")

#: Директива препроцессора C/C++/C#.
RE_CODE_PREPROCESSOR = re.compile(
    r"^\s*#\s*(?:include|define|pragma|region|ifdef|ifndef|endif|import)\b"
)

#: Объявление пространства имен.
RE_CODE_NAMESPACE = re.compile(r"^\s*namespace\s+[A-Za-z_][A-Za-z0-9_.]*")

#: Директива using с завершающей точкой с запятой.
RE_CODE_USING = re.compile(
    r"^\s*using\s+(?:static\s+)?[A-Za-z_][A-Za-z0-9_.]*"
    r"(?:\s*=\s*[A-Za-z_][A-Za-z0-9_.]*)?\s*;"
)

#: Объявление типа: `class X`, `struct X`, `interface X`, `enum X`, `record X`.
RE_CODE_TYPE_DECL = re.compile(
    r"\b(?:class|struct|interface|enum|record)\s+[A-Za-z_][A-Za-z0-9_]*"
)

#: Строка, начинающаяся с модификатора доступа/объявления поля или метода.
#: Требует латинских идентификаторов, поэтому русское предложение,
#: начинающееся со слова «Приватный», не срабатывает.
RE_CODE_DECLARATION = re.compile(
    r"^\s*(?:\[[^\]\n]*\]\s*)?"
    r"(?:(?:public|private|protected|internal|static|sealed|abstract|partial|"
    r"readonly|const|override|virtual|async|extern|unsafe|volatile)\s+)+"
    r"(?:[A-Za-z_][A-Za-z0-9_<>,\[\]\.?]*\s+)?"
    r"[A-Za-z_][A-Za-z0-9_]*\b"
)

#: Строка только из скобок и разделителей: `{`, `}`, `});`. Запятая и двоеточие
#: сюда не входят: одиночная «,» — артефакт разбора, а не строка кода.
RE_CODE_PUNCTUATION_LINE = re.compile(r"^\s*[{}()\[\];]+\s*$")

#: Латиница, за которой сразу идет вызов, присваивание или индекс. Нужна как
#: доказательство, что строка с `;` — оператор, а не русское предложение.
RE_ASCII_CODE_TOKEN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*\s*[\(=\[\]]"
    r"|\b(?:return|throw|await|yield|new|var|delete|break|continue)\b"
)


# =====================================================================
# 2. ТИТУЛЬНЫЙ ЛИСТ
# =====================================================================

#: Титульный лист занимает первые страницы документа, поэтому при
#: классификации одиночного блока (``classify_paragraph`` без документа) сигналы
#: титула действуют только на первых страницах: соседних блоков, по которым
#: видно конец титула, в этом режиме нет. Региональный проход эту оценку не
#: использует — там зона закрывается первым структурным заголовком, оглавлением
#: или началом основного текста (``classify_document_contexts``).
TITLE_PREFIX_MAX_PAGE = 2

#: Кегль, начиная с которого строка еще может быть титульной: на титуле
#: методичка разрешает 10–12 pt, основной текст набирается 14 pt.
TITLE_MAX_SIZE_PT = 12.0

#: Минимум сигналов титула. Один сигнал слишком часто встречается в обычном
#: тексте (центрированная подпись, короткая строка), поэтому их нужно два.
TITLE_MIN_SIGNALS = 2

#: Обязательный сигнал титульного листа. Мелкий кегль и короткая строка без
#: завершающей точки встречаются и в основном тексте (абзац 11 pt в начале
#: документа), поэтому без реквизита вуза/подписи блок титулом не считается:
#: иначе фильтр скрывал бы настоящие нарушения шрифта и отступа.
TITLE_REQUIRED_SIGNALS: frozenset[str] = frozenset({"marker"})

#: «Короткая строка» титула: длинная фраза без точки в конце — уже не титул.
TITLE_SHORT_LINE_MAX_CHARS = 80

#: Максимальная длина реквизита сервисной зоны: тема работы, пункт задания,
#: подписная строка. Все, что длиннее, — абзац, а не реквизит.
SERVICE_ZONE_MAX_LINE_CHARS = 300

#: Длина строки, с которой она считается предложением основного текста, а не
#: реквизитом. В сервисных зонах (лист задания, акт, реферат) длинные строки с
#: точкой в конце — это уже текст, а не служебные реквизиты.
SERVICE_ZONE_PROSE_MIN_CHARS = 100

#: Сколько подряд идущих предложений основного текста закрывают сервисный лист:
#: одиночное длинное предложение встречается и в самом листе (пункт задания),
#: и закрывать по нему зону рано.
SERVICE_ZONE_PROSE_MAX_STREAK = 2

#: Максимальная длина строки-заголовка сервисного листа.
SERVICE_SHEET_TITLE_MAX_CHARS = 80

#: Маркеры титульного листа: реквизиты вуза, тема, исполнитель, подписи.
RE_TITLE_MARKER = re.compile(
    r"министерств"
    r"|университет"
    r"|институт"
    r"|академи"
    r"|кафедр"
    r"|направлени"
    r"|специальност"
    r"|студент"
    r"|руководител"
    r"|консультант"
    r"|нормоконтрол"
    r"|допустить|допущен"
    r"|защите|защиты"
    r"|на\s+выполнение"
    r"|календарный\s+план"
    r"|задани[ея]\s+на\s+тему"
    r"|ф\.\s*и\.\s*о\."
    r"|должност"
    r"|м\.\s*п\."
    r"|подпис"
    r"|групп"
    r"|оценк"
    r"|\bгод\b"
    r"|дипломн"
    r"|выпускн"
    r"|квалификационн"
    r"|бакалавр|магистр"
    r"|ректор|декан|заведующ"
    r"|член\s+(?:гэк|комисси)"
    r"|федеральн|государственн"
    r"|имени",
    re.IGNORECASE,
)

#: Заголовок сервисного листа: задание, календарный план, акт внедрения или
#: приемки, аннотация, реферат, отзыв. Такой лист оформлен по требованиям
#: методички к служебным страницам, и в ВКР он может стоять в любом месте
#: (лист задания после титула, акт внедрения в приложении). Совпадение строгое:
#: подпись приложения «Техническое задание, лист 1» листом задания не считается,
#: потому что начинается не с маркера, а строка основного текста с точкой в
#: конце — тем более.
RE_SERVICE_SHEET_TITLE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*[.)]?\s*)?(?:"
    r"задани[ея]"
    r"|календарный\s+план"
    r"|акт\b"
    r"|аннотаци[яи]"
    r"|реферат"
    r"|отзыв"
    r"|выпускная\s+квалификационная\s+работа"
    r"|дипломн\S*\s+(?:работа|проект)"
    r"|курсовая\s+работа"
    r"|пояснительная\s+записка"
    r")(?=\s|$|[:,.;])",
    re.IGNORECASE,
)

#: Строка только из подписного реквизита: «(подпись)», «Подпись», «(М.П.)».
#: Такой блок — реквизит служебной страницы в любом месте документа: в акте
#: внедрения, в листе задания, в конце заключения.
RE_SIGNATURE_REQUISITE = re.compile(
    r"^\(?\s*(?:подпись|подписи|м\.\s*п\.|м\.п\.)\s*\)?$",
    re.IGNORECASE,
)

#: Название элемента структуры работы: такие строки закрывают сервисную зону,
#: даже если разбор не распознал в них заголовок.
RE_STRUCTURAL_TITLE = re.compile(
    r"^(?:"
    r"введение"
    r"|заключение"
    r"|содержание"
    r"|оглавление"
    r"|выводы"
    r"|список\s+(?:использованных\s+)?(?:источников|литературы)"
    r"|приложени[ея]"
    r")(?:\s|$|[:,.;])",
    re.IGNORECASE,
)

#: Строка-зачин оглавления: «СОДЕРЖАНИЕ», «Оглавление:». Это не реквизит титула
#: и не строка сервисного листа.
RE_TOC_TITLE = re.compile(r"^(?:содержание|оглавление)\s*:?$", re.IGNORECASE)


#: Подпись рисунка/таблицы/приложения. Такие абзацы классифицируются как body
#: всегда: их оформление проверяют правила подписей, а не основной текст.
#: Завершающий lookahead, а не ``\b``: после сокращения «рис.» стоит пробел, и
#: граница слова там не срабатывает.
RE_CAPTION_MARKER = re.compile(
    r"^\s*(?:"
    r"рисунок|рис\.|скриншот|скрин\.|снимок\s+экрана|"
    r"таблица|табл\.|продолжение\s+таблицы|окончание\s+таблицы|формула|"
    r"приложение\s+[А-ЯЁA-Z0-9]+"
    r")(?=\s|$)",
    re.IGNORECASE,
)


# =====================================================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================


#: Ключи моноширинных гарнитур: имя без subset-префикса и стилевых маркеров.
#: PDF отдает запись шрифта без пробелов («CourierNew», «ABCDEF+CourierNew»),
#: поэтому сравнение идет по тем же ключам, что и в правиле шрифта.
MONOSPACE_FONT_KEYS: frozenset[str] = frozenset(
    key for family in MONOSPACE_FONTS for key in family_keys(family)
)


def _is_monospace_font(name: Optional[str]) -> bool:
    """True, если гарнитура моноширинная (с учетом записи PDF и уточнений вроде CYR)."""
    keys = family_keys(name)
    if not keys:
        return False
    return any(key.startswith(mono) for key in keys for mono in MONOSPACE_FONT_KEYS)


def _visible_char_count(text: str) -> int:
    """Число видимых символов строки: пробелы и переводы строк не считаются."""
    return sum(1 for char in text if not char.isspace())


def _iter_visible_runs(paragraph: ParagraphBlock):
    """Перебирает раны с видимым текстом, пропуская скрытый текст."""
    for run in paragraph.runs:
        text = run.visible_text or run.text
        if not text or not text.strip():
            continue
        if run.effective_props.vanish_hidden:
            continue
        yield run, text


def _monospace_char_share(paragraph: ParagraphBlock) -> float:
    """Доля видимых символов абзаца, набранных моноширинной гарнитурой."""
    total = 0
    monospace = 0
    for run, text in _iter_visible_runs(paragraph):
        chars = _visible_char_count(text)
        total += chars
        props = run.effective_props
        if _is_monospace_font(props.font_ascii) or _is_monospace_font(props.font_hansi):
            monospace += chars
    if total == 0:
        return 0.0
    return monospace / total


def _dominant_font_size_pt(paragraph: ParagraphBlock) -> Optional[float]:
    """Кегль, которым набрана большая часть видимых символов абзаца."""
    weights: Dict[float, int] = {}
    for run, text in _iter_visible_runs(paragraph):
        size = round(float(run.effective_props.size_pt), 1)
        weights[size] = weights.get(size, 0) + _visible_char_count(text)
    if weights:
        # При равенстве весов берем меньший кегль: результат должен быть
        # воспроизводимым, а не зависеть от порядка ранов.
        return min(weights.items(), key=lambda item: (-item[1], item[0]))[0]
    mark = paragraph.para_mark_font_effective
    if mark is not None:
        return round(float(mark.size_pt), 1)
    return None


def _is_caption_text(text: str) -> bool:
    """True, если текст начинается с маркера подписи рисунка, таблицы или приложения."""
    return bool(RE_CAPTION_MARKER.match(text or ""))


def _is_short_line_without_terminal(text: str) -> bool:
    """True для короткой строки без завершающей точки, «!», «?» или двоеточия.

    Двоеточие в конце — признак строки-зачина («Содержание:»), а не реквизита
    титульного листа, поэтому такая строка сигналом титула не считается.
    """
    collapsed = " ".join((text or "").split())
    if not collapsed or len(collapsed) > TITLE_SHORT_LINE_MAX_CHARS:
        return False
    return not collapsed.endswith((".", "!", "?", "…", ":"))


def _title_page_signals(paragraph: ParagraphBlock) -> List[str]:
    """Возвращает сработавшие сигналы титульного листа (для отладки и порога)."""
    signals: List[str] = []
    if paragraph.effective_props.alignment == "center":
        signals.append("center")
    size = _dominant_font_size_pt(paragraph)
    if size is not None and size <= TITLE_MAX_SIZE_PT:
        signals.append("small_size")
    if RE_TITLE_MARKER.search(paragraph.text_visible):
        signals.append("marker")
    if _is_short_line_without_terminal(paragraph.text_visible):
        signals.append("short_line")
    return signals


def _is_title_page_paragraph(
    paragraph: ParagraphBlock,
    in_title_prefix: Optional[bool],
) -> bool:
    """True, если абзац — строка титульного листа.

    ``in_title_prefix`` передает решение регионального прохода (блок находится в
    сервисной зоне: до первого структурного заголовка, оглавления или начала
    основного текста). Если он равен None, используется только признак страницы:
    так модуль можно применять к одному блоку, не зная всего документа.

    Кроме порога сигналов требуется реквизит вуза или подпись
    (``TITLE_REQUIRED_SIGNALS``): короткая строка 11 pt без точки в конце сама по
    себе встречается в основном тексте, и скрывать на ней нарушения нельзя.

    Исключение — строка, которая состоит только из подписного реквизита
    («(подпись)»): она не бывает абзацем текста ни в акте внедрения, ни в конце
    заключения, поэтому распознается без привязки к зоне.
    """
    if paragraph.container_type != "body":
        return False
    if paragraph.is_empty_or_whitespace or not paragraph.text_visible.strip():
        return False
    if _is_signature_requisite(paragraph):
        return True
    signals = _title_page_signals(paragraph)
    if len(signals) < TITLE_MIN_SIGNALS:
        return False
    if not TITLE_REQUIRED_SIGNALS.issubset(signals):
        return False
    if in_title_prefix is not None:
        return bool(in_title_prefix)
    return paragraph.page_start_est <= TITLE_PREFIX_MAX_PAGE


def _collapsed_text(paragraph: ParagraphBlock) -> str:
    """Видимый текст абзаца, приведенный к одной строке с одиночными пробелами."""
    return " ".join(paragraph.text_visible.split())


def _is_signature_requisite(paragraph: ParagraphBlock) -> bool:
    """True, если весь видимый текст абзаца — подписный реквизит («(подпись)»)."""
    if paragraph.container_type != "body":
        return False
    return bool(RE_SIGNATURE_REQUISITE.match(_collapsed_text(paragraph)))


def _is_service_sheet_title(text: str) -> bool:
    """True, если строка — заголовок сервисного листа (задание, акт, реферат).

    Требования к маркеру строгие: строка начинается с названия листа, короткая и
    не заканчивается точкой. Иначе обычное предложение «Реферат содержит обзор
    предметной области.» открывало бы сервисную зону в основном тексте.
    """
    collapsed = " ".join((text or "").split())
    if not collapsed or len(collapsed) > SERVICE_SHEET_TITLE_MAX_CHARS:
        return False
    if collapsed.endswith((".", "!", "?", "…")):
        return False
    return bool(RE_SERVICE_SHEET_TITLE.match(collapsed))


def _is_structural_title(text: str) -> bool:
    """True, если строка — название элемента структуры (введение, приложение)."""
    collapsed = " ".join((text or "").split())
    if not collapsed:
        return False
    return bool(RE_STRUCTURAL_TITLE.match(collapsed))


def _is_resumed_body_prose(paragraph: ParagraphBlock) -> bool:
    """True, если блок — длинное предложение основного текста.

    Такой блок означает, что сервисная зона (титул, лист задания, акт) кончилась
    и дальше идет текст работы: реквизиты служебных страниц короткие и почти
    никогда не заканчиваются точкой.
    """
    collapsed = _collapsed_text(paragraph)
    if _visible_char_count(collapsed) < SERVICE_ZONE_PROSE_MIN_CHARS:
        return False
    return collapsed.endswith((".", "!", "?", "…"))


def _ends_with_abbreviation(text: str) -> bool:
    """True, если точка в конце строки — часть сокращения, а не конец предложения.

    На титуле и в листе задания реквизиты сокращают: «канд. физ.-мат.», «доц.».
    Для них завершающая точка ничего не значит, поэтому строка остается
    реквизитом сервисной зоны.
    """
    if not text.endswith("."):
        return False
    tokens = text.split()
    if not tokens:
        return False
    token = tokens[-1]
    if token.count(".") > 1:
        # «физ.-мат.»: точка стоит и внутри сокращения.
        return True
    stem = token.rstrip(".")
    return stem.isalpha() and stem[:1].islower() and len(stem) <= 3


def _is_service_zone_line(paragraph: ParagraphBlock) -> bool:
    """True, если строка внутри сервисной зоны — реквизит, а не абзац текста.

    Внутри распознанной зоны (титул, лист задания, календарный план, акт,
    аннотация, реферат, отзыв) реквизиты оформлены по требованиям методички к
    служебным страницам: «Екатеринбург», «2026», «(фамилия, имя, отчество)»,
    «Нормоконтролер: …», тема работы. Общий признак — строка не заканчивается
    точкой: абзац основного текста ею заканчивается всегда.
    """
    if paragraph.container_type != "body":
        return False
    if paragraph.is_empty_or_whitespace:
        return False
    collapsed = _collapsed_text(paragraph)
    if not collapsed or len(collapsed) > SERVICE_ZONE_MAX_LINE_CHARS:
        return False
    if not any(char.isalnum() for char in collapsed):
        return False
    if collapsed.endswith((".", "!", "?", "…")) and not _ends_with_abbreviation(
        collapsed
    ):
        return False
    if RE_TOC_TITLE.match(collapsed) or _is_structural_title(collapsed):
        return False
    return True


def _non_empty_lines(text: str) -> List[str]:
    """Строки абзаца без пустых и пробельных."""
    return [line for line in (text or "").splitlines() if line.strip()]


def _is_strong_code_line(line: str) -> bool:
    """True для строки с однозначным код-синтаксисом (комментарий, тип, скобки)."""
    stripped = line.strip()
    if not stripped:
        return False
    if RE_CODE_COMMENT.match(stripped):
        return True
    if RE_CODE_PREPROCESSOR.match(stripped):
        return True
    if RE_CODE_NAMESPACE.match(stripped):
        return True
    if RE_CODE_USING.match(stripped):
        return True
    if RE_CODE_TYPE_DECL.search(stripped):
        return True
    if RE_CODE_DECLARATION.match(stripped):
        return True
    if "=>" in stripped:
        return True
    return bool(RE_CODE_PUNCTUATION_LINE.match(stripped))


def _is_medium_code_line(line: str) -> bool:
    """True для строки-оператора: заканчивается на «;» и содержит вызов или присваивание."""
    stripped = line.strip()
    if not stripped.endswith(";"):
        return False
    return bool(RE_ASCII_CODE_TOKEN.search(stripped))


def _looks_like_code_text(text: str) -> bool:
    """True, если текст абзаца состоит из строк кода в пропорциональной гарнитуре."""
    lines = _non_empty_lines(text)
    if not lines:
        return False

    strong = 0
    medium = 0
    for line in lines:
        if _is_strong_code_line(line):
            strong += 1
        elif _is_medium_code_line(line):
            medium += 1

    total = len(lines)
    if strong >= 1 and strong / total >= CODE_LINE_SHARE_THRESHOLD:
        return True
    # Строки-операторы без единого сильного признака слишком похожи на обычный
    # текст, поэтому требуем минимум две такие строки в абзаце.
    if medium < CODE_MIN_MEDIUM_LINES:
        return False
    return (strong + medium) / total >= CODE_LINE_SHARE_THRESHOLD


def _is_code_listing(paragraph: ParagraphBlock) -> bool:
    """True, если абзац — фрагмент листинга кода."""
    if paragraph.is_empty_or_whitespace or not paragraph.text_visible.strip():
        return False
    if _monospace_char_share(paragraph) >= MONOSPACE_CHAR_SHARE_THRESHOLD:
        return True
    return _looks_like_code_text(paragraph.text_visible)


def _is_code_region_filler(paragraph: ParagraphBlock) -> bool:
    """True для абзаца, который может стоять между двумя листингами одного кода."""
    if paragraph.is_empty_or_whitespace or not paragraph.text_visible.strip():
        # Пустая строка внутри листинга — часть листинга.
        return True
    return _monospace_char_share(paragraph) >= CODE_REGION_FILLER_MONOSPACE_SHARE


def _merge_code_regions(
    blocks: Sequence[BlockUnion],
    contexts: Dict[str, BlockContext],
) -> None:
    """Расширяет регионы листингов на пустые и моноширинные строки между ними."""
    paragraphs = [block for block in blocks if isinstance(block, ParagraphBlock)]
    total = len(paragraphs)
    index = 0

    while index < total:
        if contexts.get(paragraphs[index].block_id) != "code_listing":
            index += 1
            continue

        run_end = index
        while (
            run_end + 1 < total
            and contexts.get(paragraphs[run_end + 1].block_id) == "code_listing"
        ):
            run_end += 1

        # Смотрим, чем регион продолжается после возможных «заполнителей».
        bridge = run_end + 1
        while (
            bridge < total
            and contexts.get(paragraphs[bridge].block_id) == "body"
            and _is_code_region_filler(paragraphs[bridge])
        ):
            bridge += 1

        if (
            bridge > run_end + 1
            and bridge < total
            and contexts.get(paragraphs[bridge].block_id) == "code_listing"
        ):
            for filler in range(run_end + 1, bridge):
                contexts[paragraphs[filler].block_id] = "code_listing"
            index = bridge
        else:
            index = run_end + 1


# =====================================================================
# 4. ПУБЛИЧНЫЙ API
# =====================================================================


def classify_paragraph(
    paragraph: ParagraphBlock,
    *,
    in_title_prefix: Optional[bool] = None,
) -> BlockContext:
    """Классифицирует один абзац по его собственным признакам.

    Порядок проверок отражает силу доказательств: заголовок и подпись
    исключаются сразу (у них свои правила), затем проверяется листинг (шрифт и
    синтаксис — самые надежные признаки), и только потом титульный лист.

    :param paragraph: абзац разобранного документа.
    :param in_title_prefix: ``True``, если абзац заведомо в сервисной зоне
        (титул или лист задания до первого заголовка/оглавления); ``False`` —
        если заведомо вне ее; ``None`` (по умолчанию) — решать по номеру
        страницы, потому что соседних блоков, по которым видно конец зоны, здесь
        нет. Строка из одного подписного реквизита («(подпись)») распознается
        как ``title_page`` в любом случае.
    :return: ``"title_page"``, ``"code_listing"`` или ``"body"``.
    """
    if paragraph.heading_info is not None:
        return "body"
    if _is_caption_text(paragraph.text_visible):
        return "body"
    if _is_code_listing(paragraph):
        return "code_listing"
    if _is_title_page_paragraph(paragraph, in_title_prefix):
        return "title_page"
    return "body"


def classify_document_contexts(
    blocks: Iterable[BlockUnion],
    *,
    caption_block_ids: Optional[Iterable[str]] = None,
) -> Dict[str, BlockContext]:
    """Классифицирует блоки документа одним региональным проходом.

    Региональный проход нужен там, где решения одного блока недостаточно:

    * титульный лист и сервисные листы — зона, а не страница. Титул открыт от
      начала документа и закрывается первым структурным заголовком, оглавлением
      или началом основного текста (длинное предложение с точкой в конце), а не
      номером страницы: у Бутакова лист задания продолжает титул на третьей
      странице, у Смолиной — на второй. Зона не ограничена двумя страницами,
      потому что лист задания/акт внедрения может занимать несколько листов;
    * сам сервисный лист распознается по маркеру заголовка (задание,
      календарный план, акт внедрения/приемки, аннотация, реферат, отзыв) в
      любом месте документа и держится, пока в нем идут реквизиты;
    * смежные листинги объединяются в регион: пустая строка или моноширинное
      продолжение между двумя листингами тоже относится к региону.

    Внутри зоны реквизит (строка без завершающей точки) получает контекст
    ``title_page``. Титульная зона требует доказательства, что это действительно
    титул: хотя бы одна строка с реквизитом вуза/подписью (``marker``). Без
    такого доказательства короткая строка без точки остается основным текстом —
    иначе фильтр скрывал бы настоящие нарушения шрифта и отступа.

    :param blocks: блоки документа в порядке следования (``document.blocks``).
    :param caption_block_ids: известные ``block_id`` подписей из
        ``cross_ref_index``; по умолчанию подписи распознаются по тексту.
    :return: словарь ``block_id -> BlockContext`` для каждого переданного блока.
    """
    ordered: Sequence[BlockUnion] = list(blocks)
    captions = set(caption_block_ids or ())

    contexts: Dict[str, BlockContext] = {}
    #: Текущая сервисная зона: "title" (от начала документа), "service"
    #: (лист задания/акта) или None (основной текст).
    zone: Optional[str] = "title"
    #: В зоне титула встретился настоящий реквизит: зона подтверждена.
    title_evidence = False
    #: Сколько подряд идущих предложений основного текста встретилось в листе.
    prose_streak = 0

    for block in ordered:
        if not isinstance(block, ParagraphBlock):
            # Таблицы и блоки оглавления не проверяются текстовыми правилами;
            # контекст "body" сохраняет за ними нейтральное значение.
            contexts[block.block_id] = "body"
            if isinstance(block, TocContainerBlock):
                zone = None
                title_evidence = False
                prose_streak = 0
            continue

        if block.heading_info is not None:
            contexts[block.block_id] = "body"
            if _is_service_sheet_title(block.text_visible):
                # «ЗАДАНИЕ», «РЕФЕРАТ», «ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА»
                # в PDF-разборе распознаются как заголовки, но открывают лист.
                zone = "service"
            elif zone == "title" and RE_TITLE_MARKER.search(block.text_visible):
                # Строка титула, распознанная как заголовок, титул не закрывает.
                zone = "title"
            else:
                zone = None
                title_evidence = False
            prose_streak = 0
            continue

        if block.block_id in captions or _is_caption_text(block.text_visible):
            contexts[block.block_id] = "body"
            continue

        if _is_code_listing(block):
            contexts[block.block_id] = "code_listing"
            zone = None
            title_evidence = False
            prose_streak = 0
            continue

        if block.is_empty_or_whitespace or not block.text_visible.strip():
            # Пустая строка внутри сервисной зоны ее не закрывает.
            contexts[block.block_id] = "body"
            continue

        if _is_service_sheet_title(block.text_visible):
            zone = "service"
            prose_streak = 0
            contexts[block.block_id] = "title_page"
            continue

        if _is_structural_title(block.text_visible):
            zone = None
            title_evidence = False
            prose_streak = 0
            contexts[block.block_id] = "body"
            continue

        prose = _is_resumed_body_prose(block)

        if zone == "title":
            if _is_title_page_paragraph(block, in_title_prefix=True):
                title_evidence = True
                contexts[block.block_id] = "title_page"
            elif title_evidence and _is_service_zone_line(block):
                contexts[block.block_id] = "title_page"
            else:
                if prose:
                    # Начался основной текст: реквизиты титула больше не действуют.
                    zone = None
                    title_evidence = False
                contexts[block.block_id] = "body"
            continue

        if zone == "service":
            if _is_title_page_paragraph(
                block, in_title_prefix=True
            ) or _is_service_zone_line(block):
                prose_streak = 0
                contexts[block.block_id] = "title_page"
                continue
            if prose:
                prose_streak += 1
                if prose_streak >= SERVICE_ZONE_PROSE_MAX_STREAK:
                    zone = None
                    title_evidence = False
            else:
                prose_streak = 0
            contexts[block.block_id] = "body"
            continue

        # Вне сервисной зоны: строка из одного подписного реквизита остается
        # служебной, остальное — основной текст.
        contexts[block.block_id] = (
            "title_page"
            if _is_title_page_paragraph(block, in_title_prefix=False)
            else "body"
        )

    _merge_code_regions(ordered, contexts)
    return contexts


__all__ = [
    "BlockContext",
    "classify_document_contexts",
    "classify_paragraph",
]
