import struct
from pathlib import Path

import numpy as np
import pytest

from hdrfy.encoder import (
    EOI,
    ISO21496_URN,
    MPF_LABEL,
    SOI,
    UltraHDREncodeOptions,
    create_gainmap,
    encode_ultrahdr,
    inspect_ultrahdr,
    probe_ultrahdr,
)
from hdrfy.errors import UltraHDREncodeError


def _options(width: int, height: int, *, multichannel: bool = True) -> UltraHDREncodeOptions:
    return UltraHDREncodeOptions(
        width=width,
        height=height,
        base_quality=93,
        gainmap_quality=91,
        gainmap_scale=2,
        multi_channel_gainmap=multichannel,
        max_content_boost=4.0,
        target_peak_nits=812.0,
    )


def _parse_primary_mpf(data: bytes) -> tuple[int, int]:
    signature = data.index(MPF_LABEL)
    tiff_base = signature + len(MPF_LABEL)
    first_ifd = struct.unpack(">I", data[tiff_base + 4 : tiff_base + 8])[0]
    ifd = tiff_base + first_ifd
    entry_count = struct.unpack(">H", data[ifd : ifd + 2])[0]
    cursor = ifd + 2
    entries_offset = None
    for _ in range(entry_count):
        tag, _, _, value = struct.unpack(">HHII", data[cursor : cursor + 12])
        cursor += 12
        if tag == 0xB002:
            entries_offset = value
    assert entries_offset is not None
    entries = tiff_base + entries_offset
    primary_size = struct.unpack(">I", data[entries + 4 : entries + 8])[0]
    gainmap_offset = struct.unpack(">I", data[entries + 24 : entries + 28])[0]
    return primary_size, tiff_base + gainmap_offset


def test_pure_python_encoder_writes_consistent_container(tmp_path: Path) -> None:
    height, width = 11, 13
    sdr = np.full((height, width, 3), [0.75, 0.45, 0.12], dtype=np.float32)
    hdr = np.full((height, width, 3), [2.4, 1.1, 0.25], dtype=np.float32)
    output = tmp_path / "out.jpg"

    result = encode_ultrahdr(
        sdr_srgb=sdr,
        hdr_linear_bt2020=hdr,
        output=output,
        options=_options(width, height),
    )

    data = output.read_bytes()
    secondary_soi = data.index(EOI + SOI) + len(EOI)
    primary_size, resolved_gainmap_offset = _parse_primary_mpf(data)
    assert primary_size == secondary_soi
    assert resolved_gainmap_offset == secondary_soi
    assert MPF_LABEL in data
    assert ISO21496_URN in data
    assert result.gainmap.shape == (6, 7, 3)

    inspection = inspect_ultrahdr(output)
    assert inspection.valid
    assert (inspection.primary_width, inspection.primary_height) == (width, height)
    assert (inspection.gainmap_width, inspection.gainmap_height) == (7, 6)
    assert inspection.gainmap_channels == 3
    assert "ISO21496-1=yes" in probe_ultrahdr(output)


def test_single_channel_gainmap_uses_luminance(tmp_path: Path) -> None:
    height, width = 8, 10
    sdr = np.full((height, width, 3), 0.4, dtype=np.float32)
    hdr = np.full((height, width, 3), 1.5, dtype=np.float32)
    options = _options(width, height, multichannel=False)

    gainmap, metadata = create_gainmap(
        sdr_srgb=sdr,
        hdr_linear_bt2020=hdr,
        options=options,
    )

    assert gainmap.shape == (4, 5)
    assert not metadata.is_multichannel
    output = tmp_path / "single.jpg"
    encode_ultrahdr(
        sdr_srgb=sdr,
        hdr_linear_bt2020=hdr,
        output=output,
        options=options,
    )
    assert inspect_ultrahdr(output).gainmap_channels == 1


def test_inspector_rejects_plain_jpeg(tmp_path: Path) -> None:
    source = tmp_path / "plain.jpg"
    source.write_bytes(SOI + EOI)
    with pytest.raises(UltraHDREncodeError):
        inspect_ultrahdr(source)
