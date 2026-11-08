# Архитектура парсинга документов: режимы `docx_only`, `pdf_only`, `docx_pdf`

Документ фиксирует контракт модуля парсинга и описывает фактическую реализацию:
ядро контракта, DOCX- и PDF-конвейеры, сервис и сквозные проверки. Имена
полей, значения enum и правила сериализации считаются замороженными: менять их
можно только синхронно во всех потребителях и с повышением `schema_version`.

- Пакет: `backend/parser` — слои `core`, `docx`, `pdf`
- Корневая модель: `ParsedDocument` (исторический алиас `ParsedDocxDocument`)
- `schema_version`: **`3.0.0`**
- Контракт режимов: `backend/parser/core/mode.py`
- JSON-контракт: `backend/parser/core/schema.py`

---

## 1. Три режима разбора

| Режим (`ParseMode`) | Что является источником структуры | Что является источником страниц | Назначение |
|---|---|---|---|
| `docx_only` | DOCX (стили, нумерация, секции, сноски, перекрестные ссылки) | Оценка по геометрии DOCX, нативные `lastRenderedPageBreak` или `docProps/app.xml` | Нормоконтроль оформления без PDF; работает всегда, когда есть DOCX |
| `pdf_only` | PDF (эвристическое восстановление абзацев по геометрии) | Сам PDF (число страниц точное) | Проверка готового PDF без исходника: пагинация, номера страниц, границы текста |
| `docx_pdf` | DOCX | Эталонный PDF, сверенный с DOCX | Полный нормоконтроль: форматирование из DOCX + физические страницы из PDF |

Ключевая идея: **режим определяет не набор файлов, а источник истины**.
`docx_only` никогда не читает PDF (даже если файл передан), `pdf_only` не
требует DOCX, `docx_pdf` требует оба файла.

### 1.1. Автоматический выбор режима

```python
from parser.core.mode import (
    DEFAULT_PARSE_MODE,   # "auto"
    PARSE_MODES,          # ("docx_only", "pdf_only", "docx_pdf")
    ParseMode,            # Literal["docx_only", "pdf_only", "docx_pdf"]
    detect_mode,          # detect_mode(docx_path, pdf_path, requested) -> ParseMode
    normalize_parse_mode, # None/""/"auto" -> "auto"; иначе проверка значения
)
```

Правила `detect_mode(docx_path, pdf_path, requested)`:

| `requested` | DOCX | PDF | Результат |
|---|---|---|---|
| `None` / `"auto"` | есть | есть | `docx_pdf` |
| `None` / `"auto"` | есть | нет | `docx_only` |
| `None` / `"auto"` | нет | есть | `pdf_only` |
| `None` / `"auto"` | нет | нет | `ValueError` |
| `"docx_only"` | есть | любой | `docx_only` (PDF игнорируется) |
| `"pdf_only"` | любой | есть | `pdf_only` (DOCX игнорируется) |
| `"docx_pdf"` | есть | есть | `docx_pdf` |
| явный режим | не хватает обязательного файла | — | `ValueError` |
| неизвестная строка | — | — | `ValueError` |

`None`, пустая строка и `"auto"` равнозначны; регистр и пробелы нормализуются
(`normalize_parse_mode(" PDF_ONLY ") -> "pdf_only"`). Наличие файла определяется
по непустому пути (`None` и `""` — «файл не передан»), существование файла
проверяет сервис/парсер, а не `detect_mode`.

---

## 2. Границы модулей

```
services (оркестрация)
 │  detect_mode -> выбор парсера -> сборка ParsedDocument -> валидация
 ├──────────────► parser.core
 │                  schema.py          — единственный JSON-контракт
 │                  mode.py            — контракт режимов
 ├──────────────► parser.docx
 │                  pipeline.py        — DOCX -> ParsedDocument (docx_only/docx_pdf)
 │                  pdf_page_aligner.py — обогащение страниц из PDF (docx_pdf)
 │                  pdf_page_text.py   — общее извлечение строк/изображений PDF
 │                  pdf_text_utils.py  — общие единицы и нормализация текста
 └──────────────► parser.pdf
                    PDF -> блоки, страницы, координаты -> ParsedDocument (pdf_only)
```

Правила зависимостей:

- `parser.core.schema` не импортирует `parser.docx`, `parser.pdf` и `services`:
  это чистый контракт данных, он не знает ни о файлах, ни о способе вызова.
- `parser.core.mode` — контракт режимов (`ParseMode`, `PARSE_MODES`,
  `detect_mode`, `normalize_parse_mode`). Сам выбор режима и разбор входного
  запроса выполняет `services`.
