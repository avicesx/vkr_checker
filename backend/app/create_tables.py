import os

from .database import create_database_engine
from .models.base import Base
from .models import *  # Импортируем все модели

def init_db():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Для создания таблиц задайте переменную DATABASE_URL")
    engine = create_database_engine(database_url)
    print("Создаем таблицы в базе данных...")
    try:
        Base.metadata.create_all(bind=engine)
        print("Готово! Таблицы успешно созданы.")
    finally:
        engine.dispose()

if __name__ == "__main__":
    init_db()