# Контракт формата правил нормоконтроля

Версия формата: **1.0.0**. Наборы правил по умолчанию: `vkr-rule-engine-test` 4.3.0, профиль УрГПУ 2.2.0.

Документ адресован двум сторонам: ИИ-команде, которая извлекает требования из методических указаний и отдаёт JSON, и разработчикам движка, которые этот JSON исполняют. Всё, что описано ниже, проверяется машинно: `backend/tests/test_contract_doc.py` сверяет таблицы этого файла с реестром кодов, моделями pydantic и схемой БД.

## 1. Назначение и границы

### 1.1. Зачем нужен контракт

Методические указания написаны для человека: «поля не менее 20 мм», «шрифт Times New Roman 14 пт», «заголовки прописными». Движок проверяет документ по формальному описанию, где у каждого требования есть код, область проверки и параметры. Контракт фиксирует ровно то, что передаётся из одной стороны в другую: JSON-объект правила и JSON-объект набора правил. Всё остальное — внутреннее дело исполняющей стороны.

Границы ответственности:

* ИИ-команда отвечает за смысл: находит требование в методичке, выбирает код из §2, заполняет `params`, приводит дословную цитату в `quote` и формулирует `message` для пользователя.
* Движок отвечает за исполнение: разбирает документ, выбирает блоки по области из §3, сравнивает измерения с `params` и выдаёт нарушения с адресами блоков.
* Контракт отвечает за стык: имена кодов, имена и типы параметров, области, состав полей и правила валидации. Если требование не выражается описанными средствами, это не повод придумывать код — см. §7 и §8.

### 1.2. Что документ не описывает

* Устройство движка: семейства чекеров, порядок обхода блоков, внутренние модели документа. Это описано в `backend/rule_engine/RULES.md`.
* Формат отчёта о проверке: `RuleCheckReport`, адреса нарушений, `llm_recommendation`. Здесь только сказано, что попадает в отчёт из правила (§7).
* Педагогические и юридические нормы: почему требование такое, решает методичка, а не контракт.
* Интерфейс пользователя и админку: как правило попадает в базу и кто его утверждает.

### 1.3. Версия формата и совместимость

Версия формата — свойство самого контракта, а не набора. Набор объявляет свою версию в `ruleset_version` (§5.3). Совместимым изменением считается такое, при котором уже написанный корректный набор продолжает проходить валидацию без правок и даёт прежние нарушения.

| Изменение | Совместимо | Что делать сторонам |
|---|---|---|
| Новый код в реестре | да | ИИ может использовать код только после появления его в §2; старые наборы не меняются |
| Новый необязательный ключ `params` с значением по умолчанию | да | движок обязан трактовать отсутствие ключа как документированное значение по умолчанию |
| Новый необязательный ключ `target` | да | по умолчанию поведение прежнее |
| Новое значение перечисления в `params` | да | движок принимает старое и новое значение |
| Новый допустимый `scope` у существующего кода | да | прежние наборы валидны, проверка не меняется |
| Новый обязательный ключ `params` | нет | ломает все наборы; требует новой версии формата и правки наборов |
| Удаление или переименование кода | нет | правила с таким кодом перестают загружаться |
| Удаление или переименование ключа `params` | нет | лишний ключ отвергается: `extra="forbid"` |
| Ужесточение типа (`number` → `integer`) | нет | наборы с дробным значением перестают валидироваться |
| Изменение значения по умолчанию | нет | меняет нарушения без правки набора |
| Изменение смысла кода при том же имени | нет | самый опасный случай: цитаты и сообщения перестают соответствовать проверке |
| Сужение списка допустимых `scope` для кода | нет | наборы с прежней областью отвергаются |

Практическое следствие: неизвестные поля отвергаются на всех уровнях (`extra="forbid"`). Набор, написанный под будущую версию формата, не загрузится, и это правильное поведение — лучше явная ошибка при загрузке, чем молча проигнорированное требование.

### 1.4. Термины

* **Правило** — один JSON-объект: что проверять (`code`), по каким параметрам (`params`), где (`target`) и как сообщать (`message`, `strictness`, `quote`).
* **Набор (ruleset)** — JSON-файл с массивом правил, идентификатором и версией.
* **Профиль** — набор, который через `extends` ссылается на базовый набор и переопределяет часть правил по `rule_id`; так оформляются требования конкретного вуза или кафедры.
* **Код** — имя вида проверки из §2; оно же дискриминатор правила в JSON.
* **Область (scope)** — какие блоки документа попадают в проверку (§3).
* **Роль заголовка** — типизированное назначение заголовка: введение, глава, приложение (§4.3).

## 2. Коды и параметры правил

### 2.1. Как читать таблицы

Таблица 2.2 — реестр кодов: по одной строке на код, с русским названием, областью по умолчанию, полным списком допустимых областей и `strictness` по умолчанию. Таблицы 2.3 — параметры каждого кода: по одной строке на ключ `params`, с типом, обязательностью, значением по умолчанию, допустимыми значениями и единицами измерения, назначением и примером. Тип `number` — число с плавающей точкой, `integer` — целое, `boolean` — `true`/`false`, `string` — строка, `X[]` — массив элементов `X`, «или `null`» — значение можно не передавать или передать `null`. Вложенные объекты описаны в §2.4.

Реестр закрытый: код вне таблицы 2.2 не существует, даже если требование методички кажется очевидным. Всего в реестре 36 кодов.

### 2.2. Реестр кодов

| Код | Название | Область по умолчанию | Допустимые области | strictness по умолчанию |
|---|---|---|---|---|
| `alignment` | Выравнивание абзаца | `captions` | `body_text`, `captions`, `table_cells` | `error` |
| `appendix_order` | Порядок приложений | `document_structure` | `document_structure` | `error` |
| `bibliography_cited` | Цитирование источников | `bibliography` | `bibliography` | `error` |
| `bibliography_count` | Количество источников | `bibliography` | `bibliography` | `error` |
| `bibliography_format` | Оформление записи списка литературы | `bibliography` | `bibliography` | `error` |
| `bibliography_link` | Разрешение ссылок на источники | `bibliography` | `bibliography` | `error` |
| `bibliography_order` | Нумерация списка литературы | `bibliography` | `bibliography` | `error` |
| `bibliography_recency` | Свежесть источников | `bibliography` | `bibliography` | `error` |
| `caption_format` | Оформление подписи | `captions` | `captions` | `error` |
| `caption_numbering` | Нумерация подписей | `captions` | `captions` | `error` |
| `caption_placement` | Положение подписи объекта | `captions` | `captions` | `error` |
| `caption_reference` | Ссылка на объект в тексте | `captions` | `captions` | `error` |
| `document_volume` | Объём работы | `document` | `document` | `error` |
| `equation_numbering` | Нумерация формул | `document` | `document` | `error` |
| `font` | Шрифтовое оформление текста | `body_text` | `all_paragraphs`, `body_paragraphs`, `body_text`, `footnotes`, `headers_footers`, `headings`, `table_cells` | `error` |
| `heading_format` | Оформление заголовка | `headings` | `headings` | `error` |
| `heading_hyphenation` | Переносы в заголовках | `headings` | `headings` | `error` |
| `heading_levels` | Иерархия заголовков | `headings` | `headings` | `error` |
| `heading_numbering` | Нумерация заголовков | `headings` | `headings` | `error` |
| `heading_page_break` | Заголовок с новой страницы | `headings` | `headings` | `error` |
| `indent` | Отступы абзаца | `body_text` | `all_paragraphs`, `body_paragraphs`, `body_text`, `footnotes`, `headers_footers`, `headings`, `table_cells` | `error` |
| `inline_reference_format` | Оформление ссылки в тексте | `body_text` | `all_paragraphs`, `body_paragraphs`, `body_text`, `footnotes`, `headers_footers`, `table_cells` | `error` |
| `keep_next` | Неотрывность заголовка от текста | `headings` | `headings` | `error` |
| `line_spacing` | Межстрочный интервал | `body_text` | `all_paragraphs`, `body_paragraphs`, `body_text`, `footnotes`, `headers_footers`, `headings`, `table_cells` | `error` |
| `list_format` | Маркеры перечислений | `body_paragraphs` | `all_paragraphs`, `body_paragraphs`, `body_text` | `error` |
| `margin` | Поля страницы | `sections` | `sections` | `error` |
| `microtypography` | Микротипографика | `body_paragraphs` | `all_paragraphs`, `body_paragraphs`, `body_text` | `error` |
| `page_format` | Формат листа | `sections` | `sections` | `error` |
| `page_number_position` | Положение номера страницы | `headers_footers` | `headers_footers` | `error` |
| `required_sections` | Состав и порядок разделов | `document_structure` | `document_structure` | `error` |
| `table_continuation` | Перенос таблицы | `table_cells` | `table_cells` | `error` |
| `table_header_format` | Оформление шапки таблицы | `table_cells` | `table_cells` | `error` |
| `table_layout` | Линии и ширина таблицы | `table_cells` | `table_cells` | `error` |
| `title_page_content` | Реквизиты титульного листа | `document_structure` | `document_structure` | `error` |
| `toc` | Оглавление | `toc` | `toc` | `error` |
| `widow_control` | Контроль висячих строк | `body_text` | `body_paragraphs`, `body_text` | `error` |

`strictness` по умолчанию — `error`: нарушение блокирует работу. `warning` — замечание, `info` — рекомендация; значение переопределяется в конкретном правиле, но не в наборе целиком. Столбец таблицы показывает значение по умолчанию из реестра: в наборе `vkr-rule-engine-test` 4.3.0 часть правил переопределена — `info` — `microtypography`; `warning` — `bibliography_count`, `bibliography_recency`, `document_volume`, `equation_numbering`, `heading_format`, `heading_page_break`, `keep_next`, `microtypography`, `widow_control`.

### 2.3. Параметры по кодам

Параметры каждого кода — закрытый объект: ключи, которых нет в таблице кода, отвергаются. Обязательные ключи (столбец «Обязательность» = «да») должны присутствовать всегда; остальные можно опускать, тогда действует значение по умолчанию. У кода может быть несколько правил с разными `params` и разными областями — так описываются, например, разные требования к шрифту основного текста и ячеек таблиц. Всего в реестре 149 ключей `params`; обязательных из них — 5.

#### `alignment` — Выравнивание абзаца

Выравнивание абзаца: по левому краю, по центру, по правому краю, по ширине.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `alignment` | `left` / `center` / `right` / `justify` | да | — | одно из: `left`, `center`, `right`, `justify` | Ожидаемое выравнивание: left, center, right или justify (по ширине) | `"justify"` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: alignment -->
```json
{
  "rule_id": "alignment_body_text",
  "code": "alignment",
  "message": "Основной текст выравнивается по ширине",
  "quote": "Шрифт: 14pt Times New Roman, 1,5 интервала, по ширине, отступ 1,25 (красная строка)",
  "target": {
    "scope": "body_text"
  },
  "params": {
    "alignment": "justify"
  }
}
```

#### `appendix_order` — Порядок приложений

Порядок приложений по первому упоминанию, каждое приложение с нового листа.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `order` | `by_first_mention` / `any` | нет | `by_first_mention` | одно из: `by_first_mention`, `any` | Требуемый порядок приложений: в порядке упоминания в тексте | `by_first_mention` |
| `new_page_each` | boolean | нет | `true` | `true` или `false` | Требовать начало каждого приложения с нового листа | `true` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: appendix_order -->
```json
{
  "rule_id": "appendix_order_default",
  "code": "appendix_order",
  "message": "Приложения расположены в порядке ссылок в тексте",
  "quote": "Приложения в порядке их упоминания в тексте, каждое с нового листа.",
  "target": {
    "scope": "document_structure"
  },
  "params": {
    "order": "by_first_mention",
    "new_page_each": true
  }
}
```

#### `bibliography_cited` — Цитирование источников

Каждая запись списка упомянута в тексте работы.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `require_all_cited` | boolean | нет | `true` | `true` или `false` | Требовать ссылку в тексте на каждый источник списка литературы | `true` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: bibliography_cited -->
```json
{
  "rule_id": "bibliography_all_cited",
  "code": "bibliography_cited",
  "message": "Каждый источник списка литературы имеет ссылку в тексте работы",
  "quote": "В список литературы могут быть включены только те источники, которые использовались при написании курсовой работы или на которые есть ссылки в тексте",
  "target": {
    "scope": "bibliography"
  },
  "params": {
    "require_all_cited": true
  }
}
```