- `parser.docx` ничего не знает о способе вызова: `DocxParserPipeline`
  принимает пути и разбирает DOCX (+PDF как обогащение).
- `parser.pdf` импортирует `parser.core.schema` и общие модули извлечения
  `parser.docx.pdf_page_text` / `parser.docx.pdf_text_utils`, чтобы складывать
  результат в те же модели и читать PDF так же, как `pdf_page_aligner`. При этом
  он **не** импортирует `parser.docx.pipeline` (иначе цикл и дублирование
  DOCX-конвейера).
- `parser/__init__.py` не импортирует слои, а `parser/docx/__init__.py` использует
  ленивые импорты (PEP 562 `__getattr__`): `import parser` и `import parser.docx`
  не поднимают `pipeline.py`, `converter.py`, lxml, pydantic и pymupdf, пока не
  запрошен соответствующий публичный символ. Это проверяется тестом в чистом
  процессе: после `import parser.pdf` в `sys.modules` нет
  `parser.docx.pipeline` и `parser.docx.converter`, после `import parser.core` —
  только `parser.core`.
- `pdf_page_text.py` — единственное место, где вызывается
  `page.get_text("dict")`; и `parser.pdf.extractor`, и
  `parser.docx.pdf_page_aligner` используют его функции. `pdf_text_utils.py`
  содержит `normalize_compact`, `parse_page_number`, `PT_TO_MM`, `pt_to_mm`.
- `services` — единственное место, где встречаются оба парсера; импорты
  парсеров внутри сервиса ленивые.
- `rule_engine` — внешний потребитель: читает `ParsedDocxDocument`
  (алиас `ParsedDocument`) и не менялся.

---

## 3. Финальные имена полей и значений enum

### 3.1. Корневая модель `ParsedDocument`

Порядок и значения полей (все новые поля имеют безопасные дефолты):

| Поле | Тип | Дефолт | Смысл |
|---|---|---|---|
| `schema_version` | `str` | `"3.0.0"` | Версия JSON-контракта |
| `parse_mode` | `ParseMode` | `"docx_only"` | Фактический режим разбора |
| `source` | `SourceInfo` | `has_docx=True, has_pdf=False` | Фактические источники и их хэши |
| `capabilities` | `DocumentCapabilities` | DOCX-набор | Какие данные реально извлечены |
| `page_count` | `PageCountInfo` | `1`, `geometric_estimation`, `low` | Число страниц и способ получения |
| `meta` | `DocumentMeta` | обязателен | Метаданные файла, пагинация, sync-score |
| `settings` | `GlobalSettings` | обязателен | `word/settings.xml` |
| `theme` | `ThemeData` | обязателен | Тема оформления |
| `styles` | `Dict[str, StyleDefinition]` | `{}` | Разобранные стили |
| `sections` | `List[SectionProperties]` | `[]` | Секции и диапазоны страниц |
| `pdf_pages_audit` | `List[PdfPageAudit]` | `[]` | Постраничный аудит PDF |
| `blocks` | `List[BlockUnion]` | `[]` | Абзацы и таблицы |
| `visuals` | `Dict[str, VisualObject]` | `{}` | Визуальные объекты |
| `footnotes` | `List[FootnoteItem]` | `[]` | Сноски и концевые сноски |
| `document_tree` | `List[DocumentTreeNode]` | `[]` | Дерево документа |
| `cross_ref_index` | `CrossReferenceIndex` | пустой | Оглавление, подписи, ссылки |
| `anomalies` | `DocumentAnomalies` | пустой | Аномалии оформления |

`ParsedDocxDocument = ParsedDocument` — алиас того же класса, сохраняется для
`rule_engine` и старых импортов (`from parser.core.schema import ParsedDocxDocument`).

### 3.2. `SourceInfo`

| Поле | Тип | Обязательное | Смысл |
|---|---|---|---|
| `mode` | `ParseMode` | да | Совпадает с `parse_mode` корневой модели |
| `has_docx` | `bool` | да | DOCX реально участвовал в разборе |
| `has_pdf` | `bool` | да | PDF реально участвовал в разборе (управляет сериализацией `pdf_*`) |
| `docx_filename` | `Optional[str]` | нет | Имя исходного входного файла (`.docx`, `.doc`, `.rtf`, `.odt`); для legacy — до конвертации |
| `docx_sha256` | `Optional[str]` | нет | SHA-256 того же исходного файла; **не** хэш результата LibreOffice (он в `meta.file_sha256`) |
| `pdf_filename` | `Optional[str]` | нет | Имя эталонного PDF |
| `pdf_sha256` | `Optional[str]` | нет | SHA-256 эталонного PDF |

