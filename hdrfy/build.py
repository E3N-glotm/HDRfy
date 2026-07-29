"""Build helper for Google's libultrahdr reference executable."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .errors import HDRfyError

LIBULTRAHDR_REPOSITORY = "https://github.com/google/libultrahdr.git"


def _run(command: list[str], cwd: Path | None = None) -> None:
    completed = subprocess.run(command, cwd=cwd, text=True, check=False)
    if completed.returncode != 0:
        raise HDRfyError(f"Command failed ({completed.returncode}): {' '.join(command)}")


def build_libultrahdr(
    destination: str | Path,
    *,
    ref: str = "main",
    jobs: int | None = None,
    build_dependencies: bool = True,
) -> Path:
    """Clone and build ``ultrahdr_app`` without installing system-wide files."""

    for executable in ("git", "cmake"):
        if not shutil.which(executable):
            raise HDRfyError(f"Required build tool is missing from PATH: {executable}")

    root = Path(destination).expanduser().resolve()
    source = root / "src"
    build_dir = root / "build"
    root.mkdir(parents=True, exist_ok=True)

    if not (source / ".git").exists():
        if source.exists() and any(source.iterdir()):
            raise HDRfyError(f"Build source directory is not empty: {source}")
        _run(["git", "clone", LIBULTRAHDR_REPOSITORY, str(source)])
    _run(["git", "fetch", "origin", ref, "--depth", "1"], cwd=source)
    _run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=source)

    configure = [
        "cmake",
        "-S",
        str(source),
        "-B",
        str(build_dir),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DUHDR_BUILD_EXAMPLES=ON",
        "-DUHDR_BUILD_TESTS=OFF",
        f"-DUHDR_BUILD_DEPS={'ON' if build_dependencies else 'OFF'}",
    ]
    if shutil.which("ninja"):
        configure.extend(["-G", "Ninja"])
    _run(configure)

    build_command = ["cmake", "--build", str(build_dir), "--config", "Release"]
    parallel = jobs if jobs is not None else max(1, os.cpu_count() or 1)
    build_command.extend(["--parallel", str(parallel)])
    _run(build_command)

    names = ("ultrahdr_app.exe", "ultrahdr_app") if os.name == "nt" else ("ultrahdr_app",)
    for name in names:
        for candidate in (build_dir / name, build_dir / "Release" / name):
            if candidate.is_file():
                return candidate.resolve()
    raise HDRfyError(f"Build completed but ultrahdr_app was not found under {build_dir}")
