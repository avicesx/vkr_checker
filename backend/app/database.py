from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


def create_database_engine(database_url: str) -> Engine:
    """Создаёт engine только для PostgreSQL."""
    if database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://", "postgresql+psycopg://", 1
        )
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    elif not database_url.startswith("postgresql+psycopg://"):
        raise ValueError("Поддерживается только PostgreSQL с драйвером psycopg")
    return create_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker:
    """Создаёт фабрику SQLAlchemy-сессий для приложения."""
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)