Пара `source.docx_filename`/`docx_sha256` всегда описывает один и тот же файл —
исходный документ пользователя. `meta.filename`/`meta.file_sha256` описывают уже
разобранный `.docx`: для нативного файла значения совпадают, для legacy-формата
`meta` содержит результат конвертации. Прямой вызов `DocxParserPipeline` или
`parse_any_to_json_file` (без сервиса) заполняет `source` из `meta`, то есть
описывает сконвертированный DOCX; это правило зафиксировано в PIPELINE 3.3.

### 3.3. `DocumentCapabilities` (все поля `bool`)

| Поле | Смысл |
|---|---|
| `styles_parsed` | Каскад стилей и `docDefaults` разобран |
| `numbering_parsed` | `numbering.xml` и уровни списков разобраны |
| `sections_parsed` | Секции (`w:sectPr`) и их страницы разобраны |
| `exact_pagination` | Пагинация точная, как в PDF (`pagination_source="pdf_exact"`) |
| `pdf_layout_coordinates` | Есть координаты вёрстки из PDF |
| `visual_registry` | Реестр визуальных объектов DOCX заполнен |
| `footnotes_parsed` | Сноски и концевые сноски разобраны |
| `cross_references` | Оглавление, подписи и перекрестные ссылки разобраны |
| `word_metadata` | Метаданные `docProps` и статистика Word разобраны |
| `page_count_estimated` | Число страниц оценочное, а не измерено |

### 3.4. `PageCountInfo`

| Поле | Тип | Значения |
|---|---|---|
| `total_pages` | `int` (`ge=1`) | Всегда `>= 1` |
| `method` | `Literal` | `"pdf_exact"`, `"docx_native_breaks"`, `"geometric_estimation"`, `"app_xml_metadata"` |
| `confidence` | `Literal` | `"high"`, `"medium"`, `"low"` |
| `note` | `Optional[str]` | Пояснение для оценочных значений |

### 3.5. `PropertySource`

Допустимые значения (добавлено `pdf_estimate`):

```
"direct" | "character_style" | "paragraph_style" | "numbering_style" |
"table_style" | "doc_defaults" | "theme" | "engine_default" | "pdf_estimate"
```

`pdf_estimate` — значение свойства измерено по геометрии PDF (только в режимах
`pdf_only` и `docx_pdf`); в `docx_only` не встречается.

### 3.6. Смежные enum и поля, важные для режимов

| Место | Поле | Значения |
|---|---|---|
| `meta` | `pagination_source` | `"pdf_exact"`, `"docx_native_breaks"`, `"geometric_fallback"` |
| `meta` | `pdf_filename`, `pdf_sha256`, `rendered_page_numbers_present`, `pdf_docx_sync_score` | Заполняются только с PDF |
| `PdfPageAudit` | `page_number_location` | `"header"`, `"footer"`, `"none"` |
| `PdfPageAudit` | `page_number_alignment` | `"left"`, `"center"`, `"right"`, `"none"` |
| блоки | `pdf_*_mm`, `pdf_lines_count_*`, `pdf_has_hyphenated_break`, `pdf_repeated_header_above` | Только с PDF |
| `anomalies` | `pdf_docx_mismatch_warning` | Только с PDF |

---

## 4. Матрица «поле / возможность × режим»

Обозначения: **да** — заполняется, **оценка** — заполняется эвристически и
помечается как оценочное, **пусто** — не заполняется (пустая коллекция,
`None`, `False` или дефолт модели), **удаляется** — ключ отсутствует в JSON.

### 4.1. Корневые поля

| Поле / признак | `docx_only` | `pdf_only` | `docx_pdf` |
|---|---|---|---|
| `parse_mode` | `"docx_only"` | `"pdf_only"` | `"docx_pdf"` |
| `source.has_docx` / `has_pdf` | `True` / `False` | `False` / `True` | `True` / `True` |
| `source.docx_filename`, `docx_sha256` | да | `None` | да |
| `source.pdf_filename`, `pdf_sha256` | `None` (ключи удаляются) | да | да |
| `meta` | да | да (PDF-метаданные + синтезированные поля) | да |
| `meta.pdf_filename`, `pdf_sha256` | `None`, ключи удаляются | по желанию (на сериализацию не влияет) | да |
| `meta.pagination_source` | `"geometric_fallback"` или `"docx_native_breaks"` | `"pdf_exact"` | `"pdf_exact"` при успешной сверке; иначе `"docx_native_breaks"`/`"geometric_fallback"` |
| `meta.pdf_docx_sync_score` | `None`, ключ удаляется | `None` | да (0.0…1.0) |
| `settings` | да | дефолты модели | да |
| `theme` | да | дефолты модели | да |
| `styles` | да | `{}` | да |
| `sections` | да | да: секции, синтезированные по группам страниц одного размера (не `w:sectPr`) | да |
| `pdf_pages_audit` | `[]`, ключ удаляется | да | да |
| `blocks` | да | да (абзацы/таблицы восстановлены по геометрии) | да |
| `visuals` | да | да: только растровые изображения, извлечённые из PDF | да |
| `footnotes` | да | пусто | да |
| `document_tree` | да | оценка (по заголовкам из PDF) | да |
| `cross_ref_index` | да | пусто (дефолт модели; перекрёстные ссылки DOCX недоступны) | да |
| `anomalies` | да | пусто (дефолт модели) | да |
| `page_count` | оценка (`docx_native_breaks`/`geometric_estimation`) | да, `pdf_exact`, `high` | да, `pdf_exact`, `high` (даже при расхождении текста — с пояснением в `note`) |
| `capabilities.*` | см. 4.2 | см. 4.2 | см. 4.2 |

