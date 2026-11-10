"""Регрессия контекст-фильтра: титульный лист, листинги кода и обычный текст.

Тесты фиксируют обе стороны контракта `rule_engine.context`:

* положительные срабатывания — строки титульного листа (11 pt кафедра,
  10 pt «(М.П.) (Подпись)») и C#-листинги из отчета с сотнями ложных нарушений;
* защиту от ложных срабатываний — моноширинная цитата в середине документа не
  становится титульным листом, обычный абзац со скобками и точкой с запятой не
  становится листингом;
* неприкосновенность заголовков и подписей рисунков/таблиц.

Отдельная группа проверяет зону сервисных страниц: титул не ограничен двумя
страницами и не закрывается листом задания, сервисный лист распознается по
маркеру в любом месте документа, а вне зоны короткая строка без реквизита
остается основным текстом.

Блоки синтетические: собираются из `parser.core.schema` напрямую, без DOCX и
LibreOffice, поэтому тест проверяет именно эвристики классификатора.
"""
from __future__ import annotations

from typing import List, Optional

from parser.core.schema import (
    BlockUnion,
    HeadingMetadata,
    ParagraphBlock,
    ParagraphPropertiesEffective,
    RunFontPropertiesEffective,
    TableBlock,
    TextRun,
    TocContainerBlock,
)
from rule_engine.context import (
    TITLE_REQUIRED_SIGNALS,
    BlockContext,
    classify_document_contexts,
    classify_paragraph,
)

TITLE_PAGE_MARKER_TEXT = "Министерство науки и высшего образования Российской Федерации"

#: Длинное предложение основного текста: больше порога длины и с точкой в конце.
LONG_PROSE = (
    "В рамках выполнения работы был проведён анализ предметной области, "
    "сформулированы требования к системе и выбраны средства реализации, "
    "после чего разработан прототип информационной системы."
)


def _run(
    text: str,
    *,
    family: str = "Times New Roman",
    size: float = 14.0,
    run_id: str = "r0",
) -> TextRun:
    """Собирает ран с эффективным шрифтом и видимым текстом."""
    return TextRun(
        run_id=run_id,
        text=text,
        visible_text=text,
        start_char=0,
        end_char=len(text),
        effective_props=RunFontPropertiesEffective(
            font_ascii=family,
            font_hansi=family,
            font_cs=family,
            size_pt=size,
        ),
    )


def _paragraph(
    block_id: str,
    text: str,
    *,
    alignment: str = "left",
    page: int = 1,
    family: str = "Times New Roman",
    size: float = 14.0,
    runs: Optional[List[TextRun]] = None,
    heading: Optional[HeadingMetadata] = None,
    container_type: str = "body",
    section_idx: int = 0,
) -> ParagraphBlock:
    """Собирает синтетический ParagraphBlock с заданным форматированием."""
    if runs is None:
        runs = [_run(text, family=family, size=size)] if text else []
    return ParagraphBlock(
        block_id=block_id,
        section_idx=section_idx,
        container_type=container_type,  # type: ignore[arg-type]
        page_start_est=page,
        page_end_est=page,
        text_raw=text,
        text_visible=text,
        is_empty_or_whitespace=not text.strip(),
        effective_props=ParagraphPropertiesEffective(alignment=alignment),  # type: ignore[arg-type]
        runs=runs,
        heading_info=heading,
    )


def _heading(title: str = "ВВЕДЕНИЕ") -> HeadingMetadata:
    """Метаданные заголовка первого уровня."""
    return HeadingMetadata(
        detection_confidence="structural_keyword_match",
        heading_kind="structural_unnumbered",
        level=1,
        clean_title_text=title,
    )


# =====================================================================
# 1. ТИТУЛЬНЫЙ ЛИСТ
# =====================================================================


def test_centered_department_line_is_title_page() -> None:
    """Строка кафедры 11 pt по центру — титульный лист."""
    block = _paragraph(
        "title_department",
        "кафедра программной инженерии",
        alignment="center",
        size=11.0,
    )

    assert classify_paragraph(block) == "title_page"


