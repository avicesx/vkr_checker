from .database import engine
from .models.base import Base
from .models import *  # Импортируем все модели

def init_db():
    if engine is None:
        raise RuntimeError("Для создания таблиц задайте переменную DATABASE_URL")
    print("Создаём таблицы в базе данных...")
    Base.metadata.create_all(bind=engine)
    print("Готово! Таблицы успешно созданы.")

if __name__ == "__main__":
    init_db()