#### `bibliography_count` — Количество источников

Число записей списка литературы в заданном диапазоне.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `min_sources` | integer или `null` | нет | `20` | целое число, не меньше 1 или `null` (источники) | Минимальное количество источников в списке литературы | `20` |
| `max_sources` | integer или `null` | нет | `null` | целое число, не меньше 1 или `null` (источники) | Максимальное количество источников (None — не проверять) | `null` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: bibliography_count -->
```json
{
  "rule_id": "bibliography_count",
  "code": "bibliography_count",
  "strictness": "warning",
  "message": "Список литературы содержит не менее 20 источников",
  "quote": "Список литературы должен включать не менее 15-17 источников в полном библиографическом описании.",
  "target": {
    "scope": "bibliography"
  },
  "params": {
    "min_sources": 20
  }
}
```

#### `bibliography_format` — Оформление записи списка литературы

Обязательные элементы описания: url, дата обращения, год, страницы, isbn/issn, «//», обозначение вида издания.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `categories` | (`book_monograph` / `journal_article` / `conference_paper` / `electronic_resource` / `normative_act_law` / `standard_gost` / `dissertation_autoref` / `patent` / `unknown`)[] или `null` | нет | `null` | список, элементы — одно из: `book_monograph`, `journal_article`, `conference_paper`, `electronic_resource`, `normative_act_law`, `standard_gost`, `dissertation_autoref`, `patent`, `unknown` или `null` | Категории источников, к которым применяется правило (None — все) | `["journal_article"]` |
| `require_url` | boolean | нет | `false` | `true` или `false` | Требовать URL источника | `false` |
| `require_access_date` | boolean | нет | `false` | `true` или `false` | Требовать дату обращения к источнику | `false` |
| `require_year` | boolean | нет | `false` | `true` или `false` | Требовать год издания | `false` |
| `require_pages` | boolean | нет | `false` | `true` или `false` | Требовать объём в страницах | `false` |
| `require_isbn_or_issn` | boolean | нет | `false` | `true` или `false` | Требовать ISBN или ISSN издания | `false` |
| `require_double_slash` | boolean | нет | `false` | `true` или `false` | Требовать разделитель «//» перед названием источника (статья, глава) | `false` |
| `require_text_type_designation` | boolean | нет | `false` | `true` или `false` | Требовать обозначение вида издания: [Текст], [Электронный ресурс] и т.п. | `false` |
| `forbid_dot_dash` | boolean | нет | `false` | `true` или `false` | Запрещать разделитель «. — » между областями описания: по методичке области разделяются точкой | `false` |
| `exempt_categories` | (`book_monograph` / `journal_article` / `conference_paper` / `electronic_resource` / `normative_act_law` / `standard_gost` / `dissertation_autoref` / `patent` / `unknown`)[] | нет | `["electronic_resource", "normative_act_law", "standard_gost"]` | список, элементы — одно из: `book_monograph`, `journal_article`, `conference_paper`, `electronic_resource`, `normative_act_law`, `standard_gost`, `dissertation_autoref`, `patent`, `unknown` | Категории с особым описанием: не требуются страницы и год, запрет точки-тире не применяется | `["electronic_resource", "normative_act_law", "standard_gost"]` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: bibliography_format -->
```json
{
  "rule_id": "bibliography_format_electronic",
  "code": "bibliography_format",
  "message": "Электронный ресурс в списке литературы содержит URL и дату обращения",
  "quote": "URL: http://www.voennoepravo.ru/node/2149 (дата обращения: 19.09.2007).",
  "target": {
    "scope": "bibliography"
  },
  "params": {
    "categories": [
      "electronic_resource"
    ],
    "require_url": true,
    "require_access_date": true,
    "require_year": false,
    "require_pages": false,
    "require_isbn_or_issn": false,
    "require_double_slash": false,
    "require_text_type_designation": false,
    "forbid_dot_dash": false
  }
}
```

#### `bibliography_link` — Разрешение ссылок на источники

Ссылки в тексте разрешаются в записи списка литературы.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `allow_unresolved` | boolean | нет | `false` | `true` или `false` | Разрешить ссылки на источники, которых нет в списке литературы | `false` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: bibliography_link -->
```json
{
  "rule_id": "bibliography_links_resolved",
  "code": "bibliography_link",
  "message": "Ссылки в тексте указывают на источники, включенные в список литературы",
  "quote": "URL: http://www.voennoepravo.ru/node/2149 (дата обращения: 19.09.2007).",
  "target": {
    "scope": "bibliography"
  },
  "params": {
    "allow_unresolved": false
  }
}
```

#### `bibliography_order` — Нумерация списка литературы

Сквозная нумерация записей, пропуски, дубли, группы, алфавитный порядок.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `start_at` | integer | нет | `1` | целое число, не меньше 0 (номер записи) | Номер первой записи списка | `1` |
| `allow_gaps` | boolean | нет | `false` | `true` или `false` | Разрешить пропуски в нумерации (False — номера идут подряд) | `false` |
| `allow_duplicates` | boolean | нет | `false` | `true` или `false` | Разрешить повторяющиеся номера записей (False — номера уникальны) | `false` |
| `allow_groups` | boolean | нет | `true` | `true` или `false` | Разрешить выделение групп источников заголовками вида «Нормативные акты:». false — группы запрещены, список нумеруется сквозняком | `true` |
| `group_order` | string[] или `null` | нет | `null` | список строк или `null` | Требуемый порядок групп: элемент списка — одна группа, категории внутри группы перечисляются через '\|', например 'book_monograph\|journal_article\|conference_paper'. Запись попадает в группу по detected_category | `["normative_act_law", "book_monograph\|journal_article"]` |
| `sort` | `alphabetical` или `null` | нет | `null` | одно из: `alphabetical` или `null` | Требуемый порядок записей внутри списка: 'alphabetical' — по первой букве описания без учета регистра | `null` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: bibliography_order -->
```json
{
  "rule_id": "bibliography_order",
  "code": "bibliography_order",
  "message": "Источники в списке литературы пронумерованы подряд",
  "quote": "Список литературы: сквозная нумерация, группы не выделяются",
  "target": {
    "scope": "bibliography"
  },
  "params": {
    "start_at": 1,
    "allow_gaps": false,
    "allow_duplicates": false,
    "allow_groups": true
  }
}
```

#### `bibliography_recency` — Свежесть источников

Доля свежих источников и покрытие годами.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `min_recent_share` | number | нет | `0.3` | число, не меньше 0.0, не больше 1.0 (доля от 0 до 1) | Минимальная доля источников не старше recent_years лет | `0.3` |
| `recent_years` | integer | нет | `5` | целое число, не меньше 1 (годы) | Сколько лет считать свежими: год источника не старше reference_year - recent_years | `5` |
| `reference_year` | integer или `null` | нет | `null` | целое число, не меньше 1900, не больше 2100 или `null` (год) | Год отсчёта свежести (None — текущий год) | `null` |
| `min_year_coverage` | number | нет | `0.5` | число, не меньше 0.0, не больше 1.0 (доля от 0 до 1) | Минимальная доля записей с известным годом для оценки свежести | `0.5` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: bibliography_recency -->
```json
{
  "rule_id": "bibliography_recency",
  "code": "bibliography_recency",
  "strictness": "warning",
  "message": "Среди источников не менее 30% изданы за последние 5 лет",
  "quote": "В список литературы могут быть включены только те источники, которые использовались при написании курсовой работы или на которые есть ссылки в тексте",
  "target": {
    "scope": "bibliography"
  },
  "params": {
    "min_recent_share": 0.3,
    "recent_years": 5,
    "min_year_coverage": 0.5
  }
}
```

#### `caption_format` — Оформление подписи

Разделитель между номером и названием, точка в конце, слово-префикс, регистр.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `entity_type` | `figure` / `table` / `appendix` / `any` | нет | `any` | одно из: `figure`, `table`, `appendix`, `any` | Вид объектов: рисунок, таблица, приложение или все вместе | `any` |
| `separator` | `dash` / `dot` / `space` / `none` или `null` | нет | `null` | одно из: `dash`, `dot`, `space`, `none` или `null` | Вид разделителя между номером и названием (None — не проверять) | `"dash"` |
| `require_trailing_dot` | boolean или `null` | нет | `null` | `true` или `false` или `null` | Требовать точку в конце подписи (None — не проверять) | `null` |
| `prefix_words` | string[] или `null` | нет | `null` | список строк или `null` | Допустимые слова-префиксы подписи (None — не проверять) | `null` |
| `uppercase` | boolean или `null` | нет | `null` | `true` или `false` или `null` | Требовать текст подписи прописными буквами (None — не проверять) | `null` |
| `capitalize_first_letter` | boolean или `null` | нет | `null` | `true` или `false` или `null` | Требовать заглавную первую букву названия подписи, например «Таблица 1. Данные» (None — не проверять) | `null` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: caption_format -->
```json
{
  "rule_id": "caption_format_figures",
  "code": "caption_format",
  "message": "Номер и название рисунка разделены тире, точка в конце подписи не ставится",
  "quote": "Номера иллюстраций и их заглавия пишутся внизу под изображением, обозначаются арабскими цифрами без номера после слова «Рис.»",
  "target": {
    "scope": "captions"
  },
  "params": {
    "entity_type": "figure",
    "separator": "dash",
    "require_trailing_dot": false
  }
}
```

#### `caption_numbering` — Нумерация подписей

Схема нумерации рисунков, таблиц и приложений, пропуски и дубли номеров.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `entity_type` | `figure` / `table` / `appendix` / `any` | нет | `any` | одно из: `figure`, `table`, `appendix`, `any` | Вид объектов: рисунок, таблица, приложение или все вместе | `any` |
| `scheme` | `continuous_arabic` / `section_based` / `appendix_based` / `any_of` или `null` | нет | `continuous_arabic` | одно из: `continuous_arabic`, `section_based`, `appendix_based`, `any_of` или `null` | Требуемая схема нумерации: сквозная арабская, своя в каждом разделе, своя в приложении или 'any_of' — любая из перечисленных в schemes. Пусто вместе с schemes — схема не проверяется | `"continuous_arabic"` |
| `schemes` | (`continuous_arabic` / `section_based` / `appendix_based`)[] или `null` | нет | `null` | список, элементы — одно из: `continuous_arabic`, `section_based`, `appendix_based` или `null` | Допустимые схемы нумерации, если методичка разрешает любую из них. Взаимоисключающе с scheme | `null` |
| `appendix` | `own` / `continuous` / `any` | нет | `any` | одно из: `own`, `continuous`, `any` | Нумерация таблиц и рисунков в приложении: 'own' — своя (приложение А, таблица А.1), 'continuous' — продолжает общую | `any` |
| `skip_number_when_single` | boolean | нет | `false` | `true` или `false` | Единственный объект вида не нумеруется и слово-префикс не пишется: проверяется по числу объектов в cross_ref_index | `false` |
| `allow_gaps` | boolean | нет | `false` | `true` или `false` | Разрешить пропуски номеров | `false` |
| `allow_duplicates` | boolean | нет | `false` | `true` или `false` | Разрешить повтор номеров | `false` |
| `allow_out_of_order` | boolean | нет | `false` | `true` или `false` | Разрешить нарушение порядка номеров (номер меньше предыдущего) | `false` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: caption_numbering -->
```json
{
  "rule_id": "caption_numbering_figures",
  "code": "caption_numbering",
  "message": "Рисунки нумеруются сквозной арабской нумерацией без дублей, пропусков и нарушения порядка",
  "quote": "Все иллюстрации именуются рисунками, нумерация сквозная.",
  "target": {
    "scope": "captions"
  },
  "params": {
    "entity_type": "figure",
    "scheme": "continuous_arabic",
    "appendix": "any",
    "skip_number_when_single": false,
    "allow_gaps": false,
    "allow_duplicates": false,
    "allow_out_of_order": false
  }
}
```

#### `caption_placement` — Положение подписи объекта