def test_long_ministerial_line_is_title_page_without_centering() -> None:
    """Маркер «Министерство» и кегль 11 pt дают два сигнала даже без центрирования."""
    block = _paragraph("title_ministry", TITLE_PAGE_MARKER_TEXT, size=11.0)

    assert classify_paragraph(block) == "title_page"


def test_signature_stamp_line_is_title_page() -> None:
    """«(М.П.) (Подпись)» 10 pt — подписная строка титульного листа."""
    block = _paragraph("title_signature", "(М.П.) (Подпись)", size=10.0)

    assert classify_paragraph(block) == "title_page"


def test_title_page_requires_two_signals() -> None:
    """Одного сигнала недостаточно: центрированная длинная фраза 14 pt — body."""
    block = _paragraph(
        "body_center_line",
        "В работе рассматривается задача детерминированной фильтрации open source "
        "библиотек по контексту блоков документа и проверке лицензий зависимостей",
        alignment="center",
        size=14.0,
    )
    small_only = _paragraph(
        "body_small_line",
        "Строка набрана мелким кеглем, но не содержит признаков титульного листа.",
        size=11.0,
    )

    assert classify_paragraph(block) == "body"
    assert classify_paragraph(small_only) == "body"


def test_title_signals_do_not_work_outside_prefix_zone() -> None:
    """Та же титульная строка на третьей странице — уже основной текст."""
    block = _paragraph(
        "title_like_page_three",
        "кафедра программной инженерии",
        alignment="center",
        page=3,
        size=11.0,
    )

    assert classify_paragraph(block) == "body"


def test_document_prefix_zone_closes_at_heading() -> None:
    """После первого заголовка титульные сигналы больше не действуют."""
    blocks: List[BlockUnion] = [
        _paragraph("title_department", "кафедра программной инженерии", alignment="center", size=11.0),
        _paragraph("heading_intro", "ВВЕДЕНИЕ", heading=_heading()),
        _paragraph("title_like_after_heading", "кафедра программной инженерии", alignment="center", size=11.0),
    ]

    contexts = classify_document_contexts(blocks)

    assert contexts["title_department"] == "title_page"
    assert contexts["heading_intro"] == "body"
    assert contexts["title_like_after_heading"] == "body"


def test_document_prefix_zone_closes_at_toc_block() -> None:
    """Блок оглавления закрывает префиксную зону титульного листа."""
    toc = TocContainerBlock(
        block_id="toc_main",
        section_idx=0,
        page_start_est=2,
        page_end_est=2,
        is_automated_field_or_sdt=True,
    )
    blocks: List[BlockUnion] = [
        _paragraph("title_department", "кафедра программной инженерии", alignment="center", size=11.0),
        toc,
        _paragraph("title_like_after_toc", "кафедра программной инженерии", alignment="center", page=2, size=11.0),
    ]

    contexts = classify_document_contexts(blocks)

    assert contexts["title_department"] == "title_page"
    assert contexts["toc_main"] == "body"
    assert contexts["title_like_after_toc"] == "body"


def test_toc_title_with_colon_is_not_title_page() -> None:
    """«Содержание:» по центру — зачин оглавления, а не строка титульного листа."""
    block = _paragraph("toc_title", "Содержание:", alignment="center", size=14.0)

    assert classify_paragraph(block) == "body"


# =====================================================================
# 2. ЛИСТИНГИ КОДА
# =====================================================================


def test_monospace_class_declaration_is_code_listing() -> None:
    """Courier New 12 pt и объявление класса — листинг."""
    block = _paragraph(
        "code_class_decl",
        "public partial class X() {",
        family="Courier New",
        size=12.0,
        page=87,
    )

    assert classify_paragraph(block) == "code_listing"


