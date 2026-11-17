from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.database import create_database_engine
from app.models import UploadedCheck
from app.models.base import Base

from .models import CheckResponse, CheckStatus


class CheckStore:
    """Сохраняет файлы проверок на диске, а их статусы — в SQLAlchemy"""

    def __init__(
        self,
        storage_dir: Path | str,
        database_url: str,
    ) -> None:
        """Задаёт каталог файлов и адрес базы данных"""
        self.storage_dir = Path(storage_dir)
        self.files_dir = self.storage_dir / "files"
        self.engine = _create_engine(database_url)

    def initialize(self) -> None:
        """Создаёт каталог файлов и таблицу загрузок, если их ещё нет"""
        self.storage_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.files_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.storage_dir, 0o700)
        os.chmod(self.files_dir, 0o700)
        Base.metadata.create_all(self.engine)

    def create_check(
        self,
        thesis_filename: str,
        thesis_content: bytes,
        guidelines_filename: str,
        guidelines_content: bytes,
    ) -> CheckResponse:
        """Сохраняет оба файла и создаёт запись проверки"""
        check_id = str(uuid4())
        created_at = datetime.now(timezone.utc)
        temporary_dir = Path(tempfile.mkdtemp(prefix=".upload-", dir=self.files_dir))
        final_dir = self.files_dir / check_id
        thesis_path = final_dir / "thesis.docx"
        guidelines_extension = Path(guidelines_filename).suffix.lower()
        guidelines_path = final_dir / f"guidelines{guidelines_extension}"

        try:
            staged_thesis = temporary_dir / thesis_path.name
            staged_guidelines = temporary_dir / guidelines_path.name
            staged_thesis.write_bytes(thesis_content)
            staged_guidelines.write_bytes(guidelines_content)
            os.chmod(staged_thesis, 0o600)
            os.chmod(staged_guidelines, 0o600)
            os.replace(temporary_dir, final_dir)

            with Session(self.engine) as session, session.begin():
                session.add(
                    UploadedCheck(
                        id=check_id,
                        status=CheckStatus.UPLOADED.value,
                        created_at=created_at,
                        thesis_filename=Path(thesis_filename).name,
                        thesis_path=str(thesis_path),
                        guidelines_filename=Path(guidelines_filename).name,
                        guidelines_path=str(guidelines_path),
                    )
                )
        except Exception:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            shutil.rmtree(final_dir, ignore_errors=True)
            raise

        return CheckResponse(
            id=check_id,
            status=CheckStatus.UPLOADED,
            created_at=created_at,
        )

    def get_check(self, check_id: str) -> CheckResponse | None:
        """Возвращает сохранённую проверку или None, если такой ID не найден"""
        with Session(self.engine) as session:
            check = session.get(UploadedCheck, check_id)
            if check is None:
                return None
            created_at = check.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            return CheckResponse(
                id=check.id,
                status=CheckStatus(check.status),
                created_at=created_at,
            )

    def check_database_connection(self) -> None:
        """Проверяет доступность базы данных"""
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def dispose(self) -> None:
        """Закрывает соединения с базой данных"""
        self.engine.dispose()


def _create_engine(database_url: str) -> Engine:
    """Создаёт SQLAlchemy engine для PostgreSQL"""
    return create_database_engine(database_url)
