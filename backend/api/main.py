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
    """проверяет расширение и структуру OOXML-файла"""
    if not filename or Path(filename).suffix.lower() != ".docx":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Поле {field} должно содержать файл .docx",
        )
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if not DOCX_REQUIRED_PARTS.issubset(archive.namelist()):
                raise ValueError("В архиве отсутствуют обязательные части OOXML")
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Поле {field} не является корректным документом DOCX",
        ) from exc
    return filename


def _validate_pdf(filename: str | None, content: bytes) -> str:
    """проверяет расширение PDF и сигнатуру файла"""
    if not filename or Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Поле guidelines должно содержать файл .docx или .pdf",
        )
    if not content.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Поле guidelines не является корректным документом PDF",
        )
    return filename


async def _read_limited(upload: UploadFile, field: str) -> bytes:
    """читает файл, не превышая установленный предел размера"""
    content = await upload.read(MAX_FILE_SIZE_BYTES + 1)
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Размер файла {field} превышает ограничение 20 МиБ",
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Файл {field} не должен быть пустым",
        )
    return content


def create_app(storage_dir: Path | str | None = None) -> FastAPI:
    """создаёт API-приложение с необязательным переопределением каталога хранения"""
    configured_storage = storage_dir or os.environ.get(
        "CHECK_STORAGE_DIR", "data/checks"
    )
    store = CheckStore(configured_storage)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        store.initialize()
        yield

    application = FastAPI(
        title="API нормоконтроля ВКР",
        description=(
            "Загрузка ВКР и методички для создания проверки. "
            "Текущая версия сохраняет файлы и возвращает начальный статус uploaded."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.check_store = store

    @application.post(
        "/api/v1/checks",
        response_model=CheckResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Проверки"],
        summary="Загрузить ВКР и методичку",
        responses={
            400: {"model": ErrorResponse, "description": "Пустой файл"},
            413: {"model": ErrorResponse, "description": "Размер файла превышает 20 МиБ"},
            415: {"model": ErrorResponse, "description": "Неподдерживаемый или некорректный файл"},
        },
    )
    async def create_check(
        thesis: Annotated[
            UploadFile,
            File(description="ВКР в формате DOCX; не более 20 МиБ"),
        ],
        guidelines: Annotated[
            UploadFile,
            File(description="Методичка в формате DOCX или PDF; не более 20 МиБ"),
        ],
    ) -> CheckResponse:
        """проверяет и сохраняет файлы, затем возвращает ID новой проверки"""
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
        tags=["Проверки"],
        summary="Получить статус проверки",
        responses={404: {"model": ErrorResponse, "description": "Проверка не найдена"}},
    )
    async def get_check_status(check_id: UUID) -> CheckResponse:
        """возвращает сохранённый статус проверки по её ID"""
        check = await run_in_threadpool(store.get_check, str(check_id))
        if check is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Проверка не найдена",
            )
        return check

    return application


app = create_app()