def test_csharp_const_declaration_is_code_listing_without_monospace() -> None:
    """C#-объявление поля распознается по синтаксису даже в пропорциональной гарнитуре."""
    block = _paragraph(
        "code_const_decl",
        "private const string PypiRoutingHeaderName = \"X-Pypi-Ocelot-Route-Is-Html\";",
        size=12.0,
        page=87,
    )

    assert classify_paragraph(block) == "code_listing"


def test_csharp_comment_line_is_code_listing() -> None:
    """Строка-комментарий `//` — листинг."""
    block = _paragraph("code_comment", "// описание метода", size=12.0, page=87)

    assert classify_paragraph(block) == "code_listing"


def test_code_syntax_without_runs_is_recognized() -> None:
    """Синтаксис работает и тогда, когда раны не заполнены (текст есть, шрифта нет)."""
    block = _paragraph(
        "code_no_runs",
        "public partial class X() {",
        runs=[],
        page=87,
    )

    assert classify_paragraph(block) == "code_listing"


def test_body_paragraph_with_brackets_is_not_code_listing() -> None:
    """Скобки и «;» в обычном предложении не делают абзац листингом."""
    sentence = _paragraph(
        "body_brackets",
        "Результаты (см. таблицу 2) приведены ниже;",
    )
    formula = _paragraph(
        "body_formula",
        "Функция f(x) = 2x + 1 описана в разделе 3.",
    )

    assert classify_paragraph(sentence) == "body"
    assert classify_paragraph(formula) == "body"


def test_single_statement_line_is_not_enough_for_code_listing() -> None:
    """Одна строка-оператор без моноширинного шрифта — еще не листинг."""
    block = _paragraph("body_call", "Console.WriteLine(\"hello\");")

    assert classify_paragraph(block) == "body"


def test_punctuation_artifact_is_not_code_listing() -> None:
    """Одиночная запятая — артефакт разбора, а не строка листинга."""
    block = _paragraph("punctuation_artifact", ",", alignment="center", page=33)

    assert classify_paragraph(block) == "body"


def test_adjacent_listings_merge_into_region() -> None:
    """Пустая строка между двумя листингами относится к их региону."""
    blocks: List[BlockUnion] = [
        _paragraph("code_class_decl", "public partial class X() {", family="Courier New", size=12.0, page=87),
        _paragraph("listing_blank", "", page=87),
        _paragraph("code_comment", "// описание метода", family="Courier New", size=12.0, page=87),
        _paragraph("body_after_listing", "После листинга снова идет обычный текст работы.", page=87),
    ]

    contexts = classify_document_contexts(blocks)

    assert contexts["code_class_decl"] == "code_listing"
    assert contexts["listing_blank"] == "code_listing"
    assert contexts["code_comment"] == "code_listing"
    assert contexts["body_after_listing"] == "body"


# =====================================================================
# 3. ОБЫЧНЫЙ ТЕКСТ, ЗАГОЛОВКИ И ПОДПИСИ
# =====================================================================


def test_plain_times_paragraph_is_body() -> None:
    """Обычный абзац Times New Roman 14 pt остается основным текстом."""
    block = _paragraph(
        "body_plain",
        "В 2024 году выполнена фильтрация библиотек и проверка лицензий.",
        alignment="both",
        page=12,
    )

    assert classify_paragraph(block) == "body"


def test_heading_is_never_title_page_or_code_listing() -> None:
    """Заголовок не переклассифицируется, даже если набран моноширинно по центру."""
    block = _paragraph(
        "heading_center_mono",
        "1.1 Методика эксперимента",
        alignment="center",
        family="Courier New",
        size=12.0,
        heading=_heading("Методика эксперимента"),
    )

    assert classify_paragraph(block) == "body"


def test_figure_caption_is_never_title_page() -> None:
    """Подпись рисунка 12 pt по центру на первой странице — body, а не титул."""
    block = _paragraph(
        "figure_caption",
        "Рисунок 1 — Схема взаимодействия клиента и сервера",
        alignment="center",
        size=12.0,
    )

    assert classify_paragraph(block) == "body"


