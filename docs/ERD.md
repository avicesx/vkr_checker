# ERD: фактическая схема базы данных vkr_checker

Документ описывает схему **как она есть**: то, что создаёт baseline-миграция
`95f6b91fcc64` и что реально лежит в PostgreSQL после `alembic upgrade head`.
Это не проект и не пожелания к схеме — предложения по её изменению живут
отдельно, в [`docs/ERD_APPENDIX.md`](ERD_APPENDIX.md), и в этой схеме **не реализованы** (см. §8).

Ревизия на момент описания: ветка `feat/urgpu-ruleset`, единственная миграция
`95f6b91fcc64_baseline_schema.py` (`down_revision = None`), PostgreSQL 16.16.

## 1. Назначение и способ проверки

Документ нужен, чтобы по одному файлу можно было понять состав таблиц, связи,
типы, ограничения и значения перечислений — без чтения моделей и миграции.

Три источника истины сверяются между собой, и ни один не считается главным:

1. `backend/app/models/*.py` — SQLAlchemy-модели (`Base.metadata`);
2. `backend/alembic/versions/95f6b91fcc64_baseline_schema.py` — миграция, которая
   создаёт схему с нуля;
3. живая база — временная БД, в которую прогнан `alembic upgrade head`.

Документ проверяется машинно: `backend/manual_test/_erd_check.py` (файл в
`.gitignore`) читает схему через `sqlalchemy.inspect`, парсит markdown-таблицы и
строки `PK/UNIQUE/FK/Индексы/CHECK` этого файла и сравнивает всё: состав таблиц,
имена колонок, типы с длиной, nullable, DEFAULT, первичные ключи, уникальные
ограничения, внешние ключи с `ON DELETE`, индексы (включая условие частичного) и
check-констрейнты. Любое расхождение — ненулевой код возврата и список
несовпадений. Команды — в §7.

## 2. Обзор схемы: от методички до нарушения

В схеме 10 рабочих таблиц; одиннадцатая, `alembic_version`, — служебная, её
ведёт Alembic, в ERD она не считается.

Поток данных устроен так:

- **`org_unit`** — дерево структуры вуза (университет → институт → кафедра),
  узел ссылается на родителя через `parent_id`.
- **`guideline`** — методичка: файл, извлечённый текст, хеш текста. Версии
  методички связываются полем `previous_version_id` — тоже на саму себя.
- **`guideline_link`** — к какому подразделению (`org_unit`) и какому
  направлению/программе относится методичка; одна методичка может быть
  привязана к нескольким местам.
- **`ruleset`** — версия набора правил для конкретной методички. У методички
  может быть много версий (`version`), но **активна одновременно только одна**:
  это гарантирует частичный уникальный индекс `uq_ruleset_active_per_guideline`
  с условием `status = 'active'`. Цепочка версий — `parent_ruleset_id`.
- **`rule`** — отдельное требование внутри набора: параметры в `params` (JSON),
  область проверки в `scope`, строгость в `strictness`. Вид проверки задаёт
  `rule_type_id`; повторно найденное в новой версии правило ссылается на
  прежнее через `inherited_from_rule_id`.
- **`rule_type`** — справочник видов проверок: `code` — машинный ключ, по
  которому движок находит функцию проверки.
- **`check`** — запуск проверки ВКР: кто (`user_id`), по какому набору правил
  (`ruleset_id`), где файлы и результаты.
- **`violation`** — одно найденное нарушение внутри проверки (`check_id`) по
  конкретному правилу (`rule_id`) со страницей, фрагментом и ожидаемым/найденным.
- **`upload_check`** — отдельный контур: запись о загрузке файлов ВКР и
  методички для API статуса. С остальными таблицами **не связана ни одним FK**;
  идентификатор строковый (UUID), а не integer.
- **`user`** — пользователь системы: подтверждает наборы (`ruleset.confirmed_by`)
  и запускает проверки (`check.user_id`).