Подпись над или под объектом, выравнивание, кавычки вокруг префикса, слово-префикс.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `entity_type` | `figure` / `table` / `appendix` | да | — | одно из: `figure`, `table`, `appendix` | Вид объекта: рисунок, таблица или приложение | `"table"` |
| `position` | `above_object` / `below_object` / `above_table` или `null` | нет | `null` | одно из: `above_object`, `below_object`, `above_table` или `null` | Положение подписи относительно объекта (None — не проверять); above_table — то же, что above_object, для названия таблицы | `"above_object"` |
| `alignment` | `left` / `center` / `right` / `both` / `distribute` или `null` | нет | `null` | одно из: `left`, `center`, `right`, `both`, `distribute` или `null` | Выравнивание абзаца подписи | `null` |
| `forbid_quotes` | boolean | нет | `false` | `true` или `false` | Запрещать кавычки вокруг слова-префикса подписи | `false` |
| `prefix_word` | string или `null` | нет | `null` | строка или `null` | Требуемое полное слово-префикс подписи, например 'Таблица' | `null` |
| `title_case` | `capitalized` / `uppercase` или `null` | нет | `null` | одно из: `capitalized`, `uppercase` или `null` | Регистр названия подписи | `null` |
| `require_number` | boolean | нет | `false` | `true` или `false` | Требовать номер объекта в подписи | `false` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: caption_placement -->
```json
{
  "rule_id": "caption_placement_table",
  "code": "caption_placement",
  "message": "Подпись таблицы размещена над таблицей, справа, без кавычек",
  "quote": "Слово «Таблица» без сокращения и кавычек пишется в правом верхнем углу над самой таблицей и ее заголовком.",
  "target": {
    "scope": "captions"
  },
  "params": {
    "entity_type": "table",
    "position": "above_table",
    "alignment": "left",
    "forbid_quotes": true,
    "prefix_word": "Таблица",
    "require_number": true
  }
}
```

#### `caption_reference` — Ссылка на объект в тексте

Объект упомянут в тексте работы.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `entity_type` | `figure` / `table` / `appendix` / `any` | нет | `any` | одно из: `figure`, `table`, `appendix`, `any` | Вид объектов: рисунок, таблица, приложение или все вместе | `"figure"` |
| `required` | boolean | нет | `true` | `true` или `false` | Требовать упоминание объекта в тексте работы | `true` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: caption_reference -->
```json
{
  "rule_id": "caption_reference_figures",
  "code": "caption_reference",
  "message": "На каждый рисунок есть ссылка в тексте работы",
  "quote": "Остальной иллюстрационный материал располагают в приложениях.",
  "target": {
    "scope": "captions"
  },
  "params": {
    "entity_type": "figure",
    "required": true
  }
}
```

#### `document_volume` — Объём работы

Объём работы в страницах и словах.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `min_pages` | integer или `null` | нет | `null` | целое число, не меньше 1 или `null` (страницы) | Минимум страниц | `40` |
| `max_pages` | integer или `null` | нет | `null` | целое число, не меньше 1 или `null` (страницы) | Максимум страниц | `null` |
| `min_words` | integer или `null` | нет | `null` | целое число, не меньше 1 или `null` (слова) | Минимум слов | `null` |
| `max_words` | integer или `null` | нет | `null` | целое число, не меньше 1 или `null` (слова) | Максимум слов | `null` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: document_volume -->
```json
{
  "rule_id": "document_volume",
  "code": "document_volume",
  "strictness": "warning",
  "message": "Объём ВКР соответствует норме: 60–90 страниц и 8000–18000 слов",
  "quote": "Структурными элементами курсовой работы являются: титульный лист, оглавление, введение, основная часть, заключение, список литературы, приложения.",
  "target": {
    "scope": "document"
  },
  "params": {
    "min_pages": 60,
    "max_pages": 90,
    "min_words": 8000,
    "max_words": 18000
  }
}
```

#### `equation_numbering` — Нумерация формул

У вынесенной формулы есть номер.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `require_numbering` | boolean | нет | `true` | `true` или `false` | Требовать номер у вынесенной в отдельную строку формулы | `true` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: equation_numbering -->
```json
{
  "rule_id": "equation_numbering",
  "code": "equation_numbering",
  "strictness": "warning",
  "message": "Вынесенные формулы пронумерованы",
  "quote": "Нумерация разделов, подразделов, пунктов и т.д.: арабские цифры, без точки после последней цифры. Образец: 1.2",
  "target": {
    "scope": "document"
  },
  "params": {
    "require_numbering": true
  }
}
```

#### `font` — Шрифтовое оформление текста

Гарнитура, кегль и начертание ранов; сравниваются видимые раны, символические раны (формулы, знаки) пропускаются.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `family` | string или `null` | нет | `null` | строка или `null` | Гарнитура, например 'Times New Roman'; сравнение по нормализованному имени | `"Times New Roman"` |
| `size_pt` | number или `null` | нет | `null` | число, не меньше 1.0, не больше 200.0 или `null` (пункты) | Кегль в пунктах | `14` |
| `min_size_pt` | number или `null` | нет | `null` | число, не меньше 1.0, не больше 200.0 или `null` (пункты) | Нижняя граница кегля в пунктах: кегль не меньше значения. Взаимоисключающе с size_pt — задается либо точный кегль, либо граница | `null` |
| `bold` | boolean или `null` | нет | `null` | `true` или `false` или `null` | Ожидаемое начертание: полужирный | `false` |
| `italic` | boolean или `null` | нет | `null` | `true` или `false` или `null` | Ожидаемое начертание: курсив | `null` |
| `size_tolerance_pt` | number | нет | `0.5` | число, не меньше 0.0 (пункты) | Допуск по кеглю в пунктах (Word хранит half-points) | `0.5` |
| `check_complex_script` | boolean | нет | `false` | `true` или `false` | Учитывать complex script (font_cs): источники шума, по умолчанию выключено | `false` |
| `normalize_family` | boolean | нет | `true` | `true` или `false` | Сравнивать гарнитуру по нормализованному имени: без учета регистра, пробелов, дефисов, subset-префикса 'XXXXXX+' и стилевых маркеров (MT/PS/PSMT/Bold/Italic/Regular/Roman). false — прежнее строгое сравнение | `true` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: font -->
```json
{
  "rule_id": "font_body_text",
  "code": "font",
  "message": "Основной текст набирается гарнитурой Times New Roman, 14 pt",
  "quote": "Шрифт: 14pt Times New Roman, 1,5 интервала, по ширине, отступ 1,25 (красная строка)",
  "target": {
    "scope": "body_text"
  },
  "params": {
    "family": "Times New Roman",
    "size_pt": 14.0,
    "size_tolerance_pt": 0.5,
    "check_complex_script": false,
    "normalize_family": true
  }
}
```

#### `heading_format` — Оформление заголовка

Точка в конце, регистр, выравнивание, подчеркивание, интервалы до и после, шрифт заголовка.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `trailing_dot` | `any` / `forbid` / `require` | нет | `any` | одно из: `any`, `forbid`, `require` | Точка в конце текста заголовка: 'forbid' — не допускается, 'require' — обязательна | `any` |
| `trailing_dot_in_number` | `any` / `forbid` / `require` | нет | `any` | одно из: `any`, `forbid`, `require` | Точка после номера заголовка (например '1.' в начале строки) | `any` |
| `case` | `any` / `uppercase` / `capitalized` / `not_uppercase` | нет | `any` | одно из: `any`, `uppercase`, `capitalized`, `not_uppercase` | 'uppercase' — весь заголовок прописными, 'capitalized' — с прописной буквы и не целиком прописными, 'not_uppercase' — не целиком прописными | `"uppercase"` |
| `alignment` | `left` / `center` / `right` / `both` / `distribute` или `null` | нет | `null` | одно из: `left`, `center`, `right`, `both`, `distribute` или `null` | Ожидаемое выравнивание абзаца заголовка (effective_props.alignment) | `null` |
| `font` | FontExpectation или `null` | нет | `null` | — или `null` | Ожидаемое шрифтовое оформление заголовка: гарнитура, кегль, полужирность. Сравниваются видимые раны заголовка, а при их отсутствии — знак абзаца | `{"family": "Times New Roman", "size_pt": 14, "bold": true}` |
| `underline` | `any` / `forbid` / `require` | нет | `any` | одно из: `any`, `forbid`, `require` | Подчеркивание текста заголовка: 'forbid' — заголовок не подчеркивается, 'require' — подчеркивание обязательно | `any` |
| `space_after_lines` | number или `null` | нет | `null` | число, не меньше 0.0, не больше 10.0 или `null` (межстрочные интервалы) | Интервал после заголовка в межстрочных интервалах: значение умножается на межстрочный интервал заголовка и сравнивается с space_after_pt | `1` |
| `space_before_lines` | number или `null` | нет | `null` | число, не меньше 0.0, не больше 10.0 или `null` (межстрочные интервалы) | Интервал перед заголовком в межстрочных интервалах (space_before_pt) | `null` |
| `tolerance_pt` | number | нет | `3.0` | число, не меньше 0.0, не больше 30.0 (пункты) | Допуск сравнения интервалов before/after в пунктах | `3.0` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: heading_format -->
```json
{
  "rule_id": "heading_format_text",
  "code": "heading_format",
  "message": "Заголовок набирается с прописной буквы и без точки в конце",
  "quote": "не подчеркивая, без точки в конце.",
  "target": {
    "scope": "headings",
    "exclude_roles": [
      "title_page",
      "task_sheet",
      "appendix"
    ]
  },
  "params": {
    "trailing_dot": "forbid",
    "trailing_dot_in_number": "any",
    "case": "any",
    "underline": "any",
    "tolerance_pt": 3.0
  }
}
```

#### `heading_hyphenation` — Переносы в заголовках

Переносы и аббревиатуры в заголовках.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `forbid_hyphenation` | boolean | нет | `true` | `true` или `false` | Запрещать переносы слов в заголовках | `true` |
| `forbid_abbreviations` | boolean | нет | `false` | `true` или `false` | Запрещать аббревиатуры из прописных букв в заголовках | `false` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: heading_hyphenation -->
```json
{
  "rule_id": "heading_hyphenation_default",
  "code": "heading_hyphenation",
  "message": "В заголовках не допускаются переносы слов",
  "quote": "Перенос слов и аббревиатуры в заголовках не допускаются.",
  "target": {
    "scope": "headings"
  },
  "params": {
    "forbid_hyphenation": true,
    "forbid_abbreviations": false
  }
}
```

#### `heading_levels` — Иерархия заголовков

Последовательность уровней заголовков, перескоки уровней, максимальная глубина.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `first_level` | integer | нет | `1` | целое число, не меньше 1, не больше 9 | Уровень, с которого начинается нумерация заголовков документа | `1` |
| `allow_skipped_levels` | boolean | нет | `false` | `true` или `false` | Разрешить перескок уровня, например 1 -> 3 (по умолчанию запрещен) | `false` |
| `max_level` | integer или `null` | нет | `null` | целое число, не меньше 1, не больше 9 или `null` | Максимальная глубина вложенности заголовков (пусто — без ограничения) | `null` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: heading_levels -->
```json
{
  "rule_id": "heading_levels",
  "code": "heading_levels",
  "message": "Заголовки образуют непрерывную иерархию: старт с уровня 1, без перескоков уровней",
  "quote": "Нумерация разделов, подразделов, пунктов и т.д.: арабские цифры, без точки после последней цифры. Образец: 1.2",
  "target": {
    "scope": "headings",
    "exclude_roles": [
      "title_page",
      "task_sheet",
      "appendix"
    ]
  },
  "params": {
    "first_level": 1,
    "allow_skipped_levels": false,
    "max_level": 3
  }
}
```

#### `heading_numbering` — Нумерация заголовков

