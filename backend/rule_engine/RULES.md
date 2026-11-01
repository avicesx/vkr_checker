# Правила нормоконтроля (`rule_engine`)

Пакет проверяет оформление документа по декларативному набору правил и
возвращает отчет с адресом нарушения, ожидаемым и фактическим значением.
Разбор DOCX остается за `docx_parser`: правила читают готовый
`ParsedDocxDocument`, где каскад стилей уже разрешен, а единицы приведены к
пунктам и миллиметрам.

Первая итерация покрывает три проверки:

| Вид правила | Что проверяет | Объект |
|---|---|---|
| `font` | гарнитура, кегль, начертание | ран (фрагмент текста) внутри абзаца |
| `indent` | отступ первой строки, слева, справа | абзац |
| `margin` | левое, правое, верхнее, нижнее поле, корешковый отступ | секция |

---

## 1. Быстрый старт

```python
from docx_parser.pipeline import DocxParserPipeline
from rule_engine import DEFAULT_RULESET_PATH, RuleEngine, load_ruleset

document = DocxParserPipeline("vkr.docx").parse()
engine = RuleEngine(load_ruleset(DEFAULT_RULESET_PATH))
report = engine.check(document)

print(report.passed, report.summary.violations_total)
for violation in report.violations[:5]:
    print(violation.rule_id, violation.found_text, violation.location.block_id)
```

Документ, уже сохраненный в JSON парсера, проверяется без повторного разбора:

```python
report = RuleEngine(load_ruleset("rules.json")).check_json_file("vkr.json")
```

Ручной прогон с печатью сводки и выгрузкой `{имя}_rules.json`:

```bash
python manual_test/check_rules.py manual_test/test.docx \
    --rules rule_engine/rules/vkr_test_rules.json \
    --outdir manual_test/out
```

Флаги CLI: `--rules` (набор правил), `--pdf` (эталонный PDF, иначе ищется
одноименный рядом с `.docx`), `--outdir`/`--out` (куда писать отчет), `--limit`
(сколько нарушений печатать, по умолчанию 10).

---

## 2. Формат набора правил

Набор — один JSON-файл. Неизвестные ключи запрещены (`extra="forbid"`), поэтому
опечатка в имени поля падает на загрузке, а не молча игнорируется.

```json
{
  "ruleset_id": "vkr-rule-engine-test",
  "ruleset_version": "1.0.0",
  "description": "Тестовый набор",
  "rules": [
    {
      "rule_id": "font_body_text",
      "kind": "font",
      "severity": "error",
      "message": "Основной текст набирается гарнитурой Times New Roman, 14 pt",
      "requirement_ref": "Методические указания, п. 4.2",
      "target": {"scope": "body_text"},
      "expected": {"family": "Times New Roman", "size_pt": 14.0}
    }
  ]
}
```

Общие поля правила:

| Поле | Обязательно | Смысл |
|---|---|---|
| `rule_id` | да | уникальный идентификатор внутри набора |
| `kind` | да | `font`, `indent` или `margin` |
| `message` | да | формулировка требования для отчета |
| `severity` | нет | `error` (по умолчанию), `warning`, `info` |
| `requirement_ref` | нет | ссылка на пункт методических указаний |
| `target` | нет | область действия, по умолчанию `{"scope": "body_text"}` |
| `expected` | да | ожидаемые значения, зависят от `kind` |

`rule_id` в пределах набора уникальны; `ruleset_id` и `ruleset_version`
попадают в отчет, поэтому по ним видно, чем именно проверяли документ.

### Области действия (`target.scope`)

| `scope` | Что попадает в выборку |
|---|---|
| `body_text` | абзацы верхнего уровня `w:body` без заголовков, подписей и списков (по умолчанию) |
| `body_paragraphs` | все абзацы верхнего уровня `w:body` как есть, без исключений |
| `all_paragraphs` | тело, ячейки таблиц, колонтитулы и сноски |
| `table_cells` | только абзацы внутри ячеек таблиц |
| `headers_footers` | только колонтитулы всех секций |
| `footnotes` | только сноски и концевые сноски |
| `sections` | секции целиком (только для `kind="margin"`) |

Строки оглавления (`TocContainerBlock.raw_paragraphs`) в выборку не попадают
никогда: это навигация, а не текст работы. Правило `margin` обязано иметь
`scope="sections"`, правила `font` и `indent` — любую из абзацных областей.

