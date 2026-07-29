from pathlib import Path

import pytest

from hdrfy.errors import HDRfyError
from run_hdrfy import build_batch_items, collect_input_files


def test_collect_input_files_recursively_and_skip_output_tree(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_root = input_root / "generated"
    nested = input_root / "album"
    nested.mkdir(parents=True)
    output_root.mkdir(parents=True)

    source_a = input_root / "a.jpg"
    source_b = nested / "b.HEIC"
    ignored = nested / "notes.txt"
    generated = output_root / "old_hdr.jpg"
    for path in (source_a, source_b, ignored, generated):
        path.write_bytes(b"test")

    result = collect_input_files(
        input_root.resolve(),
        output_path=output_root.resolve(),
        recursive=True,
    )

    assert result == sorted([source_a.resolve(), source_b.resolve()])


def test_build_batch_items_preserves_directory_structure(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    intermediates_root = tmp_path / "debug"
    source = input_root / "trip" / "photo.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"test")

    items = build_batch_items(
        input_root.resolve(),
        output_root.resolve(),
        [source.resolve()],
        output_suffix="_hdr",
        keep_intermediates=True,
        intermediates_root=intermediates_root.resolve(),
    )

    assert len(items) == 1
    assert items[0].output == output_root.resolve() / "trip" / "photo_hdr.jpg"
    assert items[0].intermediates == intermediates_root.resolve() / "trip" / "photo"


def test_build_batch_items_accepts_exact_single_file_output(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "custom-name.jpeg"
    source.write_bytes(b"test")

    items = build_batch_items(
        source.resolve(),
        output.resolve(),
        [source.resolve()],
        output_suffix="_hdr",
        keep_intermediates=False,
        intermediates_root=(tmp_path / "debug").resolve(),
    )

    assert items[0].output == output.resolve()
    assert items[0].intermediates is None


def test_directory_input_rejects_single_jpeg_output(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    source = input_root / "source.jpg"
    source.write_bytes(b"test")

    with pytest.raises(HDRfyError, match="OUTPUT_PATH"):
        build_batch_items(
            input_root.resolve(),
            (tmp_path / "wrong.jpg").resolve(),
            [source.resolve()],
            output_suffix="_hdr",
            keep_intermediates=False,
            intermediates_root=(tmp_path / "debug").resolve(),
        )