Сквозная последовательность номеров, номер родителя в подразделе, глубина номера, точка после номера.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `check_sequence` | boolean | нет | `true` | `true` или `false` | Требовать сквозную последовательность номеров среди соседних заголовков: 1, 2, 3 | `true` |
| `check_parent_number` | boolean | нет | `true` | `true` или `false` | Требовать, чтобы номер подраздела начинался с номера родителя: 1.2 внутри 1 | `true` |
| `check_depth_matches_level` | boolean | нет | `true` | `true` или `false` | Требовать, чтобы глубина номера совпадала с уровнем заголовка: 1.1 — второй уровень | `true` |
| `number_ending` | `any` / `with_dot` / `without_dot` | нет | `any` | одно из: `any`, `with_dot`, `without_dot` | Требование к точке после последней цифры номера | `"without_dot"` |
| `require_number_for_roles` | (`title_page` / `task_sheet` / `abstract_referat` / `toc` / `abbreviations` / `introduction` / `main_chapter` / `sub_section` / `conclusion` / `bibliography` / `appendix` / `unknown`)[] | нет | `["main_chapter", "sub_section"]` | список, элементы — одно из: `title_page`, `task_sheet`, `abstract_referat`, `toc`, `abbreviations`, `introduction`, `main_chapter`, `sub_section`, `conclusion`, `bibliography`, `appendix`, `unknown` | Заголовки этих ролей обязаны иметь номер (пусто — нумерация не обязательна). Роли из NUMBERLESS_HEADING_ROLES (введение, заключение, список литературы, приложения, лист задания, перечень сокращений) номер не требуют | `["main_chapter", "sub_section"]` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: heading_numbering -->
```json
{
  "rule_id": "heading_numbering",
  "code": "heading_numbering",
  "message": "Разделы нумеруются последовательно, подраздел начинается с номера родительского раздела",
  "quote": "Нумерация разделов, подразделов, пунктов и т.д.: арабские цифры, без точки после последней цифры. Образец: 1.2",
  "target": {
    "scope": "headings",
    "exclude_roles": [
      "title_page",
      "task_sheet",
      "appendix"
    ]
  },
  "params": {
    "check_sequence": true,
    "check_parent_number": true,
    "check_depth_matches_level": true,
    "number_ending": "any",
    "require_number_for_roles": [
      "main_chapter",
      "sub_section"
    ]
  }
}
```

#### `heading_page_break` — Заголовок с новой страницы

Структурные разделы начинаются с нового листа.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `roles` | (`title_page` / `task_sheet` / `abstract_referat` / `toc` / `abbreviations` / `introduction` / `main_chapter` / `sub_section` / `conclusion` / `bibliography` / `appendix` / `unknown`)[] | нет | `["main_chapter", "introduction", "conclusion", "bibliography", "appendix"]` | список, элементы — одно из: `title_page`, `task_sheet`, `abstract_referat`, `toc`, `abbreviations`, `introduction`, `main_chapter`, `sub_section`, `conclusion`, `bibliography`, `appendix`, `unknown` | Роли заголовков, которые обязаны начинаться с новой страницы | `["introduction", "main_chapter", "conclusion"]` |
| `forbid_new_page_roles` | (`title_page` / `task_sheet` / `abstract_referat` / `toc` / `abbreviations` / `introduction` / `main_chapter` / `sub_section` / `conclusion` / `bibliography` / `appendix` / `unknown`)[] | нет | `[]` | список, элементы — одно из: `title_page`, `task_sheet`, `abstract_referat`, `toc`, `abbreviations`, `introduction`, `main_chapter`, `sub_section`, `conclusion`, `bibliography`, `appendix`, `unknown` | Роли заголовков, которые не должны начинать новую страницу: параграф продолжает текст на той же странице. Пересечение с roles недопустимо | `[]` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: heading_page_break -->
```json
{
  "rule_id": "heading_page_break",
  "code": "heading_page_break",
  "strictness": "warning",
  "message": "Структурный раздел начинается с новой страницы",
  "quote": "Глава: с новой страницы (!), параграфы продолжать на той же странице",
  "target": {
    "scope": "headings"
  },
  "params": {
    "roles": [
      "main_chapter",
      "introduction",
      "conclusion",
      "bibliography",
      "appendix"
    ],
    "forbid_new_page_roles": []
  }
}
```

#### `indent` — Отступы абзаца

Красная строка, отступы слева и справа, висячий отступ отрицательным значением.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `first_line_mm` | number или `null` | нет | `null` | число или `null` (мм) | Красная строка; отрицательное значение означает висячий отступ | `1.25` |
| `left_mm` | number или `null` | нет | `null` | число или `null` (мм) | Отступ слева | `null` |
| `right_mm` | number или `null` | нет | `null` | число или `null` (мм) | Отступ справа | `null` |
| `tolerance_mm` | number | нет | `0.5` | число, не меньше 0.0 (мм) | Допуск в миллиметрах | `0.5` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: indent -->
```json
{
  "rule_id": "indent_first_line",
  "code": "indent",
  "message": "Абзац основного текста начинается с отступа 12,5 мм",
  "quote": "Шрифт: 14pt Times New Roman, 1,5 интервала, по ширине, отступ 1,25 (красная строка)",
  "target": {
    "scope": "body_text"
  },
  "params": {
    "first_line_mm": 12.5,
    "tolerance_mm": 0.5
  }
}
```

#### `inline_reference_format` — Оформление ссылки в тексте

Форма ссылки в тексте: квадратные скобки, «см.», «цит. по», номер страницы, сокращение.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `ref_type` | `bibliography` / `figure` / `table` / `appendix` / `section` / `any` | нет | `any` | одно из: `bibliography`, `figure`, `table`, `appendix`, `section`, `any` | Вид ссылки, к которому применяется правило | `"bibliography"` |
| `style` | `square_brackets` / `parentheses_see` / `ibid` / `quoted_from` / `author_then_number_and_page` / `any` | нет | `any` | одно из: `square_brackets`, `parentheses_see`, `ibid`, `quoted_from`, `author_then_number_and_page`, `any` | Требуемая форма записи ссылки | `"square_brackets"` |
| `require_page` | boolean | нет | `false` | `true` или `false` | Требовать номер страницы в ссылке на источник: «[3, с. 15]» | `false` |
| `require_abbrev` | string или `null` | нет | `null` | строка или `null` | Требуемое сокращение перед номером объекта, например 'рис.' или 'прил.'; сравнение без учета регистра | `null` |
| `require_number` | boolean | нет | `false` | `true` или `false` | Требовать номер объекта или источника в ссылке | `false` |
| `patterns` | string[] или `null` | нет | `null` | список строк или `null` | Регулярные выражения допустимой записи ссылки, если форма не покрывается style: например, '[Цит. по кн.: 6. С. 240]'. Проверяется вхождение хотя бы одного выражения в текст ссылки | `null` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: inline_reference_format -->
```json
{
  "rule_id": "inline_reference_format_body",
  "code": "inline_reference_format",
  "message": "Ссылка на источник оформлена в квадратных скобках с номером и страницей",
  "quote": "Ссылки на цитаты и заимствованные данные в тексте: [3, с. 15] (номер в списке использованных источников и номер страницы, с которой взята информация).",
  "target": {
    "scope": "body_text"
  },
  "params": {
    "ref_type": "bibliography",
    "style": "square_brackets",
    "require_page": true,
    "require_number": true
  }
}
```

#### `keep_next` — Неотрывность заголовка от текста

Заголовок не отрывается от следующего абзаца (keepnext).

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `enabled` | boolean | нет | `true` | `true` или `false` | Ожидаемое значение keepNext: true — заголовок не отрывается от текста | `true` |
| `roles` | (`title_page` / `task_sheet` / `abstract_referat` / `toc` / `abbreviations` / `introduction` / `main_chapter` / `sub_section` / `conclusion` / `bibliography` / `appendix` / `unknown`)[] | нет | `["main_chapter", "sub_section"]` | список, элементы — одно из: `title_page`, `task_sheet`, `abstract_referat`, `toc`, `abbreviations`, `introduction`, `main_chapter`, `sub_section`, `conclusion`, `bibliography`, `appendix`, `unknown` | Роли заголовков, к которым применяется требование | `["main_chapter", "sub_section"]` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: keep_next -->
```json
{
  "rule_id": "heading_keep_next",
  "code": "keep_next",
  "strictness": "warning",
  "message": "Заголовок не отрывается от следующего за ним текста",
  "quote": "Не оставлять заголовок параграфа внизу листа, если за ним не следует текст.",
  "target": {
    "scope": "headings"
  },
  "params": {
    "enabled": true,
    "roles": [
      "main_chapter",
      "sub_section"
    ]
  }
}
```

#### `line_spacing` — Межстрочный интервал

Множитель межстрочного интервала абзаца.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `ratio` | number | нет | `1.5` | число, больше 0.0, не больше 10.0 (множитель) | Множитель межстрочного интервала: 1.5 — полуторный | `1.5` |
| `tolerance` | number | нет | `0.25` | число, не меньше 0.0, не больше 5.0 (доля множителя) | Допуск по множителю интервала | `0.25` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: line_spacing -->
```json
{
  "rule_id": "line_spacing_body",
  "code": "line_spacing",
  "message": "Основной текст печатается с полуторным межстрочным интервалом",
  "quote": "Шрифт: 14pt Times New Roman, 1,5 интервала, по ширине, отступ 1,25 (красная строка)",
  "target": {
    "scope": "body_text"
  },
  "params": {
    "ratio": 1.5,
    "tolerance": 0.25
  }
}
```

#### `list_format` — Маркеры перечислений

Допустимые маркеры перечислений по уровням.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `level_markers` | ListLevelMarkerSpec[] | да | — | список объектов ListLevelMarkerSpec (см. §2.4), длина не меньше 1 | Допустимые маркеры по уровням перечисления | `[{"level": 0, "allowed": ["hyphen"]}]` |
| `forbid_other_markers` | boolean | нет | `true` | `true` или `false` | Запрещать маркеры, не описанные для уровня: без этого уровня с описанием проверяются только на вхождение | `true` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: list_format -->
```json
{
  "rule_id": "list_format_default",
  "code": "list_format",
  "message": "Перечисления оформлены дефисом или тире, вложенные уровни — цифрой или буквой со скобкой",
  "quote": "Перечисления: дефис (другие маркеры не допустимы), при необходимости – русские буквы со скобкой: б) и) /кроме ё,з,о,ч,ы,ъ,ь,й/; для дальнейшей детализации – арабские цифры: 2)",
  "target": {
    "scope": "body_paragraphs"
  },
  "params": {
    "level_markers": [
      {
        "level": 0,
        "allowed": [
          "hyphen",
          "dash"
        ]
      },
      {
        "level": 1,
        "allowed": [
          "arabic_parenthesis",
          "russian_letter_parenthesis"
        ]
      }
    ],
    "forbid_other_markers": true
  }
}
```

#### `margin` — Поля страницы

Поля секции: левое, правое, верхнее, нижнее и корешковый отступ.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `left_mm` | number или `null` | нет | `null` | число или `null` (мм) | Левое поле страницы | `30` |
| `right_mm` | number или `null` | нет | `null` | число или `null` (мм) | Правое поле страницы | `15` |
| `top_mm` | number или `null` | нет | `null` | число или `null` (мм) | Верхнее поле страницы | `20` |
| `bottom_mm` | number или `null` | нет | `null` | число или `null` (мм) | Нижнее поле страницы | `20` |
| `gutter_mm` | number или `null` | нет | `null` | число или `null` (мм) | Дополнительный корешковый отступ | `null` |
| `tolerance_mm` | number | нет | `1.0` | число, не меньше 0.0 (мм) | Допуск в миллиметрах | `1.0` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: margin -->
```json
{
  "rule_id": "page_margins",
  "code": "margin",
  "message": "Поля страницы: левое 30, правое 15, верхнее и нижнее 20 мм",
  "quote": "Поля (см): левое – 3 , правое – 2, верхнее и нижнее – 2,5.",
  "target": {
    "scope": "sections"
  },
  "params": {
    "left_mm": 30.0,
    "right_mm": 15.0,
    "top_mm": 20.0,
    "bottom_mm": 20.0,
    "tolerance_mm": 1.0
  }
}
```

#### `microtypography` — Микротипографика

Кавычки, тире, лишние пробелы, пробел перед знаком препинания, неразрывный пробел.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `checks` | (`wrong_quotes` / `wrong_dashes` / `multiple_spaces` / `space_before_punctuation` / `missing_nbsp`)[] | нет | `["wrong_quotes", "wrong_dashes", "multiple_spaces", "space_before_punctuation"]` | список, элементы — одно из: `wrong_quotes`, `wrong_dashes`, `multiple_spaces`, `space_before_punctuation`, `missing_nbsp` | Виды микротипографических ошибок, которые проверяет правило | `["wrong_quotes", "wrong_dashes", "multiple_spaces"]` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: microtypography -->
```json
{
  "rule_id": "microtypography_prose",
  "code": "microtypography",
  "strictness": "warning",
  "message": "В тексте используются кавычки-ёлочки, тире и одиночные пробелы",
  "quote": "При дословном цитировании мысль автора заключается в кавычки и приводится в той грамматической форме, в которой дана в первоисточнике.",
  "target": {
    "scope": "body_paragraphs"
  },
  "params": {
    "checks": [
      "wrong_quotes",
      "wrong_dashes",
      "multiple_spaces",
      "space_before_punctuation"
    ]
  }
}
```

