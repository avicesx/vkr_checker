# Приложение к ERD: предлагаемые изменения схемы

Это проект изменений, а не описание текущего состояния. Фактическая схема
описана в [`docs/ERD.md`](ERD.md): 10 рабочих таблиц, 89 колонок, 24 индекса,
14 внешних ключей, 0 CHECK — и там же в §8.3 перечислено, чего в ней нет.
Приложение отвечает на другой вопрос: какие колонки нужно добавить, чтобы
схема смогла хранить то, что движок правил уже умеет исполнять, и что при
этом не придётся переделывать.

Документ заменяет `docs/ERD_PROPOSAL.md`: его содержимое перенесено сюда,
устаревшие числа исправлены (27 кодов → 36, `ruleset_version` числом →
строкой в JSON-наборе), сам файл удалён, чтобы у схемы не было двух
источников истины.

## 1. Зачем это приложение

### 1.1 Что видно в фактической схеме

Три разрыва между «что нужно движку» и «что умеет БД»:

| Что нужно | Что есть в БД | Разрыв |
| --- | --- | --- |
| Правило проверяет заголовки, но не титульный лист, задание и приложения | `rule.scope` — одна колонка `varchar(50)` | исключения хранить негде |
| Нарушение помнит свой код даже после правки правила | `violation.rule_id` → `rule.rule_type_id` → `rule_type.code` | код выводится join-ом из текущего справочника и меняется вместе с ним |
| Область по умолчанию для 36 кодов | `backend/app/rule_types.py` (`RULE_TYPE_CATALOGUE`) | в БД области нет, API и BI вынуждены тянуть Python |

### 1.2 Чем это оборачивается на практике

- В наборе правил 52 правила и 36 кодов; 13 областей проверки (`scope`).
  У шести правил заполнен `target.exclude_roles`: `heading_levels`,
  `heading_numbering`, `heading_format_text`, `heading_font`,
  `heading_alignment` (не проверять титульный лист, задание, приложения) и
  `required_sections` (не искать структурные элементы на титуле и задании).
- Если исключения отбросить, движок на пяти реальных ВКР даёт 1580 → 1608
  находок: +28 (1,8 %). Все 28 — на заголовках приложений: 22 находки
  `heading_levels` на «Продолжение ПРИЛОЖЕНИЯ A/Б/В/Г/Д» (Бикчуркина,
  блоки `p_00567`–`p_00625`, страницы 43–60) и 6 находок `heading_numbering`
  на подразделах приложения (Мартемьянова, `p_00395`–`p_00448`, страницы
  53–70). Это ровно те ложные срабатывания, из-за которых исключения и
  появились: требования 46 и 48 методички УрГПУ задают приложениям своё
  оформление, а требования 60 и 61 относят титульный лист и приложения к
  структурным элементам, а не к главам.
- В БД на момент замера 1023 строки `rule`, и у всех `scope = 'body_text'`:
  схема не может отличить правило шрифта от правила заголовков, потому что
  область — одна колонка. `violation` пуста (писателя нет), `rule_type` —
  36 строк, коды совпадают с `RULE_TYPE_CATALOGUE` и с кодами движка.

### 1.3 Сводка изменений

| № | Изменение | Тип | Приоритет |
| --- | --- | --- | --- |
| 2 | `rule.target` — `jsonb` | новая колонка | must |
| 3 | `violation.rule_code` — `varchar(50) NOT NULL` + индекс | новая колонка | must |
| 4 | `rule_type.default_scope` — `varchar(50)` | новая колонка | should |
| 5.1 | `violation.quote_location` — `varchar(255)` | новая колонка | nice-to-have |
| 5.2 | CHECK на `rule.strictness` и `violation.severity` | ограничение | should |
| 5.3 | `check.parser_schema_version` — `varchar(20)` | новая колонка | should |
| 5.4 | `violation.occurrences` — `integer NOT NULL`, `violation.confidence` — `double precision` | новые колонки | should / nice-to-have |

Итого: 2 must, 4 should, 2 nice-to-have — восемь правок, из них семь новых
колонок и одно ограничение. Порядок применения — сверху вниз: пункты 2 и 3 меняют
то, как пишутся правила и нарушения, остальные дополняют справочники и
делают данные самопроверяемыми.

