"""Pure-Python Ultra HDR JPEG gain-map generation and container writing.

The project layer uses only Python, NumPy and Pillow. Pillow's installed wheel
contains the JPEG codec, so users do not need a compiler, CMake, Visual Studio,
or an external ``ultrahdr_app`` executable.
"""

from __future__ import annotations

import io
import math
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterable

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from .color import linear_bt2020_to_bt709, srgb_to_linear
from .errors import UltraHDREncodeError

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"
APP1 = b"\xff\xe1"
APP2 = b"\xff\xe2"
SOS_MARKER = 0xDA
MPF_LABEL = b"MPF\x00"
XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
HDRGM_NS = "http://ns.adobe.com/hdr-gain-map/1.0/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
ISO21496_URN = b"urn:iso:std:iso:ts:21496:-1\x00"
BT709_LUMA = np.asarray([0.2126, 0.7152, 0.0722], dtype=np.float32)


@dataclass(frozen=True, slots=True)
class UltraHDREncodeOptions:
    width: int
    height: int
    base_quality: int
    gainmap_quality: int
    gainmap_scale: int
    multi_channel_gainmap: bool
    max_content_boost: float
    target_peak_nits: float


@dataclass(frozen=True, slots=True)
class GainMapMetadata:
    gainmap_min: tuple[float, ...]
    gainmap_max: tuple[float, ...]
    gainmap_gamma: tuple[float, ...]
    baseline_offset: tuple[float, ...]
    alternate_offset: tuple[float, ...]
    baseline_hdr_headroom: float
    alternate_hdr_headroom: float
    is_multichannel: bool
    use_base_colour_space: bool = True
    minimum_version: int = 0
    writer_version: int = 0


@dataclass(frozen=True, slots=True)
class UltraHDREncodeResult:
    metadata: GainMapMetadata
    gainmap: NDArray[np.uint8]
    gainmap_width: int
    gainmap_height: int


@dataclass(frozen=True, slots=True)
class UltraHDRInspection:
    primary_width: int
    primary_height: int
    gainmap_width: int
    gainmap_height: int
    gainmap_channels: int
    has_mpf: bool
    has_gcontainer: bool
    has_hdrgm_xmp: bool
    has_iso21496: bool

    @property
    def valid(self) -> bool:
        return all(
            (
                self.has_mpf,
                self.has_gcontainer,
                self.has_hdrgm_xmp,
                self.has_iso21496,
                self.primary_width > 0,
                self.primary_height > 0,
                self.gainmap_width > 0,
                self.gainmap_height > 0,
            )
        )


def _validate_options(options: UltraHDREncodeOptions) -> None:
    if options.width <= 0 or options.height <= 0:
        raise ValueError("width and height must be positive")
    if not 0 <= options.base_quality <= 100:
        raise ValueError("base_quality must be in [0, 100]")
    if not 0 <= options.gainmap_quality <= 100:
        raise ValueError("gainmap_quality must be in [0, 100]")
    if not 1 <= options.gainmap_scale <= 128:
        raise ValueError("gainmap_scale must be in [1, 128]")
    if options.max_content_boost < 1.0:
        raise ValueError("max_content_boost must be at least 1.0")
    if options.target_peak_nits < 203.0:
        raise ValueError("target_peak_nits must be at least 203")


def _as_float_rgb(image: NDArray[np.floating], name: str) -> NDArray[np.float32]:
    array = np.asarray(image, dtype=np.float32)
    if array.ndim != 3 or array.shape[-1] != 3:
        raise ValueError(f"{name} must have shape HxWx3, received {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} contains NaN or infinity")
    return np.ascontiguousarray(array, dtype=np.float32)


def _resize_uint8(image: NDArray[np.uint8], width: int, height: int) -> NDArray[np.uint8]:
    mode = "L" if image.ndim == 2 else "RGB"
    pil = Image.fromarray(image, mode=mode)
    resized = pil.resize((width, height), resample=Image.Resampling.LANCZOS)
    return np.asarray(resized, dtype=np.uint8)