Коротко: `guideline` → `ruleset` → `rule` → `check` → `violation`, а
`org_unit`, `guideline_link`, `rule_type` и `user` — справочники вокруг этой
цепочки.

## 3. ERD

```mermaid
erDiagram
    user {
        int id PK
        varchar name
        varchar role
    }
    org_unit {
        int id PK
        int parent_id FK
        varchar kind
        varchar name
        varchar code
        varchar profile_name
    }
    guideline {
        int id PK
        int previous_version_id FK
        varchar title
        int year
        varchar file_path
        varchar source
        text raw_text
        varchar text_hash UK
        bool confirmed
        varchar relation_type
    }
    guideline_link {
        int id PK
        int guideline_id FK
        int org_unit_id FK
        text scope_note
        varchar direction_code
        varchar direction_name
        varchar program_code
        varchar program_name
        bool is_confirmed
    }
    rule_type {
        int id PK
        varchar code UK
        varchar name
        text description
    }
    ruleset {
        int id PK
        int guideline_id FK
        int parent_ruleset_id FK
        varchar name
        int version
        varchar generation_method
        varchar status
        varchar trust_level
        timestamp confirmed_at
        int confirmed_by FK
    }
    rule {
        int id PK
        int ruleset_id FK
        int rule_type_id FK
        int inherited_from_rule_id FK
        varchar name
        json params
        varchar scope
        text quote
        varchar quote_location
        float confidence
        varchar status
        varchar origin
        varchar target_element
        varchar verification_method
        varchar strictness
    }
    check {
        int id PK
        int user_id FK
        int ruleset_id FK
        varchar work_type
        varchar original_filename
        varchar file_path
        varchar result_docx_path
        varchar result_report_path
        text error_message
        varchar status
        timestamp created_at
        timestamp completed_at
    }
    violation {
        int id PK
        int check_id FK
        int rule_id FK
        int page_number
        varchar selection_path
        int paragraph_number
        text quote
        text expected
        text found
        varchar location
        varchar severity
        text comment
        varchar status
    }
    upload_check {
        varchar id PK
        varchar status
        timestamptz created_at
        varchar thesis_filename
        text thesis_path
        varchar guidelines_filename
        text guidelines_path
    }

    user |o--o{ ruleset : "confirmed_by"
    user |o--o{ check : "user_id"
    org_unit |o--o{ org_unit : "parent_id"
    guideline |o--o{ guideline : "previous_version_id"
    org_unit |o--o{ guideline_link : "org_unit_id"
    guideline |o--o{ guideline_link : "guideline_id"
    guideline ||--o{ ruleset : "guideline_id"
    ruleset |o--o{ ruleset : "parent_ruleset_id"
    ruleset |o--o{ rule : "ruleset_id"
    ruleset |o--o{ check : "ruleset_id"
    rule_type |o--o{ rule : "rule_type_id"
    rule |o--o{ rule : "inherited_from_rule_id"
    rule |o--o{ violation : "rule_id"
    check |o--o{ violation : "check_id"
```

Кардинальности показаны по nullable-признаку FK: `||` — ссылка обязательна
(`ruleset.guideline_id`), `|o` — колонка nullable. Длины типов в диаграмме
опущены; точные типы — в §4.

## 4. Таблицы

Обозначения в таблицах колонок:

- `serial` в DEFAULT — `nextval('<таблица>_id_seq'::regclass)`, то есть обычный
  `SERIAL`-столбец; отдельная последовательность есть у каждой integer-таблицы;
- `—` в DEFAULT — SQL-значения по умолчанию нет (клиентские default моделей
  перечислены в §6.3);
- `TIMESTAMP` — `timestamp without time zone`, `TIMESTAMPTZ` —
  `timestamp with time zone`;
- `JSON` — PostgreSQL `json` (не `jsonb`);
- в колонке «Ограничения» — только PK/FK/UNIQUE; индексы и CHECK — ниже таблицы.

### 4.1 `user`