### 4.2. `DocumentCapabilities`

| Возможность | `docx_only` | `pdf_only` | `docx_pdf` |
|---|---|---|---|
| `styles_parsed` | `True` | `False` | `True` |
| `numbering_parsed` | `True` | `False` | `True` |
| `sections_parsed` | `True` | `False` | `True` |
| `exact_pagination` | `False` | `True` | `True`, если сверка с PDF успешна, иначе `False` |
| `pdf_layout_coordinates` | `False` | `True` | `True` при применённом выравнивании; при `alignment_applied=False` сервис выставляет `False` (PDF прочитан, но координат у блоков нет) |
| `visual_registry` | `True` | `False` | `True` |
| `footnotes_parsed` | `True` | `False` | `True` |
| `cross_references` | `True` | `False` | `True` |
| `word_metadata` | `True` | `False` | `True` |
| `page_count_estimated` | `True` (кроме `docx_native_breaks`) | `False` | `False` |

Потребитель обязан проверять флаг перед использованием поля. Исключение для
`page_count_estimated`: если число страниц восстановлено по маркерам
`lastRenderedPageBreak` (`method="docx_native_breaks"`), оно не оценочное.

### 4.2.1. Семантика `pdf_only`: флаг `False` ≠ пустая коллекция

В `pdf_only` флаги `styles_parsed`, `numbering_parsed`, `sections_parsed`,
`visual_registry`, `footnotes_parsed`, `cross_references` и `word_metadata`
равны `False` потому, что **DOCX-производных данных в этом режиме нет** (нет
`styles.xml`, `numbering.xml`, `w:sectPr`, `word/footnotes.xml` и `docProps`).
Это не означает, что соответствующие поля JSON пусты:

| Поле JSON | `pdf_only` |
|---|---|
| `styles` | `{}` — стилей DOCX действительно нет |
| `sections` | **может быть непустым**: секции синтезированы по геометрии PDF (группы страниц одинакового размера, оцененные поля) |
| `visuals` | **может быть непустым**: зарегистрированы растровые изображения, извлечённые из PDF |
| `footnotes` | `[]` — сноски DOCX не извлекаются |
| `cross_ref_index` | пустой индекс — перекрёстные ссылки DOCX не разбираются |

Поэтому:

- `capabilities.sections_parsed=False` читается как «секции не из `w:sectPr`»;
  данные в `sections` — оценка PDF, а не DOCX-геометрия;
- `capabilities.visual_registry=False` читается как «реестр визуальных объектов
  DOCX не заполнен»; `visuals` — только растровые изображения PDF, без фигур,
  диаграмм, OLE и OMML;
- `styles_parsed=False` и `numbering_parsed=False` означают, что каскад стилей и
  списки DOCX недоступны: проверки rule_engine, которым нужны стили/нумерация,
  должны пропускаться, даже если отдельные свойства ранов заполнены
  `PropertySource="pdf_estimate"`;
- `sections`/`visuals` в `pdf_only` нельзя использовать как доказательство
  `sections_parsed`/`visual_registry`: матрица 4.2 описывает происхождение
  данных, а не непустоту полей.

Сервис (`services/document_parsing.py`) явно выставляет эти флаги по матрице и
не переопределяет их по непустоте коллекций.

### 4.3. `page_count.method`

| Режим | Основной источник | `method` | `confidence` |
|---|---|---|---|
| `docx_only` | маркеры `lastRenderedPageBreak` в DOCX | `docx_native_breaks` | `medium` |
| `docx_only` | оценка по геометрии DOCX (секции, блоки, вложенные ячейки, оглавление) | `geometric_estimation` | `low` |
| `pdf_only` | число страниц PDF | `pdf_exact` | `high` |
| `docx_pdf` | число страниц PDF (аудит) | `pdf_exact` | `high` |

