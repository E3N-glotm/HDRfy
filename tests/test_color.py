import numpy as np

from hdrfy.color import (
    bt2020_luminance,
    linear_bt709_to_bt2020,
    linear_bt2020_to_bt709,
    linear_to_srgb,
    srgb_to_linear,
)


def test_srgb_round_trip() -> None:
    values = np.linspace(0.0, 1.0, 257, dtype=np.float32)
    rgb = np.stack([values, values[::-1], np.full_like(values, 0.5)], axis=-1)
    restored = linear_to_srgb(srgb_to_linear(rgb))
    np.testing.assert_allclose(restored, rgb, atol=2e-6, rtol=0)


def test_bt709_bt2020_round_trip() -> None:
    rng = np.random.default_rng(3)
    rgb = rng.random((32, 24, 3), dtype=np.float32)
    restored = linear_bt2020_to_bt709(linear_bt709_to_bt2020(rgb))
    np.testing.assert_allclose(restored, rgb, atol=3e-6, rtol=0)


def test_bt2020_white_luminance_is_one() -> None:
    white = np.ones((1, 1, 3), dtype=np.float32)
    np.testing.assert_allclose(bt2020_luminance(white), 1.0, atol=1e-7)