### 1.4 Границы

Модели SQLAlchemy и миграции этим документом не меняются: здесь только
предложение, SQL для проверки идеи и список последствий. Ни одна правка не
удаляет и не переименовывает существующие колонки, не меняет их типы и не
добавляет таблиц — миграция остаётся аддитивной и обратимой через `DROP
COLUMN` без потери данных, которых ещё нет.

## 2. `rule.target` — `jsonb`

### 2.1 Что меняем

| Таблица.колонка | Тип | NULL | DEFAULT | Содержимое |
| --- | --- | --- | --- | --- |
| `rule.target` | `jsonb` | NULL | — | объект `RuleTarget` из контракта правил |

Внутри — те же поля, что в JSON-наборе: `scope` (одна из 13 областей),
`exclude_roles` (до 12 ролей заголовков), опциональные `style_ids`,
`exclude_contexts`, `min_heading_confidence`. Пример:

```json
{"scope": "headings", "exclude_roles": ["title_page", "task_sheet", "appendix"]}
```

Колонка `rule.scope` остаётся: она превращается в денормализованный дубль
`target->>'scope'` для старых читателей и для SQL-фильтров.

### 2.2 Зачем

Без неё нельзя сохранить правило, которое проверяет заголовки, но не
трогает титульный лист, задание и приложения, — а таких правил в наборе
шесть. Измерение в §1.2 показывает цену: 28 ложных находок на пяти ВКР
(1,8 % от 1580), причём все — на приложениях, где оформление задаётся
отдельно (требования 46, 48, 60, 61 УрГПУ). Одна колонка `scope` этого не
выражает: у правила `heading_levels` область одна (`headings`), а
исключения — три роли.

Одновременно `jsonb` закрывает и будущие поля контракта (`style_ids`,
`exclude_contexts`, `min_heading_confidence`), не требуя по колонке на
каждое: контракт версии 1.0.0 уже описывает их как часть `target`.

### 2.3 SQL

<!-- sql: target -->
```sql
ALTER TABLE rule ADD COLUMN target jsonb;

COMMENT ON COLUMN rule.target IS
    'Целевой объект правила: scope, exclude_roles, style_ids, exclude_contexts, min_heading_confidence';

UPDATE rule
SET target = jsonb_build_object('scope', scope)
WHERE scope IS NOT NULL
  AND target IS NULL;
```

Проверка после заполнения:

<!-- sql: target_verify -->
```sql
SELECT count(*)                            AS rules_total,
       count(*) FILTER (WHERE target IS NULL) AS without_target,
       count(*) FILTER (WHERE target->>'scope' = 'body_text') AS body_text
FROM rule;
```

`NOT NULL` не ставится: у правила без `target` читатель обязан взять
`{"scope": rule.scope}` — это и есть правило обратной совместимости.

### 2.4 Что не ломается

- Старые читатели видят прежние колонки; `scope` не удаляется.
- `jsonb` в PostgreSQL 16 допускает `->>`, `@>` и GIN-индексы, но индекс
  здесь не нужен: таблица правил — сотни строк, выборка идёт по
  `ruleset_id`.
- Отчёт не меняется: движок читает JSON-наборы из
  `backend/rule_engine/rules/`, а не строки БД.
- Данные: все существующие строки получают `{"scope": "body_text"}` —
  ровно то, что в ней записано сейчас.

### 2.5 Что сделать у нас, когда поле появится

1. `backend/app/models/rule.py`: `target: Mapped[dict | None] = mapped_column(JSONB)`.
2. Миграция `alembic revision --autogenerate` (после модели) + backfill из §2.3.
3. Импорт наборов в БД: писать `target` целиком из JSON-правила, `scope`
   дублировать из `target.scope`.
4. `backend/api/admin.py`: отдавать `target` в карточке правила.
5. `backend/rule_engine` при чтении из БД: `target` приоритетнее `scope`.

### 2.6 Приоритет

**must.** Это единственная правка, без которой админка не может сохранить
правило, уже исполняемое движком; всё остальное — удобство и защита данных.

### 2.7 Временное решение