Пользователь системы: подтверждает наборы правил и запускает проверки.

| Колонка | Тип | NULL | DEFAULT | Ограничения |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | NOT NULL | serial | PK |
| `name` | VARCHAR(255) | NOT NULL | — | — |
| `role` | VARCHAR(50) | NOT NULL | — | — |

- **PK:** `id`
- **UNIQUE:** нет
- **FK:** нет
- **Индексы:** `ix_user_id` (`id`)
- **CHECK:** нет

### 4.2 `org_unit`

Узел организационной структуры вуза; дерево строится ссылкой на родителя.

| Колонка | Тип | NULL | DEFAULT | Ограничения |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | NOT NULL | serial | PK |
| `parent_id` | INTEGER | NULL | — | FK → `org_unit.id` |
| `kind` | VARCHAR(50) | NOT NULL | — | — |
| `name` | VARCHAR(255) | NOT NULL | — | — |
| `code` | VARCHAR(50) | NULL | — | — |
| `profile_name` | VARCHAR(255) | NULL | — | — |

- **PK:** `id`
- **UNIQUE:** нет
- **FK:** `parent_id` → `org_unit.id` (on delete NO ACTION)
- **Индексы:** `ix_org_unit_id` (`id`)
- **CHECK:** нет

### 4.3 `guideline`

Методичка: файл, извлечённый текст и хеш текста, по которому ищется дубль.

| Колонка | Тип | NULL | DEFAULT | Ограничения |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | NOT NULL | serial | PK |
| `previous_version_id` | INTEGER | NULL | — | FK → `guideline.id` |
| `title` | VARCHAR(255) | NOT NULL | — | — |
| `year` | INTEGER | NULL | — | — |
| `file_path` | VARCHAR(512) | NULL | — | — |
| `source` | VARCHAR(50) | NULL | — | — |
| `raw_text` | TEXT | NULL | — | — |
| `text_hash` | VARCHAR(64) | NULL | — | UNIQUE |
| `confirmed` | BOOLEAN | NULL | — | — |
| `relation_type` | VARCHAR(50) | NULL | — | — |

- **PK:** `id`
- **UNIQUE:** (`text_hash`) → `guideline_text_hash_key`
- **FK:** `previous_version_id` → `guideline.id` (on delete NO ACTION)
- **Индексы:** `guideline_text_hash_key` (`text_hash`, unique); `ix_guideline_id` (`id`)
- **CHECK:** нет

Уникальность `text_hash` — обычная, без `NULLS NOT DISTINCT`: несколько строк
с `text_hash IS NULL` допустимы, дубли среди непустых хешей — нет.

### 4.4 `guideline_link`

Привязка методички к подразделению и направлению/программе обучения.

| Колонка | Тип | NULL | DEFAULT | Ограничения |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | NOT NULL | serial | PK |
| `guideline_id` | INTEGER | NULL | — | FK → `guideline.id` |
| `org_unit_id` | INTEGER | NULL | — | FK → `org_unit.id` |
| `scope_note` | TEXT | NULL | — | — |
| `direction_code` | VARCHAR(50) | NULL | — | — |
| `direction_name` | VARCHAR(255) | NULL | — | — |
| `program_code` | VARCHAR(50) | NULL | — | — |
| `program_name` | VARCHAR(255) | NULL | — | — |
| `is_confirmed` | BOOLEAN | NULL | — | — |

- **PK:** `id`
- **UNIQUE:** нет
- **FK:** `guideline_id` → `guideline.id` (on delete NO ACTION); `org_unit_id` → `org_unit.id` (on delete NO ACTION)
- **Индексы:** `ix_guideline_link_id` (`id`)
- **CHECK:** нет

### 4.5 `rule_type`

Справочник видов проверок: код → функция движка и область по умолчанию.

| Колонка | Тип | NULL | DEFAULT | Ограничения |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | NOT NULL | serial | PK |
| `code` | VARCHAR(50) | NOT NULL | — | UNIQUE |
| `name` | VARCHAR(255) | NOT NULL | — | — |
| `description` | TEXT | NULL | — | — |