def test_abbreviated_figure_caption_is_not_title_page() -> None:
    """Сокращение «Рис.» с точкой тоже распознается как подпись, а не титул."""
    block = _paragraph(
        "figure_caption_short",
        "Рис. 1 — Схема обработки запроса",
        alignment="center",
        size=12.0,
    )

    assert classify_paragraph(block) == "body"


def test_table_caption_is_never_title_page() -> None:
    """Подпись таблицы 10 pt в префиксной зоне — body."""
    blocks: List[BlockUnion] = [
        _paragraph("table_caption", "Таблица 2.1 — Результаты измерений", size=10.0),
    ]

    contexts = classify_document_contexts(blocks)

    assert contexts["table_caption"] == "body"


def test_monospace_quote_in_middle_is_not_title_page() -> None:
    """Моноширинная цитата в центре документа не становится титульным листом."""
    block = _paragraph(
        "quote_mono_center",
        "«Простота — залог надежности»",
        alignment="center",
        family="Courier New",
        size=14.0,
        page=37,
    )
    contexts = classify_document_contexts([block])

    assert classify_paragraph(block) != "title_page"
    assert contexts["quote_mono_center"] != "title_page"


# =====================================================================
# 4. КОНТРАКТ РЕГИОНАЛЬНОГО ПРОХОДА
# =====================================================================


def test_document_contexts_cover_every_block() -> None:
    """Словарь контекстов содержит каждый блок, включая таблицы и оглавление."""
    table = TableBlock(block_id="table_sample", section_idx=0)
    toc = TocContainerBlock(
        block_id="toc_main",
        section_idx=0,
        page_start_est=2,
        page_end_est=2,
        is_automated_field_or_sdt=True,
    )
    blocks: List[BlockUnion] = [
        _paragraph("title_ministry", TITLE_PAGE_MARKER_TEXT, size=11.0),
        table,
        toc,
    ]

    contexts = classify_document_contexts(blocks)

    assert set(contexts) == {"title_ministry", "table_sample", "toc_main"}
    assert contexts["title_ministry"] == "title_page"
    assert contexts["table_sample"] == "body"
    assert contexts["toc_main"] == "body"


def test_document_contexts_are_deterministic() -> None:
    """Повторный проход по тем же блокам дает тот же результат."""
    blocks: List[BlockUnion] = [
        _paragraph("title_ministry", TITLE_PAGE_MARKER_TEXT, size=11.0),
        _paragraph("code_class_decl", "public partial class X() {", family="Courier New", size=12.0, page=87),
        _paragraph("body_after_listing", "Обычный текст после листинга.", page=87),
    ]

    first = classify_document_contexts(blocks)
    second = classify_document_contexts(blocks)

    assert first == second


def test_contexts_use_only_known_literals() -> None:
    """Классификатор возвращает только три допустимых значения контекста."""
    allowed: set[BlockContext] = {"body", "title_page", "code_listing"}
    blocks: List[BlockUnion] = [
        _paragraph("title_ministry", TITLE_PAGE_MARKER_TEXT, size=11.0),
        _paragraph("code_class_decl", "public partial class X() {", family="Courier New", size=12.0, page=87),
        _paragraph("body_after_listing", "Обычный текст после листинга.", page=87),
    ]

    assert set(classify_document_contexts(blocks).values()) <= allowed


# =====================================================================
# 5. СЕРВИСНЫЕ ЛИСТЫ И ЗОНА ТИТУЛА БЕЗ ЛИМИТА СТРАНИЦ
# =====================================================================