До появления колонки `exclude_roles` живут в JSON-наборах
(`backend/rule_engine/rules/*.json`, профили в `rules/profiles/`), и отчёт
строится по ним. Ограничение: импорт набора в БД теряет исключения, поэтому
до миграции импорт должен либо отклонять правила с непустым
`target.exclude_roles`, либо складывать их в `rule.params` под служебным
ключом `_target` с пометкой в API. Второй вариант — костыль, он допустим
только на время.

## 3. `violation.rule_code` — `varchar(50) NOT NULL`

### 3.1 Что меняем

| Таблица.колонка | Тип | NULL | DEFAULT | Содержимое |
| --- | --- | --- | --- | --- |
| `violation.rule_code` | `varchar(50)` | NOT NULL | — | код проверки, породившей нарушение |
| `ix_violation_rule_code` | индекс | — | — | `(rule_code)` |

### 3.2 Зачем

Три причины, каждая проверяема:

1. **Отчёт не меняется задним числом.** Сейчас код нарушения получается
   join-ом `violation.rule_id → rule.rule_type_id → rule_type.code`. Если
   правило переклассифицируют или справочник перезапишут, старый отчёт
   начнёт показывать другой код. Снимок кода в строке нарушения это
   исключает: 1580 находок на пяти ВКР остаются теми же, чем были.
2. **Группировка без join-а.** Калибровка идёт по коду («сколько находок
   `heading_format` на заголовках»), а таблица `rule` уже содержит больше
   тысячи строк и растёт с каждым набором; группировка по `rule_code` — это
   `GROUP BY` по одной колонке с индексом.
3. **История для калибровки.** Код в нарушении позволяет считать точность
   правила по всем прошлым проверкам, не восстанавливая задним числом,
   какая версия `rule_type` была активна.

### 3.3 SQL

<!-- sql: rule_code -->
```sql
ALTER TABLE violation ADD COLUMN rule_code varchar(50);

UPDATE violation AS v
SET rule_code = rt.code
FROM rule AS r
JOIN rule_type AS rt ON rt.id = r.rule_type_id
WHERE v.rule_id = r.id
  AND v.rule_code IS NULL;
```

Проверка перед `NOT NULL`:

<!-- sql: rule_code_verify -->
```sql
SELECT count(*)                             AS violations_total,
       count(*) FILTER (WHERE rule_code IS NULL) AS without_code
FROM violation;
```

Если `without_code = 0` (сейчас таблица пуста, писателя ещё нет):

<!-- sql: rule_code_notnull -->
```sql
ALTER TABLE violation ALTER COLUMN rule_code SET NOT NULL;
CREATE INDEX ix_violation_rule_code ON violation (rule_code);
```

Если строки без `rule_id` найдутся, `NOT NULL` не применять, пока они не
разобраны: `rule_id` в схеме nullable, и код для таких строк взять неоткуда.

### 3.4 Что не ломается

- `violation.rule_id` и FK на `rule` остаются: связь с конкретной записью
  правила не теряется, `rule_code` — снимок, а не замена.
- Существующие запросы читают те же колонки; новые могут не делать join.
- Индекс не конфликтует с существующими: в `violation` сейчас нет ни одного
  индекса, кроме PK.
- Таблица пуста, поэтому миграция не переписывает данные.

### 3.5 Что сделать у нас, когда поле появится

1. `backend/app/models/violation.py`: `rule_code: Mapped[str]`.
2. Писатель нарушений (сейчас его нет): при сохранении брать
   `Violation.code` из pydantic-модели — она уже содержит код.
3. `backend/api/admin.py`: аналитику перевести с join-а на `rule_code`,
   join оставить для получения `rule.params` и цитаты.

### 3.6 Приоритет

**must.** Пока писателя нет, цена отсрочки нулевая, но как только нарушения
начнут сохраняться, backfill кода задним числом станет невозможен: наборы
правил к тому моменту успеют смениться.

### 3.7 Временное решение

Отчёт уже группирует находки по `Violation.code`; в БД код можно получать
join-ом. Этого достаточно, пока `violation` не заполняется, — но не после.

## 4. `rule_type.default_scope` — `varchar(50)`

### 4.1 Что меняем

| Таблица.колонка | Тип | NULL | DEFAULT | Содержимое |
| --- | --- | --- | --- | --- |
| `rule_type.default_scope` | `varchar(50)` | NULL | — | область проверки по умолчанию для кода |

### 4.2 Зачем

