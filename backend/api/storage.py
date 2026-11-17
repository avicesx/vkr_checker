from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.models import UploadedCheck
from app.models.base import Base

from .models import CheckResponse, CheckStatus


class CheckStore:
    """Сохраняет загруженные файлы и статус проверки в PostgreSQL."""

    def __init__(
        self,
        storage_dir: Path | str,
        engine: Engine,
        session_factory: sessionmaker,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.files_dir = self.storage_dir / "files"
        self.engine = engine
        self.session_factory = session_factory

    def initialize(self) -> None:
        self.storage_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.files_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.storage_dir, 0o700)
        os.chmod(self.files_dir, 0o700)
        Base.metadata.create_all(bind=self.engine)

    def create_check(
        self,
        thesis_filename: str,
        thesis_content: bytes,
        guidelines_filename: str,
        guidelines_content: bytes,
    ) -> CheckResponse:
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

            with self.session_factory.begin() as session:
                session.add(
                    UploadedCheck(
                        id=check_id,
                        status=CheckStatus.UPLOADED.value,
                        created_at=created_at,
                        thesis_filename=thesis_filename,
                        thesis_path=str(final_dir / thesis_path.name),
                        guidelines_filename=guidelines_filename,
                        guidelines_path=str(final_dir / guidelines_path.name),
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
        with self.session_factory() as session:
            check = session.scalar(
                select(UploadedCheck).where(UploadedCheck.id == check_id)
            )
            if check is None:
                return None
            return CheckResponse(
                id=check.id,
                status=CheckStatus(check.status),
                created_at=check.created_at,
            )
