# -*- coding: utf-8 -*-
"""
视频压缩工具箱 v1.5.0 (命令行版)

CLI is intentionally thin. Compression behavior lives in compressor_core.py so
the GUI and terminal entrypoints stay consistent.
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compressor_core import (
    APP_NAME,
    APP_VERSION,
    AUDIO_BITRATES,
    DEFAULT_AUDIO_BITRATE,
    DEFAULT_CRF,
    DEFAULT_PRESET,
    PRESETS,
    CompressionOptions,
    CancelToken,
    bytes_to_mb,
    compress_video,
    format_size,
    parse_crf,
    parse_selection,
    resolve_ffmpeg_path,
    scan_videos,
)


cancel_token = CancelToken()


def signal_handler(signum, frame):  # type: ignore[no-untyped-def]
    cancel_token.cancel()
    print("\n正在终止当前压缩任务...")


def print_header() -> None:
    print("=" * 50)
    print(f"   {APP_NAME} v{APP_VERSION}")
    print("=" * 50)


def progress_bar(percent: int) -> str:
    bar_len = 30
    filled = int(bar_len * percent / 100)
    return "█" * filled + "░" * (bar_len - filled)


def print_file_progress(item, percent: int, elapsed_seconds: float) -> None:  # type: ignore[no-untyped-def]
    sys.stdout.write(f"\r  [{progress_bar(percent)}] {percent}%  {elapsed_seconds:.0f}s    ")
    sys.stdout.flush()


def choose_videos(videos):  # type: ignore[no-untyped-def]
    print("\n" + "-" * 50)
    print("选项说明:")
    print("  y  - 压缩全部文件（默认）")
    print("  n  - 取消")
    print("  3  - 只压缩第3个文件")
    print("  1,3,5 - 压缩第1、3、5个文件")
    print("  2-5  - 压缩第2到第5个文件")
    print("-" * 50)

    selected: list[int] = []
    while not selected:
        confirm = input("请选择 (默认 y): ").strip()
        selected_or_none = parse_selection(confirm, len(videos))
        if selected_or_none is None:
            print("已取消")
            return []
        selected = selected_or_none
        if not selected:
            print(f"  输入格式错误或序号超出范围 (1-{len(videos)})")
    return selected


def load_videos(path_input: str):
    path = Path(path_input.strip().strip('"')).expanduser()
    if path.is_file():
        videos = scan_videos(path)
        if not videos:
            print(f"[错误] 不是支持的视频文件: {path.name}")
            return []
        print(f"\n[OK] 文件: {path.name} ({bytes_to_mb(videos[0].size):.1f}MB)")
        return videos

    if path.is_dir():
        videos = scan_videos(path)
        if not videos:
            print("[错误] 文件夹中没有找到支持的视频")
            return []
        print(f"\n[OK] 找到 {len(videos)} 个视频:")
        for index, item in enumerate(videos, 1):
            print(f"  {index}. {item.path.name} ({bytes_to_mb(item.size):.1f}MB)")
        return videos

    print(f"[错误] 路径无效: {path}")
    return []


def main() -> None:
    signal.signal(signal.SIGINT, signal_handler)

    parser = argparse.ArgumentParser(description="视频压缩工具箱")
    parser.add_argument("path", nargs="?", help="视频文件或文件夹路径（可直接拖拽）")
    parser.add_argument("--crf", default=str(DEFAULT_CRF), help=f"CRF 质量值 0-51（默认 {DEFAULT_CRF}）")
    parser.add_argument("--preset", default=DEFAULT_PRESET, choices=PRESETS, help=f"编码速度（默认 {DEFAULT_PRESET}）")
    parser.add_argument(
        "--audio",
        default=DEFAULT_AUDIO_BITRATE,
        choices=AUDIO_BITRATES,
        help=f"音频码率（默认 {DEFAULT_AUDIO_BITRATE}）",
    )
    parser.add_argument(
        "--conflict",
        default="skip",
        choices=("skip", "overwrite", "rename"),
        help="输出文件已存在时的处理方式（默认 skip）",
    )
    args = parser.parse_args()

    print_header()

    ffmpeg = resolve_ffmpeg_path()
    if not ffmpeg:
        print("\n[错误] 未找到 FFmpeg！")
        print("请确保 ffmpeg/bin/ffmpeg.exe 或 ffmpeg/bin/ffmpeg 存在。")
        input("\n按回车键退出...")
        return

    options = CompressionOptions(
        crf=parse_crf(args.crf),
        preset=args.preset,
        audio_bitrate=args.audio,
        conflict_policy=args.conflict,
    )
    print(f"\n[OK] FFmpeg: {ffmpeg}")
    print(f"设置: CRF={options.crf}, 速度={options.preset}, 音频={options.audio_bitrate}, 已存在={args.conflict}")

    path_input = args.path
    if not path_input:
        print("\n" + "-" * 50)
        path_input = input("请输入文件夹或文件路径（可直接拖拽）: ").strip()

    if not path_input:
        print("[错误] 未输入路径")
        return

    videos = load_videos(path_input)
    if not videos:
        return

    selected_indices = choose_videos(videos)
    if not selected_indices:
        return

    selected_videos = [videos[index] for index in selected_indices]
    print(f"\n已选择 {len(selected_videos)} 个文件，开始压缩...")
    print("\n" + "=" * 50)
    print("开始压缩...")
    print("=" * 50)

    total_orig = 0
    total_comp = 0
    success_count = 0
    fail_count = 0
    skipped_count = 0

    callbacks = {"progress": print_file_progress}
    for count, item in enumerate(selected_videos, 1):
        if cancel_token.is_cancelled():
            print("\n已取消剩余任务")
            break

        print(f"\n[{count}/{len(selected_videos)}] 压缩中: {item.path.name}")
        result = compress_video(item, options, callbacks=callbacks, cancel_token=cancel_token, ffmpeg=ffmpeg)
        if result.cancelled:
            print(f"\r  [中断] {item.path.name}                         ")
            break
        if result.skipped:
            skipped_count += 1
            print(f"\r  [跳过] 已存在: {item.path.name}                         ")
            continue
        if result.success:
            success_count += 1
            total_orig += result.original_size
            total_comp += result.output_size
            saved = result.original_size - result.output_size
            ratio = result.output_size / (result.original_size + 1) * 100
            print(
                f"\r  [完成] {bytes_to_mb(result.original_size):.1f}MB -> "
                f"{bytes_to_mb(result.output_size):.1f}MB "
                f"(节省 {bytes_to_mb(saved):.1f}MB, {ratio:.0f}%)   "
            )
        else:
            fail_count += 1
            print(f"\r  [失败] {item.path.name}: {result.error}                         ")

    print("\n" + "=" * 50)
    print("   压缩完成！")
    print("=" * 50)
    print(f"  成功: {success_count} 个")
    if skipped_count:
        print(f"  跳过: {skipped_count} 个")
    if fail_count:
        print(f"  失败: {fail_count} 个")
    if total_orig > 0:
        print(f"\n  原始大小: {format_size(total_orig)}")
        print(f"  压缩后: {format_size(total_comp)}")
        print(f"  节省: {format_size(total_orig - total_comp)}")
    print("\n输出目录: 每个视频所在目录下的 compressed 文件夹")
    input("\n按回车键退出...")


if __name__ == "__main__":
    main()
