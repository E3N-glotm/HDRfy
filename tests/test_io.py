import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from hdrfy.errors import ExistingHDRInputError
from hdrfy.io import _prepare_exif_for_jpeg, decode_sdr_image


class _FakeHeifFile:
    mode = "RGB;16"

    def __init__(self, transfer: int = 13, exif: bytes = b"Exif\x00\x00fake") -> None:
        self.info = {
            "primary": True,
            "exif": exif,
            "nclx_profile": {
                "color_primaries": 1,
                "transfer_characteristics": transfer,
                "matrix_coefficients": 0,
                "full_range_flag": True,
            },
        }
        self.array = np.full((3, 5, 3), 32768, dtype=np.uint16)

    def __array__(self, dtype=None, copy=None):  # NumPy 1.x and 2.x compatible
        result = self.array if dtype is None else self.array.astype(dtype)
        return result.copy() if copy else result


def test_heif_decoder_uses_declared_primary_image(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "input.heic"
    source.write_bytes(b"fake")
    primary = _FakeHeifFile()
    orientation_calls: list[dict] = []
    module = SimpleNamespace(
        open_heif=lambda *_args, **_kwargs: primary,
        set_orientation=lambda info: orientation_calls.append(info),
    )
    monkeypatch.setitem(sys.modules, "pillow_heif", module)

    decoded = decode_sdr_image(source)

    assert decoded.srgb.shape == (3, 5, 3)
    assert decoded.rgba8.shape == (3, 5, 4)
    assert decoded.source_info["mode"] == "RGB;16"
    assert orientation_calls


def test_heif_decoder_rejects_existing_pq_hdr(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "input.heic"
    source.write_bytes(b"fake")
    module = SimpleNamespace(
        open_heif=lambda *_args, **_kwargs: _FakeHeifFile(transfer=16),
        set_orientation=lambda _info: None,
    )
    monkeypatch.setitem(sys.modules, "pillow_heif", module)

    with pytest.raises(ExistingHDRInputError, match="PQ HDR"):
        decode_sdr_image(source)


def test_oversized_exif_is_compacted_for_jpeg() -> None:
    exif = Image.Exif()
    exif[271] = "HDRfy Camera"
    exif[272] = "HDRfy Model"
    exif[274] = 6
    exif[37500] = b"maker-note" * 9000
    raw = exif.tobytes()
    assert len(raw) > 65533

    with pytest.warns(RuntimeWarning, match="reduced"):
        compact = _prepare_exif_for_jpeg(raw)

    assert compact is not None
    assert len(compact) <= 65533
    parsed = Image.Exif()
    parsed.load(compact)
    assert parsed[271] == "HDRfy Camera"
    assert parsed[272] == "HDRfy Model"
    assert parsed[274] == 1
    assert 37500 not in parsed


def test_heif_decoder_compacts_oversized_exif(monkeypatch, tmp_path: Path) -> None:
    exif = Image.Exif()
    exif[271] = "HDRfy Camera"
    exif[37500] = b"private" * 10000
    source = tmp_path / "input.heic"
    source.write_bytes(b"fake")
    module = SimpleNamespace(
        open_heif=lambda *_args, **_kwargs: _FakeHeifFile(exif=exif.tobytes()),
        set_orientation=lambda _info: None,
    )
    monkeypatch.setitem(sys.modules, "pillow_heif", module)

    with pytest.warns(RuntimeWarning, match="reduced"):
        decoded = decode_sdr_image(source)

    assert decoded.exif is not None
    assert len(decoded.exif) <= 65533