Область по умолчанию для каждого из 36 кодов сегодня живёт в Python
(`backend/app/rule_types.py`, `RULE_TYPE_CATALOGUE`), а в БД её нет. Из-за
этого:

- API и BI не могут показать «что проверяет код», не поднимая Python;
- справочник уже расходился: в ERD §4.5 записано 27 записей, сейчас 36,
  а `seed_rule_types` засевает таблицу из того же Python-каталога;
- правило без явного `target` в БД нельзя осмысленно прочитать.

`default_scope` — подсказка, а не ограничение: допустимые области остаются
в движке (`CODE_ALLOWED_SCOPES`), потому что это логика проверки, а не
данные справочника.

### 4.3 SQL

<!-- sql: default_scope -->
```sql
ALTER TABLE rule_type ADD COLUMN default_scope varchar(50);

UPDATE rule_type AS rt
SET default_scope = v.scope
FROM (VALUES
    ('alignment', 'captions'),
    ('appendix_order', 'document_structure'),
    ('bibliography_cited', 'bibliography'),
    ('bibliography_count', 'bibliography'),
    ('bibliography_format', 'bibliography'),
    ('bibliography_link', 'bibliography'),
    ('bibliography_order', 'bibliography'),
    ('bibliography_recency', 'bibliography'),
    ('caption_format', 'captions'),
    ('caption_numbering', 'captions'),
    ('caption_placement', 'captions'),
    ('caption_reference', 'captions'),
    ('document_volume', 'document'),
    ('equation_numbering', 'document'),
    ('font', 'body_text'),
    ('heading_format', 'headings'),
    ('heading_hyphenation', 'headings'),
    ('heading_levels', 'headings'),
    ('heading_numbering', 'headings'),
    ('heading_page_break', 'headings'),
    ('indent', 'body_text'),
    ('inline_reference_format', 'body_text'),
    ('keep_next', 'headings'),
    ('line_spacing', 'body_text'),
    ('list_format', 'body_paragraphs'),
    ('margin', 'sections'),
    ('microtypography', 'body_paragraphs'),
    ('page_format', 'sections'),
    ('page_number_position', 'headers_footers'),
    ('required_sections', 'document_structure'),
    ('table_continuation', 'table_cells'),
    ('table_header_format', 'table_cells'),
    ('table_layout', 'table_cells'),
    ('title_page_content', 'document_structure'),
    ('toc', 'toc'),
    ('widow_control', 'body_text')
) AS v(code, scope)
WHERE rt.code = v.code;
```

Проверка: все 36 строк справочника получают значение, лишних кодов в
`VALUES` нет.

<!-- sql: default_scope_verify -->
```sql
SELECT count(*)                                  AS types_total,
       count(*) FILTER (WHERE default_scope IS NULL) AS without_default
FROM rule_type;
```

### 4.4 Что не ломается

- `NOT NULL` не ставится: новый код можно завести строкой в справочнике, не
  блокируя импорт; читатель при `NULL` берёт значение из Python-каталога.
- Код, который читает `RULE_TYPE_CATALOGUE`, продолжает работать; колонка —
  его зеркало в БД.
- Значения совпадают с текущими умолчаниями pydantic-моделей: `font` →
  `body_text`, `margin` → `sections`, `heading_*` → `headings`,
  `bibliography_*` → `bibliography`, `caption_*` → `captions`,
  `page_number_position` → `headers_footers`, `document_volume` и
  `equation_numbering` → `document`.

### 4.5 Что сделать у нас, когда поле появится

1. `backend/app/rule_types.py`: `seed_rule_types` пишет `default_scope`.
2. `backend/app/models/rule_type.py`: колонка в модели.
3. `backend/api`: отдавать `default_scope` в справочнике кодов.
4. Сверка: набор `default_scope` в БД обязан совпадать с умолчаниями
   `Rule`-моделей — это можно проверять тестом на живом справочнике.

### 4.6 Приоритет

**should.** Без неё всё работает, но справочник остаётся наполовину в
Python; расхождение 27 → 36 показывает, что это уже случалось.

### 4.7 Временное решение

`RULE_TYPE_CATALOGUE` остаётся источником истины; API отдаёт область из
него, а БД — только коды и названия.

## 5. Что дополнительно выяснилось