#### `page_format` — Формат листа

Размер листа и ориентация секции.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `width_mm` | number | нет | `210.0` | число, больше 0.0, не больше 2000.0 (мм) | Ширина листа, мм | `210` |
| `height_mm` | number | нет | `297.0` | число, больше 0.0, не больше 2000.0 (мм) | Высота листа, мм | `297.0` |
| `tolerance_mm` | number | нет | `1.0` | число, не меньше 0.0 (мм) | Допуск к ширине и высоте листа, мм | `1.0` |
| `orientation` | `portrait` / `landscape` / `any` | нет | `portrait` | одно из: `portrait`, `landscape`, `any` | Ориентация листа: книжная, альбомная или любая | `portrait` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: page_format -->
```json
{
  "rule_id": "page_format",
  "code": "page_format",
  "message": "Страница имеет формат A4 (210 × 297 мм), книжная ориентация",
  "quote": "Иллюстрации и таблицы могут располагаться на отдельных листах, такие страницы включаются в общую нумерацию.",
  "target": {
    "scope": "sections"
  },
  "params": {
    "width_mm": 210.0,
    "height_mm": 297.0,
    "tolerance_mm": 1.0,
    "orientation": "portrait"
  }
}
```

#### `page_number_position` — Положение номера страницы

Колонтитул с номером, выравнивание, титульный лист, арабские цифры, сквозной отсчёт.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `zone` | `header` / `footer` | да | — | одно из: `header`, `footer` | Колонтитул с номером: 'header' — верхний, 'footer' — нижний | `"footer"` |
| `alignment` | `left` / `center` / `right` / `both` / `distribute` | нет | `center` | одно из: `left`, `center`, `right`, `both`, `distribute` | Ожидаемое выравнивание абзаца с номером страницы | `"center"` |
| `skip_first_page` | boolean | нет | `false` | `true` или `false` | Не печатать номер на титульном листе, сохраняя отсчет с него. Проверяется по PdfPageAudit.has_rendered_page_number | `false` |
| `require_arabic_digits` | boolean | нет | `false` | `true` или `false` | Номер страницы состоит только из арабских цифр (без римских и букв) | `false` |
| `forbid_trailing_dot` | boolean | нет | `false` | `true` или `false` | После номера страницы нет точки | `false` |
| `require_continuous` | boolean | нет | `false` | `true` или `false` | Сквозная нумерация: напечатанный номер совпадает с физическим номером страницы на всем документе (нужны данные PDF) | `false` |
| `include_appendices` | boolean | нет | `false` | `true` или `false` | Учитывать страницы приложений при проверке сквозной нумерации; без этого приложения из сверки исключаются | `false` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: page_number_position -->
```json
{
  "rule_id": "page_number_position",
  "code": "page_number_position",
  "message": "Номер страницы проставляется в нижнем колонтитуле по центру",
  "quote": "Нумерация страниц, включая приложения, начинается с Титульного листа, которому присваивается № 1, но на странице он не ставится.",
  "target": {
    "scope": "headers_footers"
  },
  "params": {
    "zone": "footer",
    "alignment": "center",
    "skip_first_page": false,
    "require_arabic_digits": false,
    "forbid_trailing_dot": false,
    "require_continuous": false,
    "include_appendices": false
  }
}
```

#### `required_sections` — Состав и порядок разделов

Наличие, порядок и роли разделов, число глав основной части.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `sections` | SectionSpec[] | нет | `[]` | список объектов SectionSpec (см. §2.4) | Разделы в требуемом порядке следования | `[{"label": "Введение", "role": "introduction", "require_number": false}]` |
| `min_main_chapters` | integer или `null` | нет | `null` | целое число, не меньше 1, не больше 20 или `null` | Минимальное число глав основной части (роль main_chapter) | `2` |
| `max_main_chapters` | integer или `null` | нет | `null` | целое число, не меньше 1, не больше 20 или `null` | Максимальное число глав основной части (роль main_chapter) | `null` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: required_sections -->
```json
{
  "rule_id": "required_sections",
  "code": "required_sections",
  "message": "Документ содержит введение, разделы, заключение и список литературы в требуемом порядке",
  "quote": "Структурными элементами ВКР являются: титульный лист, оглавление, введение, основная часть, заключение, список литературы, приложения.",
  "target": {
    "scope": "document_structure",
    "exclude_roles": [
      "title_page",
      "task_sheet"
    ]
  },
  "params": {
    "sections": [
      {
        "label": "введение",
        "role": "introduction",
        "titles": [
          "ВВЕДЕНИЕ"
        ],
        "required": true
      },
      {
        "label": "основные разделы",
        "role": "main_chapter",
        "titles": [],
        "required": true
      },
      {
        "label": "заключение",
        "role": "conclusion",
        "titles": [
          "ЗАКЛЮЧЕНИЕ"
        ],
        "required": true
      },
      {
        "label": "список литературы",
        "role": "bibliography",
        "titles": [],
        "required": true
      },
      {
        "label": "приложения",
        "role": "appendix",
        "titles": [
          "ПРИЛОЖЕНИЕ",
          "ПРИЛОЖЕНИЯ"
        ],
        "required": false
      }
    ]
  }
}
```

#### `table_continuation` — Перенос таблицы

Метка продолжения, повтор шапки, линия на разрыве, отсутствие повторной подписи.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `require_label` | boolean | нет | `true` | `true` или `false` | Требовать метку «Продолжение/Окончание таблицы N» перед продолжением | `true` |
| `label_words` | string[] | нет | `["Продолжение", "Окончание"]` | список строк | Допустимые первые слова метки продолжения таблицы | `["Продолжение", "Окончание"]` |
| `label_alignment` | `left` / `center` / `right` / `both` / `distribute` или `null` | нет | `left` | одно из: `left`, `center`, `right`, `both`, `distribute` или `null` | Требуемое выравнивание абзаца с меткой продолжения | `left` |
| `repeat_header` | boolean | нет | `true` | `true` или `false` | Требовать повтор шапки в таблице-продолжении | `true` |
| `forbid_bottom_border_on_break` | boolean | нет | `true` | `true` или `false` | Требовать отсутствие нижней линии у строки, разорванной переносом таблицы на другую страницу | `true` |
| `forbid_repeated_caption` | boolean | нет | `true` | `true` или `false` | Запрещать повтор названия таблицы в таблице-продолжении | `true` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: table_continuation -->
```json
{
  "rule_id": "table_continuation_default",
  "code": "table_continuation",
  "message": "Продолжение таблицы оформлено с надписью и повторенной шапкой",
  "quote": "При переносе части таблицы на другой лист: нижней линии нет, на след. листе слева: Продолжение /или Окончание/ таблицы …. /номер/",
  "target": {
    "scope": "table_cells"
  },
  "params": {
    "require_label": true,
    "label_words": [
      "Продолжение",
      "Окончание"
    ],
    "label_alignment": "left",
    "repeat_header": true,
    "forbid_bottom_border_on_break": true,
    "forbid_repeated_caption": true
  }
}
```

#### `table_header_format` — Оформление шапки таблицы

Заглавная буква, точка в конце, регистр подзаголовков шапки.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `first_letter` | `capitalize` / `any` | нет | `capitalize` | одно из: `capitalize`, `any` | Требовать заглавную первую букву заголовка графы или строки | `capitalize` |
| `forbid_trailing_dot` | boolean | нет | `true` | `true` или `false` | Запрещать точку в конце заголовка графы или строки | `true` |
| `subheader_case` | `any` / `lowercase` / `lowercase_if_continuation` | нет | `any` | одно из: `any`, `lowercase`, `lowercase_if_continuation` | Регистр подзаголовков-продолжений: 'lowercase' — со строчной буквы, если подзаголовок составляет одно предложение с заголовком; 'lowercase_if_continuation' — то же для строк ниже первой строки шапки | `any` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: table_header_format -->
```json
{
  "rule_id": "table_header_format_default",
  "code": "table_header_format",
  "message": "Шапка таблицы набрана с заглавной буквы без точки в конце",
  "quote": "Заголовки граф и строк табл. (без точки в конце): с Заглавной буквы в ед. числе, подзаголовки – со строчной (маленькой) буквы, если составляют одно предложение с заголовком, или с Заглавной, если имеют самостоятельное значение.",
  "target": {
    "scope": "table_cells"
  },
  "params": {
    "first_letter": "capitalize",
    "forbid_trailing_dot": true,
    "subheader_case": "any"
  }
}
```

#### `table_layout` — Линии и ширина таблицы

Линии со всех сторон, ширина таблицы по текстовому полю.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `require_all_sides_borders` | boolean | нет | `true` | `true` или `false` | Требовать линии со всех сторон таблицы и между ячейками | `true` |
| `require_full_text_width` | boolean | нет | `false` | `true` или `false` | Требовать, чтобы ширина таблицы совпадала с шириной текстового поля | `false` |
| `width_tolerance_mm` | number | нет | `1.0` | число, не меньше 0.0, не больше 50.0 (мм) | Допуск сравнения ширины таблицы с текстовым полем, мм | `1.0` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: table_layout -->
```json
{
  "rule_id": "table_layout_borders",
  "code": "table_layout",
  "message": "Таблица ограничена линиями со всех сторон и вписана в полосу набора",
  "quote": "Таблицу со всех сторон ограничить линиями, точно соответствовать размерам полей",
  "target": {
    "scope": "table_cells"
  },
  "params": {
    "require_all_sides_borders": true,
    "require_full_text_width": true,
    "width_tolerance_mm": 1.0
  }
}
```

#### `title_page_content` — Реквизиты титульного листа

Обязательные реквизиты титульного листа и вид работы.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `required_fields` | (`министерство` / `вуз` / `институт` / `кафедра` / `вид работы` / `тема` / `автор` / `руководитель` / `оценка` / `город` / `год`)[] | да | — | список, элементы — одно из: `министерство`, `вуз`, `институт`, `кафедра`, `вид работы`, `тема`, `автор`, `руководитель`, `оценка`, `город`, `год`, длина не меньше 1 | Обязательные реквизиты титульного листа в терминах методички | `["вуз", "тема", "автор", "город", "год"]` |
| `work_type` | string или `null` | нет | `null` | строка или `null` | Ожидаемый вид работы в тексте титульного листа, например 'ВКР' | `null` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: title_page_content -->
```json
{
  "rule_id": "title_page_content_default",
  "code": "title_page_content",
  "message": "Титульный лист содержит обязательные реквизиты",
  "quote": "Титульный лист курсовой работы должен содержать: … полное наименование министерства РФ, вуза, института, кафедры, где выполнена работа; … вид исследовательской работы (курсовая работа); … название темы курсовой работы; … сведения об авторе (Ф.И.О. студента, курс, номер группы и отделение); … сведения о научном руководителе (Ф.И.О., ученая степень, должность преподавателя, ученое звание). … строка для проставления оценки; … город, год написания.",
  "target": {
    "scope": "document_structure"
  },
  "params": {
    "required_fields": [
      "министерство",
      "вуз",
      "кафедра",
      "вид работы",
      "тема",
      "автор",
      "руководитель",
      "город",
      "год"
    ]
  }
}
```

#### `toc` — Оглавление

