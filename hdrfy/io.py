"""Image decoding and metadata normalisation."""

from __future__ import annotations

import warnings
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
_JPEG_EXIF_MAX_BYTES = 65533
_EXIF_IFD_TAG = 34665
_GPS_IFD_TAG = 34853
_ORIENTATION_TAG = 274
_BULKY_EXIF_TAGS = {
    700,
    33723,
    34377,
    37500,
    37510,
}
_SAFE_ROOT_EXIF_TAGS = {
    270,
    271,
    272,
    274,
    282,
    283,
    296,
    305,
    306,
    315,
    33432,
}
_SAFE_CAMERA_EXIF_TAGS = {
    33434,
    33437,
    34850,
    34855,
    36867,
    36868,
    37377,
    37378,
    37380,
    37383,
    37385,
    37386,
    40960,
    40961,
    40962,
    40963,
    41483,
    41728,
    41729,
    41985,
    41986,
    41987,
    41988,
    41989,
    41990,
    42034,
    42035,
    42036,
}


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
        return image.convert("RGB")


def _is_oversized_exif_value(value: object, limit: int = 16384) -> bool:
    if isinstance(value, (bytes, bytearray, memoryview, str)):
        return len(value) > limit
    if isinstance(value, (tuple, list)):
        return any(_is_oversized_exif_value(item, limit) for item in value)
    return False


def _trim_ifd_values(ifd: dict[int, Any]) -> dict[int, Any]:
    return {
        int(tag): value
        for tag, value in ifd.items()
        if int(tag) not in _BULKY_EXIF_TAGS and not _is_oversized_exif_value(value)
    }


def _prepare_exif_for_jpeg(payload: bytes | bytearray | None) -> bytes | None:
    """Return JPEG-safe EXIF while preserving useful photographic metadata."""

    if not payload:
        return None
    raw = bytes(payload)
    if len(raw) <= _JPEG_EXIF_MAX_BYTES:
        return raw

    try:
        parsed = Image.Exif()
        parsed.load(raw)
        parsed[_ORIENTATION_TAG] = 1

        for tag in list(parsed):
            if int(tag) in _BULKY_EXIF_TAGS or _is_oversized_exif_value(parsed[tag]):
                parsed.pop(tag, None)

        for ifd_tag in (_EXIF_IFD_TAG, _GPS_IFD_TAG):
            try:
                compact_ifd = _trim_ifd_values(dict(parsed.get_ifd(ifd_tag)))
            except Exception:
                compact_ifd = {}
            if compact_ifd:
                parsed[ifd_tag] = compact_ifd
            else:
                parsed.pop(ifd_tag, None)

        compact = parsed.tobytes()
        if len(compact) <= _JPEG_EXIF_MAX_BYTES:
            warnings.warn(
                f"EXIF metadata was reduced from {len(raw)} to {len(compact)} bytes "
                "to satisfy the JPEG APP1 limit.",
                RuntimeWarning,
                stacklevel=2,
            )
            return compact

        minimal = Image.Exif()
        for tag in _SAFE_ROOT_EXIF_TAGS:
            if tag in parsed and not _is_oversized_exif_value(parsed[tag], 4096):
                minimal[tag] = parsed[tag]
        minimal[_ORIENTATION_TAG] = 1

        try:
            camera_ifd = {
                tag: value
                for tag, value in dict(parsed.get_ifd(_EXIF_IFD_TAG)).items()
                if tag in _SAFE_CAMERA_EXIF_TAGS and not _is_oversized_exif_value(value, 4096)
            }
        except Exception:
            camera_ifd = {}
        if camera_ifd:
            minimal[_EXIF_IFD_TAG] = camera_ifd

        try:
            gps_ifd = _trim_ifd_values(dict(parsed.get_ifd(_GPS_IFD_TAG)))
        except Exception:
            gps_ifd = {}
        if gps_ifd:
            minimal[_GPS_IFD_TAG] = gps_ifd

        compact = minimal.tobytes()
        if len(compact) <= _JPEG_EXIF_MAX_BYTES:
            warnings.warn(
                f"EXIF metadata was reduced from {len(raw)} to a {len(compact)}-byte "
                "camera/date/GPS subset to satisfy the JPEG APP1 limit.",
                RuntimeWarning,
                stacklevel=2,
            )
            return compact
    except Exception as exc:
        warnings.warn(
            f"Oversized EXIF metadata could not be normalised and will be omitted: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        return None

    warnings.warn(
        f"EXIF metadata ({len(raw)} bytes) still exceeds the JPEG APP1 limit after "
        "normalisation and will be omitted.",
        RuntimeWarning,
        stacklevel=2,
    )
    return None


def _serialise_exif(image: Image.Image) -> bytes | None:
    try:
        exif = image.getexif()
        if not exif:
            return None
        exif[_ORIENTATION_TAG] = 1
        return _prepare_exif_for_jpeg(exif.tobytes())
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
        heif = pillow_heif.open_heif(path, convert_hdr_to_8bit=False)
        info = dict(heif.info)
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
            exif=_prepare_exif_for_jpeg(exif),
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
    """Edge-pad odd dimensions for optional compatibility with older encoders."""

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