def create_gainmap(
    *,
    sdr_srgb: NDArray[np.floating],
    hdr_linear_bt2020: NDArray[np.floating],
    options: UltraHDREncodeOptions,
) -> tuple[NDArray[np.uint8], GainMapMetadata]:
    """Create an 8-bit Ultra HDR gain map from SDR and HDR renditions."""

    _validate_options(options)
    sdr = np.clip(_as_float_rgb(sdr_srgb, "sdr_srgb"), 0.0, 1.0)
    hdr_2020 = np.maximum(_as_float_rgb(hdr_linear_bt2020, "hdr_linear_bt2020"), 0.0)
    if sdr.shape != hdr_2020.shape:
        raise ValueError(f"SDR and HDR shapes differ: {sdr.shape} vs {hdr_2020.shape}")
    if (sdr.shape[1], sdr.shape[0]) != (options.width, options.height):
        raise ValueError(
            "options dimensions do not match image arrays: "
            f"{options.width}x{options.height} vs {sdr.shape[1]}x{sdr.shape[0]}"
        )

    # ISO 21496-1 requires the baseline and alternate rendition to use the same
    # colour primaries. The reconstruction works in BT.2020, so convert it back
    # to linear BT.709 before comparing against the linearised sRGB baseline.
    sdr_linear_709 = srgb_to_linear(sdr)
    hdr_linear_709 = np.maximum(linear_bt2020_to_bt709(hdr_2020), 0.0)

    offset = np.float32(1.0 / 64.0)
    maximum = np.float32(options.max_content_boost)
    if options.multi_channel_gainmap:
        ratio = (hdr_linear_709 + offset) / (sdr_linear_709 + offset)
        ratio = np.clip(ratio, 1.0, maximum)
        log2_range = max(math.log2(options.max_content_boost), 1e-6)
        recovery = np.log2(ratio) / log2_range
        encoded = np.rint(np.clip(recovery, 0.0, 1.0) * 255.0).astype(np.uint8)
        channels = 3
    else:
        sdr_luma = np.einsum("...c,c->...", sdr_linear_709, BT709_LUMA, optimize=True)
        hdr_luma = np.einsum("...c,c->...", hdr_linear_709, BT709_LUMA, optimize=True)
        ratio = (hdr_luma + offset) / (sdr_luma + offset)
        ratio = np.clip(ratio, 1.0, maximum)
        log2_range = max(math.log2(options.max_content_boost), 1e-6)
        recovery = np.log2(ratio) / log2_range
        encoded = np.rint(np.clip(recovery, 0.0, 1.0) * 255.0).astype(np.uint8)
        channels = 1

    gainmap_width = max(1, math.ceil(options.width / options.gainmap_scale))
    gainmap_height = max(1, math.ceil(options.height / options.gainmap_scale))
    if (gainmap_width, gainmap_height) != (options.width, options.height):
        encoded = _resize_uint8(encoded, gainmap_width, gainmap_height)

    log2_max = math.log2(options.max_content_boost)
    values = (0.0, 0.0, 0.0) if channels == 3 else (0.0,)
    maxima = (log2_max, log2_max, log2_max) if channels == 3 else (log2_max,)
    gamma = (1.0, 1.0, 1.0) if channels == 3 else (1.0,)
    offsets = (float(offset), float(offset), float(offset)) if channels == 3 else (float(offset),)
    metadata = GainMapMetadata(
        gainmap_min=values,
        gainmap_max=maxima,
        gainmap_gamma=gamma,
        baseline_offset=offsets,
        alternate_offset=offsets,
        baseline_hdr_headroom=0.0,
        alternate_hdr_headroom=log2_max,
        is_multichannel=channels == 3,
    )
    return np.ascontiguousarray(encoded), metadata


def _jpeg_bytes(
    image: NDArray[np.uint8],
    *,
    quality: int,
    exif: bytes | None = None,
) -> bytes:
    array = np.asarray(image, dtype=np.uint8)
    if array.ndim == 2:
        mode = "L"
    elif array.ndim == 3 and array.shape[-1] == 1:
        array = array[..., 0]
        mode = "L"
    elif array.ndim == 3 and array.shape[-1] == 3:
        mode = "RGB"
    else:
        raise ValueError(f"JPEG input must be L or RGB, received {array.shape}")
    output = io.BytesIO()
    kwargs: dict[str, object] = {
        "format": "JPEG",
        "quality": quality,
        "subsampling": 0,
        "optimize": False,
    }
    if exif:
        kwargs["exif"] = exif
    Image.fromarray(array, mode=mode).save(output, **kwargs)
    return output.getvalue()


