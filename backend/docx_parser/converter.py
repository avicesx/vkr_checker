"""Конвертация legacy-форматов в .docx через LibreOffice headless.

Поддерживает входные .doc, .rtf, .odt и другие форматы, читаемые LibreOffice.
Выполняется либо в Docker-контейнере, либо локальным soffice, если он установлен.
Результат — нормализованный .docx, пригодный для основного парсера.

Выбор backend:
    auto   — Docker, если доступен, иначе локальный soffice;
    docker — только контейнер (нет демона → RuntimeError);
    local  — только локальный soffice, без обращения к Docker.

Изоляция Docker: исходник копируется в staging-каталог и монтируется только
на чтение, сеть отключена, память ограничена, контейнер работает от UID:GID
хоста, поэтому в выходном каталоге не появляются root-файлы.

Тег образа задается переменной окружения DOCX_LO_IMAGE (по умолчанию
docx-lo:1.0.0); сам образ собирается из docker/libreoffice/Dockerfile.
Тег запинен, потому что версия LibreOffice влияет на экспорт .docx.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Literal, Optional

# Тег образа переопределяется через DOCX_LO_IMAGE, чтобы не править код при смене версии LO.
DOCKER_IMAGE = os.environ.get("DOCX_LO_IMAGE", "docx-lo:1.0.0")
# Лимит памяти защищает хост от «прожорливых» legacy-документов.
DOCKER_MEMORY_LIMIT = "2g"
# Фильтр передается ОДНИМ аргументом и без кавычек. Вариант docx:"MS Word 2007 XML"
# LibreOffice трактует как имя фильтра с кавычками и молча не создает файл (код 0x81a).
CONVERT_FILTER = "docx:MS Word 2007 XML"
# Крупные .doc с формулами и таблицами конвертируются десятки секунд.
CONVERT_TIMEOUT_SECONDS = int(os.environ.get("DOCX_CONVERT_TIMEOUT", "300"))
# В серверном окружении задается DOCX_CONVERT_BACKEND=docker, чтобы не уехать
# на локальный soffice с другой версией LO при недоступном демоне.
DEFAULT_BACKEND = os.environ.get("DOCX_CONVERT_BACKEND", "auto")
Backend = Literal["auto", "docker", "local"]


@dataclass(frozen=True)
class ConversionResult:
    """Итог конвертации: путь к docx, флаги источника и параметры инструмента."""

    docx_path: Path
    source_format: str
    converted_via_lo: bool
    tool: str
    duration_ms: Optional[int] = None
    lo_version: Optional[str] = None
    lo_image: Optional[str] = None


def detect_source_format(path: str | Path) -> str:
    """Определяет формат документа по расширению в нижнем регистре."""
    return Path(path).suffix.lower().lstrip(".")


def is_native_docx(path: str | Path) -> bool:
    """Возвращает True, если файл уже является docx и конвертация не требуется."""
    return detect_source_format(path) == "docx"


@lru_cache(maxsize=1)
def _docker_available() -> bool:
    """Проверяет, что Docker CLI установлен и демон отвечает (результат кэшируется)."""
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "ok"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _local_soffice() -> Optional[str]:
    """Ищет локальный soffice в PATH и типовых каталогах установки."""
    found = shutil.which("soffice")
    if found:
        return found
    candidates = [
        Path("C:/Program Files/LibreOffice/program/soffice.exe"),
        Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
        Path("/usr/bin/soffice"),
        Path("/opt/libreoffice/program/soffice"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _docker_user() -> str:
    """Возвращает UID:GID хоста, чтобы контейнер не оставлял root-файлы."""
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        return f"{os.getuid()}:{os.getgid()}"
    return "0:0"


def _docker_command(
    staging_dir: Path,
    source_name: str,
    outdir: Path,
    container_name: Optional[str] = None,
) -> List[str]:
    """Собирает команду docker run: staging смонтирован только на чтение."""
    profile = f"/tmp/lo-{uuid.uuid4().hex}"
    cmd = [
        "docker", "run", "--rm", "--network", "none",
        "--memory", DOCKER_MEMORY_LIMIT,
    ]
    if container_name:
        # Имя нужно, чтобы снять контейнер, если CLI убит по таймауту.
        cmd += ["--name", container_name]
    cmd += [
        "--user", _docker_user(),
        "-v", f"{staging_dir.resolve()}:/data:ro",
        "-v", f"{outdir.resolve()}:/out",
        DOCKER_IMAGE,
        "soffice", "--headless", "--norestore",
        f"-env:UserInstallation=file://{profile}",
        "--convert-to", CONVERT_FILTER,
        "--outdir", "/out", f"/data/{source_name}",
    ]
    return cmd


def _local_command(soffice: str, source: Path, outdir: Path) -> List[str]:
    """Собирает команду локального soffice с изолированным профилем."""
    profile = Path(tempfile.gettempdir()) / f"lo-{uuid.uuid4().hex}"
    return [
        soffice, "--headless", "--norestore",
        f"-env:UserInstallation={profile.as_uri()}",
        "--convert-to", CONVERT_FILTER,
        "--outdir", str(outdir), str(source),
    ]


def _expected_output(outdir: Path, stem: str) -> Optional[Path]:
    """
    Находит созданный docx в выходном каталоге.

    LibreOffice может изменить регистр имени, поэтому кроме точного совпадения
    проверяется регистронезависимый вариант; из нескольких файлов берется самый
    свежий. Каталог всегда одноразовый, поэтому старый результат сюда не попадет.
    """
    candidate = outdir / f"{stem}.docx"
    if candidate.is_file():
        return candidate
    casefold_stem = stem.casefold()
    matches = [
        item
        for item in outdir.iterdir()
        if item.is_file()
        and item.suffix.lower() == ".docx"
        and item.stem.casefold() == casefold_stem
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: item.stat().st_mtime_ns)


def _version_line(raw: str) -> Optional[str]:
    """Достает первую непустую строку вывода `soffice --version`."""
    for line in raw.splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return None


@lru_cache(maxsize=4)
def _local_lo_version(soffice: str) -> Optional[str]:
    """Возвращает версию локального LibreOffice (кэшируется по пути к soffice)."""
    try:
        proc = subprocess.run(
            [soffice, "--version"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _version_line(proc.stdout or proc.stderr)


@lru_cache(maxsize=4)
def _docker_lo_version(image: str) -> Optional[str]:
    """Возвращает версию LibreOffice из Docker-образа (один запуск на процесс)."""
    try:
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", image, "soffice", "--version"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _version_line(proc.stdout or proc.stderr)


def _remove_container(container_name: str) -> None:
    """Снимает контейнер, оставшийся после убитого по таймауту docker CLI."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Контейнера может уже не быть — ошибка очистки не должна скрывать таймаут.
        pass


