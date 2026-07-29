from pathlib import Path

from PIL import Image

from hdrfy.config import ConversionConfig
from hdrfy.encoder import inspect_ultrahdr
from hdrfy.pipeline import convert_image


def test_end_to_end_pure_python_pipeline_preserves_odd_size(tmp_path: Path) -> None:
    source = tmp_path / "input.png"
    Image.new("RGB", (9, 7), (230, 180, 40)).save(source)
    output = tmp_path / "output.jpg"
    intermediates = tmp_path / "debug"

    result = convert_image(
        source,
        output,
        config=ConversionConfig(preserve_exif=False),
        keep_intermediates=intermediates,
    )

    assert output.is_file()
    assert (result.width, result.height) == (9, 7)
    assert (result.padded_width, result.padded_height) == (9, 7)
    assert "gainmap metadata: valid" in result.probe_output
    inspection = inspect_ultrahdr(output)
    assert inspection.valid
    assert (inspection.primary_width, inspection.primary_height) == (9, 7)
    assert (intermediates / "hdr_intent_linear_bt2020.npy").is_file()
    assert (intermediates / "sdr_intent_srgb.npy").is_file()
    assert (intermediates / "gainmap.png").is_file()


def test_padding_remains_an_explicit_compatibility_option(tmp_path: Path) -> None:
    source = tmp_path / "input.png"
    Image.new("RGB", (9, 7), (180, 180, 180)).save(source)
    output = tmp_path / "padded.jpg"

    result = convert_image(
        source,
        output,
        config=ConversionConfig(preserve_exif=False, pad_to_even=True),
    )

    assert (result.width, result.height) == (9, 7)
    assert (result.padded_width, result.padded_height) == (10, 8)
    inspection = inspect_ultrahdr(output)
    assert (inspection.primary_width, inspection.primary_height) == (10, 8)