Наличие оглавления, автоматическое поле, номера страниц, отточие, сверка названий с заголовками.

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `require_toc` | boolean | нет | `true` | `true` или `false` | Требовать наличие оглавления | `true` |
| `require_automated` | boolean | нет | `false` | `true` или `false` | Требовать автоматическое оглавление (поле TOC или SDT) вместо ручного списка | `false` |
| `require_page_numbers` | boolean | нет | `true` | `true` или `false` | Требовать номер страницы в каждой записи оглавления | `true` |
| `check_page_numbers` | boolean | нет | `false` | `true` или `false` | Сверять номера страниц с фактическими; имеет смысл только при точной пагинации | `false` |
| `check_titles` | boolean | нет | `true` | `true` или `false` | Сверять названия записей с найденными заголовками документа | `true` |
| `require_dot_leader` | boolean | нет | `false` | `true` или `false` | Требовать отточие между названием и номером страницы | `false` |
| `forbid_dot_leader` | boolean | нет | `false` | `true` или `false` | Запрещать отточие между названием и номером страницы. Взаимоисключающе с require_dot_leader | `false` |
| `entry_alignment` | `left` / `center` / `right` / `both` / `distribute` или `null` | нет | `null` | одно из: `left`, `center`, `right`, `both`, `distribute` или `null` | Ожидаемое выравнивание абзаца записи оглавления: по методичке записи начинаются с левого края страницы | `null` |
| `entry_bold` | boolean или `null` | нет | `null` | `true` или `false` или `null` | Требование к полужирному начертанию записи оглавления: false — не полужирно | `null` |
| `capitalize_entry` | boolean | нет | `false` | `true` или `false` | Требовать заглавную первую букву названия в записи оглавления | `false` |
| `check_completeness` | boolean или `null` | нет | `null` | `true` или `false` или `null` | Требовать, чтобы обязательные разделы были перечислены в оглавлении; по умолчанию включается, если задан required_entries | `null` |
| `required_entries` | SectionSpec[] | нет | `[]` | список объектов SectionSpec (см. §2.4) | Разделы, которые обязаны быть в оглавлении: роль и/или допустимые названия. Названия отделяют структурный раздел «Заключение» от главы «Выводы» | `[{"label": "Заключение", "role": "conclusion"}]` |
| `max_depth` | integer или `null` | нет | `null` | целое число, не меньше 1, не больше 9 или `null` | До какой глубины заголовки обязаны быть в оглавлении: при 2 сверяются главы и разделы, подразделы глубже второго уровня пропуском не считаются. Пусто — все уровни. Записи оглавления глубже этой глубины допускаются | `null` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: toc -->
```json
{
  "rule_id": "toc",
  "code": "toc",
  "message": "Оглавление перечисляет главы и разделы и указывает номера страниц",
  "quote": "Названия разделов в оглавлении должны точно совпадать с заголовками в тексте. Запрещается сокращать название глав и параграфов.",
  "target": {
    "scope": "toc"
  },
  "params": {
    "require_toc": true,
    "require_automated": false,
    "require_page_numbers": true,
    "check_page_numbers": false,
    "check_titles": true,
    "require_dot_leader": false,
    "forbid_dot_leader": false,
    "capitalize_entry": false,
    "check_completeness": true,
    "required_entries": [
      {
        "label": "введение",
        "role": "introduction",
        "titles": [
          "ВВЕДЕНИЕ"
        ],
        "required": true
      },
      {
        "label": "раздел",
        "role": "main_chapter",
        "titles": [],
        "required": true
      },
      {
        "label": "подраздел",
        "role": "sub_section",
        "titles": [],
        "required": true
      },
      {
        "label": "заключение",
        "role": "conclusion",
        "titles": [
          "ЗАКЛЮЧЕНИЕ"
        ],
        "required": true
      },
      {
        "label": "список литературы",
        "role": "bibliography",
        "titles": [],
        "required": true
      }
    ],
    "max_depth": 2
  }
}
```

#### `widow_control` — Контроль висячих строк

Запрет висячих строк (widowcontrol).

| Ключ | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `enabled` | boolean | нет | `true` | `true` или `false` | Ожидаемое значение widowControl: true — висячие строки запрещены | `true` |

Пример правила (реальное правило из набора по умолчанию `vkr-rule-engine-test` 4.3.0):

<!-- rule: widow_control -->
```json
{
  "rule_id": "widow_control_body",
  "code": "widow_control",
  "strictness": "warning",
  "message": "В основном тексте включён контроль висячих строк",
  "quote": "Не оставлять заголовок параграфа внизу листа, если за ним не следует текст.",
  "target": {
    "scope": "body_text"
  },
  "params": {
    "enabled": true
  }
}
```

### 2.4. Вложенные модели

Значения с типом-объектом раскрываются таблицами ниже. Лишние ключи во вложенных объектах тоже отвергаются.

#### `FontExpectation`

Ожидаемое оформление фрагмента текста: гарнитура, кегль, начертание. Используется как `params` кода `font` и как `heading_format.font`.

| Поле | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `family` | string или `null` | нет | `null` | строка или `null` | Гарнитура, например 'Times New Roman'; сравнение по нормализованному имени | `"Times New Roman"` |
| `size_pt` | number или `null` | нет | `null` | число, не меньше 1.0, не больше 200.0 или `null` (пункты) | Кегль в пунктах | `14` |
| `min_size_pt` | number или `null` | нет | `null` | число, не меньше 1.0, не больше 200.0 или `null` (пункты) | Нижняя граница кегля в пунктах: кегль не меньше значения. Взаимоисключающе с size_pt — задается либо точный кегль, либо граница | `null` |
| `bold` | boolean или `null` | нет | `null` | `true` или `false` или `null` | Ожидаемое начертание: полужирный | `false` |
| `italic` | boolean или `null` | нет | `null` | `true` или `false` или `null` | Ожидаемое начертание: курсив | `null` |
| `size_tolerance_pt` | number | нет | `0.5` | число, не меньше 0.0 (пункты) | Допуск по кеглю в пунктах (Word хранит half-points) | `0.5` |
| `check_complex_script` | boolean | нет | `false` | `true` или `false` | Учитывать complex script (font_cs): источники шума, по умолчанию выключено | `false` |
| `normalize_family` | boolean | нет | `true` | `true` или `false` | Сравнивать гарнитуру по нормализованному имени: без учета регистра, пробелов, дефисов, subset-префикса 'XXXXXX+' и стилевых маркеров (MT/PS/PSMT/Bold/Italic/Regular/Roman). false — прежнее строгое сравнение | `true` |

#### `SectionSpec`

Описание раздела документа в `required_sections.sections` и `toc.required_entries`.

| Поле | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `label` | string | да | — | строка | Название раздела для отчета, например 'введение' | `"Введение"` |
| `role` | `title_page` / `task_sheet` / `abstract_referat` / `toc` / `abbreviations` / `introduction` / `main_chapter` / `sub_section` / `conclusion` / `bibliography` / `appendix` / `unknown` или `null` | нет | `null` | одно из: `title_page`, `task_sheet`, `abstract_referat`, `toc`, `abbreviations`, `introduction`, `main_chapter`, `sub_section`, `conclusion`, `bibliography`, `appendix`, `unknown` или `null` | Семантическая роль заголовка из document_tree (пусто — роль не важна) | `"introduction"` |
| `titles` | string[] | нет | `[]` | список строк | Допустимые названия заголовка; сравнение без учета регистра, точки и лишних пробелов. Пусто — раздел определяется только по роли | `["Введение", "ВВЕДЕНИЕ"]` |
| `required` | boolean | нет | `true` | `true` или `false` | Раздел обязателен в документе | `true` |
| `require_number` | boolean или `null` | нет | `null` | `true` или `false` или `null` | Требование к номеру раздела в оглавлении: true — запись нумеруется, false — не нумеруется (введение, заключение, список литературы, приложения). Пусто — номер не проверяется | `false` |

#### `ListLevelMarkerSpec`

Допустимые маркеры одного уровня перечисления в `list_format.level_markers`.

| Поле | Тип | Обязательность | По умолчанию | Допустимые значения и единицы | Назначение | Пример |
|---|---|---|---|---|---|---|
| `level` | integer | да | — | целое число, не меньше 0, не больше 8 | Уровень вложенности перечисления | `0` |
| `allowed` | (`hyphen` / `dash` / `bullet` / `arabic_dot` / `arabic_parenthesis` / `russian_letter_dot` / `russian_letter_parenthesis` / `other`)[] | да | — | список, элементы — одно из: `hyphen`, `dash`, `bullet`, `arabic_dot`, `arabic_parenthesis`, `russian_letter_dot`, `russian_letter_parenthesis`, `other`, длина не меньше 1 | Допустимые виды маркеров на этом уровне | `["hyphen"]` |

### 2.5. Общие правила для `params`

* Передаются только ключи из таблицы кода. Лишний ключ — ошибка валидации, а не предупреждение.
* Необязательный ключ, который не нужно проверять, опускается; передавать `null` явно нужно только там, где тип допускает `null` и требуется отличить «не проверять» от значения по умолчанию.
* Числа с допуском (`tolerance_*`) задаются в тех же единицах, что и проверяемая величина.
* Ключи с `null` в типе означают «не проверять»: правило проверяет только заполненные expectations.
* Перечисления пишутся ровно так, как в таблице, включая регистр и подчёркивания.

## 3. Области проверки (`scope`)

Область говорит, какие блоки документа попадают в проверку. Область задаётся в `target.scope`, и её значение ограничено списком допустимых для кода (таблица 2.2). Областей всего 13.

| Область | Подпись в отчёте | Что входит | Коды, которые её допускают |
|---|---|---|---|
| `body_text` | основной текст | абзацы верхнего уровня вне таблиц, колонтитулов и сносок; дополнительно отсекаются заголовки, подписи, элементы списков, пустые абзацы и абзацы из одних формул или рисунков, а также контексты из `exclude_contexts`; флаги `include_*` снимают соответствующий отсев | `alignment`, `font`, `indent`, `inline_reference_format`, `line_spacing`, `list_format`, `microtypography`, `widow_control` |
| `body_paragraphs` | абзацы документа | те же абзацы, что и `body_text`, но без отсева заголовков, подписей, списков и пустых абзацев; `exclude_contexts` и `style_ids` действуют | `font`, `indent`, `inline_reference_format`, `line_spacing`, `list_format`, `microtypography`, `widow_control` |
| `all_paragraphs` | все абзацы | все абзацы документа: тело, ячейки таблиц, колонтитулы секций, сноски; действует только `style_ids` | `font`, `indent`, `inline_reference_format`, `line_spacing`, `list_format`, `microtypography` |
| `table_cells` | ячейки таблиц | абзацы внутри ячеек таблиц | `alignment`, `font`, `indent`, `inline_reference_format`, `line_spacing`, `table_continuation`, `table_header_format`, `table_layout` |
| `headers_footers` | колонтитулы | абзацы колонтитулов и служебных абзацев секций | `font`, `indent`, `inline_reference_format`, `line_spacing`, `page_number_position` |
| `footnotes` | сноски | абзацы сносок | `font`, `indent`, `inline_reference_format`, `line_spacing` |
| `sections` | секции | секции документа: поля и формат листа | `margin`, `page_format` |
| `headings` | заголовки | заголовки из дерева документа с учётом `min_heading_confidence` и `exclude_roles` | `font`, `heading_format`, `heading_hyphenation`, `heading_levels`, `heading_numbering`, `heading_page_break`, `indent`, `keep_next`, `line_spacing` |
| `document_structure` | структуру разделов | структура документа: состав и порядок разделов, приложения, титульный лист | `appendix_order`, `required_sections`, `title_page_content` |
| `toc` | оглавление | записи оглавления | `toc` |
| `bibliography` | список литературы | записи списка литературы | `bibliography_cited`, `bibliography_count`, `bibliography_format`, `bibliography_link`, `bibliography_order`, `bibliography_recency` |
| `captions` | подписи рисунков и таблиц | подписи рисунков, таблиц и приложений | `alignment`, `caption_format`, `caption_numbering`, `caption_placement`, `caption_reference` |
| `document` | документ | документ целиком: объём работы, нумерация вынесенных формул | `document_volume`, `equation_numbering` |

Два пояснения, которые чаще всего нужны при разборе нарушений.

Во-первых, `body_text` — это «основной текст» в смысле методички, а не все абзацы: заголовки, подписи, элементы списков и пустые абзацы из него выпадают, пока правило не попросит их флагами `include_headings`, `include_captions`, `include_list_items`, `include_empty`. `body_paragraphs` — соседняя область без этого отсева: она нужна правилам, которые смотрят на абзац как таковой (микротипографика, списки).

