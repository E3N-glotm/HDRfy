"""HDRfy 的脚本配置入口。

使用方式：
1. 只修改下方“用户配置区”；
2. 运行 ``python run_hdrfy.py``；
3. 不需要在命令行传递输入路径或 HDR 参数。
"""

from __future__ import annotations

from pathlib import Path

from hdrfy.build import build_libultrahdr
from hdrfy.config import ConversionConfig
from hdrfy.encoder import find_ultrahdr_binary
from hdrfy.errors import HDRfyError, UltraHDREncoderNotFound
from hdrfy.pipeline import ConversionResult, convert_image
from hdrfy.script_runner import build_batch_items, collect_input_files

# =============================================================================
# 用户配置区：通常只需要修改本区域
# =============================================================================

# 输入可以是单张图片，也可以是图片目录。
# 相对路径始终相对于本脚本所在的项目根目录，而不是终端当前目录。
INPUT_PATH = Path(r"input")

# 单文件输入时：
#   - 写成 output/result.jpg，可指定确切输出文件；
#   - 写成 output，则自动输出为 output/<原文件名>_hdr.jpg。
# 目录输入时：必须填写输出目录，原目录层级会被保留。
OUTPUT_PATH = Path(r"output")

# 可选：显式指定 ultrahdr_app 或 ultrahdr_app.exe。
# 保持 None 时，程序会依次搜索环境变量、PATH 和 .tools/libultrahdr。
ULTRAHDR_BINARY: Path | None = None

# 未找到参考编码器时是否自动下载并构建 google/libultrahdr。
AUTO_BUILD_ULTRAHDR = True
LIBULTRAHDR_BUILD_DIR = Path(r".tools/libultrahdr")
LIBULTRAHDR_REF = "main"
LIBULTRAHDR_BUILD_JOBS: int | None = None
LIBULTRAHDR_BUILD_DEPENDENCIES = True

# 批处理行为。
RECURSIVE = True
OVERWRITE_EXISTING = False
STOP_ON_ERROR = False
OUTPUT_NAME_SUFFIX = "_hdr"

# HDR 重建参数。
# preset: conservative（保守）/ natural（自然）/ vivid（较强）
PRESET = "natural"
PEAK_NITS = 1000.0
REFERENCE_WHITE_NITS = 203.0

# Ultra HDR JPEG 与 Gain Map 编码参数。
BASE_JPEG_QUALITY = 95
GAINMAP_QUALITY = 95
GAINMAP_SCALE = 2
MULTI_CHANNEL_GAINMAP = True

# 输入、元数据与验证参数。
PAD_ODD_DIMENSIONS_TO_EVEN = True
PRESERVE_EXIF = True
FORCE_SDR_HEIF = False
VERIFY_OUTPUT = True

# 是否保留每张图的 RGBA16F、RGBA8888、NumPy HDR Intent 等中间结果。
KEEP_INTERMEDIATES = False
INTERMEDIATES_PATH = Path(r"artifacts/intermediates")

# =============================================================================
# 配置区结束：通常不需要修改下面的代码
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent


def resolve_project_path(path: str | Path) -> Path:
    """将脚本配置中的路径稳定解析为绝对路径。"""

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def resolve_encoder_binary() -> Path:
    """查找参考编码器，并按脚本配置决定是否自动构建。"""

    explicit = resolve_project_path(ULTRAHDR_BINARY) if ULTRAHDR_BINARY is not None else None
    try:
        return find_ultrahdr_binary(explicit)
    except UltraHDREncoderNotFound:
        if not AUTO_BUILD_ULTRAHDR:
            raise

    destination = resolve_project_path(LIBULTRAHDR_BUILD_DIR)
    print(f"[HDRfy] 未找到 ultrahdr_app，开始构建到：{destination}")
    return build_libultrahdr(
        destination,
        ref=LIBULTRAHDR_REF,
        jobs=LIBULTRAHDR_BUILD_JOBS,
        build_dependencies=LIBULTRAHDR_BUILD_DEPENDENCIES,
    )


def create_conversion_config() -> ConversionConfig:
    config = ConversionConfig(
        preset=PRESET,
        peak_nits=PEAK_NITS,
        reference_white_nits=REFERENCE_WHITE_NITS,
        base_quality=BASE_JPEG_QUALITY,
        gainmap_quality=GAINMAP_QUALITY,
        gainmap_scale=GAINMAP_SCALE,
        multi_channel_gainmap=MULTI_CHANNEL_GAINMAP,
        pad_to_even=PAD_ODD_DIMENSIONS_TO_EVEN,
        preserve_exif=PRESERVE_EXIF,
        force_sdr_heif=FORCE_SDR_HEIF,
    )
    config.validate()
    return config


def print_result(result: ConversionResult) -> None:
    padded = ""
    if (result.width, result.height) != (result.padded_width, result.padded_height):
        padded = f"，补边后 {result.padded_width}x{result.padded_height}"
    print(
        f"[完成] {result.input_path.name} -> {result.output_path} "
        f"({result.width}x{result.height}{padded}, {result.peak_nits:g} nit)"
    )


def main() -> int:
    input_path = resolve_project_path(INPUT_PATH)
    output_path = resolve_project_path(OUTPUT_PATH)
    intermediates_root = resolve_project_path(INTERMEDIATES_PATH)

    sources = collect_input_files(
        input_path,
        output_path=output_path,
        recursive=RECURSIVE,
    )
    if not sources:
        raise HDRfyError(f"没有找到可处理的图片：{input_path}")

    items = build_batch_items(
        input_path,
        output_path,
        sources,
        output_suffix=OUTPUT_NAME_SUFFIX,
        keep_intermediates=KEEP_INTERMEDIATES,
        intermediates_root=intermediates_root,
    )
    config = create_conversion_config()
    binary = resolve_encoder_binary()

    print(f"[HDRfy] 输入：{input_path}")
    print(f"[HDRfy] 编码器：{binary}")
    print(f"[HDRfy] 待处理：{len(items)} 张")

    succeeded = 0
    skipped = 0
    failures: list[tuple[Path, str]] = []

    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] {item.source}")
        if item.output.exists() and not OVERWRITE_EXISTING:
            print(f"[跳过] 输出已存在：{item.output}")
            skipped += 1
            continue

        try:
            result = convert_image(
                item.source,
                item.output,
                config=config,
                ultrahdr_binary=binary,
                keep_intermediates=item.intermediates,
                verify=VERIFY_OUTPUT,
            )
            print_result(result)
            succeeded += 1
        except Exception as exc:  # noqa: BLE001 - 批处理需要记录单文件失败并继续
            failures.append((item.source, str(exc)))
            print(f"[失败] {item.source}: {exc}")
            if STOP_ON_ERROR:
                raise

    print("\n========== HDRfy 处理汇总 ==========")
    print(f"成功：{succeeded}")
    print(f"跳过：{skipped}")
    print(f"失败：{len(failures)}")
    if failures:
        for source, message in failures:
            print(f"- {source}: {message}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HDRfyError as exc:
        print(f"[HDRfy 错误] {exc}")
        raise SystemExit(1) from exc
