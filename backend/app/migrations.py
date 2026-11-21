"""Единая точка применения миграций схемы PostgreSQL.

Схема создаётся и обновляется только ревизиями Alembic из backend/alembic/versions.
`Base.metadata.create_all` в обычном пути не используется: он умеет лишь
добавлять отсутствующие таблицы, не меняет существующие и не хранит номер
версии схемы. Он остаётся только для досоздания недостающих таблиц в неполной
легаси-базе перед штампом baseline.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from .database import create_database_engine
from .models import *  # noqa: F401,F403  импорт регистрирует все модели в metadata
from .models.base import Base

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def build_alembic_config(database_url: str) -> Config:
    """Собирает конфигурацию Alembic с явно заданным URL базы."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    # configparser подставляет %(...)s, поэтому знаки % в пароле экранируются.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _database_url(bind: Engine | str) -> str:
    if isinstance(bind, Engine):
        # str(engine.url) маскирует пароль как ***, поэтому рендерим его явно.
        return bind.url.render_as_string(hide_password=False)
    return bind


def _is_legacy_create_all_schema(engine: Engine) -> bool:
    """Проверяет, что перед нами база старого create_all без версии схемы."""
    with engine.connect() as connection:
        inspector = inspect(connection)
        if inspector.has_table("alembic_version"):
            return False
        existing_tables = set(inspector.get_table_names())
    return bool(existing_tables & set(Base.metadata.tables))


def _schema_gaps(engine: Engine) -> tuple[set[str], dict[str, set[str]]]:
    """Возвращает недостающие таблицы и колонки относительно моделей."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    expected_tables = Base.metadata.tables
    missing_tables = set(expected_tables) - existing_tables
    missing_columns: dict[str, set[str]] = {}
    for table_name in set(expected_tables) & existing_tables:
        actual_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        expected_columns = {
            column.name for column in expected_tables[table_name].columns
        }
        gap = expected_columns - actual_columns
        if gap:
            missing_columns[table_name] = gap
    return missing_tables, missing_columns


def _describe_schema_gaps(
    missing_tables: set[str], missing_columns: dict[str, set[str]]
) -> str:
    """Перечисляет расхождения схемы для текста ошибки."""
    parts: list[str] = []
    if missing_tables:
        parts.append("таблицы: " + ", ".join(sorted(missing_tables)))
    if missing_columns:
        columns = ", ".join(
            f"{table_name}.{column_name}"
            for table_name, column_names in sorted(missing_columns.items())
            for column_name in sorted(column_names)
        )
        parts.append("колонки: " + columns)
    return "; ".join(parts)


def _adopt_legacy_schema(engine: Engine, config: Config) -> None:
    """Досоздаёт неполную базу create_all и помечает её baseline-ревизией.

    Штамп ставится только при полном совпадении набора таблиц и колонок с
    моделями: иначе база осталась бы неполной, но уже считалась актуальной.
    Отсутствующие таблицы досоздаются прежним ``create_all`` — это ровно
    прежнее поведение для баз, созданных старой версией приложения; колонки
    в существующих таблицах ``create_all`` не добавляет, поэтому такое
    расхождение требует ручной миграции.
    """
    missing_tables, missing_columns = _schema_gaps(engine)
    if missing_tables:
        logger.info(
            "Легаси-база неполная: досоздаём отсутствующие таблицы (%s)",
            ", ".join(sorted(missing_tables)),
        )
        Base.metadata.create_all(bind=engine)
        missing_tables, missing_columns = _schema_gaps(engine)
    if missing_tables or missing_columns:
        raise RuntimeError(
            "Схему легаси-базы нельзя привести к baseline автоматически: "
            f"не хватает {_describe_schema_gaps(missing_tables, missing_columns)}. "
            "Версия схемы не записана, чтобы неполная база не считалась "
            "актуальной. Выполните ручную миграцию (добавьте недостающие "
            "колонки или пересоздайте таблицы) и повторите запуск."
        )
    logger.info("Схема создана прежним create_all: помечаем её baseline-ревизией")
    command.stamp(config, "head")


def apply_migrations(bind: Engine | str) -> None:
    """Создаёт или обновляет схему БД до последней ревизии Alembic.

    ``bind`` — engine приложения или строка подключения. База, созданная
    прежним ``create_all`` (таблицы есть, ``alembic_version`` нет), один раз
    помечается baseline-ревизией: её схема совпадает с первой миграцией, и
    повторно создавать таблицы нельзя. Если легаси-база неполная, недостающие
    таблицы досоздаются, а при расхождении колонок поднимается ``RuntimeError``.
    """
    database_url = _database_url(bind)
    engine = bind if isinstance(bind, Engine) else create_database_engine(database_url)
    config = build_alembic_config(database_url)
    try:
        if _is_legacy_create_all_schema(engine):
            _adopt_legacy_schema(engine, config)
        command.upgrade(config, "head")
    finally:
        if not isinstance(bind, Engine):
            engine.dispose()
