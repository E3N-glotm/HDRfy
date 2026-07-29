from pathlib import Path

import numpy as np

from hdrfy.raw import make_rgba16f, write_rgba8888


def test_rgba16f_layout() -> None:
    rgb = np.asarray([[[1.0, 2.0, 3.0]]], dtype=np.float32)
    packed = make_rgba16f(rgb)
    assert packed.shape == (1, 1, 4)
    assert packed.dtype == np.dtype("<f2")
    np.testing.assert_allclose(packed[0, 0], [1.0, 2.0, 3.0, 1.0])


def test_rgba8888_file_size(tmp_path: Path) -> None:
    pixels = np.zeros((7, 9, 4), dtype=np.uint8)
    output = write_rgba8888(tmp_path / "sdr.raw", pixels)
    assert output.stat().st_size == 7 * 9 * 4
