# Video Compressor Toolbox — local batch video compression

[中文](README.md) · **English**

Recorded an event, a performance, or an everyday moment, but the video is too large to share? This small desktop tool puts common compression settings in one window: **drop in videos or folders, choose a preset, and compress the batch.**

Videos stay on your computer. No uploads, no account, and no ads. Original videos are kept; results go into a nearby `compressed` folder by default. It is free and open source: improve it, or adapt it to your own workflow.

**[⬇ Download the Windows portable app](https://github.com/Marquis-Chan/video-compressor/releases/latest/download/VideoCompressor-1.5.0-windows-x64-public.zip)** · [All releases and checksums](https://github.com/Marquis-Chan/video-compressor/releases) · [Feedback and ideas](https://github.com/Marquis-Chan/video-compressor/issues)

![Video Compressor Toolbox v1.5.0 interface](docs/preview.png)

## Download and use

The current portable release supports **Windows 10/11 x64** and includes the Python runtime.

1. Install [FFmpeg for Windows](https://www.gyan.dev/ffmpeg/builds/) once. Alternatively, run `winget install --id Gyan.FFmpeg --exact` in Windows Terminal.
2. Download and fully extract the portable ZIP, then open `VideoCompressor.exe`.
3. Drop in videos or folders, choose a preset, confirm the output location, and start.

After installing FFmpeg, reopen your terminal and the app. Check that `ffmpeg -version` and `ffprobe -version` work. If the app still cannot find them, put both executables in an `ffmpeg/bin/` folder beside the app. The public portable package does not bundle FFmpeg. Use a build with libx264, AAC, zscale, and tonemap.

This release is not Windows code-signed, so Windows may identify the publisher as unknown. SHA256 checksums are provided on the release page. No publicly validated macOS binary is offered yet. The current application interface is in Chinese.

## Choose a preset

| Preset in the app | Intended use | Settings |
|---|---|---|
| 高清分享 / Share (default) | Everyday sharing | CRF 25, fast, AAC 128k, original resolution |
| 清晰优先 / Quality | More detail, with a slower encode | CRF 23, medium, AAC 160k, original resolution |
| 体积优先 / Smaller file | Prioritize file size | CRF 28, fast, AAC 96k, up to 1080p |

Output uses H.264, 8-bit yuv420p, and faststart. HDR input is converted to SDR BT.709. Compression involves a quality trade-off, and savings depend on the original video. Outputs that are not smaller are marked explicitly.

## More features

- Drag-and-drop files or folders, recursive scanning, search, selection, and batch processing.
- Output beside each source video, or to a shared destination folder.
- Per-file and batch progress, pause, resume, cancel, retry, and result summaries.
- Output conflict handling, automatic renaming, temporary files, and output verification.
- Optional idle-sleep prevention on Windows, local background images, and appearance controls.
- Local diagnostic logs; background images are not added to your videos.

## Make it your own

Fork the repository, open an Issue, or send a Pull Request. Improvements to presets, queue behavior, error messages, and platform support are welcome. Include the app version, operating system, and error message when reporting a problem; you do not need to upload private videos.

For source use, Python 3.12 is recommended. Configure FFmpeg first, then run:

```sh
python -m pip install -r requirements.txt
python video_compress_gui.py
```

Development checks and a public portable build:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -p no:cacheprovider tests -q
python tools/build_public_windows.py
```

The compression code is in `video_compressor/` and `compressor_core.py`; the desktop UI is in `video_compress_gui.py`. See [development notes](DEVELOPMENT.md) for earlier build records (Chinese). The public builder excludes FFmpeg; the existing offline builder with a bundled toolchain remains in `build_windows.ps1`.

## License and credits

Original code, icons, and documentation use the [MIT License](LICENSE). You may use, modify, and share them while preserving the license notice. Dependencies retain their individual licenses; see [third-party notices](THIRD_PARTY_NOTICES.txt) and [corresponding source access](licenses/SOURCE_ACCESS.md). FFmpeg is installed separately and follows the license of the chosen build.

Thank you to the open-source projects that make this tool possible.

Keywords: video compressor, batch video compression, local video compression, Windows video compressor, MP4 compression, FFmpeg GUI, free and open source.
