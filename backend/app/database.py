import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def create_database_engine(database_url: str):
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    elif not database_url.startswith("postgresql+psycopg://"):
        raise ValueError("Поддерживается только PostgreSQL с драйвером psycopg")
    return create_engine(database_url, pool_pre_ping=True)


SQLALCHEMY_DATABASE_URL = os.environ.get("DATABASE_URL")
engine = (
    create_database_engine(SQLALCHEMY_DATABASE_URL) if SQLALCHEMY_DATABASE_URL else None
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    if engine is None:
        raise RuntimeError(
            "Для подключения к базе данных задайте переменную DATABASE_URL"
        )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
