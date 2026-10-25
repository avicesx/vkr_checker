"""Тесты PDF-выравнивания: страницы, координаты, сверка оглавления, защита от несовпадений.

PDF рисуется на лету через PyMuPDF, DOCX собирается DocxPackageBuilder, поэтому
проверяется именно сопоставление двух документов, а не разбор бинарных образцов.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from conftest import COMMON_XMLNS, DocxPackageBuilder, make_fake_png_bytes
from docx_parser.pipeline import DocxParserPipeline
from docx_parser.schema import ParagraphBlock, TableBlock, TocContainerBlock

fitz = pytest.importorskip("fitz", reason="Для тестов PDF-выравнивателя требуется PyMuPDF (pip install pymupdf)")

MM_TO_PT = 72.0 / 25.4  # Конвертация мм -> пункты PDF


def _draw_pdf_line(page, x_mm: float, y_mm: float, text: str, fontsize: float = 12.0) -> None:
    """
    Отрисовывает строку текста на странице PDF по координатам в миллиметрах.
    Поддерживает кириллицу и латиницу через встроенный Unicode-рендер PyMuPDF.
    """
    rect = fitz.Rect(
        x_mm * MM_TO_PT,
        y_mm * MM_TO_PT,
        (x_mm + 170.0) * MM_TO_PT,
        (y_mm + 18.0) * MM_TO_PT,
    )
    if hasattr(page, "insert_htmlbox"):
        css = f"* {{ font-family: sans-serif; font-size: {fontsize}pt; margin: 0; padding: 0; }}"
        page.insert_htmlbox(rect, text, css=css)
    else:
        page.insert_text(
            fitz.Point(x_mm * MM_TO_PT, (y_mm + 4.0) * MM_TO_PT),
            text,
            fontsize=fontsize,
        )



def test_edge_cases_title_page_initials_and_toc_prefix(docx_builder: DocxPackageBuilder) -> None:
    """
    Три краевых случая верстки титульного листа и оглавления.

    1. Код специальности «09.03.01 Алгоритмы искусственного интеллекта Группа РИ-240917»
       не распознается как заголовок 3-го уровня.
    2. В строке «Президента России Б. Н. Ельцина» сканер микротипографики находит
       «Б. Н. Ельцина», а не «России Б. Н.», и продолжает находить «Коломыцева И.К.».
    3. В строке оглавления «Раздел 1 СОЗДАНИЕ БАЗЫ ДАННЫХ GrantPlatform_AI» номер
       раздела извлекается как «1», а запись связывается с заголовком в тексте.
    """
    body_xml = """
    <!-- Титульный лист: шапка с инициалами, ФИО руководителя и код специальности 09.03.01 -->
    <w:p>
      <w:r><w:t>ФГАОУ ВО «УрФУ имени первого Президента России Б. Н. Ельцина»</w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>Руководитель практики от предприятия (организации) Коломыцева И.К. </w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>09.03.01 Алгоритмы искусственного интеллекта Группа РИ-240917</w:t></w:r>
    </w:p>

    <!-- Оглавление с префиксом 'Раздел 1' -->
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Содержание</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9350"/></w:tabs></w:pPr>
      <w:r><w:fldChar w:fldCharType="begin"/></w:r>
      <w:r><w:instrText>PAGEREF _Toc1</w:instrText></w:r>
      <w:r><w:fldChar w:fldCharType="separate"/></w:r>
      <w:r><w:t>Раздел 1 СОЗДАНИЕ БАЗЫ ДАННЫХ GrantPlatform_AI\t8</w:t></w:r>
      <w:r><w:fldChar w:fldCharType="end"/></w:r>
    </w:p>

    <!-- Реальный заголовок раздела в тексте -->
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Раздел 1 СОЗДАНИЕ БАЗЫ ДАННЫХ GrantPlatform_AI</w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>Текст первого раздела отчета.</w:t></w:r>
    </w:p>
    """

    docx_path = docx_builder.set_body(body_xml).build()
    doc = DocxParserPipeline(docx_path).parse()

    paragraphs = [b for b in doc.blocks if isinstance(b, ParagraphBlock)]
    p_urfu = paragraphs[0]
    p_kolomytseva = paragraphs[1]
    p_spec_code = paragraphs[2]

    # 1. Проверка: '09.03.01 ...' — это обычный абзац титульника, а не заголовок
    assert p_spec_code.heading_info is None

    # 2. Проверка инициалов в 'Б. Н. Ельцина' и 'Коломыцева И.К.'
    urfu_nbsp_snippets = [m.snippet for m in p_urfu.typography.missing_nbsp]
    assert "Б. Н. Ельцина" in urfu_nbsp_snippets
    assert "России Б. Н." not in urfu_nbsp_snippets

    kolom_nbsp_snippets = [m.snippet for m in p_kolomytseva.typography.missing_nbsp]
    assert "Коломыцева И.К." in kolom_nbsp_snippets

    # 3. Проверка парсинга и линковки 'Раздел 1 ...' в оглавлении
    toc_blocks = [b for b in doc.blocks if isinstance(b, TocContainerBlock)]
    assert len(toc_blocks) == 1
    toc_entry = toc_blocks[0].entries[0]
    assert toc_entry.extracted_number == "1"
    assert toc_entry.extracted_title == "СОЗДАНИЕ БАЗЫ ДАННЫХ GrantPlatform_AI"
    assert toc_entry.matched_heading_block_id is not None
    assert toc_entry.is_title_exact_match is True



def test_pdf_page_aligner_exact_pages_corners_and_toc(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """
    Проверяет работу PdfPageAligner:
    - Зонирование верхнего и нижнего колонтитулов PDF (чтение номеров страниц по центру и в правом углу);
    - Отлов несовпадения напечатанного номера страницы ('99') с физическим номером листа (4);
    - Определение точных страниц для разорванного между стр. 3 и 4 абзаца;
    - 100%-ю сверку номеров страниц в оглавлении (TOC) по координатам из PDF.
    """
    body_xml = """
    <w:p><w:r><w:t>ТИТУЛЬНЫЙ ЛИСТ ОТЧЕТА ПО ПРАКТИКЕ</w:t></w:r></w:p>

    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>СОДЕРЖАНИЕ</w:t></w:r></w:p>
    <w:p>
      <w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9350"/></w:tabs></w:pPr>
      <w:r><w:t>ВВЕДЕНИЕ\t3</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9350"/></w:tabs></w:pPr>
      <w:r><w:t>Раздел 1 АРХИТЕКТУРА СИСТЕМЫ\t4</w:t></w:r>
    </w:p>

    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>ВВЕДЕНИЕ</w:t></w:r></w:p>
    <w:p>
      <w:r><w:t>Первая половина длинного абзаца введения начинается внизу третьей страницы, а вторая половина этого же абзаца переносится на четвертую страницу.</w:t></w:r>
    </w:p>

    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Раздел 1 АРХИТЕКТУРА СИСТЕМЫ</w:t></w:r></w:p>
    <w:p><w:r><w:t>Описание базы данных и сервисов платформы.</w:t></w:r></w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("report.docx")

    # Генерируем эталонный 4-страничный PDF формата А4 (210 x 297 мм)
    pdf_path = tmp_path / "report.pdf"
    pdf_doc = fitz.open()

    # Стр. 1: Титульный лист (без номера страницы в колонтитулах)
    p1 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p1, 30, 80, "ТИТУЛЬНЫЙ ЛИСТ ОТЧЕТА ПО ПРАКТИКЕ")

    # Стр. 2: Оглавление + номер страницы '2' внизу по центру (x=104 мм, y=286 мм)
    p2 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p2, 80, 25, "СОДЕРЖАНИЕ")
    _draw_pdf_line(p2, 30, 40, "ВВЕДЕНИЕ .... 3")
    _draw_pdf_line(p2, 30, 52, "Раздел 1 АРХИТЕКТУРА СИСТЕМЫ .... 4")
    _draw_pdf_line(p2, 104, 286, "2")

    # Стр. 3: Введение + начало разорванного абзаца + номер страницы '3' в правом нижнем углу (x=185 мм, y=286 мм)
    p3 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p3, 80, 25, "ВВЕДЕНИЕ")
    _draw_pdf_line(p3, 30, 255, "Первая половина длинного абзаца введения начинается внизу третьей страницы,")
    _draw_pdf_line(p3, 185, 286, "3")

    # Стр. 4: Продолжение абзаца + Раздел 1 + ошибочный номер '99' в верхнем колонтитуле (y=10 мм)
    p4 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p4, 104, 10, "99")
    _draw_pdf_line(p4, 30, 25, "а вторая половина этого же абзаца переносится на четвертую страницу.")
    _draw_pdf_line(p4, 30, 50, "Раздел 1 АРХИТЕКТУРА СИСТЕМЫ")
    _draw_pdf_line(p4, 30, 68, "Описание базы данных и сервисов платформы.")

    pdf_doc.save(str(pdf_path))
    pdf_doc.close()

    # Запускаем пайплайн в паре DOCX + PDF
    doc = DocxParserPipeline(docx_path=docx_path, pdf_path=pdf_path).parse()

    assert doc.meta.pagination_source == "pdf_exact"
    assert doc.meta.word_stat_pages == 4
    assert doc.meta.pdf_docx_sync_score is not None and doc.meta.pdf_docx_sync_score >= 0.95
    assert doc.anomalies.pdf_docx_mismatch_warning is None

    # 1. Проверяем аудит колонтитулов и номеров страниц на физических листах PDF
    assert len(doc.pdf_pages_audit) == 4
    assert doc.pdf_pages_audit[0].has_rendered_page_number is False  # На титульнике номера нет

    assert doc.pdf_pages_audit[1].has_rendered_page_number is True
    assert doc.pdf_pages_audit[1].page_number_location == "footer"
    assert doc.pdf_pages_audit[1].page_number_alignment == "center"
    assert doc.pdf_pages_audit[1].rendered_page_number_int == 2
    assert doc.pdf_pages_audit[1].matches_physical_page_num is True

    assert doc.pdf_pages_audit[2].page_number_location == "footer"
    assert doc.pdf_pages_audit[2].page_number_alignment == "right"
    assert doc.pdf_pages_audit[2].rendered_page_number_int == 3
    assert doc.pdf_pages_audit[2].matches_physical_page_num is True

    # На 4-м листе в верхнем колонтитуле стояло число '99' вместо '4'
    assert doc.pdf_pages_audit[3].page_number_location == "header"
    assert doc.pdf_pages_audit[3].rendered_page_number_int == 99
    assert doc.pdf_pages_audit[3].matches_physical_page_num is False

    # 2. Проверяем точную сверку страниц оглавления (TOC)
    toc = next(b for b in doc.blocks if isinstance(b, TocContainerBlock))
    assert toc.page_start_est == 2
    assert toc.entries[0].extracted_title == "ВВЕДЕНИЕ"
    assert toc.entries[0].actual_heading_page_est == 3
    assert toc.entries[0].page_number_delta == 0
    assert toc.entries[0].is_page_number_match is True

    assert toc.entries[1].extracted_number == "1"
    assert toc.entries[1].actual_heading_page_est == 4
    assert toc.entries[1].page_number_delta == 0
    assert toc.entries[1].is_page_number_match is True

    # 3. Проверяем абзац, разорванный между 3-й и 4-й страницами
    split_para = next(
        b for b in doc.blocks
        if isinstance(b, ParagraphBlock) and "Первая половина длинного абзаца" in b.text_visible
    )
    assert split_para.page_start_est == 3
    assert split_para.page_end_est == 4
    assert split_para.pdf_lines_count_on_start_page == 1
    assert split_para.pdf_lines_count_on_end_page == 1
    assert len(split_para.rendered_page_breaks_offsets) == 1