### 5.1 `quote` и `quote_location`

`rule.quote` заполнен у всех 52 правил набора: цитата из методички — часть
контракта, и она есть. `rule.quote_location` в БД пуст всегда: движок такого
поля не отдаёт, в контракте правил есть только `quote`. Значит, колонка либо
остаётся под ручное заполнение («п. 4.2 методички»), либо её роль берёт
`target`-подобный объект цитаты.

Для отчёта важнее другое: `violation.quote` — снимок цитаты на момент
проверки, он есть; а `violation.quote_location` нет, поэтому старый отчёт не
воспроизведёт ссылку на пункт. Это **nice-to-have**:

<!-- sql: quote_location -->
```sql
ALTER TABLE violation ADD COLUMN quote_location varchar(255);
```

Заполнять при записи нарушения из правила. Если цитаты в отчёте достаточно,
колонку можно не добавлять.

### 5.2 `strictness` и `severity`

Маппинг однозначен: `Violation.severity` принимает те же три значения, что
`rule.strictness` — `error`, `warning`, `info`; перевод не нужен. Проблема не
в значениях, а в том, что обе колонки nullable и в схеме 0 CHECK (§8.2
ERD): `strictness = 'fatal'` вставится без ошибки, и отчёт упадёт уже на
чтении. **should** — ограничения, которые не ломают существующие строки:

<!-- sql: checks -->
```sql
ALTER TABLE rule ADD CONSTRAINT rule_strictness_check
    CHECK (strictness IS NULL OR strictness IN ('error', 'warning', 'info')) NOT VALID;
ALTER TABLE violation ADD CONSTRAINT violation_severity_check
    CHECK (severity IS NULL OR severity IN ('error', 'warning', 'info')) NOT VALID;
```

`NOT VALID` не проверяет старые строки; после разбора данных —
`ALTER TABLE ... VALIDATE CONSTRAINT ...`. Если политика «перечисления
проверяет только приложение» сохраняется, пункт можно отложить: он не
блокирует остальные.

### 5.3 Версия схемы парсера

`RuleCheckReport.parser_schema_version` = `"3.0.0"`: отчёт всегда помнит, на
какой схеме разбора он построен. В таблице `check` такой колонки нет, поэтому
после обновления парсера восстановить, чем именно проверялся документ, можно
только по `ruleset_id` — а разбор мог измениться независимо от набора.
**should**:

<!-- sql: parser_version -->
```sql
ALTER TABLE "check" ADD COLUMN parser_schema_version varchar(20);
```

Имя `check` в PostgreSQL зарезервировано, поэтому в SQL оно в кавычках —
так же, как в миграции. Писатель проверок заполняет колонку из отчёта;
старые строки остаются `NULL`, что честно означает «версия неизвестна».

### 5.4 `occurrences` и `confidence`

Движок агрегирует одинаковые находки: в 73 из 1580 нарушений на пяти ВКР
`occurrences > 1`, максимум — 75 повторов одной находки (Мартемьянова), 67
(3.docx). В БД такого поля нет: при записи кратность потеряется, и отчёт по
БД покажет одно нарушение вместо семидесяти пяти. **should**:

<!-- sql: occurrences -->
```sql
ALTER TABLE violation ADD COLUMN occurrences integer;
UPDATE violation SET occurrences = 1 WHERE occurrences IS NULL;
ALTER TABLE violation ALTER COLUMN occurrences SET NOT NULL;
```

`confidence` (`double precision`, **nice-to-have**) хранит уверенность
правила; сейчас она есть в pydantic-модели и в JSON-наборе, но не в БД:

<!-- sql: confidence -->
```sql
ALTER TABLE violation ADD COLUMN confidence double precision;
```

### 5.5 Сводка дополнительных правок

| Изменение | Приоритет | Что даёт |
| --- | --- | --- |
| `violation.quote_location` | nice-to-have | ссылка на пункт методички в старом отчёте |
| CHECK на `rule.strictness`, `violation.severity` | should | мусорное значение не вставится |
| `check.parser_schema_version` | should | проверку можно воспроизвести после апгрейда парсера |
| `violation.occurrences` | should | кратность находки не теряется |
| `violation.confidence` | nice-to-have | уверенность правила видна в БД |

## 6. Чего не надо менять

