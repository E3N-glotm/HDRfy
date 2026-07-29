"""Image decoding and metadata normalisation."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageCms, ImageOps

from .errors import ExistingHDRInputError, UnsupportedInputError

_HEIF_SUFFIXES = {".heic", ".heif", ".avif", ".hif"}
_HDR_TRANSFER_CODES = {16, 18}  # ITU-R BT.2100 PQ and HLG in H.273/NCLX.


@dataclass(slots=True)
class DecodedImage:
    """Normalised SDR source image and metadata required by the encoder."""

    srgb: NDArray[np.float32]
    rgba8: NDArray[np.uint8]
    exif: bytes | None
    source_info: dict[str, Any]

    @property
    def width(self) -> int:
        return int(self.srgb.shape[1])

    @property
    def height(self) -> int:
        return int(self.srgb.shape[0])


def _normalise_array(array: NDArray[np.generic]) -> NDArray[np.float32]:
    arr = np.asarray(array)
    if arr.ndim == 2:
        arr = np.repeat(arr[..., None], 3, axis=-1)
    if arr.ndim != 3 or arr.shape[-1] not in (3, 4):
        raise UnsupportedInputError(f"Unsupported decoded array shape: {arr.shape}")
    arr = arr[..., :3]
    if np.issubdtype(arr.dtype, np.integer):
        max_value = float(np.iinfo(arr.dtype).max)
        return (arr.astype(np.float32) / max_value).astype(np.float32, copy=False)
    arr = arr.astype(np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        raise UnsupportedInputError("Decoded image contains no finite samples")
    if float(np.max(finite)) > 1.0:
        arr = arr / float(np.max(finite))
    return np.clip(arr, 0.0, 1.0).astype(np.float32, copy=False)


def _convert_embedded_icc_to_srgb(image: Image.Image) -> Image.Image:
    icc_profile = image.info.get("icc_profile")
    if not icc_profile:
        return image.convert("RGB")
    try:
        source = ImageCms.ImageCmsProfile(BytesIO(icc_profile))
        target = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(image.convert("RGB"), source, target, outputMode="RGB")
    except Exception:
        # Invalid profiles should not make otherwise decodable photographs unusable.
        return image.convert("RGB")


def _serialise_exif(image: Image.Image) -> bytes | None:
    try:
        exif = image.getexif()
        if not exif:
            return None
        # Orientation has already been applied by ImageOps.exif_transpose.
        exif[274] = 1
        payload = exif.tobytes()
        return payload or None
    except Exception:
        return None


def _decode_with_pillow(path: Path) -> DecodedImage:
    try:
        with Image.open(path) as opened:
            transposed = ImageOps.exif_transpose(opened)
            exif = _serialise_exif(transposed)
            rgb = _convert_embedded_icc_to_srgb(transposed)
            array = np.asarray(rgb, dtype=np.uint8)
            info = {"format": opened.format, "mode": opened.mode, "source": str(path)}
    except Exception as exc:
        raise UnsupportedInputError(f"Unable to decode {path}: {exc}") from exc

    srgb = array.astype(np.float32) / 255.0
    alpha = np.full((*array.shape[:2], 1), 255, dtype=np.uint8)
    rgba8 = np.concatenate([array, alpha], axis=-1)
    return DecodedImage(srgb=srgb, rgba8=rgba8, exif=exif, source_info=info)


def _decode_heif(path: Path, force_sdr: bool) -> DecodedImage:
    try:
        import pillow_heif
    except ImportError as exc:
        raise UnsupportedInputError(
            "HEIF/HEIC/AVIF input requires the pillow-heif package"
        ) from exc

    try:
        # HeifFile itself addresses the declared primary image. Index 0 is not
        # necessarily primary in multi-image HEIF containers.
        heif = pillow_heif.open_heif(path, convert_hdr_to_8bit=False)
        info = dict(heif.info)
        # Standalone open_heif() does not normalise EXIF/XMP orientation. HEIF
        # presentation transforms are already applied by libheif, so reset the
        # informational metadata before embedding it into the output JPEG.
        pillow_heif.set_orientation(info)
        nclx = info.get("nclx_profile") or {}
        transfer = int(nclx.get("transfer_characteristics", -1))
        if transfer in _HDR_TRANSFER_CODES and not force_sdr:
            label = "PQ" if transfer == 16 else "HLG"
            raise ExistingHDRInputError(
                f"{path} is tagged as existing {label} HDR. Use --force-sdr-heif only when the "
                "metadata is known to be wrong."
            )
        array = np.asarray(heif)
        srgb = _normalise_array(array)
        rgb8 = np.rint(srgb * 255.0).clip(0, 255).astype(np.uint8)
        alpha = np.full((*rgb8.shape[:2], 1), 255, dtype=np.uint8)
        rgba8 = np.concatenate([rgb8, alpha], axis=-1)
        exif = info.get("exif")
        if not isinstance(exif, (bytes, bytearray)):
            exif = None
        source_info = {
            "format": path.suffix.lower().lstrip("."),
            "mode": heif.mode,
            "source": str(path),
            "nclx_profile": nclx,
        }
        return DecodedImage(
            srgb=srgb,
            rgba8=rgba8,
            exif=bytes(exif) if exif else None,
            source_info=source_info,
        )
    except ExistingHDRInputError:
        raise
    except Exception as exc:
        raise UnsupportedInputError(f"Unable to decode HEIF-family image {path}: {exc}") from exc


def decode_sdr_image(path: str | Path, *, force_sdr_heif: bool = False) -> DecodedImage:
    """Decode an SDR photograph into sRGB float32 and raw RGBA8888 views."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise UnsupportedInputError(f"Input file does not exist: {source}")
    if source.suffix.lower() in _HEIF_SUFFIXES:
        return _decode_heif(source, force_sdr=force_sdr_heif)
    return _decode_with_pillow(source)


def pad_to_even(image: DecodedImage) -> tuple[DecodedImage, tuple[int, int]]:
    """Edge-pad odd dimensions because some libultrahdr builds mishandle them."""

    pad_h = image.height % 2
    pad_w = image.width % 2
    if not pad_h and not pad_w:
        return image, (0, 0)
    srgb = np.pad(image.srgb, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")
    rgba8 = np.pad(image.rgba8, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")
    return (
        DecodedImage(
            srgb=np.ascontiguousarray(srgb, dtype=np.float32),
            rgba8=np.ascontiguousarray(rgba8, dtype=np.uint8),
            exif=image.exif,
            source_info=dict(image.source_info),
        ),
        (pad_w, pad_h),
    )