Во-вторых, `exclude_contexts` действует только на `body_text` и `body_paragraphs`: контекст абзаца (титульный лист, листинг, название таблицы) вычисляется для тела документа. Заголовки, таблицы, сноски и оглавление фильтруются своими областями, а не контекстами.

## 4. Поля правила

### 4.1. Верхний уровень

Правило — объект с закрытым набором полей: в JSON у правила ровно семь возможных полей, перечисленных ниже. `code` — дискриминатор: по нему выбирается модель параметров, поэтому состав `params` зависит от кода (§2.3).

| Поле | Тип | Обязательность | По умолчанию | Назначение | Пример |
|---|---|---|---|---|---|
| `rule_id` | string | да | — | Уникальный идентификатор правила внутри набора | — |
| `code` | код из §2.2 | да | — | Вид проверки; одновременно дискриминатор модели параметров (§2.3) | `font` |
| `params` | object | да | — | Параметры проверки: объект по таблице кода (§2.3). Ключи и их типы зависят от кода | {"family": "Times New Roman", "size_pt": 14} |
| `strictness` | `error` / `warning` / `info` | нет | `error` | — | `error` |
| `message` | string | да | — | Формулировка требования для отчета | — |
| `quote` | string или `null` | нет | `null` | Ссылка на пункт методических указаний | `null` |
| `target` | RuleTarget | нет | `{"scope": "body_text"}` и отсев служебных контекстов | Где проверять: область, роли, стили, контексты (§4.2). Объект можно опустить целиком | {"scope": "body_text"} |

`message` — текст нарушения для пользователя: одна фраза, без подстановок и без ссылки на пункт методички (для пункта есть `quote`). `quote` — дословный фрагмент методички, на котором основано правило; он попадает в отчёт рядом с нарушением и позволяет проверить правило, не открывая документ.

### 4.2. `target`

`target` описывает выборку блоков. Все поля необязательны; пустой `target` равносилен `{"scope": "body_text"}` с отсевом служебных контекстов по умолчанию.

| Поле | Тип | Обязательность | По умолчанию | Допустимые значения | Назначение |
|---|---|---|---|---|---|
| `scope` | `body_text` / `body_paragraphs` / `all_paragraphs` / `table_cells` / `headers_footers` / `footnotes` / `sections` / `headings` / `document_structure` / `toc` / `bibliography` / `captions` / `document` | нет | `body_text` | одно из: `body_text`, `body_paragraphs`, `all_paragraphs`, `table_cells`, `headers_footers`, `footnotes`, `sections`, `headings`, `document_structure`, `toc`, `bibliography`, `captions`, `document` | Класс проверяемых объектов: абзацы основного текста, заголовки, секции, оглавление |
| `style_ids` | string[] | нет | `[]` | список строк; styleId из styles.xml документа | Ограничить выборку перечисленными styleId абзацев (пусто — без ограничения) |
| `exclude_roles` | (`title_page` / `task_sheet` / `abstract_referat` / `toc` / `abbreviations` / `introduction` / `main_chapter` / `sub_section` / `conclusion` / `bibliography` / `appendix` / `unknown`)[] | нет | `[]` | список, элементы — одно из: `title_page`, `task_sheet`, `abstract_referat`, `toc`, `abbreviations`, `introduction`, `main_chapter`, `sub_section`, `conclusion`, `bibliography`, `appendix`, `unknown`; только для scope `headings` и `document_structure` | Не проверять заголовки этих семантических ролей вместе с их поддеревом (scope='headings' и 'document_structure'); например приложения и задание |
| `min_heading_confidence` | `explicit_outline_or_style` / `structural_keyword_match` / `heuristic_visual` | нет | `heuristic_visual` | одно из: `explicit_outline_or_style`, `structural_keyword_match`, `heuristic_visual` | Минимальная уверенность распознавания заголовка: 'explicit_outline_or_style' берет только явные заголовки, 'heuristic_visual' — все найденные |
| `include_headings` | boolean | нет | `false` | `true` или `false` | Проверять абзацы-заголовки (по умолчанию исключены) |
| `include_captions` | boolean | нет | `false` | `true` или `false` | Проверять абзацы-подписи рисунков, таблиц и приложений |
| `include_list_items` | boolean | нет | `false` | `true` или `false` | Проверять элементы списков (по умолчанию исключены) |
| `include_empty` | boolean | нет | `false` | `true` или `false` | Проверять пустые абзацы и абзацы только с графикой/формулой |
| `exclude_contexts` | string[] | нет | 7 служебных контекстов (см. §4.4) | список строк; свободный список: неизвестные значения не отвергаются | Не проверять блоки этих контекстов в областях body_text и body_paragraphs: 'title_page' — титульный лист, 'code_listing' — листинг кода, 'structural_title' — название элемента структуры («Содержание», «Приложение»), 'appendix_label' — метка продолжения приложения, 'caption_like' — подпись рисунка/таблицы по текстовому маркеру, 'table_title' — заголовок таблицы, 'text_formula' — формула, набранная текстом. Пусто — прежнее поведение без контекстной фильтрации |

`include_*` действуют только при `scope="body_text"`: они возвращают в выборку то, что этот режим отсеивает по умолчанию. `style_ids` — фильтр по `styleId` из `styles.xml`; пустой список означает «любой стиль». `min_heading_confidence` действует только на `scope="headings"`.

### 4.3. Роли заголовков

Роль — типизированное назначение заголовка, определённое по структуре документа (дерево разделов, стили, визуальные признаки). Роли используются в `target.exclude_roles`, `keep_next.roles`, `heading_page_break.roles` и `heading_numbering.require_number_for_roles`.

| Роль | Что означает |
|---|---|
| `title_page` | титульный лист |
| `task_sheet` | лист задания |
| `abstract_referat` | реферат (аннотация) к работе |
| `toc` | оглавление |
| `abbreviations` | перечень сокращений и обозначений |
| `introduction` | введение |
| `main_chapter` | глава основной части |
| `sub_section` | подраздел внутри главы |
| `conclusion` | заключение |
| `bibliography` | список литературы |
| `appendix` | приложение |
| `unknown` | роль не определена |

`exclude_roles` применим только к областям `headings` и `document_structure`: в остальных областях роли заголовков не определены. Роли из списка «без номера» (введение, заключение, список литературы, приложения, титульный лист, лист задания, перечень сокращений) номер не требуют независимо от `require_number_for_roles`.

### 4.4. Контексты (`exclude_contexts`)

Контекст — признак абзаца, вычисленный классификатором до применения правил. Значения по умолчанию — служебные контексты, которые почти никогда не нужно проверять как основной текст.

| Контекст | Что это | В отсеве по умолчанию |
|---|---|---|
| `title_page` | абзацы титульного листа | да |
| `code_listing` | листинг кода | да |
| `structural_title` | названия структурных элементов (оглавление, реферат и т. п.) | да |
| `appendix_label` | метка приложения («ПРИЛОЖЕНИЕ А») | да |
| `caption_like` | абзацы, похожие на подписи объектов | да |
| `table_title` | название таблицы | да |
| `text_formula` | формула, вынесенная в отдельную строку | да |

Список `exclude_contexts` — свободный: незнакомое значение не отвергается валидатором, но и не отсеивает ничего. Это сделано намеренно: перечень контекстов расширяется без изменения формата. Полагаться на незнакомое значение нельзя — правило просто не отсеет абзацы.

### 4.5. Уверенность в заголовке

`min_heading_confidence` управляет строгостью отбора заголовков: `explicit_outline_or_style` — только заголовки с явным уровнем структуры или стилем, `structural_keyword_match` — плюс совпадение по ключевому слову («ВВЕДЕНИЕ»), `heuristic_visual` — плюс визуальные признаки (полужирный, отдельная строка). По умолчанию `heuristic_visual`: правило видит все заголовки, которые нашёл парсер.

## 5. Формат набора

### 5.1. Поля набора

| Поле | Тип | Обязательность | По умолчанию | Назначение |
|---|---|---|---|---|
| `ruleset_id` | string | да | — | Идентификатор набора, попадает в отчет |
| `ruleset_version` | string | да | — | Версия набора правил |
| `description` | string или `null` | нет | `null` | Назначение набора |
| `extends` | string или `null` | нет | `null` | Идентификатор базового набора, поверх которого применяется профиль |
| `rules` | Rule[] | да | — | Правила набора; минимум одно |

### 5.2. Скелет набора

```json
{
  "ruleset_id": "urgpu-vkr-profile",
  "ruleset_version": "2.0.0",
  "extends": "vkr-rule-engine-test",
  "description": "Требования УрГПУ к оформлению ВКР",
  "rules": [
    {
      "rule_id": "font_body_text",
      "code": "font",
      "params": {"family": "Times New Roman", "size_pt": 14}
    }
  ]
}
```

Ключи `ruleset_id` и `ruleset_version` — необязательные по смыслу, но обязательные по схеме: движок пишет их в отчёт, чтобы потом можно было понять, по какой редакции требований проверялась работа. `description` — свободный текст для человека. Порядок правил в массиве сохраняется и определяет порядок нарушений в отчёте.

### 5.3. Версионирование

`ruleset_version` — семантическая версия набора. Меняется при любой правке правил: мажорная часть — несовместимое изменение набора (удалён код, изменён смысл), минорная — новое правило или параметр, патч — исправление опечатки в тексте. Версия не влияет на исполнение, но попадает в отчёт и в базу.

Версия схемы парсера в набор не входит. Её фиксирует документ как `parser_schema_version` (сейчас `3.0.0`) и отчёт: правило, написанное под одну схему разбора, может опираться на блоки, которых в другой схеме нет. При смене мажорной версии схемы наборы перепроверяются.

### 5.4. Профили и `extends`

Профиль — такой же набор, но с полем `extends`, равным `ruleset_id` базового набора. Файлы профилей лежат в `backend/rule_engine/rules/profiles/`, идентификатор профиля — имя файла без расширения. Слияние идёт по `rule_id`: правило профиля с тем же `rule_id` заменяет базовое целиком (не по отдельным полям), правило с новым `rule_id` добавляется в конец, порядок базовых правил сохраняется. Итоговый набор получает `ruleset_id` и `ruleset_version` профиля, а в отчёт попадает число применённых правил. Профиль может и переопределять существующие правила, и добавлять новые: без первого не выразить требования вуза, без второго — проверки, которых в базовом наборе нет.

Базовый набор `vkr-rule-engine-test` 4.3.0 — 52 правил. `mpgu` — 5 правил (5 переопределений и 0 новых), после слияния 52 `urfu` — 6 правил (6 переопределений и 0 новых), после слияния 52 `urgpu` — 32 правил (24 переопределений и 8 новых), после слияния 60;

`extends` обязательно для профиля: набор без `extends` движок считает самостоятельным и не смешивает с базой. Профиль, который переопределяет больше половины правил, — сигнал, что базовый набор не подходит и нужен отдельный набор.

## 6. Валидация

Валидация выполняется при загрузке набора и при сохранении правила. Она закрытая: неизвестное поле, неизвестный код, лишний параметр и неверный тип — ошибка, а не предупреждение. Ниже перечислены ситуации, которые отвергаются, с типом ошибки pydantic, по которому их можно отличить в логах.

