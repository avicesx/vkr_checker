"""Конвертер legacy-форматов в docx: определение формата, транзит docx и ошибки окружения."""
from __future__ import annotations

import importlib
import shutil
import subprocess
from pathlib import Path

import pytest

from docx_parser import converter
from docx_parser.converter import (
    convert_to_docx,
    detect_source_format,
    is_native_docx,
)
from docx_parser.pipeline import DocxParserPipeline, parse_any_to_json_file

MANUAL_SAMPLE = Path(__file__).resolve().parents[1] / "manual_test" / "3.doc"


def test_detect_source_format_is_case_insensitive() -> None:
    """Расширение нормализуется в нижний регистр без точки."""
    assert detect_source_format("Thesis.DOC") == "doc"
    assert detect_source_format("report.Rtf") == "rtf"
    assert detect_source_format("/tmp/x.docx") == "docx"


def test_is_native_docx_detects_docx_only() -> None:
    """Только .docx считается родным форматом."""
    assert is_native_docx("a.docx") is True
    assert is_native_docx("a.doc") is False


def test_docx_passes_through_without_conversion(tmp_path: Path) -> None:
    """Файл .docx не конвертируется и возвращается как есть."""
    src = tmp_path / "native.docx"
    src.write_bytes(b"PK")
    result = convert_to_docx(src)
    assert result.docx_path == src.resolve()
    assert result.converted_via_lo is False
    assert result.tool == "passthrough"


def test_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    """Несуществующий исходник приводит к FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        convert_to_docx(tmp_path / "absent.doc")


def test_no_libreoffice_raises_runtime_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Без Docker и локального soffice конвертация сообщает об ошибке окружения."""
    src = tmp_path / "legacy.doc"
    src.write_bytes(b"\xd0\xcf\x11\xe0")
    monkeypatch.setattr(converter, "_docker_available", lambda: False)
    monkeypatch.setattr(converter, "_local_soffice", lambda: None)
    with pytest.raises(RuntimeError, match="LibreOffice недоступен"):
        convert_to_docx(src)


def test_invalid_backend_is_rejected(tmp_path: Path) -> None:
    """Опечатка в backend не приводит к неявному выбору конвертера."""
    src = tmp_path / "legacy.doc"
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="Неизвестный backend"):
        convert_to_docx(src, backend="podman")  # type: ignore[arg-type]


def test_docker_command_mounts_staging_read_only(tmp_path: Path) -> None:
    """Команда docker run монтирует staging только на чтение и изолирует сеть."""
    staging = tmp_path / "stage"
    staging.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    cmd = converter._docker_command(staging, "legacy.doc", out)
    assert cmd[:2] == ["docker", "run"]
    assert f"{staging.resolve()}:/data:ro" in cmd
    assert f"{out.resolve()}:/out" in cmd
    assert "--network" in cmd and "none" in cmd
    assert converter.DOCKER_IMAGE in cmd
    assert converter.CONVERT_FILTER in cmd
    assert "/data/legacy.doc" in cmd


def test_backend_docker_requires_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Явный backend=docker не откатывается на локальный soffice."""
    src = tmp_path / "legacy.doc"
    src.write_bytes(b"x")
    monkeypatch.setattr(converter, "_docker_available", lambda: False)
    monkeypatch.setattr(converter, "_local_soffice", lambda: "soffice")
    with pytest.raises(RuntimeError, match="LibreOffice недоступен"):
        convert_to_docx(src, backend="docker")


def test_docker_image_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Переменная DOCX_LO_IMAGE подменяет образ без правки кода."""
    monkeypatch.setenv("DOCX_LO_IMAGE", "custom/lo:1.0")
    try:
        importlib.reload(converter)
        assert converter.DOCKER_IMAGE == "custom/lo:1.0"
    finally:
        monkeypatch.delenv("DOCX_LO_IMAGE", raising=False)
        importlib.reload(converter)
    assert converter.DOCKER_IMAGE == "docx-lo:1.0.0"


def test_stale_output_is_not_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Старый {stem}.docx в outdir не принимается за результат «пустой» конвертации."""
    src = tmp_path / "legacy.doc"
    src.write_bytes(b"\xd0\xcf\x11\xe0")
    out = tmp_path / "out"
    out.mkdir()
    (out / "legacy.docx").write_bytes(b"stale")
    monkeypatch.setattr(converter, "_docker_available", lambda: False)
    monkeypatch.setattr(converter, "_local_soffice", lambda: "soffice")
    monkeypatch.setattr(converter, "_run_conversion", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="не создан"):
        convert_to_docx(src, outdir=out, backend="local")
    assert (out / "legacy.docx").read_bytes() == b"stale"


def test_conversion_writes_fresh_output_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Свежий результат из одноразового каталога заменяет старый и не оставляет мусора."""
    src = tmp_path / "legacy.rtf"
    src.write_text(r"{\rtf1\ansi Hello}", encoding="ascii")
    out = tmp_path / "out"
    out.mkdir()
    (out / "legacy.docx").write_bytes(b"stale")

    def fake_run(cmd, timeout, source, tool, container_name=None):
        fresh_dir = Path(cmd[cmd.index("--outdir") + 1])
        (fresh_dir / "legacy.docx").write_bytes(b"fresh")

    monkeypatch.setattr(converter, "_docker_available", lambda: False)
    monkeypatch.setattr(converter, "_local_soffice", lambda: "soffice")
    monkeypatch.setattr(converter, "_run_conversion", fake_run)
    monkeypatch.setattr(converter, "_local_lo_version", lambda _path: "LibreOffice test")

    result = convert_to_docx(src, outdir=out, backend="local")
    assert result.docx_path == out / "legacy.docx"
    assert result.docx_path.read_bytes() == b"fresh"
    assert result.duration_ms is not None and result.duration_ms >= 0
    assert result.lo_version == "LibreOffice test"
    assert result.tool == "local:soffice"
    assert result.lo_image is None
    assert [item.name for item in out.iterdir()] == ["legacy.docx"]