- **PK:** `id`
- **UNIQUE:** (`code`) → `rule_type_code_key`
- **FK:** нет
- **Индексы:** `ix_rule_type_id` (`id`); `rule_type_code_key` (`code`, unique)
- **CHECK:** нет

Каталог кодов живёт в `backend/app/rule_types.py` (36 записей) и засевается в
таблицу функцией `seed_rule_types`; модель БД этот список не ограничивает.

### 4.6 `ruleset`

Версия набора правил для методички; одновременно активна максимум одна.

| Колонка | Тип | NULL | DEFAULT | Ограничения |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | NOT NULL | serial | PK |
| `guideline_id` | INTEGER | NOT NULL | — | FK → `guideline.id`, UNIQUE (составной) |
| `parent_ruleset_id` | INTEGER | NULL | — | FK → `ruleset.id` |
| `name` | VARCHAR(255) | NOT NULL | — | — |
| `version` | INTEGER | NOT NULL | — | UNIQUE (составной) |
| `generation_method` | VARCHAR(50) | NULL | — | — |
| `status` | VARCHAR(50) | NULL | — | — |
| `trust_level` | VARCHAR(50) | NULL | — | — |
| `confirmed_at` | TIMESTAMP | NULL | — | — |
| `confirmed_by` | INTEGER | NULL | — | FK → `user.id` |

- **PK:** `id`
- **UNIQUE:** (`guideline_id`, `version`) → `uq_ruleset_guideline_version`
- **FK:** `confirmed_by` → `user.id` (on delete NO ACTION); `guideline_id` → `guideline.id` (on delete NO ACTION); `parent_ruleset_id` → `ruleset.id` (on delete NO ACTION)
- **Индексы:** `ix_ruleset_id` (`id`); `uq_ruleset_active_per_guideline` (`guideline_id`, unique, where status = 'active'); `uq_ruleset_guideline_version` (`guideline_id`, `version`, unique)
- **CHECK:** нет

Частичный уникальный индекс `uq_ruleset_active_per_guideline` создан как
`CREATE UNIQUE INDEX ... ON ruleset (guideline_id) WHERE ((status)::text = 'active'::text)`:
он запрещает вторую строку со `status = 'active'` для той же методички, но не
мешает любому числу строк с другими статусами. `status` при этом nullable, и
`NULL` под условие индекса не попадает.

### 4.7 `rule`

Отдельное требование внутри набора: что проверять, как и насколько строго.

| Колонка | Тип | NULL | DEFAULT | Ограничения |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | NOT NULL | serial | PK |
| `ruleset_id` | INTEGER | NULL | — | FK → `ruleset.id` |
| `rule_type_id` | INTEGER | NULL | — | FK → `rule_type.id` |
| `inherited_from_rule_id` | INTEGER | NULL | — | FK → `rule.id` |
| `name` | VARCHAR(255) | NOT NULL | — | — |
| `params` | JSON | NULL | — | — |
| `scope` | VARCHAR(50) | NULL | — | — |
| `quote` | TEXT | NULL | — | — |
| `quote_location` | VARCHAR(255) | NULL | — | — |
| `confidence` | DOUBLE PRECISION | NULL | — | — |
| `status` | VARCHAR(50) | NULL | — | — |
| `origin` | VARCHAR(50) | NULL | — | — |
| `target_element` | VARCHAR(50) | NULL | — | — |
| `verification_method` | VARCHAR(50) | NULL | — | — |
| `strictness` | VARCHAR(50) | NULL | — | — |

- **PK:** `id`
- **UNIQUE:** нет
- **FK:** `inherited_from_rule_id` → `rule.id` (on delete NO ACTION); `rule_type_id` → `rule_type.id` (on delete NO ACTION); `ruleset_id` → `ruleset.id` (on delete NO ACTION)
- **Индексы:** `ix_rule_id` (`id`)
- **CHECK:** нет