Исключения по умолчанию действуют только для `body_text` — это семантическая
выборка «основного текста». Остальные области берут объекты как есть: правило
«все ячейки таблиц» не должно пропускать элементы списков внутри ячеек.

Флаги расширяют выборку `body_text`:

| Поле `target` | По умолчанию | Что делает при `true` |
|---|---|---|
| `include_headings` | `false` | проверяет абзацы с `heading_info` |
| `include_captions` | `false` | проверяет подписи рисунков, таблиц и приложений |
| `include_list_items` | `false` | проверяет элементы списков (`list_info` или итоговый `num_id`) |
| `include_empty` | `false` | проверяет пустые абзацы и абзацы только с графикой/формулой |
| `style_ids` | `[]` | ограничивает выборку перечисленными `styleId` (`[]` — без ограничения; работает во всех областях) |

Подписи определяются по `cross_ref_index` (`figures`, `tables`, `Apps`):
`caption_block_id` каждого объекта исключается из выборки.

---

## 3. Виды правил и `expected`

### `font` — шрифт рана

| Поле | По умолчанию | Смысл |
|---|---|---|
| `family` | `null` | гарнитура; сравнение без учета регистра и лишних пробелов |
| `size_pt` | `null` | кегль в пунктах (1…200) |
| `bold` | `null` | ожидаемое начертание |
| `italic` | `null` | ожидаемое начертание |
| `size_tolerance_pt` | `0.5` | допуск по кеглю, пункты |
| `check_complex_script` | `false` | дополнительно сверять `font_cs` |

Проверяются эффективные свойства рана (`RunFontPropertiesEffective`), то есть
результат каскада «документ → стиль абзаца → стиль символа → прямое
форматирование». Пропускаются раны без видимого текста, раны внутри полей
(`in_field`) и скрытый текст (`vanish_hidden`).

### `indent` — отступы абзаца

| Поле | По умолчанию | Смысл |
|---|---|---|
| `first_line_mm` | `null` | красная строка; отрицательное значение — висячий отступ |
| `left_mm` | `null` | отступ слева |
| `right_mm` | `null` | отступ справа |
| `tolerance_mm` | `0.5` | допуск, миллиметры |

### `margin` — поля страницы

| Поле | По умолчанию | Смысл |
|---|---|---|
| `left_mm`, `right_mm`, `top_mm`, `bottom_mm` | `null` | поля страницы |
| `gutter_mm` | `null` | корешковый отступ |
| `tolerance_mm` | `1.0` | допуск, миллиметры |

Правило применяется к каждой секции отдельно: в отчет попадает нарушение на
секцию, а не одно на документ. Незаданные поля (`null`) не проверяются.


---

## 4. Структура отчета

`RuleEngine.check()` возвращает `RuleCheckReport`:

```json
{
  "ruleset_id": "vkr-rule-engine-test",
  "ruleset_version": "1.0.0",
  "parser_schema_version": "2.4.0",
  "document": {
    "filename": "vkr.docx",
    "file_sha256": "…",
    "source_format": null,
    "pagination_source": "geometric_fallback",
    "total_pages_est": 80
  },
  "summary": {
    "rules_applied": 3,
    "objects_checked": 761,
    "violations_total": 9,
    "by_severity": {"error": 9},
    "by_kind": {"font": 5, "indent": 4},
    "by_rule": {"font_body_text": 5, "indent_first_line": 4}
  },
  "violations": [
    {
      "violation_id": "font_body_text:p_00041",
      "rule_id": "font_body_text",
      "kind": "font",
      "severity": "error",
      "message": "Основной текст набирается гарнитурой Times New Roman, 14 pt",
      "requirement_ref": "Методические указания, п. 4.2",
      "location": {
        "block_id": "p_00041",
        "section_idx": 0,
        "page_start_est": 5,
        "page_end_est": 5,
        "container_type": "body",
        "style_id": "BodyText2",
        "run_id": "p_00041_r1",
        "char_start": 33,
        "char_end": 310,
        "snippet": "основой дипломной работы являются…"
      },
      "expected": {"family": "Times New Roman"},
      "found": {"family": "Times New Roman CYR"},
      "expected_text": "Times New Roman",
      "found_text": "Times New Roman CYR",
      "occurrences": 1
    }
  ],
  "passed": false
}
```

Что важно знать про отчет:

