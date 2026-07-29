"""Optimized pure-Python Ultra HDR encoding path.

This module preserves the byte-level container format implemented by
``hdrfy.encoder`` while removing duplicate colour conversion and overlapping
full-resolution base-JPEG compression with gain-map generation.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from .color import linear_bt2020_to_bt709, srgb_to_linear
from .encoder import (
    APP1,
    APP2,
    GainMapMetadata,
    UltraHDREncodeOptions,
    UltraHDREncodeResult,
    _as_float_rgb,
    _find_sos_offset,
    _gainmap_xmp,
    _gcontainer_xmp,
    _insert_before_sos,
    _iso_gainmap_payload,
    _jpeg_bytes,
    _minimal_mpf_payload,
    _mpf_payload,
    _primary_iso_payload,
    _segment,
    _validate_options,
)
from .errors import UltraHDREncodeError


def _as_uint8_rgb(image: NDArray[np.generic], width: int, height: int) -> NDArray[np.uint8]:
    array = np.asarray(image)
    if array.ndim != 3 or array.shape[-1] != 3:
        raise ValueError(f"base_rgb8 must have shape HxWx3, received {array.shape}")
    if array.shape[:2] != (height, width):
        raise ValueError(
            "base_rgb8 dimensions do not match encode options: "
            f"{array.shape[1]}x{array.shape[0]} vs {width}x{height}"
        )
    if array.dtype != np.uint8:
        raise ValueError(f"base_rgb8 must be uint8, received {array.dtype}")
    return np.ascontiguousarray(array)


def create_gainmap_from_linear(
    *,
    sdr_linear_bt709: NDArray[np.floating],
    hdr_linear_bt2020: NDArray[np.floating],
    options: UltraHDREncodeOptions,
) -> tuple[NDArray[np.uint8], GainMapMetadata]:
    """Create a gain map while reusing the pipeline's linear BT.709 SDR array."""

    _validate_options(options)
    sdr_linear = np.maximum(_as_float_rgb(sdr_linear_bt709, "sdr_linear_bt709"), 0.0)
    hdr_2020 = np.maximum(_as_float_rgb(hdr_linear_bt2020, "hdr_linear_bt2020"), 0.0)
    if sdr_linear.shape != hdr_2020.shape:
        raise ValueError(
            f"SDR linear and HDR shapes differ: {sdr_linear.shape} vs {hdr_2020.shape}"
        )
    if (sdr_linear.shape[1], sdr_linear.shape[0]) != (options.width, options.height):
        raise ValueError(
            "options dimensions do not match image arrays: "
            f"{options.width}x{options.height} vs "
            f"{sdr_linear.shape[1]}x{sdr_linear.shape[0]}"
        )

    hdr_linear_709 = np.maximum(linear_bt2020_to_bt709(hdr_2020), 0.0)
    offset = np.float32(1.0 / 64.0)
    maximum = np.float32(options.max_content_boost)
    log2_range = max(np.log2(options.max_content_boost), 1e-6)

    if options.multi_channel_gainmap:
        ratio = (hdr_linear_709 + offset) / (sdr_linear + offset)
        np.clip(ratio, 1.0, maximum, out=ratio)
        recovery = np.log2(ratio) / log2_range
        encoded = np.rint(np.clip(recovery, 0.0, 1.0) * 255.0).astype(np.uint8)
        channels = 3
    else:
        luma_weights = np.asarray([0.2126, 0.7152, 0.0722], dtype=np.float32)
        sdr_luma = np.einsum("...c,c->...", sdr_linear, luma_weights, optimize=True)
        hdr_luma = np.einsum("...c,c->...", hdr_linear_709, luma_weights, optimize=True)
        ratio = (hdr_luma + offset) / (sdr_luma + offset)
        np.clip(ratio, 1.0, maximum, out=ratio)
        recovery = np.log2(ratio) / log2_range
        encoded = np.rint(np.clip(recovery, 0.0, 1.0) * 255.0).astype(np.uint8)
        channels = 1

    gainmap_width = max(1, (options.width + options.gainmap_scale - 1) // options.gainmap_scale)
    gainmap_height = max(
        1,
        (options.height + options.gainmap_scale - 1) // options.gainmap_scale,
    )
    if (gainmap_width, gainmap_height) != (options.width, options.height):
        from .encoder import _resize_uint8

        encoded = _resize_uint8(encoded, gainmap_width, gainmap_height)

    log2_max = float(np.log2(options.max_content_boost))
    minima = (0.0, 0.0, 0.0) if channels == 3 else (0.0,)
    maxima = (log2_max, log2_max, log2_max) if channels == 3 else (log2_max,)
    gamma = (1.0, 1.0, 1.0) if channels == 3 else (1.0,)
    offsets = (float(offset), float(offset), float(offset)) if channels == 3 else (float(offset),)
    metadata = GainMapMetadata(
        gainmap_min=minima,
        gainmap_max=maxima,
        gainmap_gamma=gamma,
        baseline_offset=offsets,
        alternate_offset=offsets,
        baseline_hdr_headroom=0.0,
        alternate_hdr_headroom=log2_max,
        is_multichannel=channels == 3,
    )
    return np.ascontiguousarray(encoded), metadata


def encode_ultrahdr_fast(
    *,
    sdr_srgb: NDArray[np.floating],
    sdr_linear_bt709: NDArray[np.floating] | None,
    base_rgb8: NDArray[np.generic] | None,
    hdr_linear_bt2020: NDArray[np.floating],
    output: str | Path,
    options: UltraHDREncodeOptions,
    exif: bytes | None = None,
) -> UltraHDREncodeResult:
    """Encode Ultra HDR while overlapping independent JPEG and gain-map work."""

    try:
        _validate_options(options)
        sdr = np.clip(_as_float_rgb(sdr_srgb, "sdr_srgb"), 0.0, 1.0)
        if sdr_linear_bt709 is None:
            linear_709 = srgb_to_linear(sdr)
        else:
            linear_709 = _as_float_rgb(sdr_linear_bt709, "sdr_linear_bt709")
        if base_rgb8 is None:
            base_u8 = np.rint(sdr * 255.0).astype(np.uint8)
        else:
            base_u8 = _as_uint8_rgb(base_rgb8, options.width, options.height)

        # libjpeg compression is independent of the HDR/gain-map calculation and
        # releases the GIL. Running it in one worker overlaps that full-resolution
        # compression with NumPy work in the calling thread without process-copy cost.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="hdrfy-jpeg") as executor:
            primary_future = executor.submit(
                _jpeg_bytes,
                base_u8,
                quality=options.base_quality,
                exif=exif,
            )
            gainmap, metadata = create_gainmap_from_linear(
                sdr_linear_bt709=linear_709,
                hdr_linear_bt2020=hdr_linear_bt2020,
                options=options,
            )
            gainmap_raw = _jpeg_bytes(gainmap, quality=options.gainmap_quality)
            primary_raw = primary_future.result()

        gainmap_segments = (
            _segment(APP1, _gainmap_xmp(metadata))
            + _segment(APP2, _iso_gainmap_payload(metadata))
            + _segment(APP2, _minimal_mpf_payload(2))
        )
        gainmap_final = _insert_before_sos(gainmap_raw, gainmap_segments)

        gcontainer = _segment(APP1, _gcontainer_xmp(len(gainmap_final)))
        primary_iso = _segment(APP2, _primary_iso_payload())
        temporary_mpf = _segment(APP2, _mpf_payload(0, len(gainmap_final), 0))
        primary_sos_offset = _find_sos_offset(primary_raw)
        total_primary_length = (
            len(primary_raw) + len(gcontainer) + len(primary_iso) + len(temporary_mpf)
        )
        mpf_marker_offset = primary_sos_offset + len(gcontainer) + len(primary_iso)
        mpf_header_file_offset = mpf_marker_offset + 8
        gainmap_relative_offset = total_primary_length - mpf_header_file_offset
        final_mpf = _segment(
            APP2,
            _mpf_payload(total_primary_length, len(gainmap_final), gainmap_relative_offset),
        )
        primary_final = (
            primary_raw[:primary_sos_offset]
            + gcontainer
            + primary_iso
            + final_mpf
            + primary_raw[primary_sos_offset:]
        )

        destination = Path(output).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        with temporary.open("wb") as stream:
            stream.write(primary_final)
            stream.write(gainmap_final)
        temporary.replace(destination)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise UltraHDREncodeError(f"output was not created: {destination}")
        return UltraHDREncodeResult(
            metadata=metadata,
            gainmap=gainmap,
            gainmap_width=int(gainmap.shape[1]),
            gainmap_height=int(gainmap.shape[0]),
        )
    except UltraHDREncodeError:
        raise
    except Exception as exc:
        raise UltraHDREncodeError(f"optimized Ultra HDR encoding failed: {exc}") from exc
