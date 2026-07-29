"""libultrahdr executable discovery, invocation and validation."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import UltraHDREncodeError, UltraHDREncoderNotFound


@dataclass(frozen=True, slots=True)
class UltraHDREncodeOptions:
    width: int
    height: int
    base_quality: int
    gainmap_quality: int
    gainmap_scale: int
    multi_channel_gainmap: bool
    max_content_boost: float
    target_peak_nits: float


def _platform_binary_names() -> tuple[str, ...]:
    return ("ultrahdr_app.exe", "ultrahdr_app") if os.name == "nt" else ("ultrahdr_app",)


def find_ultrahdr_binary(explicit: str | Path | None = None) -> Path:
    """Find a usable ``ultrahdr_app`` executable."""

    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_path = os.environ.get("HDRFY_ULTRAHDR_BIN")
    if env_path:
        candidates.append(Path(env_path).expanduser())

    for name in _platform_binary_names():
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    project_root = Path(__file__).resolve().parent.parent
    for name in _platform_binary_names():
        candidates.extend(
            [
                project_root / ".tools" / "libultrahdr" / "build" / name,
                project_root / ".tools" / "libultrahdr" / "build" / "Release" / name,
            ]
        )

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved

    raise UltraHDREncoderNotFound(
        "ultrahdr_app was not found. Run `hdrfy build-ultrahdr`, pass "
        "`--ultrahdr-bin PATH`, or set HDRFY_ULTRAHDR_BIN."
    )


def _runtime_environment(binary: Path) -> dict[str, str]:
    env = dict(os.environ)
    binary_dir = str(binary.parent)
    if sys.platform.startswith("linux"):
        key = "LD_LIBRARY_PATH"
    elif sys.platform == "darwin":
        key = "DYLD_FALLBACK_LIBRARY_PATH"
    else:
        key = "PATH"
    env[key] = binary_dir + os.pathsep + env.get(key, "")
    return env


def encode_ultrahdr(
    *,
    binary: Path,
    hdr_raw: Path,
    sdr_raw: Path,
    output: Path,
    options: UltraHDREncodeOptions,
    exif_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Encode raw HDR and SDR intents as a backward-compatible Ultra HDR JPEG."""

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(binary),
        "-m",
        "0",
        "-p",
        str(hdr_raw),
        "-y",
        str(sdr_raw),
        "-w",
        str(options.width),
        "-h",
        str(options.height),
        "-a",
        "4",  # UHDR_IMG_FMT_64bppRGBAHalfFloat
        "-b",
        "3",  # UHDR_IMG_FMT_32bppRGBA8888
        "-C",
        "2",  # UHDR_CG_BT_2100
        "-c",
        "0",  # UHDR_CG_BT_709
        "-t",
        "0",  # UHDR_CT_LINEAR
        "-R",
        "1",  # full-range RGB
        "-q",
        str(options.base_quality),
        "-Q",
        str(options.gainmap_quality),
        "-s",
        str(options.gainmap_scale),
        "-M",
        "1" if options.multi_channel_gainmap else "0",
        "-D",
        "1",  # best-quality preset
        "-k",
        "1.0",
        "-K",
        f"{options.max_content_boost:.8g}",
        "-L",
        f"{options.target_peak_nits:.8g}",
        "-z",
        str(output),
    ]
    if exif_path is not None:
        command.extend(["-x", str(exif_path)])

    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=_runtime_environment(binary),
    )
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        details = (completed.stderr or completed.stdout or "unknown encoder failure").strip()
        raise UltraHDREncodeError(
            f"libultrahdr encoding failed with exit code {completed.returncode}: {details}"
        )
    return completed


def probe_ultrahdr(binary: Path, image: str | Path) -> str:
    """Use libultrahdr probe mode to verify gain-map metadata."""

    source = Path(image).expanduser().resolve()
    completed = subprocess.run(
        [str(binary), "-m", "1", "-j", str(source), "-P"],
        text=True,
        capture_output=True,
        check=False,
        env=_runtime_environment(binary),
    )
    combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    if completed.returncode != 0:
        raise UltraHDREncodeError(
            f"libultrahdr probe rejected {source} with exit code {completed.returncode}: {combined}"
        )
    return combined
