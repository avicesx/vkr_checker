from __future__ import annotations

import io
import os
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator
from uuid import UUID

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from .models import CheckResponse, ErrorResponse
from .storage import CheckStore

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
DOCX_REQUIRED_PARTS = {"[Content_Types].xml", "word/document.xml"}


def _validate_docx(filename: str | None, content: bytes, field: str) -> str:
    """Validate a DOCX filename and its OOXML ZIP structure."""
    if not filename or Path(filename).suffix.lower() != ".docx":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"{field} must be a .docx file",
        )
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if not DOCX_REQUIRED_PARTS.issubset(archive.namelist()):
                raise ValueError("required OOXML parts are missing")
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"{field} is not a valid DOCX document",
        ) from exc
    return filename


def _validate_pdf(filename: str | None, content: bytes) -> str:
    """Validate a PDF filename and its file signature."""
    if not filename or Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="guidelines must be a .docx or .pdf file",
        )
    if not content.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="guidelines is not a valid PDF document",
        )
    return filename


async def _read_limited(upload: UploadFile, field: str) -> bytes:
    """Read an upload while enforcing the configured per-file size limit."""
    content = await upload.read(MAX_FILE_SIZE_BYTES + 1)
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"{field} exceeds the 20 MiB per-file limit",
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field} must not be empty",
        )
    return content


def create_app(storage_dir: Path | str | None = None) -> FastAPI:
    """Build the API application with an optional storage directory override."""
    configured_storage = storage_dir or os.environ.get(
        "CHECK_STORAGE_DIR", "data/checks"
    )
    store = CheckStore(configured_storage)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        store.initialize()
        yield

    application = FastAPI(
        title="VKR Norm Control API",
        description=(
            "Upload a thesis and its formatting guide to create a check. "
            "This version stores uploads and exposes the initial uploaded status."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.check_store = store

    @application.post(
        "/api/v1/checks",
        response_model=CheckResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["checks"],
        summary="Upload thesis and formatting guide",
        responses={
            400: {"model": ErrorResponse, "description": "Empty file"},
            413: {"model": ErrorResponse, "description": "File exceeds 20 MiB"},
            415: {"model": ErrorResponse, "description": "Unsupported or invalid file"},
        },
    )
    async def create_check(
        thesis: Annotated[
            UploadFile,
            File(description="Thesis document in DOCX format; maximum 20 MiB"),
        ],
        guidelines: Annotated[
            UploadFile,
            File(description="Formatting guide in DOCX or PDF format; maximum 20 MiB"),
        ],
    ) -> CheckResponse:
        """Validate and save uploaded files, then return the new check ID."""
        thesis_content = await _read_limited(thesis, "thesis")
        thesis_filename = _validate_docx(thesis.filename, thesis_content, "thesis")
        guidelines_content = await _read_limited(guidelines, "guidelines")
        if guidelines.filename and Path(guidelines.filename).suffix.lower() == ".pdf":
            guidelines_filename = _validate_pdf(guidelines.filename, guidelines_content)
        else:
            guidelines_filename = _validate_docx(
                guidelines.filename, guidelines_content, "guidelines"
            )

        return await run_in_threadpool(
            store.create_check,
            thesis_filename,
            thesis_content,
            guidelines_filename,
            guidelines_content,
        )

    @application.get(
        "/api/v1/checks/{check_id}",
        response_model=CheckResponse,
        tags=["checks"],
        summary="Get check status",
        responses={404: {"model": ErrorResponse, "description": "Check not found"}},
    )
    async def get_check_status(check_id: UUID) -> CheckResponse:
        """Return the current persisted status for a check ID."""
        check = await run_in_threadpool(store.get_check, str(check_id))
        if check is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Check not found",
            )
        return check

    return application


app = create_app()