`params` — тип `json`, а не `jsonb` (см. §8): PostgreSQL проверяет только
синтаксис JSON, но не схему параметров; схему проверяет движок при компиляции
набора (`backend/app/rules_from_db.py`).

### 4.8 `check`

Запуск проверки ВКР: пользователь, набор правил, файлы и результат.

| Колонка | Тип | NULL | DEFAULT | Ограничения |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | NOT NULL | serial | PK |
| `user_id` | INTEGER | NULL | — | FK → `user.id` |
| `ruleset_id` | INTEGER | NULL | — | FK → `ruleset.id` |
| `work_type` | VARCHAR(50) | NULL | — | — |
| `original_filename` | VARCHAR(255) | NULL | — | — |
| `file_path` | VARCHAR(512) | NULL | — | — |
| `result_docx_path` | VARCHAR(512) | NULL | — | — |
| `result_report_path` | VARCHAR(512) | NULL | — | — |
| `error_message` | TEXT | NULL | — | — |
| `status` | VARCHAR(50) | NULL | — | — |
| `created_at` | TIMESTAMP | NULL | — | — |
| `completed_at` | TIMESTAMP | NULL | — | — |

- **PK:** `id`
- **UNIQUE:** нет
- **FK:** `ruleset_id` → `ruleset.id` (on delete NO ACTION); `user_id` → `user.id` (on delete NO ACTION)
- **Индексы:** `ix_check_id` (`id`)
- **CHECK:** нет

### 4.9 `violation`

Одно найденное нарушение: адрес в документе, ожидание, факт, строгость.

| Колонка | Тип | NULL | DEFAULT | Ограничения |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | NOT NULL | serial | PK |
| `check_id` | INTEGER | NULL | — | FK → `check.id` |
| `rule_id` | INTEGER | NULL | — | FK → `rule.id` |
| `page_number` | INTEGER | NULL | — | — |
| `selection_path` | VARCHAR(255) | NULL | — | — |
| `paragraph_number` | INTEGER | NULL | — | — |
| `quote` | TEXT | NULL | — | — |
| `expected` | TEXT | NULL | — | — |
| `found` | TEXT | NULL | — | — |
| `location` | VARCHAR(255) | NULL | — | — |
| `severity` | VARCHAR(50) | NULL | — | — |
| `comment` | TEXT | NULL | — | — |
| `status` | VARCHAR(50) | NULL | — | — |

- **PK:** `id`
- **UNIQUE:** нет
- **FK:** `check_id` → `check.id` (on delete NO ACTION); `rule_id` → `rule.id` (on delete NO ACTION)
- **Индексы:** `ix_violation_id` (`id`)
- **CHECK:** нет

### 4.10 `upload_check`

Запись о загрузке файлов ВКР и методички для API статуса; отдельный контур.

| Колонка | Тип | NULL | DEFAULT | Ограничения |
| --- | --- | --- | --- | --- |
| `id` | VARCHAR(36) | NOT NULL | — | PK |
| `status` | VARCHAR(32) | NOT NULL | — | — |
| `created_at` | TIMESTAMPTZ | NOT NULL | — | — |
| `thesis_filename` | VARCHAR(255) | NOT NULL | — | — |
| `thesis_path` | TEXT | NOT NULL | — | — |
| `guidelines_filename` | VARCHAR(255) | NOT NULL | — | — |
| `guidelines_path` | TEXT | NOT NULL | — | — |

- **PK:** `id`
- **UNIQUE:** нет
- **FK:** нет
- **Индексы:** `ix_upload_check_status` (`status`)
- **CHECK:** нет

`id` — строка UUID длиной 36, генерируется в API. `created_at` — единственный
`timestamptz` в схеме; остальные временные метки — `timestamp` без часового пояса.

## 5. Схема в цифрах

| Показатель | Значение | Как считается |
| --- | --- | --- |
| Таблиц | 10 | рабочие таблицы; `alembic_version` не считается (в БД 11) |
| Колонок | 89 | во всех 10 таблицах (в БД 90 — с `alembic_version.version_num`) |
| Первичных ключей | 10 | по одному на таблицу, у `upload_check` — `varchar` |
| Внешних ключей | 14 | 10 на чужие таблицы + 4 самоссылки: `org_unit.parent_id`, `guideline.previous_version_id`, `ruleset.parent_ruleset_id`, `rule.inherited_from_rule_id` |
| UNIQUE-ограничений | 3 | `guideline.text_hash`, `rule_type.code`, `ruleset(guideline_id, version)` |
| Физических индексов | 24 | 10 под PK + 14 прочих (включая 3 индекса от UNIQUE и 1 частичный) |
| Из них частичных | 1 | `uq_ruleset_active_per_guideline` |
| CHECK-констрейнтов | 0 | ни модель, ни миграция их не создают |
| Последовательностей | 9 | по одной на каждый integer-PK |
| ENUM-типов PostgreSQL | 0 | все перечисления — `VARCHAR(50)` |

Счётчики 89/24/14 проверяются самотестом из §7: он берёт их из `inspector` и
сверяет с числами этой таблицы.

## 6. Перечисления и значения

### 6.1 Где хранятся

Ни одно перечисление не оформлено типом PostgreSQL `ENUM` и ни одно не
ограничено `CHECK`-констрейнтом: все они — обычные `VARCHAR(50)` (`status`
в `upload_check` — `VARCHAR(32)`, `rule_type.code` — `VARCHAR(50)`). Значит БД
примет любое значение, а список ниже — это то, что реально задаёт код, модель
или документация; его нужно обновлять вместе с ними.

| Поле | Тип в БД | Полный список значений | Кто задаёт |
| --- | --- | --- | --- |
| `ruleset.status` | VARCHAR(50), NULL | `draft`, `review`, `approved`, `rejected`, `active`, `archived` | default модели `draft`; переходы — `backend/api/admin.py`; `active` — условие частичного индекса |
| `rule.status` | VARCHAR(50), NULL | `draft`, `review`, `approved`, `rejected` | default модели `draft`; `RuleDecision.decision` — `Literal["approved", "rejected"]`; `review` ставит админка |
| `check.status` | VARCHAR(50), NULL | `pending` | default модели; других значений код не присваивает |
| `violation.status` | VARCHAR(50), NULL | `open` | default модели; других значений код не присваивает |
| `upload_check.status` | VARCHAR(32), NOT NULL | `uploaded`, `queued`, `processing`, `completed`, `failed` | `CheckStatus` в `backend/api/models.py`; записывается `uploaded` |
| `guideline.relation_type` | VARCHAR(50), NULL | значений не задано | свободная строка |
| `ruleset.generation_method` | VARCHAR(50), NULL | значений не задано | свободная строка |
| `ruleset.trust_level` | VARCHAR(50), NULL | значений не задано | свободная строка |
| `rule.origin` | VARCHAR(50), NULL | `llm` | встречается только в тесте админки; свободная строка |
| `rule.verification_method` | VARCHAR(50), NULL | `code`, `llm_recommendation` | `docs/RULES_CONTRACT.md`; движок значение не читает |
| `rule.target_element` | VARCHAR(50), NULL | `heading`, `table`, `figure` | только в раннем предложении схемы; в коде не используется |
| `rule.strictness` | VARCHAR(50), NULL | `error`, `warning`, `info` | `Strictness` в `backend/rule_engine/rules.py`; `NULL` → `error` при компиляции |
| `violation.severity` | VARCHAR(50), NULL | `error`, `warning`, `info` | `Strictness` движка; в БД пишется как есть |
| `rule.scope` | VARCHAR(50), NULL | `body_text`, `body_paragraphs`, `all_paragraphs`, `table_cells`, `headers_footers`, `footnotes`, `sections`, `headings`, `document_structure`, `toc`, `bibliography`, `captions`, `document` | `Scope` в `backend/rule_engine/rules.py` |
| `org_unit.kind` | VARCHAR(50), NOT NULL | `university`, `institute`, `department` | `Literal` в `backend/api/admin.py` (`OrgUnitUpdate`) |
| `rule_type.code` | VARCHAR(50), NOT NULL | 36 кодов каталога | `backend/app/rule_types.py`, сид `seed_rule_types` |

