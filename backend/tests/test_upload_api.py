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


def _docx_bytes() -> bytes:
    """Build a minimal OOXML package for upload API tests."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    return buffer.getvalue()


def _client(tmp_path: Path) -> TestClient:
    """Create a test client whose storage is isolated to the test directory."""
    return TestClient(create_app(tmp_path / "store"))


def _upload_files(guidelines_name: str = "guide.docx") -> dict[str, tuple]:
    """Return a valid thesis and guide multipart payload."""
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
    """Create a check for supported guide formats and retrieve its status."""
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


def test_upload_rejects_non_docx_thesis(tmp_path: Path) -> None:
    """Reject thesis uploads whose extension is not DOCX."""
    files = _upload_files()
    files["thesis"] = ("thesis.pdf", b"%PDF-1.7", "application/pdf")

    with _client(tmp_path) as client:
        response = client.post("/api/v1/checks", files=files)

    assert response.status_code == 415
    assert "thesis must be a .docx" in response.json()["detail"]


def test_upload_rejects_invalid_docx_content(tmp_path: Path) -> None:
    """Reject a DOCX extension when the uploaded bytes are not an OOXML package."""
    files = _upload_files()
    files["thesis"] = ("thesis.docx", b"not a docx", "application/octet-stream")

    with _client(tmp_path) as client:
        response = client.post("/api/v1/checks", files=files)

    assert response.status_code == 415
    assert "not a valid DOCX" in response.json()["detail"]


def test_upload_rejects_invalid_pdf_content(tmp_path: Path) -> None:
    """Reject a PDF extension without a PDF file signature."""
    files = _upload_files("guide.pdf")
    files["guidelines"] = ("guide.pdf", b"not a pdf", "application/pdf")

    with _client(tmp_path) as client:
        response = client.post("/api/v1/checks", files=files)

    assert response.status_code == 415
    assert "not a valid PDF" in response.json()["detail"]


def test_upload_rejects_file_over_size_limit(tmp_path: Path) -> None:
    """Reject files larger than the per-file upload limit."""
    files = _upload_files()
    files["thesis"] = (
        "thesis.docx",
        b"x" * (MAX_FILE_SIZE_BYTES + 1),
        "application/octet-stream",
    )

    with _client(tmp_path) as client:
        response = client.post("/api/v1/checks", files=files)

    assert response.status_code == 413
    assert "20 MiB" in response.json()["detail"]


def test_status_endpoint_returns_not_found_for_unknown_id(tmp_path: Path) -> None:
    """Return 404 when no check exists for the requested ID."""
    with _client(tmp_path) as client:
        response = client.get(f"/api/v1/checks/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Check not found"}


def test_files_and_status_survive_store_recreation(tmp_path: Path) -> None:
    """Persist both uploaded files and status beyond a client/application restart."""
    storage_dir = tmp_path / "persistent"
    with TestClient(create_app(storage_dir)) as client:
        created = client.post(
            "/api/v1/checks",
            files=_upload_files(),
        ).json()

    with TestClient(create_app(storage_dir)) as client:
        response = client.get(f"/api/v1/checks/{created['id']}")

    assert response.status_code == 200
    check_dir = storage_dir / "files" / created["id"]
    assert (check_dir / "thesis.docx").is_file()
    assert (check_dir / "guidelines.docx").is_file()
    assert (check_dir / "thesis.docx").read_bytes() == _docx_bytes()
    assert (check_dir / "guidelines.docx").read_bytes() == _docx_bytes()
    if os.name == "posix":
        assert stat.S_IMODE((check_dir / "thesis.docx").stat().st_mode) == 0o600
        assert stat.S_IMODE(check_dir.stat().st_mode) == 0o700


def test_openapi_contract_is_checked_in_and_matches_application() -> None:
    """Keep the checked-in OpenAPI contract synchronized with FastAPI routes."""
    contract_path = Path(__file__).resolve().parents[1] / "openapi.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    application_contract = create_app().openapi()

    assert contract == application_contract