def _segment(marker: bytes, payload: bytes) -> bytes:
    if len(marker) != 2 or marker[0] != 0xFF:
        raise ValueError("invalid JPEG marker")
    length = len(payload) + 2
    if length > 0xFFFF:
        raise UltraHDREncodeError(f"JPEG metadata segment is too large: {length} bytes")
    return marker + length.to_bytes(2, "big") + payload


def _format_float(value: float) -> str:
    if abs(value) < 1e-9:
        return "0"
    return f"{value:.9f}".rstrip("0").rstrip(".")


def _xmp_sequence(tag: str, values: Iterable[float]) -> str:
    items = "".join(f"<rdf:li>{_format_float(value)}</rdf:li>" for value in values)
    return f"<hdrgm:{tag}><rdf:Seq>{items}</rdf:Seq></hdrgm:{tag}>"


def _gainmap_xmp(metadata: GainMapMetadata) -> bytes:
    def scalar(values: tuple[float, ...]) -> str | None:
        if len(values) == 1:
            return _format_float(values[0])
        if max(values) - min(values) < 1e-8:
            return _format_float(values[0])
        return None

    fields = {
        "GainMapMin": metadata.gainmap_min,
        "GainMapMax": metadata.gainmap_max,
        "Gamma": metadata.gainmap_gamma,
        "OffsetSDR": metadata.baseline_offset,
        "OffsetHDR": metadata.alternate_offset,
    }
    attributes: list[str] = [
        'hdrgm:Version="1.0"',
        f'hdrgm:HDRCapacityMin="{_format_float(metadata.baseline_hdr_headroom)}"',
        f'hdrgm:HDRCapacityMax="{_format_float(metadata.alternate_hdr_headroom)}"',
        'hdrgm:BaseRenditionIsHDR="False"',
    ]
    children: list[str] = []
    for name, values in fields.items():
        value = scalar(values)
        if value is None:
            children.append(_xmp_sequence(name, values))
        else:
            attributes.append(f'hdrgm:{name}="{value}"')

    xml = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        f'<rdf:RDF xmlns:rdf="{RDF_NS}">'
        f'<rdf:Description xmlns:hdrgm="{HDRGM_NS}" {" ".join(attributes)}>'
        f'{"".join(children)}'
        "</rdf:Description>"
        "</rdf:RDF>"
        "</x:xmpmeta>"
    )
    return XMP_HEADER + xml.encode("utf-8")


def _gcontainer_xmp(gainmap_length: int) -> bytes:
    xml = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        f'<rdf:RDF xmlns:rdf="{RDF_NS}">'
        '<rdf:Description xmlns:Container="http://ns.google.com/photos/1.0/container/" '
        'xmlns:Item="http://ns.google.com/photos/1.0/container/item/" '
        f'xmlns:hdrgm="{HDRGM_NS}" hdrgm:Version="1.0">'
        "<Container:Directory><rdf:Seq>"
        '<rdf:li rdf:parseType="Resource"><Container:Item '
        'Item:Semantic="Primary" Item:Mime="image/jpeg"/></rdf:li>'
        '<rdf:li rdf:parseType="Resource"><Container:Item '
        f'Item:Semantic="GainMap" Item:Mime="image/jpeg" Item:Length="{gainmap_length}"/>'
        "</rdf:li></rdf:Seq></Container:Directory>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    return XMP_HEADER + xml.encode("utf-8")


def _mpf_payload(primary_size: int, gainmap_size: int, gainmap_offset: int) -> bytes:
    entries = bytearray()
    entries.extend(struct.pack(">IIII", 0x00030000, primary_size, 0, 0))
    entries.extend(struct.pack(">IIII", 0x00050000, gainmap_size, gainmap_offset, 0))

    ifd = bytearray()
    ifd.extend(struct.pack(">HHI", 0xB000, 7, 4))
    ifd.extend(b"0100")
    ifd.extend(struct.pack(">HHII", 0xB001, 4, 1, 2))
    ifd.extend(struct.pack(">HHII", 0xB002, 7, 32, 50))

    payload = bytearray(MPF_LABEL)
    payload.extend(b"MM\x00\x2a")
    payload.extend(struct.pack(">I", 8))
    payload.extend(struct.pack(">H", 3))
    payload.extend(ifd)
    payload.extend(struct.pack(">I", 0))
    payload.extend(entries)
    return bytes(payload)