def test_pdf_table_page_break_and_repeated_header_detection(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """
    Проверяет:
    - Точное определение строки таблицы, уехавшей на следующую страницу в PDF;
    - Детекцию повторенной шапки таблицы вверху 2-й страницы PDF (pdf_repeated_header_above = True);
    - Детекцию выхода правой границы таблицы за пределы текстового поля страницы по координатам PDF.
    """
    body_xml = """
    <w:p><w:r><w:t>Таблица 1.1 — Сводка показателей</w:t></w:r></w:p>
    <w:tbl>
      <w:tblGrid><w:gridCol w:w="4000"/><w:gridCol w:w="4000"/></w:tblGrid>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Наименование модуля</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Статус готовности</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Модуль авторизации пользователей</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Протестирован успешно</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Модуль генерации отчетов</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Перенесен на вторую страницу</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    """
    docx_path = docx_builder.set_body(body_xml).build("table_split.docx")

    pdf_path = tmp_path / "table_split.pdf"
    pdf_doc = fitz.open()

    # Стр. 1: Подпись таблицы + Шапка (row 0) + Первая строка данных (row 1)
    p1 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p1, 30, 210, "Таблица 1.1 — Сводка показателей")
    _draw_pdf_line(p1, 30, 225, "Наименование модуля Статус готовности")
    _draw_pdf_line(p1, 30, 240, "Модуль авторизации пользователей Протестирован успешно")

    # Стр. 2: Автоматический повтор шапки + Вторая строка данных (row 2)
    p2 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p2, 30, 25, "Наименование модуля Статус готовности")
    _draw_pdf_line(p2, 30, 40, "Модуль генерации отчетов Перенесен на вторую страницу")

    pdf_doc.save(str(pdf_path))
    pdf_doc.close()

    doc = DocxParserPipeline(docx_path=docx_path, pdf_path=pdf_path).parse()
    tbl = next(b for b in doc.blocks if isinstance(b, TableBlock))

    assert tbl.page_start_est == 1
    assert tbl.page_end_est == 2
    assert tbl.spans_multiple_pages is True

    assert tbl.rows[0].page_start_est == 1
    assert tbl.rows[1].page_start_est == 1
    assert tbl.rows[1].has_page_break_inside_or_before is False

    # Строка 2 переехала на 2-ю страницу и над ней в PDF обнаружена повторенная шапка!
    assert tbl.rows[2].page_start_est == 2
    assert tbl.rows[2].has_page_break_inside_or_before is True
    assert tbl.rows[2].pdf_repeated_header_above is True