`app_xml_metadata`/`low` в рабочем потоке не выставляется: `docx_only` сохраняет
`meta.word_stat_pages` из `docProps/app.xml`, но не использует его для
`page_count` (значение `Pages` часто устаревшее). Этот метод встречается только
при валидации старого JSON 2.x, где нового поля `page_count` нет (раздел 6.1).

В `docx_pdf` при `sync_score < 0.80` число страниц PDF всё равно измерено
(`pdf_exact`/`high`), но `page_count.note` объясняет, что блоки не привязаны к
страницам; `capabilities.exact_pagination` при этом остаётся `False`. Это два
разных пространства страниц, и контракт фиксирует расхождение явно:

- `page_count.total_pages` — **фактическое** число листов PDF (в `meta.word_stat_pages`
  и `pdf_pages_audit` тоже описан PDF);
- `sections[-1].page_end_est` синхронизирован с числом листов PDF;
- `block.page_start_est`/`page_end_est` в этой ветке — **оценка DOCX** и может
  превышать `page_count.total_pages`; `pdf_*`-координат у блоков нет;
- машинный признак неприменённого выравнивания —
  `capabilities.pdf_layout_coordinates=False` (вместе с `exact_pagination=False`),
  а `page_count.note` содержит число страниц PDF и `sync_score`.

Потребитель, которому нужны страницы блоков, обязан проверять
`capabilities.pdf_layout_coordinates`/`exact_pagination`, а не сравнивать номера
блоков с `page_count.total_pages`.

`total_pages >= 1` всегда: даже пустой документ описывается одной страницей,
чтобы `PageCountInfo` оставался валидным.

### 4.4. Что видит `rule_engine`

`rule_engine` читает `document.meta.pagination_source` и
`document.schema_version` (попадает в `RuleCheckReport.parser_schema_version`).
В `pdf_only` проверки, которым нужны стили, нумерация, секции и сноски, должны
пропускаться по `capabilities` — флаги `styles_parsed`, `numbering_parsed`,
`sections_parsed`, `visual_registry`, `footnotes_parsed`, `cross_references`,
`word_metadata` там `False`. При этом `sections` и `visuals` могут быть
непустыми (оценка PDF, см. 4.2.1): наличие элементов в этих коллекциях не
разрешает запускать DOCX-проверки, рассчитанные на `w:sectPr` и реестр DOCX.
DOCX-ветка `rule_engine` не меняется.

---

## 5. Поток данных по режимам

### 5.1. `docx_only`

```
файл (.docx/.doc/.rtf/.odt)
  └─ parser.docx.converter.convert_to_docx (.docx — passthrough, legacy — LibreOffice)
      └─ DocxParserPipeline(docx_path).parse()  # в сервисе — parse_docx_only()
          └─ ParsedDocument(parse_mode="docx_only",
                            source=SourceInfo(mode="docx_only", has_docx=True, has_pdf=False),
                            capabilities=<DOCX-набор>, page_count=<оценка>,
                            meta/settings/theme/styles/sections/blocks/...)
              └─ model_dump(mode="json")  # pdf_* и rendered_page_numbers_present удалены
```

### 5.2. `pdf_only`

```
файл .pdf
  └─ parser.pdf: extractor -> layout -> structure
      ├─ blocks: ParagraphBlock/TableBlock/TocContainerBlock (эвристика по геометрии)
      ├─ sections: SectionProperties по группам страниц одного размера (оценка PDF)
      ├─ visuals: растровые изображения PDF (visual_kind="raster_image")
      ├─ pdf_pages_audit: PdfPageAudit по каждой странице
      ├─ document_tree: по найденным заголовкам
      └─ page_count = PageCountInfo(total_pages=len(pages), method="pdf_exact", confidence="high")
          └─ ParsedDocument(parse_mode="pdf_only",
                            source=SourceInfo(mode="pdf_only", has_docx=False, has_pdf=True,
                                              pdf_filename=..., pdf_sha256=...),
                            meta=DocumentMeta(filename=<имя PDF>, file_sha256=<хэш PDF>,
                                              pdf_filename=..., pagination_source="pdf_exact"),
                            capabilities=DocumentCapabilities(
                                styles_parsed=False, numbering_parsed=False, sections_parsed=False,
                                exact_pagination=True, pdf_layout_coordinates=True,
                                visual_registry=False, footnotes_parsed=False,
                                cross_references=False, word_metadata=False,
                                page_count_estimated=False))
```

