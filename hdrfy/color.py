"""Colour transforms used by the reconstruction pipeline."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

FloatImage = NDArray[np.float32]

# Linear-light BT.709/sRGB to linear-light BT.2020, both using D65.
# Matrix derived from the standard RGB-to-XYZ and XYZ-to-RGB matrices.
_BT709_TO_BT2020 = np.asarray(
    [
        [0.6274039, 0.3292830, 0.0433131],
        [0.0690973, 0.9195404, 0.0113612],
        [0.0163914, 0.0880133, 0.8955953],
    ],
    dtype=np.float32,
)

_BT2020_TO_BT709 = np.asarray(
    [
        [1.6604910, -0.5876411, -0.0728499],
        [-0.1245505, 1.1328999, -0.0083494],
        [-0.0181508, -0.1005789, 1.1187297],
    ],
    dtype=np.float32,
)

BT2020_LUMA = np.asarray([0.2627, 0.6780, 0.0593], dtype=np.float32)


def srgb_to_linear(rgb: NDArray[np.floating]) -> FloatImage:
    """Decode sRGB code values in [0, 1] into linear-light RGB."""

    x = np.asarray(rgb, dtype=np.float32)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(
        np.float32,
        copy=False,
    )


def linear_to_srgb(rgb: NDArray[np.floating]) -> FloatImage:
    """Encode non-negative linear-light RGB into sRGB code values."""

    x = np.maximum(np.asarray(rgb, dtype=np.float32), 0.0)
    encoded = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)
    return encoded.astype(np.float32, copy=False)


def linear_bt709_to_bt2020(rgb: NDArray[np.floating]) -> FloatImage:
    """Convert linear-light BT.709 RGB to linear-light BT.2020 RGB."""

    x = np.asarray(rgb, dtype=np.float32)
    return np.einsum("...c,dc->...d", x, _BT709_TO_BT2020, optimize=True).astype(
        np.float32,
        copy=False,
    )


def linear_bt2020_to_bt709(rgb: NDArray[np.floating]) -> FloatImage:
    """Convert linear-light BT.2020 RGB to linear-light BT.709 RGB."""

    x = np.asarray(rgb, dtype=np.float32)
    return np.einsum("...c,dc->...d", x, _BT2020_TO_BT709, optimize=True).astype(
        np.float32,
        copy=False,
    )


def bt2020_luminance(rgb: NDArray[np.floating]) -> FloatImage:
    """Return linear BT.2020 relative luminance."""

    x = np.asarray(rgb, dtype=np.float32)
    return np.einsum("...c,c->...", x, BT2020_LUMA, optimize=True).astype(
        np.float32,
        copy=False,
    )


def smoothstep(edge0: float, edge1: float, x: NDArray[np.floating]) -> FloatImage:
    """Vectorised Hermite smoothstep."""

    if edge1 <= edge0:
        raise ValueError("edge1 must be greater than edge0")
    t = np.clip((np.asarray(x, dtype=np.float32) - edge0) / (edge1 - edge0), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32, copy=False)


def hue_preserving_peak_limit(rgb: FloatImage, peak_ratio: float) -> FloatImage:
    """Limit channel peaks by scaling complete pixels rather than clipping channels."""

    if peak_ratio <= 0:
        raise ValueError("peak_ratio must be positive")
    x = np.maximum(np.asarray(rgb, dtype=np.float32), 0.0)
    pixel_peak = np.max(x, axis=-1, keepdims=True)
    scale = np.minimum(1.0, peak_ratio / np.maximum(pixel_peak, 1e-7))
    return (x * scale).astype(np.float32, copy=False)
