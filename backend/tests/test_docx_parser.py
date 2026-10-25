from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from docx_parser.pipeline import parse_docx_to_json_file
from docx_parser.schema import (
    DocumentTreeNode,
    ParagraphBlock,
    ParsedDocxDocument,
    TableBlock,
    TocContainerBlock,
)


def _print_tree_recursive(nodes: list[DocumentTreeNode], depth: int = 0) -> None:
    indent = "  " * depth
    for n in nodes:
        sec_num = f"[{n.section_number}] " if n.section_number else ""
        print(
            f"{indent}├─ {sec_num}{n.title} "
            f"(роль: {n.semantic_role}, уровень: {n.level}, стр.: {n.page_start_est}..{n.page_end_est}, "
            f"блоков: {len(n.direct_block_ids)})"
        )
        if n.children:
            _print_tree_recursive(n.children, depth + 1)


def print_diagnostic_summary(doc: ParsedDocxDocument, elapsed_sec: float) -> None:
    sep = "=" * 88
    print(f"\n{sep}")
    print(f" ДИАГНОСТИЧЕСКИЙ ОТЧЕТ ПАРСЕРА: {doc.meta.filename}")
    print(sep)
    print(f"• Время парсинга:         {elapsed_sec:.2f} сек")
    print(f"• Приложение создания:    {doc.meta.application} (шаблон: {doc.meta.template_name})")
    print(f"• Автор / Изменил:        {doc.meta.author} / {doc.meta.last_modified_by}")
    print(f"• Источник пагинации:     {doc.meta.pagination_source} (всего страниц: {doc.meta.word_stat_pages or doc.sections[-1].page_end_est})")
    if doc.meta.pdf_filename:
        print(f"• Привязанный PDF:        {doc.meta.pdf_filename} (индекс синхронности: {(doc.meta.pdf_docx_sync_score or 0)*100:.1f}%)")
    if doc.anomalies.pdf_docx_mismatch_warning:
        print(f"  [!] ВНИМАНИЕ: {doc.anomalies.pdf_docx_mismatch_warning}")

    # 1. Геометрия разделов и колонтитулы
    print(f"\n--- 1. РАЗДЕЛЫ И ПОЛЯ СТРАНИЦ ({len(doc.sections)} шт.) ---")
    for s in doc.sections:
        ftr_def = s.footers.get("default")
        has_pg = ftr_def.has_page_field if ftr_def else False
        print(
            f"  Раздел #{s.section_idx}: {s.page_width_mm}x{s.page_height_mm} мм ({s.orientation}, А4={s.is_a4_standard}), "
            f"поля [Л={s.margins.left_mm}, П={s.margins.right_mm}, В={s.margins.top_mm}, Н={s.margins.bottom_mm} мм], "
            f"особая 1-я стр.={s.different_first_page}, поле PAGE в нижнем колонтитуле={has_pg}"
        )

    # 2. Постраничный аудит колонтитулов из PDF (если был передан PDF)
    if doc.pdf_pages_audit:
        print(f"\n--- 2. АУДИТ НОМЕРОВ СТРАНИЦ В УГЛАХ/КОЛОНТИТУЛАХ PDF ({len(doc.pdf_pages_audit)} стр.) ---")
        pages_with_num = [p for p in doc.pdf_pages_audit if p.has_rendered_page_number]
        pages_without_num = [p.page_num for p in doc.pdf_pages_audit if not p.has_rendered_page_number]
        mismatched_nums = [
            f"стр.{p.page_num}->'{p.rendered_page_number_raw}'"
            for p in pages_with_num
            if p.matches_physical_page_num is False
        ]
        print(f"  • Найдены номера на страницах: {len(pages_with_num)} из {len(doc.pdf_pages_audit)}")
        if pages_without_num:
            print(f"  • Страницы БЕЗ номера:         {pages_without_num[:15]}{'...' if len(pages_without_num) > 15 else ''}")
        if mismatched_nums:
            print(f"  • Расхождение цифры и листа:   {', '.join(mismatched_nums)}")

    # 3. Иерархическое дерево документа
    print("\n--- 3. СТРУКТУРНОЕ ДЕРЕВО ДОКУМЕНТА (document_tree) ---")
    _print_tree_recursive(doc.document_tree)

    # 4. Сверка оглавления (TOC)
    toc_blocks = [b for b in doc.blocks if isinstance(b, TocContainerBlock)]
    print(f"\n--- 4. АУДИТ ОГЛАВЛЕНИЯ (найдено блоков TOC: {len(toc_blocks)}) ---")
    for toc in toc_blocks:
        print(f"  Автособираемое оглавление: {toc.is_automated_field_or_sdt} | Строк: {len(toc.entries)}")
        for e in toc.entries:
            num_prefix = f"[{e.extracted_number}] " if e.extracted_number else ""
            t_ok = "OK" if e.is_title_exact_match else "НЕТ"
            p_ok = "OK" if e.is_page_number_match else "НЕТ"
            print(
                f"    {e.entry_index + 1:2d}. {num_prefix}{e.extracted_title[:45]:<45} "
                f"| стр. в TOC: {str(e.extracted_page_str):>3} | реальная: {str(e.actual_heading_page_est):>3} "
                f"| текст={t_ok}, стр={p_ok}"
            )

    # 5. Статистика шрифтов в основном тексте
    font_counter: Counter[str] = Counter()
    for b in doc.blocks:
        if isinstance(b, ParagraphBlock) and not b.is_empty_or_whitespace:
            for r in b.runs:
                if r.visible_text.strip():
                    key = f"{r.effective_props.font_hansi} ({r.effective_props.size_pt} pt, src={r.effective_props.font_source})"
                    font_counter[key] += len(r.visible_text)

    print("\n--- 5. РАСПРЕДЕЛЕНИЕ ШРИФТОВ ПО ОБЪЕМУ ТЕКСТА (в символах) ---")
    for font_desc, char_cnt in font_counter.most_common(8):
        print(f"  • {font_desc:<52} : {char_cnt:>6} симв.")

    # 6. Таблицы, рисунки, формулы и библиография
    tables = [b for b in doc.blocks if isinstance(b, TableBlock)]
    multi_page_tables = [t for t in tables if t.spans_multiple_pages]
    wide_tables = [t for t in tables if t.exceeds_page_text_width]

    print("\n--- 6. ОБЪЕКТЫ, ССЫЛКИ И БИБЛИОГРАФИЯ ---")
    print(
        f"  • Таблиц в потоке:       {len(tables)} (многостраничных: {len(multi_page_tables)}, выходящих за поля: {len(wide_tables)})"
    )
    print(f"  • Визуальных объектов:   {len(doc.visuals)} (подписей рисунков: {len(doc.cross_ref_index.figures)})")
    print(f"  • Формул (OMML/OLE):     {len(doc.cross_ref_index.equations)}")
    print(
        f"  • Источников в списке:   {len(doc.cross_ref_index.bibliography)} "
        f"(процитировано в тексте: {sum(1 for b in doc.cross_ref_index.bibliography if b.is_cited_in_text)})"
    )
    print(f"  • Перекрестных ссылок:   {len(doc.cross_ref_index.mentions)}")

    # 7. Аномалии и микротипографика
    total_missing_nbsp = sum(
        len(b.typography.missing_nbsp) for b in doc.blocks if isinstance(b, ParagraphBlock)
    )
    total_multi_spaces = sum(
        len(b.typography.multiple_spaces) for b in doc.blocks if isinstance(b, ParagraphBlock)
    )
    total_lead_spaces = sum(
        1 for b in doc.blocks if isinstance(b, ParagraphBlock) and b.typography.leading_spaces_count > 0
    )

    print("\n--- 7. АНОМАЛИИ И ОШИБКИ МИКРОТИПОГРАФИКИ ---")
    print(f"  • Кластеров пустых строк подряд:   {len(doc.anomalies.empty_paragraph_clusters)}")
    print(f"  • Блоков с не-черным текстом:      {len(doc.anomalies.non_black_text_block_ids)}")
    print(f"  • Блоков с фоновой заливкой:       {len(doc.anomalies.shaded_background_block_ids)}")
    print(f"  • Непринятых правок Track Changes: {len(doc.anomalies.unaccepted_track_changes)}")
    print(f"  • Забытых комментариев:            {len(doc.anomalies.leftover_comments)}")
    print(f"  • Абзацев с лидирующими пробелами: {total_lead_spaces}")
    print(f"  • Множественных пробелов внутри:   {total_multi_spaces}")
    print(f"  • Пропущенных неразрывных пробелов:{total_missing_nbsp}")
    print(f"{sep}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ручной запуск и диагностика парсера ВКР (.docx + опциональный .pdf -> .json)"
    )
    parser.add_argument(
        "docx",
        nargs="?",
        default="test.docx",
        help="Путь к входному файлу .docx (по умолчанию: test.docx)",
    )
    parser.add_argument(
        "--pdf",
        default=None,
        help="Путь к эталонному файлу .pdf (если не указан, ищется одноименный .pdf рядом с .docx)",
    )
    parser.add_argument(
        "--out",
        default="test_out.json",
        help="Путь для сохранения выходного JSON (по умолчанию: test_out.json)",
    )
    args = parser.parse_args()

    docx_path = Path(args.docx)
    if not docx_path.exists():
        print(f"[ОШИБКА] Файл '{docx_path}' не найден.", file=sys.stderr)
        sys.exit(1)

    pdf_path: Optional[Path] = Path(args.pdf) if args.pdf else None
    if pdf_path is None:
        auto_pdf = docx_path.with_suffix(".pdf")
        if auto_pdf.exists():
            pdf_path = auto_pdf
            print(f"[*] Автоматически обнаружен парный PDF-файл: {pdf_path}")

    t0 = time.perf_counter()
    parsed_doc = parse_docx_to_json_file(
        docx_path=docx_path,
        pdf_path=pdf_path,
        output_json_path=args.out,
    )
    elapsed = time.perf_counter() - t0

    print_diagnostic_summary(parsed_doc, elapsed)
    print(f"[+] Полный JSON успешно сохранен в: {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()