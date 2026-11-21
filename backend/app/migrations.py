"""Единая точка применения миграций схемы PostgreSQL.

Схема создаётся и обновляется только ревизиями Alembic из backend/alembic/versions.
`Base.metadata.create_all` для этого больше не используется: он умеет лишь
добавлять отсутствующие таблицы, не меняет существующие и не хранит номер
версии схемы.
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


def apply_migrations(bind: Engine | str) -> None:
    """Создаёт или обновляет схему БД до последней ревизии Alembic.

    ``bind`` — engine приложения или строка подключения. База, созданная
    прежним ``create_all`` (таблицы есть, ``alembic_version`` нет), один раз
    помечается baseline-ревизией: её схема совпадает с первой миграцией, и
    повторно создавать таблицы нельзя.
    """
    database_url = _database_url(bind)
    engine = bind if isinstance(bind, Engine) else create_database_engine(database_url)
    config = build_alembic_config(database_url)
    try:
        if _is_legacy_create_all_schema(engine):
            logger.info(
                "Схема создана прежним create_all: помечаем её baseline-ревизией"
            )
            command.stamp(config, "head")
        command.upgrade(config, "head")
    finally:
        if not isinstance(bind, Engine):
            engine.dispose()
