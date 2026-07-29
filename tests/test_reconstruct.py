import numpy as np

from hdrfy.color import srgb_to_linear
from hdrfy.config import PRESETS
from hdrfy.reconstruct import (
    _box_blur,
    reconstruct_hdr_from_linear_bt709,
    reconstruct_hdr_linear_bt2020,
)


def _reference_integral_box_blur(image: np.ndarray, radius: int) -> np.ndarray:
    """Reference implementation used before the separable optimization."""

    x = np.asarray(image, dtype=np.float32)
    radius = min(radius, min(x.shape) - 1)
    padded = np.pad(x, ((radius, radius), (radius, radius)), mode="reflect")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant")
    integral = np.cumsum(
        np.cumsum(integral, axis=0, dtype=np.float64),
        axis=1,
        dtype=np.float64,
    )
    diameter = 2 * radius + 1
    total = (
        integral[diameter:, diameter:]
        - integral[:-diameter, diameter:]
        - integral[diameter:, :-diameter]
        + integral[:-diameter, :-diameter]
    )
    return (total / float(diameter * diameter)).astype(np.float32)


def test_box_blur_preserves_constant_image() -> None:
    image = np.full((19, 23), 0.37, dtype=np.float32)
    blurred = _box_blur(image, radius=3)
    np.testing.assert_allclose(blurred, image, atol=1e-6)


def test_separable_box_blur_matches_integral_reference() -> None:
    random = np.random.default_rng(42)
    image = random.random((73, 91), dtype=np.float32)
    expected = _reference_integral_box_blur(image, radius=7)
    actual = _box_blur(image, radius=7)
    np.testing.assert_allclose(actual, expected, atol=1e-6, rtol=1e-6)


def test_reconstruction_preserves_shape_and_bounds() -> None:
    gradient = np.linspace(0.0, 1.0, 64, dtype=np.float32)
    image = np.broadcast_to(gradient[None, :, None], (48, 64, 3)).copy()
    maximum = 1000.0 / 203.0
    hdr = reconstruct_hdr_linear_bt2020(
        image,
        max_content_boost=maximum,
        preset=PRESETS["natural"],
    )
    assert hdr.shape == image.shape
    assert hdr.dtype == np.float32
    assert np.isfinite(hdr).all()
    assert float(hdr.min()) >= 0.0
    assert float(hdr.max()) <= maximum + 1e-5


def test_linear_input_path_matches_srgb_wrapper() -> None:
    random = np.random.default_rng(7)
    image = random.random((37, 53, 3), dtype=np.float32)
    expected = reconstruct_hdr_linear_bt2020(
        image,
        max_content_boost=4.0,
        preset=PRESETS["natural"],
    )
    actual = reconstruct_hdr_from_linear_bt709(
        srgb_to_linear(image),
        max_content_boost=4.0,
        preset=PRESETS["natural"],
    )
    np.testing.assert_allclose(actual, expected, atol=1e-6, rtol=1e-6)


def test_dark_tones_change_less_than_highlights() -> None:
    image = np.zeros((16, 16, 3), dtype=np.float32)
    image[:, :8] = 0.15
    image[:, 8:] = 0.95
    hdr = reconstruct_hdr_linear_bt2020(
        image,
        max_content_boost=4.0,
        preset=PRESETS["natural"],
    )
    dark_ratio = float(np.mean(hdr[:, :8]) / np.mean(image[:, :8]))
    bright_ratio = float(np.mean(hdr[:, 8:]) / np.mean(image[:, 8:]))
    assert bright_ratio > dark_ratio
