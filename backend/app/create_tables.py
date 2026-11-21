import os

from .database import create_database_engine
from .migrations import apply_migrations


def init_db():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Для создания таблиц задайте переменную DATABASE_URL")
    engine = create_database_engine(database_url)
    print("Применяем миграции Alembic...")
    try:
        apply_migrations(engine)
        print("Готово! Схема обновлена до последней ревизии.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    init_db()