### 6.2 Особенности перечислений

- `ruleset.status` — единственное поле, от значения которого зависит
  ограничение целостности (частичный уникальный индекс). Значение `active`
  пишется админкой и проверяется индексом; остальные статусы индекс не трогает.
- `rule.verification_method = 'llm_recommendation'` означает «движок это
  требование не проверяет»: оно сохраняется и попадает в отчёт отдельным блоком.
  `NULL` трактуется как проверяемое кодом.
- `rule.target_element` и `violation.severity` в моделях не связаны:
  `target_element` — планируемое поле админки, `severity` — фактическая строгость
  сработавшего правила.

### 6.3 Клиентские значения по умолчанию (не DEFAULT в БД)

Модели задают значения на стороне Python; в DDL их нет, поэтому INSERT в обход
ORM (psql, внешний сервис) оставит `NULL` в nullable-колонках:

| Поле | Значение по умолчанию в модели |
| --- | --- |
| `check.status` | `pending` |
| `check.created_at` | `datetime.utcnow` (naive UTC) |
| `violation.status` | `open` |
| `rule.status` | `draft` |
| `ruleset.status` | `draft` |
| `guideline.confirmed` | `False` |
| `guideline_link.is_confirmed` | `False` |

У `upload_check` клиентских default нет: значения обязаны передать явно.

## 7. Как проверить

Всё выполняется из корня репозитория `C:\\code\\vkr_checker`; контейнер
`vkr-db-test` слушает порт 5433.

```bash
# 1. Временная БД в том же контейнере
docker exec vkr-db-test dropdb -U vkr_checker --if-exists vkr_erd_check
docker exec vkr-db-test createdb -U vkr_checker vkr_erd_check

# 2. Схема из миграции (не create_all)
cd backend
DATABASE_URL=postgresql+psycopg://vkr_checker:local-dev-password@localhost:5433/vkr_erd_check \
  python -m alembic upgrade head
DATABASE_URL=postgresql+psycopg://vkr_checker:local-dev-password@localhost:5433/vkr_erd_check \
  python -m alembic current        # ожидается: 95f6b91fcc64 (head)

# 3. Самопроверка документа: parse docs/ERD.md ↔ sqlalchemy.inspect
DATABASE_URL=postgresql+psycopg://vkr_checker:local-dev-password@localhost:5433/vkr_erd_check \
  python manual_test/_erd_check.py   # 0 при совпадении, 1 и список расхождений иначе

# 4. Независимая сверка моделей с живой БД (alembic compare_metadata)
python manual_test/_erd_model_diff.py   # ожидается: [] и "diff entries: 0"

# 5. Ручные срезы каталога
docker exec vkr-db-test psql -U vkr_checker -d vkr_erd_check \
  -c "select tablename, indexname, indexdef from pg_indexes where schemaname='public' order by 1,2;"
docker exec vkr-db-test psql -U vkr_checker -d vkr_erd_check \
  -c "select conrelid::regclass as tbl, conname, contype, pg_get_constraintdef(oid) from pg_constraint where connamespace='public'::regnamespace order by 1,2;"

# 6. Убрать временную БД
docker exec vkr-db-test dropdb -U vkr_checker vkr_erd_check
```

`_erd_check.py` и `_erd_model_diff.py` лежат в `backend/manual_test/`, каталог
целиком в `.gitignore` — это инструменты проверки документа, а не часть
поставки.

## 8. Расхождения и дефекты

### 8.1 Модели ↔ миграция: расхождений нет