| Сценарий | Что сломано | Тип ошибки pydantic | Что видит автор набора |
|---|---|---|---|
| `unknown_code` | код правила отсутствует в реестре (опечатка, выдуманный код) | `union_tag_invalid` | `does not match any of the expected tags` и перечень допустимых кодов |
| `extra_param` | в `params` передан ключ, которого нет у кода | `extra_forbidden` | `Extra inputs are not permitted` |
| `wrong_type` | строка передана вместо числа | `float_parsing` | `Input should be a valid number` |
| `missing_rule_id` | правило без `rule_id` | `missing` | `Field required` |
| `missing_params` | правило без `params` | `missing` | `Field required` |
| `missing_message` | правило без `message` | `missing` | `Field required` |
| `blank_rule_id` | `rule_id` из одних пробелов | `value_error` | `rule_id не может быть пустым` |
| `empty_message` | `message` из одних пробелов | `value_error` | `message не может быть пустым` |
| `bad_scope_for_code` | область не входит в список допустимых для кода | `value_error` | `scope 'toc' не применим к абзацам` |
| `bad_scope_literal` | область вне перечня из 13 значений | `literal_error` | `Input should be 'body_text', 'body_paragraphs'` и далее перечень всех областей |
| `exclude_roles_bad_scope` | `exclude_roles` при области, отличной от заголовков и структуры | `value_error` | `exclude_roles применим только к scope 'headings' и 'document_structure'` |
| `bad_strictness` | `strictness` вне `error`/`warning`/`info` | `literal_error` | `Input should be 'error', 'warning' or 'info'` |
| `unknown_field` | лишнее поле верхнего уровня правила (например `name`) | `extra_forbidden` | `Extra inputs are not permitted` |
| `unknown_ruleset_field` | лишнее поле набора | `extra_forbidden` | `Extra inputs are not permitted` |
| `target_extra` | лишний ключ в `target` | `extra_forbidden` | `Extra inputs are not permitted` |
| `missing_zone` | `page_number_position` без обязательного `zone` | `missing` | `Field required` |
| `empty_level_markers` | пустой список `level_markers` | `too_short` | `List should have at least 1 item` |
| `empty_rules` | пустой массив `rules` | `too_short` | `List should have at least 1 item` |
| `duplicate_rule_id` | два правила с одинаковым `rule_id` | `value_error` | `rule_id 'r' встречается в наборе более одного раза` |
| `empty_ruleset_id` | пустой `ruleset_id` | `value_error` | `ruleset_id и ruleset_version не могут быть пустыми` |
| `bad_orientation` | `orientation` вне `portrait`/`landscape`/`any` | `literal_error` | `Input should be 'portrait', 'landscape' or 'any'` |
| `params_null` | `params` равно `null` | `model_type` | `Input should be a valid dictionary or instance of FontExpectation` |
| `ruleset_version_number` | `ruleset_version` передан числом | `string_type` | `Input should be a valid string` |

Сверх перечисленного валидатор проверяет инварианты набора: `rule_id` уникален в пределах набора; `margin` допускает только `scope="sections"`; `exclude_roles` — только для `headings` и `document_structure`; непустые `rules`, `level_markers`, `rule_id`, `message`, `ruleset_id`, `ruleset_version`; `extends` профиля совпадает с `ruleset_id` базового набора. Ошибка загрузки файла выглядит как `путь: некорректный JSON (...)` или `ожидался JSON-объект набора правил`.

## 7. Неизвестный код

Реестр кодов закрыт, поэтому требование методички, для которого нет подходящего кода, нельзя выдавать за правило. Поведение двухслойное, и его важно не путать.

**На загрузке набора.** Правило с кодом вне §2.2 отвергается с ошибкой `union_tag_invalid` и перечнем допустимых кодов. Набор целиком не загружается: молча пропустить неизвестное правило нельзя, иначе нормоконтроль тихо перестанет проверять требование, которое автор набора считал проверяемым.

**На приёме от ИИ-команды.** Неизвестный код не повод выбрасывать требование. Требование сохраняется в базе как рекомендация: у правила проставляется `verification_method="llm_recommendation"`, а в наборе для исполнения такого правила нет. В отчёте о проверке такие требования выводятся отдельным блоком `llm_recommendation` — рядом с нарушениями, но без адресов блоков и без влияния на `strictness`: движок их не проверял. Пометка означает «требование найдено в методичке, но машинной проверки для него пока нет», а не «правило выполнено».

Статус реализации на ветке `feat/urgpu-ruleset`: поле `verification_method` в таблице `rule` есть и переносится при копировании набора, а отдельный блок `llm_recommendation` в отчёте движка ещё не выводится — сейчас движок исполняет только правила с известным кодом. Раздел описывает соглашение, к которому приводится приём требований: требование без кода не теряется, а ждёт реализации проверки.

Что делать ИИ-команде при отсутствии кода: выбрать ближайший по смыслу код, если он действительно описывает требование; иначе отдать требование без `code`, с дословной цитатой и предложением нового кода в свободном тексте. Придуманный код в `params`-наборе — худший вариант: он либо не загрузится, либо (если код существует, но смысл другой) даст ложные нарушения.

## 8. Что не извлекать как правила

Часть методички принципиально не переводится в проверки по документу. Такие требования остаются текстом для человека; попытка выразить их кодом даёт ложные нарушения и подрывает доверие к отчёту.

* **Смысловые требования.** Актуальность темы, цель и задачи, научная новизна, практическая значимость, стиль изложения, логичность выводов. Движок не понимает смысл текста; проверка «есть ли задачи» выродится в поиск слова «задачи».
* **Оригинальность и заимствования.** Процент оригинальности определяется внешней системой, а не разбором docx.
* **Требования к вёрстке, которых нет в документе.** Переплёт, бумага, количество экземпляров, подписи на бумаге, сроки сдачи.
* **Условные конструкции.** «Если таблица больше страницы, её переносят с повторением шапки», «если рисунок не помещается, его уменьшают» — условие зависит от вёрстки, а не от разметки. Проверяется следствие, а не условие: правило `table_continuation` смотрит факт переноса.
* **Альтернативы без выбора.** «Шрифт 12 или 14 пт», «интервал 1,5 или двойной» — если методичка допускает оба варианта, правило фиксирует один (тот, что выбрал вуз) либо не создаётся вовсе. Перечисление вариантов в `params` поддерживается только там, где оно есть в модели (например, `caption_numbering.schemes`).
* **Состав приложений.** Какие именно приложения должны быть в работе, определяется темой, а не методичкой; проверяется порядок и оформление приложений, а не их наличие.
* **Требования к содержанию структурных элементов.** Что должно быть во введении, какие разделы обязательны по существу — проверяется наличие и оформление раздела, но не его наполнение.
* **Нормы, зависящие от внешних данных.** Год источников относительно текущего года проверяется параметром `reference_year`; если методичка требует «не старше пяти лет от года защиты», это передаётся параметром, а не вычисляется движком.

## 9. Примеры

### 9.1. Одно правило

Минимальное правило: проверить шрифт основного текста. Всё, чего нет в `params`, берётся по умолчанию, `target` можно не указывать — область `body_text` подставится сама.

<!-- example: single_rule -->
```json
{
  "rule_id": "font_body_text",
  "code": "font",
  "strictness": "error",
  "message": "Шрифт основного текста не соответствует требованиям",
  "quote": "Текст печатается шрифтом Times New Roman, 14 пт",
  "target": {"scope": "body_text"},
  "params": {"family": "Times New Roman", "size_pt": 14, "bold": false}
}
```

### 9.2. Набор из трёх правил

Набор проверяет три требования: шрифт, интервал и начало главы с нового листа. Второе правило переопределяет `strictness` на `warning`, потому что вуз считает отклонение интервала замечанием.

<!-- example: ruleset_three -->
```json
{
  "ruleset_id": "vkr-minimal",
  "ruleset_version": "1.0.0",
  "description": "Минимальный набор: шрифт, интервал, разрыв перед главой",
  "rules": [
    {
      "rule_id": "font_body_text",
      "code": "font",
      "message": "Шрифт основного текста не соответствует требованиям",
      "quote": "Текст печатается шрифтом Times New Roman, 14 пт",
      "params": {"family": "Times New Roman", "size_pt": 14}
    },
    {
      "rule_id": "line_spacing_body",
      "code": "line_spacing",
      "strictness": "warning",
      "message": "Межстрочный интервал в основном тексте не полуторный",
      "quote": "Межстрочный интервал — 1,5",
      "params": {"ratio": 1.5, "tolerance": 0.05}
    },
    {
      "rule_id": "heading_page_break_chapters",
      "code": "heading_page_break",
      "message": "Глава начинается не с новой страницы",
      "quote": "Каждый раздел начинается с новой страницы",
      "target": {"scope": "headings"},
      "params": {"roles": ["main_chapter"]}
    }
  ]
}
```

### 9.3. Профиль организации

Профиль УрГПУ поверх базового набора: те же `rule_id`, что в базе, но с другими `params`, плюс правила, которых в базе нет. Слияние выполняется по `rule_id` (§5.4), поэтому базовый набор не копируется.

<!-- example: profile -->
```json
{
  "ruleset_id": "urgpu-profile-example",
  "ruleset_version": "1.0.0",
  "extends": "vkr-rule-engine-test",
  "description": "Пример профиля: требования кафедры поверх базового набора",
  "rules": [
    {
      "rule_id": "font_body_text",
      "code": "font",
      "message": "Шрифт основного текста не Times New Roman 14 пт",
      "quote": "Основной текст набирается шрифтом Times New Roman, 14 пт",
      "params": {"family": "Times New Roman", "size_pt": 14, "bold": false}
    },
    {
      "rule_id": "margin_urgpu",
      "code": "margin",
      "message": "Поля страницы не соответствуют требованиям кафедры",
      "quote": "Поля: левое 30 мм, правое 15 мм, верхнее и нижнее 20 мм",
      "target": {"scope": "sections"},
      "params": {"left_mm": 30, "right_mm": 15, "top_mm": 20, "bottom_mm": 20}
    },
    {
      "rule_id": "toc_entries_urgpu",
      "code": "toc",
      "message": "В оглавлении нет обязательных разделов",
      "quote": "Оглавление включает введение, главы, заключение, список литературы",
      "target": {"scope": "toc"},
      "params": {
        "required_entries": [
          {"label": "Введение", "role": "introduction"},
          {"label": "Заключение", "role": "conclusion"},
          {"label": "Список литературы", "role": "bibliography"}
        ]
      }
    }
  ]
}
```

## 10. Чек-лист приёмки от ИИ-команды

Набор принимается, если на все вопросы ниже ответ «да»; проверка занимает минуты и не требует запуска движка.

1. Каждое правило содержит `rule_id`, `code`, `message` и `params`; `code` есть в таблице 2.2, а `rule_id` уникален в наборе и осмыслен: по нему понятно, что и где проверяется.
2. В `params` нет ключей вне таблицы кода, типы совпадают, перечисления написаны буквально.
3. `scope` входит в список допустимых для кода; `exclude_roles` указан только для `headings` и `document_structure`.
4. `quote` — дословный фрагмент методички, а не пересказ, и он действительно обосновывает `params`.
5. `message` сформулирован как нарушение («не соответствует», «начинается не с новой страницы»), а не как требование, и не дублирует `quote`.
6. `strictness` выставлен осознанно: `error` только там, где вуз действительно блокирует работу.
7. Набор целиком загружается валидатором без ошибок, а профиль содержит `extends`, равный `ruleset_id` базового набора.
8. Требования, для которых не нашлось кода, попадают в отчёт блоком `llm_recommendation`, а не подменяются чужим кодом (§7, §8).

## 11. Соответствие схеме БД

Правило в базе повторяет структуру JSON, но не полностью: часть полей нужна только для исполнения и не хранится, часть хранится для админки. Таблица показывает, куда попадает каждое поле контракта.

| Поле контракта | Таблица и колонка | Тип в БД | Примечание |
|---|---|---|---|
| `code` | `rule_type.code` | varchar(50), unique | Справочник видов проверок; соответствует §2.2 |
| `params` | `rule.params` | json | Хранится как есть, без разбора по ключам |
| `target.scope` | `rule.scope` | varchar(50) | Область проверки; остальные поля `target` — в `rule.target` |
| `target` (целиком) | `rule.target` | json (предложение ERD) | В текущей модели колонки нет: `scope` хранится отдельно, остальные поля не сохраняются |
| `quote` | `rule.quote` | text | Дословный фрагмент методички |
| `quote` (пункт методички) | `rule.quote_location` | varchar(255) | Номер пункта; в обменном формате отдельного поля нет — пункт указывается вместе с цитатой |
| `strictness` | `rule.strictness` | varchar(50) | Значение по умолчанию — `error` |
| `message` | — | — | В БД не хранится: текст нарушения формируется правилом при проверке |
| `rule_id` | `rule.id` / `rule.name` | integer / varchar(255) | `rule_id` набора уникален в пределах набора; в БД правило адресуется суррогатным ключом |
| `ruleset_id`, `ruleset_version` | `ruleset.id`, `ruleset.version` | integer / varchar(50) | Набор — отдельная сущность, правила ссылаются на неё |

Два следствия для ИИ-команды. Первое: `message` не нужно согласовывать с базой — оно живёт в JSON и в отчёте. Второе: `params` хранится как JSON без нормализации, поэтому ошибка в ключе не будет поймана базой — только валидатором движка при загрузке набора.