def test_pdf_docx_mismatch_guard_warning(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """
    Проверяет, что если студент загрузил DOCX от одной работы, а PDF — от совершенно другой,
    парсер вычисляет низкий pdf_docx_sync_score и записывает предупреждение в anomalies.
    """
    body_xml = """
    <w:p><w:r><w:t>Разработка микросервисной архитектуры на базе FastAPI и PostgreSQL.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Настройка контейнеров Docker и пайплайна непрерывной интеграции GitLab CI.</w:t></w:r></w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("real_vkr.docx")

    wrong_pdf_path = tmp_path / "wrong_file.pdf"
    pdf_doc = fitz.open()
    p1 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p1, 30, 40, "Лабораторная работа по микроклимату производственных помещений.")
    pdf_doc.save(str(wrong_pdf_path))
    pdf_doc.close()

    doc = DocxParserPipeline(docx_path=docx_path, pdf_path=wrong_pdf_path).parse()

    assert doc.meta.pdf_docx_sync_score == 0.0
    assert doc.meta.pagination_source != "pdf_exact"
    assert doc.anomalies.pdf_docx_mismatch_warning is not None
    assert "Низкое совпадение текста" in doc.anomalies.pdf_docx_mismatch_warning


def test_page_number_partly_above_bottom_margin_is_detected(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """
    Номер страницы 14 pt может начинаться выше нижнего поля, но заходить в него.

    В таких документах (например, после LibreOffice/OnlyOffice) bbox строки
    пересекает границу поля, хотя визуально номер стоит в колонтитуле. Детектор
    обязан относить такую строку к footer, а не к телу документа. Одновременно
    проверяется, что устаревший Pages из docProps/app.xml заменяется фактическим
    числом страниц PDF.
    """
    body_xml = """
    <w:p><w:r><w:t>Титульный лист</w:t></w:r></w:p>
    <w:p>
      <w:pPr><w:pageBreakBefore/></w:pPr>
      <w:r><w:t>Основной текст второй страницы</w:t></w:r>
    </w:p>
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1134" w:right="850" w:bottom="1134"
               w:left="1701" w:header="708" w:footer="708" w:gutter="0"/>
    </w:sectPr>
    """
    docx_builder.set_body(body_xml)
    docx_builder.set_xml(
        "docProps/app.xml",
        """<?xml version="1.0" encoding="UTF-8"?>
        <Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
          <Pages>1</Pages>
          <Words>999</Words>
        </Properties>""",
    )
    docx_path = docx_builder.build("page_number_partly_in_margin.docx")

    pdf_path = tmp_path / "page_number_partly_in_margin.pdf"
    pdf_doc = fitz.open()
    p1 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p1, 30, 40, "Титульный лист")
    p2 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p2, 30, 40, "Основной текст второй страницы")
    # y=274 мм: строка 14 pt заходит в нижнее поле (граница 277 мм), но её
    # вертикальный центр ещё находится в текстовой области.
    _draw_pdf_line(p2, 103, 274, "2", fontsize=14.0)
    pdf_doc.save(str(pdf_path))
    pdf_doc.close()

    doc = DocxParserPipeline(docx_path=docx_path, pdf_path=pdf_path).parse()

    assert doc.meta.pagination_source == "pdf_exact"
    assert doc.meta.word_stat_pages == 2
    assert doc.pdf_pages_audit[0].has_rendered_page_number is False
    assert doc.pdf_pages_audit[1].has_rendered_page_number is True
    assert doc.pdf_pages_audit[1].page_number_location == "footer"
    assert doc.pdf_pages_audit[1].page_number_alignment == "center"
    assert doc.pdf_pages_audit[1].rendered_page_number_int == 2
    assert doc.pdf_pages_audit[1].matches_physical_page_num is True


def test_blank_toc_spacer_does_not_skip_beginning_of_body(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """
    Пустой абзац со стилем TOC между оглавлением и телом не должен считаться
    строкой оглавления. Иначе поиск новой области с отточиями перескакивает
    через начало тела и первый заголовок получает чужую страницу.
    """
    body_xml = """
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Содержание</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="TOC1"/></w:pPr><w:r><w:t>ВВЕДЕНИЕ&#9;2</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="TOC1"/></w:pPr></w:p>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/><w:pageBreakBefore/></w:pPr>
      <w:r><w:t>ВВЕДЕНИЕ</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>Основной текст введения.</w:t></w:r></w:p>
    <w:p><w:r><w:t>................</w:t></w:r></w:p>
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1134" w:right="850" w:bottom="1134"
               w:left="1701" w:header="708" w:footer="708" w:gutter="0"/>
    </w:sectPr>
    """
    docx_builder.set_body(body_xml)
    docx_path = docx_builder.build("blank_toc_spacer.docx")

    pdf_path = tmp_path / "blank_toc_spacer.pdf"
    pdf_doc = fitz.open()
    p1 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p1, 30, 40, "Содержание")
    _draw_pdf_line(p1, 30, 60, "ВВЕДЕНИЕ........................................2")
    p2 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p2, 30, 40, "ВВЕДЕНИЕ")
    _draw_pdf_line(p2, 30, 60, "Основной текст введения.")
    _draw_pdf_line(p2, 30, 80, "................")
    pdf_doc.save(str(pdf_path))
    pdf_doc.close()

    doc = DocxParserPipeline(docx_path=docx_path, pdf_path=pdf_path).parse()

    intro = next(
        b for b in doc.blocks
        if isinstance(b, ParagraphBlock) and b.text_visible == "ВВЕДЕНИЕ"
    )
    assert intro.page_start_est == 2
    assert intro.page_end_est == 2


def test_stale_toc_numbers_do_not_steal_body_pages(
    docx_builder: DocxPackageBuilder, tmp_path: Path
) -> None:
    """
    Устаревшие номера в оглавлении DOCX не должны уводить выравнивание в тело.

    В DOCX оглавление указывает страницы 4 и 5, в PDF те же заголовки стоят на
    страницах 3 и 4. Строки оглавления обязаны привязаться к области с отточиями
    (стр. 2), а не к найденным по тексту заголовкам в теле документа.
    """
    filler = [
        f"Содержательный абзац номер {i} с достаточной длиной текста для сверки."
        for i in range(1, 11)
    ]
    filler_xml = "\n".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in filler
    )

    def toc_para(title: str, page: str) -> str:
        return (
            "<w:p><w:pPr><w:pStyle w:val=\"TOC1\"/>"
            "<w:tabs><w:tab w:val=\"right\" w:leader=\"dot\" w:pos=\"9350\"/></w:tabs>"
            f"</w:pPr><w:r><w:t>{title}&#9;{page}</w:t></w:r></w:p>"
        )

    body_xml = f"""
    <w:p><w:r><w:t>ТИТУЛЬНЫЙ ЛИСТ</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>СОДЕРЖАНИЕ</w:t></w:r></w:p>
    {toc_para("ВВЕДЕНИЕ", "4")}
    {toc_para("Раздел 1 АРХИТЕКТУРА СИСТЕМЫ", "5")}
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>ВВЕДЕНИЕ</w:t></w:r></w:p>
    {filler_xml}
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Раздел 1 АРХИТЕКТУРА СИСТЕМЫ</w:t></w:r></w:p>
    <w:p><w:r><w:t>Описание архитектуры системы и ее компонентов.</w:t></w:r></w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("stale_toc.docx")

    pdf_path = tmp_path / "stale_toc.pdf"
    pdf_doc = fitz.open()

    p1 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p1, 30, 80, "ТИТУЛЬНЫЙ ЛИСТ")

    p2 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p2, 80, 25, "СОДЕРЖАНИЕ")
    _draw_pdf_line(p2, 30, 40, "ВВЕДЕНИЕ .... 3")
    _draw_pdf_line(p2, 30, 52, "Раздел 1 АРХИТЕКТУРА СИСТЕМЫ .... 4")
    _draw_pdf_line(p2, 104, 286, "2")

    p3 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p3, 80, 25, "ВВЕДЕНИЕ")
    for idx, text in enumerate(filler):
        _draw_pdf_line(p3, 30, 60 + idx * 12, text)
    _draw_pdf_line(p3, 104, 286, "3")

    p4 = pdf_doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)
    _draw_pdf_line(p4, 30, 25, "Раздел 1 АРХИТЕКТУРА СИСТЕМЫ")
    _draw_pdf_line(p4, 30, 50, "Описание архитектуры системы и ее компонентов.")
    _draw_pdf_line(p4, 104, 286, "4")

    pdf_doc.save(str(pdf_path))
    pdf_doc.close()

    doc = DocxParserPipeline(docx_path=docx_path, pdf_path=pdf_path).parse()

    assert doc.meta.pagination_source == "pdf_exact"
    toc = next(b for b in doc.blocks if isinstance(b, TocContainerBlock))
    assert toc.page_start_est == 2
    assert toc.page_end_est == 2
    assert [e.actual_heading_page_est for e in toc.entries] == [3, 4]
    assert [e.page_number_delta for e in toc.entries] == [1, 1]

    intro = next(
        b
        for b in doc.blocks
        if isinstance(b, ParagraphBlock) and b.text_visible.strip() == "ВВЕДЕНИЕ"
    )
    assert intro.page_start_est == 3


