from pathlib import Path

from PIL import Image

from hdrfy.config import ConversionConfig
from hdrfy.pipeline import convert_image


def test_end_to_end_pipeline_with_reference_app_contract(tmp_path: Path) -> None:
    source = tmp_path / "input.png"
    Image.new("RGB", (9, 7), (230, 180, 40)).save(source)

    binary = tmp_path / "ultrahdr_app"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "mode = args[args.index('-m') + 1]\n"
        "if mode == '0':\n"
        "    pathlib.Path(args[args.index('-z') + 1]).write_bytes(b'fake-ultrahdr')\n"
        "elif mode == '1' and '-P' in args:\n"
        "    print('gainmap metadata: valid')\n"
        "else:\n"
        "    raise SystemExit(3)\n"
    )
    binary.chmod(0o755)

    output = tmp_path / "output.jpg"
    result = convert_image(
        source,
        output,
        config=ConversionConfig(preserve_exif=False),
        ultrahdr_binary=binary,
    )

    assert output.read_bytes() == b"fake-ultrahdr"
    assert (result.width, result.height) == (9, 7)
    assert (result.padded_width, result.padded_height) == (10, 8)
    assert "valid" in result.probe_output
