"""End-to-end SDR photograph to optimized pure-Python Ultra HDR conversion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image

from .color import srgb_to_linear
from .config import ConversionConfig
from .encoder import UltraHDREncodeOptions, probe_ultrahdr
from .errors import UnsupportedInputError
from .fast_encoder import encode_ultrahdr_fast
from .io import decode_sdr_image, pad_to_even
from .reconstruct import reconstruct_hdr_from_linear_bt709


@dataclass(frozen=True, slots=True)
class ConversionResult:
    input_path: Path
    output_path: Path
    width: int
    height: int
    padded_width: int
    padded_height: int
    peak_nits: float
    max_content_boost: float
    preset: str
    probe_output: str
    decode_seconds: float = 0.0
    reconstruct_seconds: float = 0.0
    encode_seconds: float = 0.0
    verify_seconds: float = 0.0
    total_seconds: float = 0.0


def _normalise_output_path(path: str | Path) -> Path:
    output = Path(path).expanduser().resolve()
    if output.suffix.lower() not in {".jpg", ".jpeg"}:
        raise UnsupportedInputError("Ultra HDR output must use a .jpg or .jpeg suffix")
    return output


def convert_image(
    input_path: str | Path,
    output_path: str | Path,
    *,
    config: ConversionConfig | None = None,
    keep_intermediates: str | Path | None = None,
    verify: bool = True,
) -> ConversionResult:
    """Convert one SDR image into a displayable Ultra HDR JPEG.

    The SDR transfer-function decode is performed once and reused by both HDR
    reconstruction and gain-map generation. Full-resolution base-JPEG encoding
    is overlapped with the independent gain-map calculations.
    """

    total_started = perf_counter()
    cfg = config or ConversionConfig()
    cfg.validate()
    source = Path(input_path).expanduser().resolve()
    output = _normalise_output_path(output_path)

    stage_started = perf_counter()
    decoded = decode_sdr_image(source, force_sdr_heif=cfg.force_sdr_heif)
    original_width, original_height = decoded.width, decoded.height
    if cfg.pad_to_even and (decoded.width % 2 or decoded.height % 2):
        decoded, _ = pad_to_even(decoded)
    decode_seconds = perf_counter() - stage_started

    stage_started = perf_counter()
    linear_709 = srgb_to_linear(np.clip(decoded.srgb, 0.0, 1.0))
    hdr = reconstruct_hdr_from_linear_bt709(
        linear_709,
        max_content_boost=cfg.max_content_boost,
        preset=cfg.reconstruction_preset,
    )
    if not np.all(np.isfinite(hdr)):
        raise RuntimeError("HDR reconstruction produced non-finite samples")
    reconstruct_seconds = perf_counter() - stage_started

    stage_started = perf_counter()
    encoded = encode_ultrahdr_fast(
        sdr_srgb=decoded.srgb,
        sdr_linear_bt709=linear_709,
        base_rgb8=decoded.rgba8[..., :3],
        hdr_linear_bt2020=hdr,
        output=output,
        exif=decoded.exif if cfg.preserve_exif else None,
        options=UltraHDREncodeOptions(
            width=decoded.width,
            height=decoded.height,
            base_quality=cfg.base_quality,
            gainmap_quality=cfg.gainmap_quality,
            gainmap_scale=cfg.gainmap_scale,
            multi_channel_gainmap=cfg.multi_channel_gainmap,
            max_content_boost=cfg.max_content_boost,
            target_peak_nits=cfg.peak_nits,
        ),
    )
    encode_seconds = perf_counter() - stage_started

    if keep_intermediates:
        work = Path(keep_intermediates).expanduser().resolve()
        work.mkdir(parents=True, exist_ok=True)
        np.save(work / "hdr_intent_linear_bt2020.npy", hdr)
        np.save(work / "sdr_intent_srgb.npy", decoded.srgb)
        Image.fromarray(encoded.gainmap).save(work / "gainmap.png")

    stage_started = perf_counter()
    probe_output = probe_ultrahdr(output) if verify else "verification disabled"
    verify_seconds = perf_counter() - stage_started
    total_seconds = perf_counter() - total_started

    return ConversionResult(
        input_path=source,
        output_path=output,
        width=original_width,
        height=original_height,
        padded_width=decoded.width,
        padded_height=decoded.height,
        peak_nits=cfg.peak_nits,
        max_content_boost=cfg.max_content_boost,
        preset=cfg.preset,
        probe_output=probe_output,
        decode_seconds=decode_seconds,
        reconstruct_seconds=reconstruct_seconds,
        encode_seconds=encode_seconds,
        verify_seconds=verify_seconds,
        total_seconds=total_seconds,
    )
