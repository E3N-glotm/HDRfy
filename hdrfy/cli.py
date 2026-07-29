"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .build import build_libultrahdr
from .config import PRESETS, ConversionConfig
from .encoder import find_ultrahdr_binary, probe_ultrahdr
from .errors import HDRfyError
from .pipeline import convert_image


def _add_encoder_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ultrahdr-bin",
        type=Path,
        default=None,
        help="Path to Google's ultrahdr_app executable.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hdrfy",
        description="Convert SDR JPEG, PNG and HEIF photographs into Ultra HDR JPEG files.",
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
        "--no-pad-even",
        action="store_true",
        help="Reject odd dimensions instead of edge-padding one pixel.",
    )
    convert.add_argument("--strip-exif", action="store_true")
    convert.add_argument(
        "--force-sdr-heif",
        action="store_true",
        help="Treat PQ/HLG-tagged HEIF input as SDR. Use only for incorrect source metadata.",
    )
    convert.add_argument(
        "--keep-intermediates",
        type=Path,
        default=None,
        help="Keep generated raw intents and the linear BT.2020 NumPy array in this directory.",
    )
    convert.add_argument("--no-verify", action="store_true")
    _add_encoder_argument(convert)

    inspect = subparsers.add_parser("inspect", help="Probe an Ultra HDR JPEG gain map.")
    inspect.add_argument("input", type=Path)
    _add_encoder_argument(inspect)

    build = subparsers.add_parser(
        "build-ultrahdr",
        help="Build Google's libultrahdr reference app into a local tools directory.",
    )
    build.add_argument(
        "--destination",
        type=Path,
        default=Path(".tools/libultrahdr"),
    )
    build.add_argument("--ref", default="main", help="Git branch, tag or commit to build.")
    build.add_argument("--jobs", type=int, default=None)
    build.add_argument(
        "--system-jpeg",
        action="store_true",
        help="Use a system libjpeg instead of letting libultrahdr build its dependencies.",
    )
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
        pad_to_even=not args.no_pad_even,
        preserve_exif=not args.strip_exif,
        force_sdr_heif=args.force_sdr_heif,
    )
    result = convert_image(
        args.input,
        args.output,
        config=config,
        ultrahdr_binary=args.ultrahdr_bin,
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
            binary = find_ultrahdr_binary(args.ultrahdr_bin)
            print(probe_ultrahdr(binary, args.input))
            return 0
        if args.command == "build-ultrahdr":
            binary = build_libultrahdr(
                args.destination,
                ref=args.ref,
                jobs=args.jobs,
                build_dependencies=not args.system_jpeg,
            )
            print(binary)
            return 0
    except (HDRfyError, ValueError) as exc:
        print(f"hdrfy: error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"Unsupported command: {args.command}")
    return 2
