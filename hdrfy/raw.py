"""Raw intent serialisation expected by the libultrahdr sample application."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray


def make_rgba16f(hdr_rgb: NDArray[np.floating]) -> NDArray[np.float16]:
    """Pack linear RGB into little-endian RGBA half-float pixels."""

    rgb = np.asarray(hdr_rgb, dtype=np.float32)
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError(f"Expected HxWx3 HDR RGB, received {rgb.shape}")
    alpha = np.ones((*rgb.shape[:2], 1), dtype=np.float32)
    rgba = np.concatenate([rgb, alpha], axis=-1)
    return np.ascontiguousarray(rgba.astype(np.dtype("<f2"), copy=False))


def write_rgba16f(path: str | Path, hdr_rgb: NDArray[np.floating]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    make_rgba16f(hdr_rgb).tofile(destination)
    return destination


def write_rgba8888(path: str | Path, rgba8: NDArray[np.uint8]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.asarray(rgba8)
    if pixels.ndim != 3 or pixels.shape[-1] != 4 or pixels.dtype != np.uint8:
        raise ValueError("SDR raw intent must be an HxWx4 uint8 RGBA array")
    np.ascontiguousarray(pixels).tofile(destination)
    return destination