def test_urfu_title_page_after_task_sheet_on_third_page_is_title_page() -> None:
    """Титул УРФУ на третьей странице (после листа задания) — сервисная зона.

    У Бутакова титул и лист задания занимают страницы 1–3: титул повторяется
    после задания, поэтому 35 нарушений шрифта и отступа приходили на строки
    реквизитов, которые правила «основной текст» проверять не должны.
    """
    blocks: List[BlockUnion] = [
        _paragraph("task_title", "ЗАДАНИЕ", alignment="center", page=2, size=12.0),
        _paragraph(
            "task_first_line",
            "на выполнение выпускной квалификационной работы",
            page=2,
            size=12.0,
        ),
        _paragraph(
            "title_ministry",
            TITLE_PAGE_MARKER_TEXT,
            alignment="center",
            page=3,
            size=11.0,
        ),
        _paragraph(
            "title_university",
            "«Уральский федеральный университет имени первого Президента "
            "России Б.Н. Ельцина»",
            alignment="center",
            page=3,
            size=11.0,
        ),
        _paragraph(
            "title_normocontrol",
            "Нормоконтролер: Е.Д. Ливадний",
            page=3,
            size=12.0,
        ),
        _paragraph("title_signature", "(подпись)", page=3, size=10.0),
        _paragraph("title_city", "Екатеринбург", alignment="center", page=3),
        _paragraph("title_year", "2026", alignment="center", page=3),
        _paragraph("heading_abstract", "РЕФЕРАТ", heading=_heading("РЕФЕРАТ"), page=4),
    ]

    contexts = classify_document_contexts(blocks)

    for block_id in (
        "task_title",
        "task_first_line",
        "title_ministry",
        "title_university",
        "title_normocontrol",
        "title_signature",
        "title_city",
        "title_year",
    ):
        assert contexts[block_id] == "title_page", block_id
    assert contexts["heading_abstract"] == "body"


def test_title_zone_is_not_limited_to_two_pages() -> None:
    """Зона титула закрывается заголовком, а не номером страницы."""
    blocks: List[BlockUnion] = [
        _paragraph(
            "title_ministry",
            TITLE_PAGE_MARKER_TEXT,
            alignment="center",
            page=1,
            size=11.0,
        ),
        _paragraph("title_page_break", "", page=2),
        _paragraph(
            "title_normocontrol",
            "Нормоконтролер: Е.Д. Ливадний",
            page=4,
            size=12.0,
        ),
        _paragraph("title_city", "Екатеринбург", alignment="center", page=4),
        _paragraph("title_year", "2026", alignment="center", page=4),
    ]

    contexts = classify_document_contexts(blocks)

    assert contexts["title_ministry"] == "title_page"
    assert contexts["title_normocontrol"] == "title_page"
    assert contexts["title_city"] == "title_page"
    assert contexts["title_year"] == "title_page"


def test_title_zone_without_marker_keeps_short_lines_as_body() -> None:
    """Без реквизита вуза или подписи зона титула не скрывает короткие строки."""
    blocks: List[BlockUnion] = [
        _paragraph("centered_city", "Екатеринбург", alignment="center", page=1),
        _paragraph("year_line", "2026", alignment="center", page=1),
        _paragraph("short_body_line", "Результаты приведены ниже", page=1),
    ]

    contexts = classify_document_contexts(blocks)

    assert set(contexts.values()) == {"body"}


def test_title_required_signals_still_require_marker() -> None:
    """Обязательный сигнал титула остался прежним: без маркера блок — body."""
    assert TITLE_REQUIRED_SIGNALS == frozenset({"marker"})

    block = _paragraph("centered_city", "Екатеринбург", alignment="center", page=1)

    assert classify_paragraph(block) == "body"