- `expected`/`found` — машинно-читаемые значения только по тем полям, которые
  правило проверяло и которые не совпали. Ими удобно пользоваться в тестах и в
  интерфейсе.
- `expected_text`/`found_text` — те же значения словами, для выгрузки
  нормоконтролеру.
- Нарушение адресуется **абзацем**, а не раном: `occurrences` показывает,
  сколько ранов внутри абзаца не соответствует правилу. Для `margin` адрес —
  секция (`violation_id = "<rule_id>:section:<idx>"`).
- `passed` равен `true`, только если нет нарушений уровня `error`. Правила с
  `severity="warning"` или `"info"` не блокируют документ.
- Отчет детерминирован: нет таймстемпов и абсолютных путей, поэтому два прогона
  на одном файле дают одинаковый JSON. Это позволяет сравнивать отчеты
  «до/после» правок.

---

## 5. Что проверяется не всегда

Сознательные ограничения первой итерации:

1. **Шрифт абзацного знака** (`para_mark_font_effective`) не проверяется —
   нарушение ищется по видимому тексту.
2. **Complex script** (`font_cs`) по умолчанию выключен: в русских документах он
   часто заполняется шумом. Включается флагом `check_complex_script`.
3. **Поля и отступы** сравниваются с абсолютным значением допуска; специальных
   правил для «допустимого диапазона» нет.
4. **Гарнитура** сравнивается точно (без учета регистра и пробелов). Алиасы
   вроде `Times New Roman CYR` попадут в нарушения — это осознанно, набор правил
   можно расширить отдельным полем.
5. **Стили не анализируются как цепочка**: проверяется результат каскада, а не
   то, в каком стиле задано значение. Для отладки в `found` отступа добавляется
   `first_line_source` (`direct`, `paragraph_style`, `doc_defaults` и т.п.).
6. **Оглавление** исключено из выборки: строки `TocContainerBlock.raw_paragraphs`
   не считаются основным текстом.


---

## 6. Как добавить правило

1. Возьмите существующий пример из `rule_engine/rules/vkr_test_rules.json`.
2. Задайте `rule_id`, `kind`, `message` и `target.scope`.
3. Заполните `expected` только теми полями, которые действительно требует
   методичка: незаполненные поля не проверяются.
4. Прогоните документ и посмотрите сводку:

```bash
python manual_test/check_rules.py manual_test/test.docx --rules my_rules.json --limit 20
```

5. Если правило ловит лишнее, сначала настройте `target`
   (`include_headings`, `style_ids`), и только потом трогайте `tolerance`.
6. Проверьте набор в тестах: `RuleSet.model_validate(json.loads(...))` падает на
   противоречивых правилах, поэтому ошибку видно без запуска документа.

Пример правила для подписей рисунков:

```json
{
  "rule_id": "font_captions",
  "kind": "font",
  "severity": "warning",
  "message": "Подписи рисунков набираются 12 pt",
  "target": {"scope": "body_text", "include_captions": true},
  "expected": {"size_pt": 12.0, "size_tolerance_pt": 0.5}
}
```

---

## 7. Ограничения и направление развития

Чего в первой итерации нет:

- интервалов (межстрочный, до/после абзаца), выравнивания, отступов списков;
- проверки колонтитулов и нумерации страниц как отдельного требования;
- автоматического подбора требований по методичке — набор пишется руками;
- отчета для человека в Markdown/PDF: есть JSON и консольная сводка.

Задел на следующие шаги:

- новые `kind` (`spacing`, `alignment`, `page_size`) добавляются как
  дискриминированные модели в `rules.py` и отдельный метод проверки в
  `engine.py`; остальной код не меняется;
- если понадобится «допустимый диапазон», в `expected` можно добавить
  `min_mm`/`max_mm` без ломания существующих наборов;
- `check_json_file()` позволяет прогонять правила на уже собранных JSON без
  повторного разбора и без LibreOffice.

---

## 8. Тесты

```bash
python -m pytest tests/test_rule_schema.py tests/test_rules_engine.py \
    tests/test_rules_fonts.py tests/test_rules_indents.py tests/test_rules_margins.py -q
python -m pytest tests -q
```

Тесты собирают DOCX из XML-фрагментов (`DocxPackageBuilder`), поэтому не
зависят от бинарных образцов и проверяют именно тот контракт, который нужен
правилам: разрешенный каскад стилей, исключения по `scope` и точность адреса
нарушения.

