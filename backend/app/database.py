from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models.base import Base

SQLALCHEMY_DATABASE_URL = "postgresql://postgres:12345@localhost:5432/vkr_checker_db"

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()