def test_docker_command_passes_container_name(tmp_path: Path) -> None:
    """Имя контейнера передается в docker run, чтобы его можно было снять по таймауту."""
    staging = tmp_path / "stage"
    staging.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    cmd = converter._docker_command(staging, "legacy.doc", out, container_name="docx-lo-test")
    assert cmd[cmd.index("--name") + 1] == "docx-lo-test"


def test_timeout_removes_docker_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """При таймауте CLI снимает контейнер, который иначе продолжил бы работу."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, 1)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(converter.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Таймаут конвертации"):
        converter._run_conversion(
            ["docker", "run", "image"],
            1,
            tmp_path / "legacy.doc",
            "docker:image",
            container_name="docx-lo-test",
        )
    assert calls[0][:2] == ["docker", "run"]
    assert calls[1] == ["docker", "rm", "-f", "docx-lo-test"]


def test_parse_any_passes_conversion_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """parse_any_to_json_file прокидывает backend и timeout в конвертер."""
    src = tmp_path / "legacy.doc"
    src.write_bytes(b"x")
    captured = {}

    def fake_convert(path, outdir=None, timeout=0, backend="auto"):
        captured.update(path=path, outdir=outdir, timeout=timeout, backend=backend)
        raise RuntimeError("stop")

    monkeypatch.setattr(converter, "convert_to_docx", fake_convert)
    with pytest.raises(RuntimeError, match="stop"):
        parse_any_to_json_file(
            src, tmp_path / "out.json", backend="docker", timeout=42
        )
    assert captured["timeout"] == 42
    assert captured["backend"] == "docker"


def test_pipeline_reports_conversion_metadata(docx_builder) -> None:
    """Сведения о конвертере попадают в meta, не подменяя application из docProps."""
    src = docx_builder.set_body(
        "<w:p><w:r><w:t>Text</w:t></w:r></w:p>"
    ).build("converted.docx")
    parsed = DocxParserPipeline(
        src,
        source_format="doc",
        converted_via_lo=True,
        conversion_tool="docker:docx-lo:1.0.0",
        conversion_duration_ms=1234,
        lo_version="LibreOffice 25.2",
        lo_image="docx-lo:1.0.0",
    ).parse()
    assert parsed.meta.converted_via_lo is True
    assert parsed.meta.source_format == "doc"
    assert parsed.meta.conversion_tool == "docker:docx-lo:1.0.0"
    assert parsed.meta.conversion_duration_ms == 1234
    assert parsed.meta.lo_version == "LibreOffice 25.2"
    assert parsed.meta.lo_image == "docx-lo:1.0.0"


@pytest.mark.integration
def test_real_doc_conversion_keeps_structure(tmp_path: Path) -> None:
    """Реальный .doc с формулами и сносками конвертируется без потери структуры."""
    if not MANUAL_SAMPLE.is_file():
        pytest.skip("Нет manual_test/3.doc")
    if not converter._docker_available():
        pytest.skip("Docker недоступен")
    doc = parse_any_to_json_file(MANUAL_SAMPLE, tmp_path / "out.json", outdir=tmp_path)
    assert (tmp_path / "3.docx").is_file()
    assert doc.meta.source_format == "doc"
    assert doc.meta.converted_via_lo is True
    assert doc.meta.conversion_tool == f"docker:{converter.DOCKER_IMAGE}"
    assert doc.meta.conversion_duration_ms is not None
    assert doc.meta.conversion_duration_ms > 0
    assert doc.meta.lo_image == converter.DOCKER_IMAGE
    assert (doc.meta.lo_version or "").startswith("LibreOffice")
    assert len(doc.footnotes) > 0
    assert len(doc.cross_ref_index.equations) >= 1
    assert any(v.visual_kind == "ole_object" for v in doc.visuals.values())
    total_text = sum(
        len(p.text_visible) for p in doc.blocks if hasattr(p, "text_visible")
    )
    assert total_text > 10_000


def test_end_to_end_conversion_when_libreoffice_available(tmp_path: Path) -> None:
    """Реальная конвертация .rtf в .docx, если доступен локальный soffice."""
    if converter._local_soffice() is None and not converter._docker_available():
        pytest.skip("LibreOffice/Docker недоступны в окружении")
    src = tmp_path / "sample.rtf"
    src.write_text(r"{\rtf1\ansi Hello world.\par}", encoding="ascii")
    result = convert_to_docx(src, outdir=tmp_path / "out", timeout=240)
    assert result.docx_path.suffix == ".docx"
    assert result.docx_path.is_file()
    assert result.source_format == "rtf"
    assert result.converted_via_lo is True
    shutil.rmtree(tmp_path / "out", ignore_errors=True)
