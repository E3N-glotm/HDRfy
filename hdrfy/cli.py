"""Secondary command-line interface for automation and structural inspection."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import PRESETS, ConversionConfig
from .encoder import probe_ultrahdr
from .errors import HDRfyError
from .pipeline import convert_image


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hdrfy",
        description="Convert SDR photographs into pure-Python Ultra HDR JPEG files.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert = subparsers.add_parser("convert", help="Convert one SDR photograph to Ultra HDR.")
    convert.add_argument("input", type=Path)
    convert.add_argument("output", type=Path)
    convert.add_argument("--preset", choices=sorted(PRESETS), default="natural")
    convert.add_argument("--peak-nits", type=float, default=1000.0)
    convert.add_argument("--reference-white-nits", type=float, default=203.0)
    convert.add_argument("--quality", type=int, default=95, help="SDR base JPEG quality.")
    convert.add_argument("--gainmap-quality", type=int, default=95)
    convert.add_argument("--gainmap-scale", type=int, default=2)
    convert.add_argument(
        "--single-channel-gainmap",
        action="store_true",
        help="Use one gain-map channel instead of the default RGB gain map.",
    )
    convert.add_argument(
        "--pad-even",
        action="store_true",
        help="Compatibility option: edge-pad odd dimensions by at most one pixel.",
    )
    convert.add_argument("--strip-exif", action="store_true")
    convert.add_argument(
        "--force-sdr-heif",
        action="store_true",
        help="Treat PQ/HLG-tagged HEIF input as SDR only when its metadata is wrong.",
    )
    convert.add_argument(
        "--keep-intermediates",
        type=Path,
        default=None,
        help="Keep HDR/SDR NumPy arrays and the generated gain-map PNG.",
    )
    convert.add_argument("--no-verify", action="store_true")

    inspect = subparsers.add_parser("inspect", help="Inspect an Ultra HDR JPEG structure.")
    inspect.add_argument("input", type=Path)
    return parser


def _run_convert(args: argparse.Namespace) -> int:
    config = ConversionConfig(
        preset=args.preset,
        peak_nits=args.peak_nits,
        reference_white_nits=args.reference_white_nits,
        base_quality=args.quality,
        gainmap_quality=args.gainmap_quality,
        gainmap_scale=args.gainmap_scale,
        multi_channel_gainmap=not args.single_channel_gainmap,
        pad_to_even=args.pad_even,
        preserve_exif=not args.strip_exif,
        force_sdr_heif=args.force_sdr_heif,
    )
    result = convert_image(
        args.input,
        args.output,
        config=config,
        keep_intermediates=args.keep_intermediates,
        verify=not args.no_verify,
    )
    print(
        json.dumps(
            {
                "input": str(result.input_path),
                "output": str(result.output_path),
                "source_size": [result.width, result.height],
                "encoded_size": [result.padded_width, result.padded_height],
                "preset": result.preset,
                "peak_nits": result.peak_nits,
                "max_content_boost": result.max_content_boost,
                "probe": result.probe_output,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "convert":
            return _run_convert(args)
        if args.command == "inspect":
            print(probe_ultrahdr(args.input))
            return 0
    except (HDRfyError, ValueError) as exc:
        print(f"hdrfy: error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"Unsupported command: {args.command}")
    return 2