`meta.filename`/`meta.file_sha256` обязательны и описывают сам PDF; `source`
всегда заполняется явно (не полагайтесь на вывод из `meta`). PDF-парсер
заполняет и `meta.pdf_filename`/`meta.pdf_sha256`, но для сериализации это не
обязательно: решающий признак — `source.has_pdf=True` (иначе сериализатор удалит
`pdf_*`-ключи). `sections`/`visuals` в `pdf_only` могут быть непустыми, хотя
`sections_parsed`/`visual_registry` равны `False` — это оценка PDF, а не данные
DOCX (см. 4.2.1). `styles={}`, `footnotes=[]`, `cross_ref_index` пустой,
`settings`/`theme` — дефолты модели.

Фактический результат на `manual_test/2.pdf`: `total_pages=69`, `method="pdf_exact"`,
`sections=1` (страницы 1–69), `blocks=407`, `pdf_pages_audit=69`.

Поля `meta`, которые заполняет только PDF-парсер:

| Поле `meta` | Значение в `pdf_only` | Происхождение |
|---|---|---|
| `filename`, `file_sha256` | имя и SHA-256 PDF | сам файл |
| `source_format` | `"pdf"` | формат источника |
| `converted_via_lo`, `conversion_tool`, `conversion_duration_ms`, `lo_version`, `lo_image` | `False` и `None` | конвертации не было |
| `pdf_filename`, `pdf_sha256` | имя и SHA-256 PDF | дублируют источник |
| `rendered_page_numbers_present` | `True`/`False` | аудит колонтитулов `layout.py` |
| `pagination_source` | `"pdf_exact"` | страницы измерены по PDF |
| `pdf_docx_sync_score` | `None` | DOCX в режиме нет |
| `word_stat_pages` | число страниц PDF | физические листы |
| `word_stat_words`, `word_stat_chars_with_spaces`, `word_stat_chars_no_spaces` | статистика по тексту PDF | текстовый слой (`structure.full_text`) |
| `author`, `last_modified_by`, `created_iso`, `modified_iso`, `template_name`, `application` | `None` | DOCX-метаданные `docProps` недоступны |

`meta.pdf_docx_sync_score` в `pdf_only` остаётся `None` (в JSON ключ сохраняется,
потому что `source.has_pdf=True`), а `word_stat_pages` совпадает с
`page_count.total_pages`.

### 5.3. `docx_pdf`

```
.docx + .pdf
  └─ DocxParserPipeline(docx_path, pdf_path).parse()
      ├─ DOCX -> blocks/styles/sections/footnotes/tree/xref
      └─ PdfPageAligner -> pdf_pages_audit, pdf_docx_sync_score, точные страницы
          └─ ParsedDocument(parse_mode="docx_pdf",
                            source=SourceInfo(mode="docx_pdf", has_docx=True, has_pdf=True, ...),
                            capabilities=DocumentCapabilities(
                                exact_pagination=<успешная сверка>,
                                pdf_layout_coordinates=<успешная сверка>, ...),
                            page_count=PageCountInfo(method="pdf_exact", confidence="high"))
```

Порог сверки — `sync_score >= 0.80`. При меньшем значении парсер пишет
`anomalies.pdf_docx_mismatch_warning`, не применяет координатную привязку
(`alignment_applied=False`) и оставляет `pagination_source` без `pdf_exact`
(`docx_native_breaks` или `geometric_fallback`). `pdf_pages_audit` сохраняется.
Сервис в этом случае:

- ставит `capabilities.exact_pagination=False` и
  `capabilities.pdf_layout_coordinates=False` — это машинный признак того, что
  координат вёрстки у блоков нет;
- **не** понижает `page_count`: число листов PDF измерено, поэтому остаётся
  `method="pdf_exact"`, `confidence="high"`, а в `page_count.note` появляется
  пояснение про расхождение текста и `sync_score`; фактическое число листов PDF
  остаётся в `meta.word_stat_pages` и `pdf_pages_audit`;
- синхронизирует `sections[-1].page_end_est` с числом листов PDF, но блоки
  сохраняют оценочные `page_start_est`/`page_end_est` DOCX и могут выходить за
  `page_count.total_pages` (см. 4.3);
- не удаляет `pdf_pages_audit` и `meta.pdf_docx_sync_score`.

Прямой вызов `DocxParserPipeline` (без сервиса) в этой ветке оставляет
`capabilities.pdf_layout_coordinates=True`, потому что PDF действительно
прочитан; нормализованный флаг `False` — часть контракта сервиса и CLI,
через которые идут все трёхрежимные потребители.

---

## 6. Совместимость и сериализация