def _minimal_mpf_payload(num_images: int) -> bytes:
    ifd = bytearray()
    ifd.extend(struct.pack(">HHI", 0xB000, 7, 4))
    ifd.extend(b"0100")
    ifd.extend(struct.pack(">HHII", 0xB001, 4, 1, int(num_images)))
    payload = bytearray(MPF_LABEL)
    payload.extend(b"MM\x00\x2a")
    payload.extend(struct.pack(">I", 8))
    payload.extend(struct.pack(">H", 2))
    payload.extend(ifd)
    payload.extend(struct.pack(">I", 0))
    return bytes(payload)


def _rational(value: float, *, signed: bool) -> tuple[int, int]:
    fraction = Fraction(float(value)).limit_denominator(100000)
    numerator, denominator = fraction.numerator, fraction.denominator
    if signed:
        numerator = max(-(2**31), min(2**31 - 1, numerator))
    else:
        numerator = max(0, min(2**32 - 1, numerator))
    denominator = max(1, min(2**32 - 1, denominator))
    return int(numerator), int(denominator)


def _iso_gainmap_payload(metadata: GainMapMetadata) -> bytes:
    output = bytearray(ISO21496_URN)
    flags = ((1 if metadata.is_multichannel else 0) << 7) | (
        (1 if metadata.use_base_colour_space else 0) << 6
    )
    output.extend(struct.pack(">HHB", metadata.minimum_version, metadata.writer_version, flags))
    for headroom in (metadata.baseline_hdr_headroom, metadata.alternate_hdr_headroom):
        numerator, denominator = _rational(headroom, signed=False)
        output.extend(struct.pack(">II", numerator, denominator))

    count = 3 if metadata.is_multichannel else 1
    for index in range(count):
        for values, signed in (
            (metadata.gainmap_min, True),
            (metadata.gainmap_max, True),
            (metadata.gainmap_gamma, False),
            (metadata.baseline_offset, True),
            (metadata.alternate_offset, True),
        ):
            value = values[index if len(values) > 1 else 0]
            numerator, denominator = _rational(value, signed=signed)
            fmt = ">iI" if signed else ">II"
            output.extend(struct.pack(fmt, numerator, denominator))
    return bytes(output)


def _primary_iso_payload() -> bytes:
    return ISO21496_URN + struct.pack(">HH", 0, 0)


def _split_concatenated_jpegs(data: bytes) -> tuple[bytes, bytes]:
    boundary = data.find(EOI + SOI)
    if boundary < 0:
        raise UltraHDREncodeError("secondary gain-map JPEG was not found")
    return data[: boundary + 2], data[boundary + 2 :]


def _find_sos_offset(jpeg: bytes) -> int:
    """Return the byte offset of the JPEG SOS marker without scanning entropy data."""

    if not jpeg.startswith(SOI):
        raise UltraHDREncodeError("JPEG stream is missing SOI")
    position = 2
    while position + 4 <= len(jpeg):
        if jpeg[position] != 0xFF:
            raise UltraHDREncodeError("invalid JPEG marker sequence before SOS")
        marker = jpeg[position + 1]
        while marker == 0xFF:
            position += 1
            marker = jpeg[position + 1]
        if marker == SOS_MARKER:
            return position
        if marker == 0xD9:
            break
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            position += 2
            continue
        segment_length = int.from_bytes(jpeg[position + 2 : position + 4], "big")
        if segment_length < 2:
            raise UltraHDREncodeError("invalid JPEG segment length")
        position += 2 + segment_length
    raise UltraHDREncodeError("JPEG SOS marker was not found")


def _insert_before_sos(jpeg: bytes, segments: bytes) -> bytes:
    offset = _find_sos_offset(jpeg)
    return jpeg[:offset] + segments + jpeg[offset:]


