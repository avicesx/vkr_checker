from __future__ import annotations

import io
import json
import os
import stat
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.main import MAX_FILE_SIZE_BYTES, create_app


def _docx_bytes(marker: str = "") -> bytes:
    """создаёт минимальный пакет OOXML для тестов API загрузки"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", f"<document>{marker}</document>")
    return buffer.getvalue()


def _docx_parts(content: bytes) -> dict[str, bytes]:
    """возвращает содержимое частей OOXML без учета метаданных ZIP-архива"""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _database_url() -> str:
    """возвращает URL тестовой PostgreSQL или пропускает тест без базы"""
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Для API-тестов задайте TEST_DATABASE_URL PostgreSQL")
    return database_url


def _client(tmp_path: Path) -> TestClient:
    """создаёт тестовый клиент с PostgreSQL и отдельным каталогом файлов"""
    return TestClient(
        create_app(
            tmp_path / "store",
            database_url=_database_url(),
            admin_token="test-admin-token",
        )
    )


def _upload_files(guidelines_name: str = "guide.docx") -> dict[str, tuple]:
    """возвращает multipart-данные ВКР и методички"""
    guidelines = (
        b"%PDF-1.7\nexample" if guidelines_name.endswith(".pdf") else _docx_bytes()
    )
    return {
        "thesis": ("thesis.docx", _docx_bytes(), "application/octet-stream"),
        "guidelines": (guidelines_name, guidelines, "application/octet-stream"),
    }


@pytest.mark.parametrize("guidelines_name", ["guide.docx", "guide.pdf"])
def test_upload_creates_check_and_status_is_retrievable(
    tmp_path: Path,
    guidelines_name: str,
) -> None:
    """создаёт проверку с поддерживаемой методичкой и получает её статус"""
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/checks",
            files=_upload_files(guidelines_name),
        )

        assert response.status_code == 201
        created = response.json()
        assert created["status"] == "uploaded"
        assert created["id"]

        status_response = client.get(f"/api/v1/checks/{created['id']}")

        assert status_response.status_code == 200
        assert status_response.json() == created


def test_upload_stores_pdf_guidelines_without_conversion(tmp_path: Path) -> None:
    """сохраняет методичку PDF как есть и не создаёт DOCX-копию"""
    pdf_content = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
    files = _upload_files("guide.pdf")
    files["guidelines"] = ("guide.pdf", pdf_content, "application/pdf")

    with _client(tmp_path) as client:
        response = client.post("/api/v1/checks", files=files)

        assert response.status_code == 201
        check_dir = tmp_path / "store" / "files" / response.json()["id"]
        assert (check_dir / "guidelines.pdf").read_bytes() == pdf_content
        assert not (check_dir / "guidelines.docx").exists()


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("thesis.pdf", b"%PDF-1.7", "application/pdf"),
        ("thesis.txt", b"plain text", "text/plain"),
    ],
)
def test_upload_rejects_non_docx_thesis(
    tmp_path: Path,
    filename: str,
    content: bytes,
    content_type: str,
) -> None:
    """отклоняет ВКР с расширением, отличным от DOCX"""
    files = _upload_files()
    files["thesis"] = (filename, content, content_type)

    with _client(tmp_path) as client:
        response = client.post("/api/v1/checks", files=files)

    assert response.status_code == 415
    assert "Поле thesis должно содержать файл .docx" in response.json()["detail"]


def test_upload_rejects_unsupported_guidelines_extension(tmp_path: Path) -> None:
    """отклоняет методичку с расширением, отличным от DOCX и PDF"""
    files = _upload_files()
    files["guidelines"] = ("guide.txt", b"plain text", "text/plain")

    with _client(tmp_path) as client:
        response = client.post("/api/v1/checks", files=files)

    assert response.status_code == 415
    assert "Поле guidelines должно содержать файл .docx" in response.json()["detail"]


def test_upload_rejects_invalid_docx_content(tmp_path: Path) -> None:
    """отклоняет файл DOCX, содержимое которого не является пакетом OOXML"""
    files = _upload_files()
    files["thesis"] = ("thesis.docx", b"not a docx", "application/octet-stream")

    with _client(tmp_path) as client:
        response = client.post("/api/v1/checks", files=files)

    assert response.status_code == 415
    assert "не является корректным документом DOCX" in response.json()["detail"]


def test_upload_rejects_invalid_pdf_content(tmp_path: Path) -> None:
    """отклоняет файл PDF с неверной сигнатурой"""
    files = _upload_files("guide.pdf")
    files["guidelines"] = ("guide.pdf", b"not a pdf", "application/pdf")

    with _client(tmp_path) as client:
        response = client.post("/api/v1/checks", files=files)

    assert response.status_code == 415
    assert "не является корректным документом PDF" in response.json()["detail"]


def test_upload_rejects_file_over_size_limit(tmp_path: Path) -> None:
    """отклоняет ВКР, превышающую установленный предел размера"""
    files = _upload_files()
    files["thesis"] = (
        "thesis.docx",
        b"x" * (MAX_FILE_SIZE_BYTES + 1),
        "application/octet-stream",
    )

    with _client(tmp_path) as client:
        response = client.post("/api/v1/checks", files=files)

    assert response.status_code == 413
    assert "20 МиБ" in response.json()["detail"]


def test_upload_rejects_over_size_guidelines(tmp_path: Path) -> None:
    """отклоняет методичку, превышающую установленный предел размера"""
    files = _upload_files("guide.pdf")
    files["guidelines"] = (
        "guide.pdf",
        b"x" * (MAX_FILE_SIZE_BYTES + 1),
        "application/pdf",
    )

    with _client(tmp_path) as client:
        response = client.post("/api/v1/checks", files=files)

    assert response.status_code == 413
    assert "guidelines" in response.json()["detail"]
    assert "20 МиБ" in response.json()["detail"]


@pytest.mark.parametrize(
    ("field", "filename", "content_type"),
    [
        ("thesis", "thesis.docx", "application/octet-stream"),
        ("guidelines", "guide.pdf", "application/pdf"),
    ],
)
def test_upload_rejects_empty_files(
    tmp_path: Path,
    field: str,
    filename: str,
    content_type: str,
) -> None:
    """отклоняет пустой файл ВКР или методички"""
    files = _upload_files("guide.pdf")
    files[field] = (filename, b"", content_type)

    with _client(tmp_path) as client:
        response = client.post("/api/v1/checks", files=files)

    assert response.status_code == 400
    assert response.json() == {"detail": f"Файл {field} не должен быть пустым"}


@pytest.mark.parametrize("missing_field", ["thesis", "guidelines"])
def test_upload_requires_both_multipart_parts(
    tmp_path: Path,
    missing_field: str,
) -> None:
    """возвращает ошибку валидации, если не передана одна из частей multipart"""
    files = _upload_files("guide.pdf")
    files.pop(missing_field)

    with _client(tmp_path) as client:
        response = client.post("/api/v1/checks", files=files)

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any(
        error.get("type") == "missing" and error.get("loc") == ["body", missing_field]
        for error in errors
    )


def test_status_endpoint_returns_not_found_for_unknown_id(tmp_path: Path) -> None:
    """возвращает 404, если проверка с указанным ID не найдена"""
    with _client(tmp_path) as client:
        response = client.get(f"/api/v1/checks/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Проверка не найдена"}


def test_files_and_status_survive_store_recreation(tmp_path: Path) -> None:
    """сохраняет файлы и статус после перезапуска клиента и приложения"""
    storage_dir = tmp_path / "persistent"
    database_url = _database_url()
    with TestClient(
        create_app(
            storage_dir,
            database_url=database_url,
            admin_token="test-admin-token",
        )
    ) as client:
        created = client.post(
            "/api/v1/checks",
            files=_upload_files(),
        ).json()

    with TestClient(
        create_app(
            storage_dir,
            database_url=database_url,
            admin_token="test-admin-token",
        )
    ) as client:
        response = client.get(f"/api/v1/checks/{created['id']}")

    assert response.status_code == 200
    check_dir = storage_dir / "files" / created["id"]
    assert (check_dir / "thesis.docx").is_file()
    assert (check_dir / "guidelines.docx").is_file()
    assert _docx_parts((check_dir / "thesis.docx").read_bytes()) == _docx_parts(
        _docx_bytes()
    )
    assert _docx_parts((check_dir / "guidelines.docx").read_bytes()) == _docx_parts(
        _docx_bytes()
    )
    if os.name == "posix":
        for filename in ("thesis.docx", "guidelines.docx"):
            assert stat.S_IMODE((check_dir / filename).stat().st_mode) == 0o600
        assert stat.S_IMODE(check_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE((storage_dir / "files").stat().st_mode) == 0o700
        assert stat.S_IMODE(storage_dir.stat().st_mode) == 0o700


def test_repeated_uploads_get_distinct_ids_and_keep_previous_files(
    tmp_path: Path,
) -> None:
    """создаёт отдельную проверку на каждую загрузку и не перезаписывает прежние файлы"""
    files_dir = tmp_path / "store" / "files"
    first_files = {
        "thesis": ("thesis.docx", _docx_bytes("first"), "application/octet-stream"),
        "guidelines": ("guide.pdf", b"%PDF-1.7\nfirst", "application/pdf"),
    }
    second_files = {
        "thesis": ("thesis.docx", _docx_bytes("second"), "application/octet-stream"),
        "guidelines": ("guide.docx", _docx_bytes("second"), "application/octet-stream"),
    }

    with _client(tmp_path) as client:
        first = client.post("/api/v1/checks", files=first_files)
        second = client.post("/api/v1/checks", files=second_files)
        assert first.status_code == 201
        assert second.status_code == 201
        first_id = first.json()["id"]
        second_id = second.json()["id"]

        assert first_id != second_id
        assert client.get(f"/api/v1/checks/{first_id}").status_code == 200
        assert client.get(f"/api/v1/checks/{second_id}").status_code == 200

    first_dir = files_dir / first_id
    second_dir = files_dir / second_id
    staging_dirs = [
        path for path in files_dir.iterdir() if path.name.startswith(".upload-")
    ]
    assert first_dir != second_dir
    assert not staging_dirs
    assert sorted(path.name for path in first_dir.iterdir()) == [
        "guidelines.pdf",
        "thesis.docx",
    ]
    assert sorted(path.name for path in second_dir.iterdir()) == [
        "guidelines.docx",
        "thesis.docx",
    ]
    assert _docx_parts((first_dir / "thesis.docx").read_bytes()) == _docx_parts(
        _docx_bytes("first")
    )
    assert _docx_parts((second_dir / "thesis.docx").read_bytes()) == _docx_parts(
        _docx_bytes("second")
    )
    assert (first_dir / "guidelines.pdf").read_bytes() == b"%PDF-1.7\nfirst"


def test_openapi_contract_is_checked_in_and_matches_application() -> None:
    """проверяет соответствие OpenAPI-контракта схеме приложения"""
    contract_path = Path(__file__).resolve().parents[1] / "openapi.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    application_contract = create_app().openapi()

    assert contract == application_contract


def test_upload_storage_dir_environment_is_respected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """использует путь хранилища, заданный Docker Compose"""
    storage_dir = tmp_path / "uploads"
    monkeypatch.delenv("CHECK_STORAGE_DIR", raising=False)
    monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(storage_dir))

    application = create_app(
        database_url="postgresql+psycopg://user:password@localhost/test"
    )

    assert application.state.check_store.storage_dir == storage_dir
    application.state.check_store.engine.dispose()


def test_health_endpoint_checks_the_database(tmp_path: Path) -> None:
    """проверяет ответ служебного эндпоинта при доступной базе данных"""
    database_url = _database_url()

    with TestClient(create_app(tmp_path / "health", database_url)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_can_save_and_read_checks_in_postgresql(tmp_path: Path) -> None:
    """проверяет сохранение и чтение проверки в PostgreSQL"""
    database_url = _database_url()

    with TestClient(create_app(tmp_path / "postgres", database_url)) as client:
        created = client.post(
            "/api/v1/checks",
            files=_upload_files(),
        )
        assert created.status_code == 201
        check_id = created.json()["id"]

        status_response = client.get(f"/api/v1/checks/{check_id}")

    assert status_response.status_code == 200
    assert status_response.json()["status"] == "uploaded"