### 6.1. Старый JSON (schema 2.x) валидируется без правок

Если в payload нет `parse_mode`/`source`/`capabilities`/`page_count`, корневая
модель достраивает их из legacy-полей (`model_validator(mode="before")`):

1. `parse_mode`: из `source.mode`, иначе `docx_pdf`, если заполнен
   `meta.pdf_filename` или непустой `pdf_pages_audit`, иначе `docx_only`.
2. `source`: `has_docx = (mode != "pdf_only")`,
   `docx_filename = meta.filename` и `docx_sha256 = meta.file_sha256` (для
   `pdf_only` — `None`); `pdf_filename = meta.pdf_filename`,
   `pdf_sha256 = meta.pdf_sha256`, а для `pdf_only` без `meta.pdf_filename`
   берутся `meta.filename`/`meta.file_sha256` (там описан сам PDF). Если
   `has_pdf == False`, все `pdf_*`-поля в `source` равны `None`.
3. `page_count` по приоритету:
   непустой `pdf_pages_audit` → `pdf_exact`/`high`;
   `meta.word_stat_pages` при наличии PDF → `app_xml_metadata`/`low`;
   `pagination_source == "docx_native_breaks"` → `docx_native_breaks`/`medium`
   (по максимальному `sections[].page_end_est`);
   `meta.word_stat_pages` → `app_xml_metadata`/`low`;
   иначе `geometric_estimation`/`low` (не менее одной страницы).
4. `capabilities`: DOCX-набор для `docx_only`/`docx_pdf`, пустой для `pdf_only`;
   `pdf_layout_coordinates = has_pdf`;
   `page_count_estimated = page_count.method not in {"pdf_exact", "docx_native_breaks"}`;
   `exact_pagination = (page_count.method == "pdf_exact")` для `pdf_only` и
   `meta.pagination_source == "pdf_exact"` для `docx_only`/`docx_pdf`.

Явно переданные поля не перетираются: продюсер всегда имеет приоритет над
выводом из `meta`. Обратный round-trip (`model_dump` → `model_validate`)
устойчив: JSON после сериализации валидируется без потерь и повторная
сериализация даёт тот же результат.

### 6.2. Правило удаления `pdf_*`

`model_serializer(mode="wrap")` удаляет PDF-данные — ключи `pdf_*`
(кроме `capabilities.pdf_layout_coordinates`), `rendered_page_numbers_present`
и `pdf_pages_audit` — **только если `source.has_pdf == False`**. Признак берётся
из `source`, а не из `meta.pdf_filename`:

- `docx_only`: `pdf_*`-ключи удаляются на всех уровнях (в `meta`, в блоках,
  в `source`, в `anomalies`); `pdf_pages_audit` отсутствует.
- `pdf_only` и `docx_pdf`: `pdf_*`-ключи остаются, даже если `meta.pdf_filename`
  не заполнен (у PDF-парсера PDF — единственный источник).
- `capabilities.pdf_layout_coordinates` не удаляется никогда: это контрактный
  флаг, а не PDF-данные; без PDF он равен `False`.

Удаление действует рекурсивно, поэтому вложенные `pdf_*`-поля абзацев, таблиц
и аудита подчиняются тому же правилу.

### 6.3. Прочие правила

- `schema_version` = `"3.0.0"`; `rule_engine` передаёт его в
  `RuleCheckReport.parser_schema_version` без изменений.
- `ParsedDocxDocument` — алиас `ParsedDocument`; импорт из `parser.docx` и
  `parser.core.schema` равнозначен.
- JSON детерминирован: в модели нет таймстемпов и абсолютных путей.
- Сериализация не зависит от режима, кроме правила 6.2: набор ключей
  определяется только `source.has_pdf`.
- Публичные имена `parser.docx` сохраняются: `DocxParserPipeline`,
  `parse_docx_to_json_file`, `parse_any_to_json_file`, `convert_to_docx`,
  `ParsedDocxDocument`.

---

## 7. Ограничения

- **PDF-структура эвристическая.** В `pdf_only` нет ни `styles.xml`, ни
  `numbering.xml`, ни `w:sectPr`: абзацы, таблицы и колонтитулы
  восстанавливаются по координатам текста. Возможны склейка/разрыв абзацев,
  неверное определение границ таблиц и колонок. Такие значения помечаются
  `PropertySource="pdf_estimate"` и `page_count.method`/`capabilities`, а не
  выдаются за точные.
- **`sections`/`visuals` в `pdf_only` — оценка PDF.** Они могут быть непустыми,
  хотя `sections_parsed`/`visual_registry` равны `False`: секции синтезированы
  по группам страниц одного размера, а `visuals` содержат только растровые
  изображения. Нельзя трактовать непустоту этих коллекций как наличие
  DOCX-данных (см. 4.2.1).
