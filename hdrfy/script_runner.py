"""Reusable path planning for the script-configured HDRfy entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import HDRfyError

SUPPORTED_INPUT_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".heif", ".heic", ".avif"})


@dataclass(frozen=True, slots=True)
class BatchItem:
    source: Path
    output: Path
    intermediates: Path | None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def collect_input_files(
    input_path: Path,
    *,
    output_path: Path,
    recursive: bool,
) -> list[Path]:
    """Collect supported inputs while excluding the configured output tree."""

    if input_path.is_file():
        if input_path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
            supported = ", ".join(sorted(SUPPORTED_INPUT_SUFFIXES))
            raise HDRfyError(f"不支持的输入格式：{input_path.suffix}；支持：{supported}")
        return [input_path]

    if not input_path.is_dir():
        raise HDRfyError(f"输入路径不存在或不是文件/目录：{input_path}")

    iterator = input_path.rglob("*") if recursive else input_path.glob("*")
    files: list[Path] = []
    for candidate in iterator:
        if not candidate.is_file() or candidate.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
            continue
        resolved = candidate.resolve()
        if _is_relative_to(resolved, output_path):
            continue
        files.append(resolved)
    return sorted(files)


def build_batch_items(
    input_path: Path,
    output_path: Path,
    sources: list[Path],
    *,
    output_suffix: str,
    keep_intermediates: bool,
    intermediates_root: Path,
) -> list[BatchItem]:
    """Plan deterministic output paths for single-file or directory input."""

    input_is_file = input_path.is_file()
    if not input_is_file and output_path.suffix.lower() in {".jpg", ".jpeg"}:
        raise HDRfyError("目录批处理时 OUTPUT_PATH 必须是目录，不能是单个 JPEG 文件")

    items: list[BatchItem] = []
    for source in sources:
        if input_is_file:
            if output_path.suffix.lower() in {".jpg", ".jpeg"}:
                destination = output_path
            else:
                destination = output_path / f"{source.stem}{output_suffix}.jpg"
            relative_stem = Path(source.stem)
        else:
            relative = source.relative_to(input_path)
            destination = output_path / relative.parent / f"{source.stem}{output_suffix}.jpg"
            relative_stem = relative.with_suffix("")

        intermediate_dir = intermediates_root / relative_stem if keep_intermediates else None
        items.append(
            BatchItem(
                source=source,
                output=destination,
                intermediates=intermediate_dir,
            )
        )
    return items