`alembic.autogenerate.compare_metadata` с `compare_type=True` на живой БД даёт
пустой список (`[]`, `diff entries: 0`): имена таблиц, колонок, типы с длиной,
nullable, PK, UNIQUE, FK и индексы в моделях и в миграции совпадают полностью.
Отдельная сверка `Base.metadata` с БД (`backend/manual_test/_t17_schema_check.py`)
тоже проходит. Поэтому раздел ниже — не про расхождение трёх источников, а про
дефекты самой схемы, которые видны при чтении каталога.

### 8.2 Дефекты и ограничения фактической схемы

1. **Нет SQL-значений по умолчанию.** Все default (`draft`, `pending`, `open`,
   `False`, `utcnow`) заданы в Python-моделях и отсутствуют в DDL (§6.3).
   Строка, вставленная в обход ORM, получит `NULL` в nullable-колонке —
   например, набор правил без статуса или нарушение без статуса.
2. **Дублирующие индексы на первичных ключах.** У каждой из 10 таблиц есть
   индекс PK (`<table>_pkey`) и дополнительный `ix_<table>_id` по той же
   колонке: 10 лишних индексов из 24. Причина — `index=True` рядом с
   `primary_key=True` в моделях.
3. **Ни одного CHECK-констрейнта.** Перечисления из §6 не проверяются БД:
   `status = 'activee'` или `strictness = 'fatal'` вставятся без ошибки.
4. **`rule.params` — `json`, не `jsonb`.** Первоначальное предложение схемы
   предполагало `jsonb`; в миграции `sa.JSON()` даёт `json`. Нет ни GIN-индексов, ни
   нормализации ключей, сравнение параметров в SQL невозможно.
5. **Временные метки несогласованы.** `check.created_at`, `check.completed_at`,
   `ruleset.confirmed_at` — `timestamp` без часового пояса (в модели
   `datetime.utcnow`, то есть naive UTC), а `upload_check.created_at` —
   `timestamptz`. Один и тот же момент хранится в двух разных типах.
6. **Таблицы `check` и `violation` не заполняются кодом.** Движок строит
   pydantic-модель `Violation` (`backend/rule_engine/findings.py`), а не строку
   ORM; `Check` используется только в запросах аналитики
   (`backend/api/admin.py`). Фактически это «спящая» половина схемы: FK
   `violation.check_id`/`rule_id` есть, но писателя нет.
7. **`guideline_link.guideline_id` nullable**, хотя связь «привязка → методичка»
   без методички бессмысленна (для сравнения: `ruleset.guideline_id` —
   `NOT NULL`). Строка-сирота вставится без ошибки.
8. **Все FK — `ON DELETE NO ACTION`.** Удалить методичку, у которой есть
   наборы правил, нельзя; каскадов и `SET NULL` в схеме нет ни одного.
9. **`upload_check` изолирована.** Ни одного FK к `guideline`/`check`: связь
   загрузки с проверкой и методичкой существует только на уровне значений
   (имя файла, время), БД её не гарантирует.
10. **`Base` объявлен через `sqlalchemy.ext.declarative.declarative_base`** —
    устаревший путь для SQLAlchemy 2.0 (работает, но при обновлении библиотеки
    требует замены на `sqlalchemy.orm.DeclarativeBase`).

### 8.3 Что из предложения по схеме не реализовано

Базовые три правки [`docs/ERD_APPENDIX.md`](ERD_APPENDIX.md), которых в схеме нет:

| Предложено | Фактически |
| --- | --- |
| `rule.target` — `jsonb` (область + исключения) | колонки нет; есть `rule.scope VARCHAR(50)` |
| `violation.rule_code` — `varchar(50) NOT NULL` | колонки нет |
| `rule_type.default_scope` — `varchar(50)` | колонки нет; область по умолчанию живёт в `backend/app/rule_types.py` |

Поэтому `docs/ERD_APPENDIX.md` нельзя использовать как описание БД: это проект
изменений, а не текущее состояние.
