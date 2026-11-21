"""Настройка окружения Alembic для схемы vkr_checker.

URL базы берётся из переменной окружения DATABASE_URL, а если она не задана —
из sqlalchemy.url в alembic.ini. Реальные пароли и хосты в репозитории не
хранятся: значение по умолчанию в alembic.ini пустое.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

# Каталог backend/ добавляется в sys.path, чтобы `app` импортировался при
# запуске alembic из любого каталога, а не только из backend/.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import create_database_engine  # noqa: E402
from app.models import *  # noqa: F401,F403,E402  импорт регистрирует все модели
from app.models.base import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    """Возвращает URL базы из окружения или из alembic.ini."""
    url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "Не задан URL базы: экспортируйте DATABASE_URL "
            "или укажите sqlalchemy.url в alembic.ini"
        )
    return url


def run_migrations_offline() -> None:
    """Генерирует миграции без подключения к базе (alembic upgrade --sql)."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Применяет миграции к реальной базе."""
    engine = create_database_engine(get_database_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