def convert_to_docx(
    path: str | Path,
    outdir: Optional[str | Path] = None,
    timeout: int = CONVERT_TIMEOUT_SECONDS,
    backend: Optional[Backend] = None,
) -> ConversionResult:
    """
    Конвертирует документ в .docx через LibreOffice (Docker или локально).

    .docx проходит транзитом без конвертации. Результат создается в outdir
    (по умолчанию рядом с исходником) под тем же именем с расширением .docx;
    существующий файл перезаписывается только после успешной конвертации.

    backend: auto — Docker при доступном демоне, иначе локальный soffice;
    docker — только контейнер; local — только локальный soffice. По умолчанию
    берется DOCX_CONVERT_BACKEND (auto, если переменная не задана).

    Конвертация идет в одноразовый подкаталог outdir: старый {stem}.docx не может
    быть принят за результат, если LibreOffice завершился с кодом 0, но ничего
    не записал.

    Raises:
        FileNotFoundError: исходный файл не существует.
        ValueError: backend имеет недопустимое значение.
        RuntimeError: выбранный backend недоступен, либо конвертация
            завершилась ошибкой, таймаутом или не создала выходной файл.
    """
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Исходный файл не найден: {source}")

    resolved_backend: Backend = backend or DEFAULT_BACKEND  # type: ignore[assignment]
    if resolved_backend not in ("auto", "docker", "local"):
        raise ValueError(
            f"Неизвестный backend конвертации: {resolved_backend!r}; "
            "ожидается auto, docker или local"
        )

    source_format = detect_source_format(source)
    if source_format == "docx":
        return ConversionResult(source, source_format, False, "passthrough")

    target_dir = Path(outdir).resolve() if outdir else source.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    docker_ready = resolved_backend != "local" and _docker_available()
    if resolved_backend == "docker" and not docker_ready:
        raise RuntimeError("LibreOffice недоступен: Docker-демон не отвечает")
    use_docker = resolved_backend == "docker" or (
        resolved_backend == "auto" and docker_ready
    )

    # Одноразовый каталог внутри target_dir: os.replace атомарен (та же ФС),
    # а старый результат в target_dir не виден конвертеру.
    with tempfile.TemporaryDirectory(prefix=".docx-out-", dir=target_dir) as fresh_raw:
        fresh_out = Path(fresh_raw)
        lo_version: Optional[str] = None
        lo_image: Optional[str] = None

        if use_docker:
            container_name = f"docx-lo-{uuid.uuid4().hex}"
            tool = f"docker:{DOCKER_IMAGE}"
            lo_image = DOCKER_IMAGE
            with tempfile.TemporaryDirectory(prefix="docx-stage-") as staging_raw:
                staging = Path(staging_raw)
                shutil.copy2(source, staging / source.name)
                cmd = _docker_command(
                    staging, source.name, fresh_out, container_name=container_name
                )
                duration_ms = _timed_run(
                    cmd, timeout, source, tool, container_name=container_name
                )
        else:
            soffice = _local_soffice()
            if soffice is None:
                raise RuntimeError(
                    "LibreOffice недоступен: нет Docker-демона и локального soffice"
                )
            cmd = _local_command(soffice, source, fresh_out)
            tool = f"local:{soffice}"
            duration_ms = _timed_run(cmd, timeout, source, tool)

        produced = _expected_output(fresh_out, source.stem)
        if produced is None:
            created = sorted(item.name for item in fresh_out.glob("*.docx"))
            details = f"; создано: {created}" if created else "; каталог результата пуст"
            raise RuntimeError(
                f"Конвертация не удалась ({tool}): файл {source.stem}.docx не создан"
                f"{details}"
            )

        output_docx = target_dir / f"{source.stem}.docx"
        os.replace(produced, output_docx)

        if use_docker:
            lo_version = _docker_lo_version(DOCKER_IMAGE)
        else:
            lo_version = _local_lo_version(soffice)

    return ConversionResult(
        docx_path=output_docx,
        source_format=source_format,
        converted_via_lo=True,
        tool=tool,
        duration_ms=duration_ms,
        lo_version=lo_version,
        lo_image=lo_image,
    )


def _timed_run(
    cmd: List[str],
    timeout: int,
    source: Path,
    tool: str,
    container_name: Optional[str] = None,
) -> int:
    """Запускает конвертацию и возвращает ее длительность в миллисекундах."""
    started = time.monotonic()
    if container_name:
        _run_conversion(
            cmd, timeout, source, tool, container_name=container_name
        )
    else:
        _run_conversion(cmd, timeout, source, tool)
    return int((time.monotonic() - started) * 1000)


def _run_conversion(
    cmd: List[str],
    timeout: int,
    source: Path,
    tool: str,
    container_name: Optional[str] = None,
) -> None:
    """Запускает soffice и проверяет код возврата, сохраняя хвост stderr."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=dict(os.environ)
        )
    except subprocess.TimeoutExpired as exc:
        if container_name:
            # subprocess.run убивает только CLI; сам контейнер продолжает работу.
            _remove_container(container_name)
        raise RuntimeError(f"Таймаут конвертации ({timeout} c): {source}") from exc
    if proc.returncode != 0:
        stderr_tail = (proc.stderr or proc.stdout or "")[-500:]
        raise RuntimeError(
            f"Конвертация не удалась ({tool}), код {proc.returncode}: {stderr_tail}"
        )