- **Таблицы PDF ищутся по векторным линиям.** Таблица без рамок (только
  отступы/пробелы) не распознаётся; оглавление без отточия остаётся обычными
  абзацами.
- **Без OCR.** Сканы и PDF без текстового слоя дают пустой `blocks`; сервис
  должен вернуть понятную ошибку или документ с `capabilities`, где данные
  отсутствуют. Распознавание изображений не выполняется.
- **Форматирование в `pdf_only` неполное.** Доступны размеры и гарнитуры,
  измеренные по PDF, но нет каскада стилей, нумерации списков, сносок и
  перекрестных ссылок DOCX. Проверки rule_engine, требующие этих данных,
  должны пропускаться по `capabilities`.
- **`docx_pdf` доверяет сверке.** PDF должен быть снят с того же документа, что
  и DOCX. При низком `pdf_docx_sync_score` парсер пишет
  `anomalies.pdf_docx_mismatch_warning`, а пагинация не помечается как точная.
  LibreOffice-конвертация меняет вёрстку, поэтому эталонный PDF нужно снимать с
  исходного файла, а не с результата конвертации.
- **`page_count.total_pages >= 1`.** Пустой документ описывается одной
  страницей; ноль страниц невозможен по контракту.
- **Шифрованные и повреждённые PDF** не поддерживаются: ошибка пробрасывается
  сервису, JSON не собирается.
- **Многоколоночная вёрстка и сложные таблицы** в `pdf_only` могут давать
  ложные абзацы; координаты (`pdf_*_mm`) позволяют потребителю проверить
  результат самостоятельно.

---

## 8. Реализация и проверки

Ниже — фактическое состояние подсистем: DOCX- и PDF-конвейеры, общее извлечение
PDF и сервис. Точки входа — сервис (`services`), CLI в `manual_test/` и
`rule_engine`.

| Что реализовано | Основные файлы |
|---|---|
| автономный `pdf_only`: извлечение, вёрстка, структура, точные страницы, `pdf_pages_audit` | `parser/pdf/extractor.py`, `parser/pdf/layout.py`, `parser/pdf/structure.py`, `parser/pdf/parser.py` |
| `docx_only`: DOCX-конвейер без PDF, оценка страниц (`docx_native_breaks`/`geometric_estimation`), `meta.pdf_* = None` | `parser/docx/pipeline.py` |
| сервис `parse_document`/`parse_to_json_file`, коды ошибок, CLI | `services/document_parsing.py` |
| общее извлечение PDF (`pdf_page_text.py`, `pdf_text_utils.py`), сквозная интеграция трёх режимов | `parser/docx/pdf_page_text.py`, `tests/test_integration_modes.py` |

Что проверяется автоматически:

- матрица режимов и `capabilities` из раздела 4;
- `docx_only` не читает PDF и не содержит `pdf_*`-ключей (кроме
  `capabilities.pdf_layout_coordinates`);
- `pdf_only` без DOCX/LibreOffice: точные страницы, `pdf_estimate` у свойств,
  `sections`/`visuals` заполнены оценкой PDF при `sections_parsed=False`;
- `docx_pdf` при совпадающей паре: `sync_score >= 0.80`, точные страницы и
  координаты; при расхождении — warning, `page_count` остаётся `pdf_exact` с
  пояснением, `exact_pagination=False`;
- legacy-JSON 2.x без новых полей валидируется, round-trip устойчив, удаление
  `pdf_*` выполняется только при `source.has_pdf=False`;
- `detect_mode` для всех комбинаций файлов и явных режимов;
- контракт сервиса и CLI: `parse_document`/`parse_to_json_file`, ошибки
  `ValueError`/`FileNotFoundError`/`DocumentParsingError`, три режима;
- границы импорта: `import parser.pdf` не поднимает DOCX-конвейер и lxml,
  `import parser.docx` — pipeline/converter и их зависимости, а
  `import parser.core` загружает только `parser.core`;
- обратная совместимость публичных имён `parser.docx`.

Команды (из `backend`):

```powershell
python -m compileall -q parser services rule_engine tests
python -m pytest tests -q
python -m pytest tests -q -m integration
```

Тесты по режимам: `tests/test_parse_modes_docx.py` (`docx_only`),
`tests/test_pdf_only_parser.py` (`pdf_only`), `tests/test_service_modes.py`
(сервис), `tests/test_import_boundaries.py` (границы импорта),
`tests/test_integration_modes.py` (сквозные сценарии, legacy-JSON, обратная
совместимость).