- **Состав таблиц.** Все 10 таблиц ERD остаются: `user`, `org_unit`,
  `guideline`, `guideline_link`, `rule_type`, `ruleset`, `rule`, `check`,
  `violation`, `upload_check`. Новых таблиц не нужно: ни для версий правил
  (это `ruleset`), ни для нарушений (это `violation`).
- **Существующие колонки.** Ни одна из 89 колонок не переименовывается, не
  удаляется и не меняет тип. Правки только аддитивные.
- **`rule.params`.** Остаётся `json`. Перевод в `jsonb` — отдельная задача:
  он не нужен ни `target`, ни отчёту, а трогает все существующие строки таблицы.
- **Внешние ключи.** 14 FK и `ON DELETE NO ACTION` не меняются: каскады и
  `SET NULL` — вопрос политики удаления, а не хранения правил.
- **Индексы-дубли.** 10 лишних `ix_<table>_id` на PK — дефект из §8.2 ERD,
  но чистка индексов не относится к этому приложению и делается отдельно.
- **Временные метки.** `timestamp` без часового пояса у `check` и `ruleset`
  против `timestamptz` у `upload_check` — несогласованность из §8.2; менять
  её вместе с этими правками не нужно, это отдельная миграция с пересчётом.
- **`upload_check`.** Остаётся изолированной: FK к `guideline`/`check` — шаг
  вперёд, но он требует решения, что делать с уже загруженными файлами.
- **Типы-перечисления.** ENUM-типов в PostgreSQL не появляется: значения
  проверяются CHECK (§5.2), а не типом.
- **Писатель `check`/`violation`.** Появление строк в этих таблицах — задача
  кода, а не схемы; приложение лишь готовит колонки, которые писатель
  заполнит.

## 7. Чек-лист перед миграцией

1. `alembic upgrade head` на чистой БД даёт 10 таблиц и 89 колонок; после
   применения всех правок — 96 колонок (89 + 7), в must-минимуме — 91.
2. `compare_metadata(compare_type=True)` после добавления полей в модели
   пуст: расхождений модели ↔ миграция ↔ БД нет.
3. Backfill полный: `target` заполнен у всех строк `rule` со `scope`,
   `rule_code` — у всех `violation` с `rule_id`, `default_scope` — у всех
   36 `rule_type`.
4. Ограничения работают: вставка `strictness = 'fatal'` и `violation` без
   `rule_code` отклоняются; `occurrences` не `NULL`.
5. Отчёт не изменился: parse → engine → report на пяти ВКР даёт
   374/365/677/58/106 (1580) до и после миграции — движок читает JSON-наборы,
   а не БД.
6. Разрушающих операций нет: ни одного `DROP`, `RENAME` или `ALTER TYPE`
   существующей колонки, ни одной новой таблицы, 14 FK остаются
   `NO ACTION`.
7. `docs/ERD.md` после миграции обновлён (колонки, числа, §8.3), а ссылки на
   `docs/ERD_PROPOSAL.md` уже переведены на это приложение — второго источника
   истины нет.

## 8. Источники

- [`docs/ERD.md`](ERD.md) — фактическая схема (коммит `396e13a`), оттуда
  взяты имена таблиц, колонок, типов и числа 10/89/24/14/0.
- [`docs/URGPU_SPEC.md`](URGPU_SPEC.md) — требования 46, 48, 60, 61 (приложения
  и структурные элементы) и 8–10 (нумерация страниц).
- [`docs/RULES_CONTRACT.md`](RULES_CONTRACT.md) — контракт формата правил:
  `target`, 13 областей, 12 ролей, `strictness`, `quote`.
- `backend/rule_engine/rules/vkr_test_rules.json` — базовый набор, 52 правила,
  36 кодов; `backend/rule_engine/rules/profiles/urgpu.json` — профиль УрГПУ.
- `backend/rule_engine/rules.py` — `RuleTarget`, `CODE_ALLOWED_SCOPES`,
  `RuleSet`; `backend/app/rule_types.py` — `RULE_TYPE_CATALOGUE`.
- Замеры на пяти ВКР (Бикчуркина, Бутаков, 3.docx, Смолина, Мартемьянова)
  выполнены на коммите `30f12c5`; исключения отбрасывались копией набора с
  `exclude_roles = []`.
