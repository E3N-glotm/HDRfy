"""Deterministic inverse tone mapping for single SDR photographs."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from .color import (
    bt2020_luminance,
    hue_preserving_peak_limit,
    linear_bt709_to_bt2020,
    smoothstep,
    srgb_to_linear,
)
from .config import ReconstructionPreset


def _box_blur_axis(
    image: NDArray[np.float32],
    radius: int,
    axis: int,
) -> NDArray[np.float32]:
    """Apply one reflect-padded box-blur pass with bounded temporary memory."""

    padding = [(0, 0)] * image.ndim
    padding[axis] = (radius, radius)
    padded = np.pad(image, padding, mode="reflect")

    leading_shape = list(padded.shape)
    leading_shape[axis] = 1
    leading_zero = np.zeros(leading_shape, dtype=np.float64)
    cumulative = np.concatenate(
        [leading_zero, np.cumsum(padded, axis=axis, dtype=np.float64)],
        axis=axis,
    )

    diameter = 2 * radius + 1
    upper = [slice(None)] * image.ndim
    lower = [slice(None)] * image.ndim
    upper[axis] = slice(diameter, None)
    lower[axis] = slice(None, -diameter)
    total = cumulative[tuple(upper)] - cumulative[tuple(lower)]
    return (total / float(diameter)).astype(np.float32)


def _box_blur(image: NDArray[np.float32], radius: int) -> NDArray[np.float32]:
    """Fast separable square blur with the same reflect-border semantics as before.

    The previous two-dimensional integral image allocated a full float64 padded
    frame and performed two cumulative scans over it. A box kernel is separable,
    so horizontal and vertical one-dimensional cumulative passes produce the
    same result while reducing peak temporary memory and execution time.
    """

    x = np.asarray(image, dtype=np.float32)
    if radius <= 0 or min(x.shape) <= 1:
        return x
    radius = min(radius, min(x.shape) - 1)
    if radius <= 0:
        return x
    horizontal = _box_blur_axis(x, radius, axis=1)
    return _box_blur_axis(horizontal, radius, axis=0)


def reconstruct_hdr_from_linear_bt709(
    linear_bt709: NDArray[np.floating],
    *,
    max_content_boost: float,
    preset: ReconstructionPreset,
) -> NDArray[np.float32]:
    """Create a linear BT.2020 HDR intent from an already-linear BT.709 image."""

    if max_content_boost < 1.0:
        raise ValueError("max_content_boost must be at least 1.0")
    linear_709 = np.asarray(linear_bt709, dtype=np.float32)
    if linear_709.ndim != 3 or linear_709.shape[-1] != 3:
        raise ValueError(f"Expected HxWx3 linear BT.709 image, received {linear_709.shape}")
    if not np.all(np.isfinite(linear_709)):
        raise ValueError("Input image contains NaN or infinity")

    linear_2020 = np.maximum(linear_bt709_to_bt2020(linear_709), 0.0)
    luminance = bt2020_luminance(linear_2020)

    highlight = smoothstep(preset.highlight_start, preset.highlight_end, luminance)
    shaped = np.power(highlight, preset.curve_power, dtype=np.float32)
    boost = 1.0 + (max_content_boost - 1.0) * shaped

    # A low-frequency luminance estimate separates texture from illumination.
    # Only high-luminance detail is gently expanded, keeping flat skies and skin
    # from acquiring aggressive halos or noise.
    radius = max(1, min(24, min(luminance.shape) // 256))
    base = _box_blur(luminance, radius)
    log_detail = np.log2((luminance + 1e-5) / (base + 1e-5))
    detail_gain = np.exp2(np.clip(log_detail, -2.0, 2.0) * preset.detail_strength * highlight)

    target_luminance = luminance * boost * detail_gain
    luminance_scale = target_luminance / np.maximum(luminance, 1e-6)
    hdr = linear_2020 * luminance_scale[..., None]

    # Bright saturated colours easily leave a practical display gamut. Reduce
    # chroma gradually while retaining the luminance expansion and hue.
    target_luma = bt2020_luminance(hdr)
    saturation_scale = 1.0 - preset.saturation_rolloff * highlight
    hdr = target_luma[..., None] + (hdr - target_luma[..., None]) * saturation_scale[..., None]

    hdr = hue_preserving_peak_limit(hdr, max_content_boost)
    return np.ascontiguousarray(hdr, dtype=np.float32)


def reconstruct_hdr_linear_bt2020(
    srgb: NDArray[np.floating],
    *,
    max_content_boost: float,
    preset: ReconstructionPreset,
) -> NDArray[np.float32]:
    """Create a scene-consistent linear BT.2020 HDR intent from sRGB.

    Returned values are relative to SDR reference white. Therefore 1.0 is the
    reference white and ``max_content_boost`` is the target display peak.
    """

    rgb_sdr = np.asarray(srgb, dtype=np.float32)
    if rgb_sdr.ndim != 3 or rgb_sdr.shape[-1] != 3:
        raise ValueError(f"Expected HxWx3 sRGB image, received {rgb_sdr.shape}")
    if not np.all(np.isfinite(rgb_sdr)):
        raise ValueError("Input image contains NaN or infinity")
    linear_709 = srgb_to_linear(np.clip(rgb_sdr, 0.0, 1.0))
    return reconstruct_hdr_from_linear_bt709(
        linear_709,
        max_content_boost=max_content_boost,
        preset=preset,
    )
