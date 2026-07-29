"""End-to-end SDR photograph to Ultra HDR conversion pipeline."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import ConversionConfig
from .encoder import (
    UltraHDREncodeOptions,
    encode_ultrahdr,
    find_ultrahdr_binary,
    probe_ultrahdr,
)
from .errors import UnsupportedInputError
from .io import decode_sdr_image, pad_to_even
from .raw import write_rgba16f, write_rgba8888
from .reconstruct import reconstruct_hdr_linear_bt2020


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
    ultrahdr_binary: str | Path | None = None,
    keep_intermediates: str | Path | None = None,
    verify: bool = True,
) -> ConversionResult:
    """Convert one SDR image into a displayable Ultra HDR JPEG."""

    cfg = config or ConversionConfig()
    cfg.validate()
    source = Path(input_path).expanduser().resolve()
    output = _normalise_output_path(output_path)
    decoded = decode_sdr_image(source, force_sdr_heif=cfg.force_sdr_heif)
    original_width, original_height = decoded.width, decoded.height

    if decoded.width % 2 or decoded.height % 2:
        if not cfg.pad_to_even:
            raise UnsupportedInputError(
                "Odd image dimensions are disabled because some libultrahdr builds have unsafe "
                "odd-dimension gain-map paths. Enable pad_to_even to duplicate the final row/column."
            )
        decoded, _ = pad_to_even(decoded)

    hdr = reconstruct_hdr_linear_bt2020(
        decoded.srgb,
        max_content_boost=cfg.max_content_boost,
        preset=cfg.reconstruction_preset,
    )
    if not np.all(np.isfinite(hdr)):
        raise RuntimeError("HDR reconstruction produced non-finite samples")

    binary = find_ultrahdr_binary(ultrahdr_binary)
    persistent_dir = Path(keep_intermediates).expanduser().resolve() if keep_intermediates else None
    if persistent_dir:
        persistent_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="hdrfy-") as temporary:
        work = persistent_dir or Path(temporary)
        hdr_raw = write_rgba16f(work / "hdr_intent_rgba16f.raw", hdr)
        sdr_raw = write_rgba8888(work / "sdr_intent_rgba8888.raw", decoded.rgba8)
        exif_path: Path | None = None
        if cfg.preserve_exif and decoded.exif:
            exif_path = work / "source.exif"
            exif_path.write_bytes(decoded.exif)

        encode_ultrahdr(
            binary=binary,
            hdr_raw=hdr_raw,
            sdr_raw=sdr_raw,
            output=output,
            exif_path=exif_path,
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
        if persistent_dir:
            np.save(work / "hdr_intent_linear_bt2020.npy", hdr)

    probe_output = probe_ultrahdr(binary, output) if verify else "verification disabled"
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
    )