def test_act_sheet_zone_is_recognized_anywhere_in_document() -> None:
    """Акт внедрения в середине документа распознается по маркеру, а не по странице."""
    blocks: List[BlockUnion] = [
        _paragraph("heading_chapter", "1 Анализ", heading=_heading(), page=10),
        _paragraph("body_before", LONG_PROSE, page=10),
        _paragraph("act_title", "АКТ ВНЕДРЕНИЯ", alignment="center", page=40),
        _paragraph(
            "act_normocontrol",
            "Нормоконтролер: Е.Д. Ливадний",
            page=40,
            size=12.0,
        ),
        _paragraph("act_signature", "(подпись)", page=40, size=10.0),
        _paragraph("act_city", "Екатеринбург", alignment="center", page=40),
        _paragraph("act_year", "2026", alignment="center", page=40),
        _paragraph("heading_after", "2 Реализация", heading=_heading(), page=41),
        _paragraph("body_after", LONG_PROSE, page=41),
    ]

    contexts = classify_document_contexts(blocks)

    assert contexts["body_before"] == "body"
    for block_id in (
        "act_title",
        "act_normocontrol",
        "act_signature",
        "act_city",
        "act_year",
    ):
        assert contexts[block_id] == "title_page", block_id
    assert contexts["heading_after"] == "body"
    assert contexts["body_after"] == "body"


def test_service_sheet_zone_closes_when_body_prose_resumes() -> None:
    """Сервисный лист закрывается, когда снова идет текст работы."""
    blocks: List[BlockUnion] = [
        _paragraph("sheet_title", "ЗАДАНИЕ", alignment="center", page=2),
        _paragraph("sheet_requisite", "(фамилия, имя, отчество)", page=2, size=10.0),
        _paragraph("prose_one", LONG_PROSE, page=2),
        _paragraph("prose_two", LONG_PROSE, page=2),
        _paragraph("after_short", "Екатеринбург", alignment="center", page=2),
    ]

    contexts = classify_document_contexts(blocks)

    assert contexts["sheet_title"] == "title_page"
    assert contexts["sheet_requisite"] == "title_page"
    assert contexts["prose_one"] == "body"
    assert contexts["prose_two"] == "body"
    assert contexts["after_short"] == "body"


def test_toc_title_closes_service_zone() -> None:
    """«СОДЕРЖАНИЕ» закрывает зону титула и само остается основным текстом."""
    blocks: List[BlockUnion] = [
        _paragraph(
            "title_ministry",
            TITLE_PAGE_MARKER_TEXT,
            alignment="center",
            page=1,
            size=11.0,
        ),
        _paragraph("toc_title", "СОДЕРЖАНИЕ", alignment="center", page=2),
        _paragraph("after_toc", "Екатеринбург", alignment="center", page=2),
    ]

    contexts = classify_document_contexts(blocks)

    assert contexts["title_ministry"] == "title_page"
    assert contexts["toc_title"] == "body"
    assert contexts["after_toc"] == "body"


def test_signature_only_line_outside_service_zone_is_title_page() -> None:
    """«(подпись)» в конце заключения — подписной реквизит, а не абзац текста."""
    blocks: List[BlockUnion] = [
        _paragraph(
            "heading_conclusion",
            "ЗАКЛЮЧЕНИЕ",
            heading=_heading("ЗАКЛЮЧЕНИЕ"),
            page=40,
        ),
        _paragraph("conclusion_prose", LONG_PROSE, page=40),
        _paragraph("signature_line", "(подпись)", page=40, size=10.0),
    ]

    contexts = classify_document_contexts(blocks)

    assert contexts["heading_conclusion"] == "body"
    assert contexts["conclusion_prose"] == "body"
    assert contexts["signature_line"] == "title_page"


def test_body_paragraph_mentioning_signature_is_not_service_zone() -> None:
    """Упоминание подписи в предложении не делает абзац сервисной зоной."""
    blocks: List[BlockUnion] = [
        _paragraph("heading_chapter", "1 Анализ", heading=_heading(), page=5),
        _paragraph(
            "body_mention",
            "Документ содержит подпись руководителя и печать организации.",
            page=5,
        ),
        _paragraph(
            "body_starts_with_signature",
            "Подпись руководителя заверена печатью организации.",
            page=5,
        ),
    ]

    contexts = classify_document_contexts(blocks)

    assert contexts["heading_chapter"] == "body"
    assert contexts["body_mention"] == "body"
    assert contexts["body_starts_with_signature"] == "body"