def test_unprefixed_toc_pages_and_appendix_word_boundaries(
    docx_builder: DocxPackageBuilder,
) -> None:
    body_xml = """
    <w:p><w:r><w:t>ВВЕДЕНИЕ\t4</w:t></w:r></w:p>
    <w:p><w:r><w:t>Список используемых источников\t12</w:t></w:r></w:p>
    <w:p>
      <w:r><w:t>См. Приложение А, приложения чтобы ознакомиться, приложения Dпосреди слова.</w:t></w:r>
    </w:p>
    """
    docx_path = docx_builder.set_body(body_xml).build("toc_and_appendix.docx")
    doc = DocxParserPipeline(docx_path).parse()
    toc = next(b for b in doc.blocks if isinstance(b, TocContainerBlock))
    intro_entry, bibliography_entry = toc.entries

    assert intro_entry.extracted_number is None
    assert intro_entry.extracted_title == "ВВЕДЕНИЕ"
    assert intro_entry.extracted_page_str == "4"
    assert bibliography_entry.extracted_number is None
    assert bibliography_entry.extracted_title == "Список используемых источников"
    assert bibliography_entry.extracted_page_str == "12"
    no_pdf_json = doc.model_dump(mode="json")
    assert no_pdf_json["meta"]["pagination_source"] != "pdf_exact"
    assert "pdf_pages_audit" not in no_pdf_json
    assert not any(key.startswith("pdf_") for key in no_pdf_json["meta"])
    assert not any(
        key.startswith("pdf_")
        for block in no_pdf_json["blocks"]
        for key in block
    )

    appendix_mentions = [
        mention
        for mention in doc.cross_ref_index.mentions
        if mention.ref_type == "appendix"
    ]
    assert [mention.raw_snippet for mention in appendix_mentions] == ["Приложение А"]