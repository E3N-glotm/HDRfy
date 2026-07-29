from pathlib import Path

from hdrfy.encoder import UltraHDREncodeOptions, encode_ultrahdr


def test_encoder_command_contract(tmp_path: Path) -> None:
    log = tmp_path / "args.txt"
    binary = tmp_path / "ultrahdr_app"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        f"pathlib.Path({str(log)!r}).write_text('\\n'.join(sys.argv[1:]))\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('-z') + 1])\n"
        "out.write_bytes(b'fake-ultrahdr')\n"
    )
    binary.chmod(0o755)
    hdr = tmp_path / "hdr.raw"
    sdr = tmp_path / "sdr.raw"
    hdr.write_bytes(b"hdr")
    sdr.write_bytes(b"sdr")
    output = tmp_path / "out.jpg"

    encode_ultrahdr(
        binary=binary,
        hdr_raw=hdr,
        sdr_raw=sdr,
        output=output,
        options=UltraHDREncodeOptions(
            width=12,
            height=10,
            base_quality=93,
            gainmap_quality=91,
            gainmap_scale=2,
            multi_channel_gainmap=True,
            max_content_boost=4.0,
            target_peak_nits=812.0,
        ),
    )

    arguments = log.read_text().splitlines()
    assert arguments[arguments.index("-a") + 1] == "4"
    assert arguments[arguments.index("-b") + 1] == "3"
    assert arguments[arguments.index("-C") + 1] == "2"
    assert arguments[arguments.index("-t") + 1] == "0"
    assert output.read_bytes() == b"fake-ultrahdr"
