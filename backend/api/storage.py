from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import CheckResponse, CheckStatus


class CheckStore:
    """Persist uploaded files and check status in a local directory."""

    def __init__(self, storage_dir: Path | str) -> None:
        """Set the root directory for database and uploaded files."""
        self.storage_dir = Path(storage_dir)
        self.files_dir = self.storage_dir / "files"
        self.database_path = self.storage_dir / "checks.sqlite3"

    def initialize(self) -> None:
        """Create storage directories and initialize the checks database."""
        self.storage_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.files_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.storage_dir, 0o700)
        os.chmod(self.files_dir, 0o700)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS checks (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (
                        status IN ('uploaded', 'queued', 'processing', 'completed', 'failed')
                    ),
                    created_at TEXT NOT NULL,
                    thesis_filename TEXT NOT NULL,
                    guidelines_filename TEXT NOT NULL
                )
                """
            )
        os.chmod(self.database_path, 0o600)

    def create_check(
        self,
        thesis_filename: str,
        thesis_content: bytes,
        guidelines_filename: str,
        guidelines_content: bytes,
    ) -> CheckResponse:
        """Atomically store both files and create a check record."""
        check_id = str(uuid4())
        created_at = datetime.now(timezone.utc)
        temporary_dir = Path(tempfile.mkdtemp(prefix=".upload-", dir=self.files_dir))
        final_dir = self.files_dir / check_id

        try:
            thesis_path = temporary_dir / "thesis.docx"
            guidelines_extension = Path(guidelines_filename).suffix.lower()
            guidelines_path = temporary_dir / f"guidelines{guidelines_extension}"
            thesis_path.write_bytes(thesis_content)
            guidelines_path.write_bytes(guidelines_content)
            os.chmod(thesis_path, 0o600)
            os.chmod(guidelines_path, 0o600)
            os.replace(temporary_dir, final_dir)

            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO checks (
                        id, status, created_at, thesis_filename, guidelines_filename
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        check_id,
                        CheckStatus.UPLOADED.value,
                        created_at.isoformat(),
                        thesis_filename,
                        guidelines_filename,
                    ),
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
        """Return a persisted check, or None when its ID is unknown."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, status, created_at FROM checks WHERE id = ?",
                (check_id,),
            ).fetchone()

        if row is None:
            return None

        return CheckResponse(
            id=row["id"],
            status=CheckStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _connect(self) -> sqlite3.Connection:
        """Open a row-addressable SQLite connection."""
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection
