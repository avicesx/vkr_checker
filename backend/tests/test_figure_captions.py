"""Распознавание подписей рисунков «Скриншот N.» и связанные аномалии.

Подпись может стоять как над иллюстрацией (типично для скриншотов), так и под
ней («Рисунок N — ...»). Тесты фиксируют оба варианта привязки и правила
аномалий: иллюстрация без подписи, подпись без объекта, отсутствие ссылки
в тексте, пропуск/дубль/нарушение порядка нумерации.
"""
from __future__ import annotations

from conftest import DocxPackageBuilder, make_fake_png_bytes
from docx_parser.pipeline import DocxParserPipeline


IMAGE_PARAGRAPH = """
<w:p>
  <w:r>
    <w:drawing>
      <wp:inline>
        <wp:extent cx="914400" cy="914400"/>
        <wp:docPr id="{doc_pr}" name="Picture {doc_pr}"/>
        <a:graphic>
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
            <pic:pic>
              <pic:blipFill><a:blip r:embed="{r_id}"/></pic:blipFill>
            </pic:pic>
          </a:graphicData>
        </a:graphic>
      </wp:inline>
    </w:drawing>
  </w:r>
</w:p>
"""

TEXT_PARAGRAPH = "<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def _image(builder: DocxPackageBuilder, r_id: str, filename: str, doc_pr: int) -> str:
    """Регистрирует PNG и возвращает XML абзаца с inline-картинкой."""
    builder.add_rel(r_id, f"media/{filename}")
    builder.set_bytes(f"word/media/{filename}", make_fake_png_bytes(40, 30))
    return IMAGE_PARAGRAPH.format(doc_pr=doc_pr, r_id=r_id)


def test_screenshot_captions_above_images_are_linked_and_numbering_checked(
    docx_builder: DocxPackageBuilder,
) -> None:
    """«Скриншот N.» над рисунком: подписи распознаны, дубль и пропуск — в аномалиях."""
    body = "".join(
        [
            _image(docx_builder, "rId3", "orphan.png", 3),
            TEXT_PARAGRAPH.format(text="В тексте используется скриншот 1."),
            TEXT_PARAGRAPH.format(text="Скриншот 1. Первый экран установки"),
            _image(docx_builder, "rId1", "screen1.png", 1),
            TEXT_PARAGRAPH.format(text="Скриншот 3. Третий экран установки"),
            _image(docx_builder, "rId2", "screen3.png", 2),
            TEXT_PARAGRAPH.format(text="Скриншот 3. Повтор номера без иллюстрации"),
        ]
    )
    docx_path = docx_builder.set_body(body).build("screenshot_captions.docx")
    doc = DocxParserPipeline(docx_path).parse()

    figures = doc.cross_ref_index.figures
    assert [f.number_str for f in figures] == ["1", "3", "3"]
    assert all(f.prefix_word.lower().startswith("скриншот") for f in figures)

    # Первые две подписи нашли свои иллюстрации, третья — нет.
    assert [f.target_object_id for f in figures[:2]] == ["vis_0002", "vis_0003"]
    assert figures[2].target_object_id is None
    assert doc.visuals["vis_0002"].caption_block_id == figures[0].caption_block_id
    assert doc.visuals["vis_0003"].caption_block_id == figures[1].caption_block_id

    assert doc.anomalies.uncaptioned_visual_ids == ["vis_0001"]
    assert doc.anomalies.figure_captions_without_visual_ids == [figures[2].caption_block_id]

    issue_kinds = [
        (issue.issue_kind, issue.number_str) for issue in doc.anomalies.figure_numbering_issues
    ]
    assert issue_kinds == [("gap", "2"), ("duplicate", "3")]

    # Ссылка "скриншот 1" разрешилась, номера 3 в тексте нет.
    assert figures[0].is_mentioned_in_text is True
    assert doc.anomalies.unreferenced_figure_ids == [figures[1].entity_id, figures[2].entity_id]


def test_figure_caption_below_image_still_links(
    docx_builder: DocxPackageBuilder,
) -> None:
    """Классический «Рисунок N — ...» под иллюстрацией: направление поиска меняется."""
    body = "".join(
        [
            _image(docx_builder, "rId1", "scheme1.png", 1),
            TEXT_PARAGRAPH.format(text="Рисунок 1 — Схема стенда"),
            TEXT_PARAGRAPH.format(text="Рисунок 2 — Схема сети"),
        ]
    )
    docx_path = docx_builder.set_body(body).build("figure_below.docx")
    doc = DocxParserPipeline(docx_path).parse()

    figures = doc.cross_ref_index.figures
    assert [f.number_str for f in figures] == ["1", "2"]
    assert figures[0].target_object_id == "vis_0001"
    assert doc.visuals["vis_0001"].caption_block_id == figures[0].caption_block_id

    # Вторая подпись осталась без иллюстрации: под ней ничего нет, а предыдущая занята.
    assert figures[1].target_object_id is None
    assert doc.anomalies.figure_captions_without_visual_ids == [figures[1].caption_block_id]
    assert doc.anomalies.uncaptioned_visual_ids == []
