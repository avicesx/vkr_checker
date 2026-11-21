# Миграции схемы БД (Alembic)

Каталог содержит окружение Alembic и ревизии схемы PostgreSQL для `vkr_checker`.
Первая ревизия `95f6b91fcc64_baseline_schema.py` фиксирует схему, которую ранее
создавал `Base.metadata.create_all`, поэтому модели и БД больше не расходятся
молча.

## Зачем миграции, если есть create_all

`create_all` умеет только создавать отсутствующие таблицы. Он не меняет уже
существующие: новую колонку, индекс или ограничение он не добавит, удалённую —
не уберёт, а переименование для него выглядит как «создать новую таблицу».
На живой базе это приводит к тому, что код ожидает одну схему, а в БД лежит
другая. Alembic применяет изменения пошагово, хранит в таблице
`alembic_version` номер текущей ревизии и позволяет откатить изменение
(`downgrade`). Поэтому все изменения схемы оформляются ревизиями, а
`create_all` остаётся только для тестов и локальной инициализации.

## Как применить миграции

URL базы берётся из переменной окружения `DATABASE_URL` (в `alembic.ini`
значение `sqlalchemy.url` пустое, реальные пароли в репозиторий не попадают).
Поддерживается драйвер `psycopg` (SQLAlchemy 2):

```bash
cd backend
export DATABASE_URL='postgresql+psycopg://vkr_checker:local-dev-password@localhost:5433/vkr_checker_test'
python -m alembic upgrade head     # применить все ревизии
python -m alembic current          # какая ревизия применена сейчас
python -m alembic history          # список ревизий
python -m alembic downgrade -1     # откатить последнюю ревизию
```

Если `DATABASE_URL` не задан, Alembic возьмёт `sqlalchemy.url` из
`alembic.ini`; при пустом значении будет понятная ошибка.

Приложение применяет миграции само: `api.storage.CheckStore.initialize()` и
`app.create_tables.init_db()` вызывают `app.migrations.apply_migrations()`,
которая передаёт URL через `Config`, и этот URL важнее переменной окружения.
База, созданная прежним `create_all` (таблицы есть, `alembic_version` нет), при
первом запуске один раз помечается baseline-ревизией (`stamp head`): её схема
совпадает с первой миграцией, поэтому повторно создавать таблицы не нужно.
Перед штампом сверяется полнота базы: недостающие таблицы досоздаются тем же
`create_all`, а если в существующей таблице не хватает колонки, запуск падает
с `RuntimeError` и списком расхождений — такую базу нужно править ручной
миграцией, иначе неполная схема считалась бы актуальной.

## Как добавить новую ревизию

1. Измените модели в `app/models/`.
2. Убедитесь, что база обновлена до `head` (`python -m alembic upgrade head`).
3. Сгенерируйте ревизию:

   ```bash
   cd backend
   export DATABASE_URL='...'
   python -m alembic revision --autogenerate -m "краткое описание изменения"
   ```

4. Откройте созданный файл в `alembic/versions/` и вычитайте его вручную:
   autogenerate не видит переименования (получится `drop_column` +
   `add_column`), не переносит данные и иногда путает типы. При необходимости
   поправьте `upgrade()` и `downgrade()`.
5. Проверьте на чистой базе: `python -m alembic upgrade head`, затем
   `python -m alembic check` — команда должна ответить
   `No new upgrade operations detected.`

Для одноразовой генерации baseline удобно завести отдельную пустую базу,
например `vkr_checker_migrations`: autogenerate по ней создаёт все таблицы
с нуля, не затрагивая рабочую БД. После проверки временную базу удаляют.

## Проверка согласованности

```bash
cd backend
export DATABASE_URL='postgresql+psycopg://...'
python -m alembic check
```

`alembic check` сравнивает модели с фактической схемой и падает, если в БД
есть неприменённые изменения. Эту команду стоит запускать в CI вместе с
тестами, чтобы модель и миграции не разъезжались.

## Структура

- `alembic.ini` — конфигурация (путь к ревизиям, логирование, пустой
  `sqlalchemy.url`).
- `alembic/env.py` — подключает `Base.metadata`, импортирует все модели и
  берёт URL из `DATABASE_URL`.
- `alembic/script.py.mako` — шаблон новых ревизий.
- `alembic/versions/` — файлы ревизий.