def encode_ultrahdr(
    *,
    sdr_srgb: NDArray[np.floating],
    hdr_linear_bt2020: NDArray[np.floating],
    output: str | Path,
    options: UltraHDREncodeOptions,
    exif: bytes | None = None,
) -> UltraHDREncodeResult:
    """Encode SDR/HDR renditions as a backward-compatible Ultra HDR JPEG."""

    try:
        _validate_options(options)
        sdr = np.clip(_as_float_rgb(sdr_srgb, "sdr_srgb"), 0.0, 1.0)
        gainmap, metadata = create_gainmap(
            sdr_srgb=sdr,
            hdr_linear_bt2020=hdr_linear_bt2020,
            options=options,
        )
        base_u8 = np.rint(sdr * 255.0).astype(np.uint8)
        gainmap_raw = _jpeg_bytes(gainmap, quality=options.gainmap_quality)
        gainmap_segments = (
            _segment(APP1, _gainmap_xmp(metadata))
            + _segment(APP2, _iso_gainmap_payload(metadata))
            + _segment(APP2, _minimal_mpf_payload(2))
        )
        gainmap_final = _insert_before_sos(gainmap_raw, gainmap_segments)

        primary_raw = _jpeg_bytes(base_u8, quality=options.base_quality, exif=exif)
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
        temporary.write_bytes(primary_final + gainmap_final)
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
        raise UltraHDREncodeError(f"pure-Python Ultra HDR encoding failed: {exc}") from exc


def _xmp_has_gainmap_metadata(gainmap_jpeg: bytes) -> bool:
    start = gainmap_jpeg.find(XMP_HEADER)
    if start < 0:
        return False
    xml_start = gainmap_jpeg.find(b"<", start)
    xml_end = gainmap_jpeg.find(b"</x:xmpmeta>", xml_start)
    if xml_start < 0 or xml_end < 0:
        return False
    xml_end += len(b"</x:xmpmeta>")
    try:
        root = ET.fromstring(gainmap_jpeg[xml_start:xml_end].decode("utf-8"))
    except (UnicodeDecodeError, ET.ParseError):
        return False
    description = root.find(f".//{{{RDF_NS}}}Description")
    if description is None:
        return False
    return description.attrib.get(f"{{{HDRGM_NS}}}Version") == "1.0"


def inspect_ultrahdr(image: str | Path) -> UltraHDRInspection:
    source = Path(image).expanduser().resolve()
    try:
        data = source.read_bytes()
        primary, gainmap = _split_concatenated_jpegs(data)
        with Image.open(io.BytesIO(primary)) as base_image:
            primary_width, primary_height = base_image.size
            base_image.verify()
        with Image.open(io.BytesIO(gainmap)) as gain_image:
            gainmap_width, gainmap_height = gain_image.size
            gainmap_channels = 1 if gain_image.mode == "L" else len(gain_image.getbands())
            gain_image.verify()
    except Exception as exc:
        raise UltraHDREncodeError(f"invalid Ultra HDR JPEG {source}: {exc}") from exc

    has_gcontainer = b'Item:Semantic="GainMap"' in primary and b"hdrgm:Version=\"1.0\"" in primary
    return UltraHDRInspection(
        primary_width=primary_width,
        primary_height=primary_height,
        gainmap_width=gainmap_width,
        gainmap_height=gainmap_height,
        gainmap_channels=gainmap_channels,
        has_mpf=MPF_LABEL in primary,
        has_gcontainer=has_gcontainer,
        has_hdrgm_xmp=_xmp_has_gainmap_metadata(gainmap),
        has_iso21496=ISO21496_URN in primary and ISO21496_URN in gainmap,
    )


def probe_ultrahdr(image: str | Path) -> str:
    inspection = inspect_ultrahdr(image)
    if not inspection.valid:
        raise UltraHDREncodeError(f"Ultra HDR structural validation failed: {inspection}")
    return (
        "gainmap metadata: valid; "
        f"primary={inspection.primary_width}x{inspection.primary_height}; "
        f"gainmap={inspection.gainmap_width}x{inspection.gainmap_height}x"
        f"{inspection.gainmap_channels}; MPF=yes; GContainer=yes; ISO21496-1=yes"
    )
