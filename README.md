# 视频压缩工具箱｜本地批量视频压缩

**中文** · [English](README.en.md)

拍完活动、演出或日常视频，想发给朋友，却发现文件太大？这个小工具把常用的压缩设置放进一个桌面窗口：**拖入视频或文件夹，选一个档位，批量压缩。**

视频在自己的电脑上处理，无需上传或注册，没有广告；原视频保留，压缩结果默认放在旁边的 `compressed` 文件夹。免费开源，欢迎改进，也欢迎做一个更适合自己工作流的版本。

**[⬇ 下载 Windows 便携版](https://github.com/Marquis-Chan/video-compressor/releases/latest/download/VideoCompressor-1.5.0-windows-x64-public.zip)** · [所有版本与校验值](https://github.com/Marquis-Chan/video-compressor/releases) · [反馈问题或想法](https://github.com/Marquis-Chan/video-compressor/issues)

![视频压缩工具箱 v1.5.0 界面](docs/preview.png)

## 下载后怎么用

当前提供 **Windows 10/11 x64** 便携版，不需要安装 Python。

1. 先安装一次 [FFmpeg Windows 版](https://www.gyan.dev/ffmpeg/builds/)。也可在 Windows 终端执行：`winget install --id Gyan.FFmpeg --exact`。
2. 下载并完整解压便携包，双击 `VideoCompressor.exe`。
3. 拖入视频或文件夹，选择“高清分享”等档位，确认输出位置后开始压缩。

安装 FFmpeg 后请重新打开终端和应用，确认 `ffmpeg -version`、`ffprobe -version` 可运行。如果应用仍找不到它们，可将这两个程序放入应用旁的 `ffmpeg/bin/` 文件夹。公开便携包不附带 FFmpeg；需要具备 libx264、AAC、zscale 和 tonemap 的构建。

当前包尚未进行 Windows 代码签名，系统可能提示发布者未知；发布页提供 SHA256 便于核对。macOS 暂未提供公开验收的成品包。

## 三个档位，按需要选择

| 档位 | 用途 | 设置 |
|---|---|---|
| 高清分享（默认） | 日常分享，兼顾体积和清晰度 | CRF 25、fast、AAC 128k，保留分辨率 |
| 清晰优先 | 更在意细节，愿意多等一会儿 | CRF 23、medium、AAC 160k，保留分辨率 |
| 体积优先 | 更在意文件大小 | CRF 28、fast、AAC 96k，最高 1080p |

输出采用 H.264、8-bit yuv420p 和 faststart；HDR 输入会转换成 SDR BT.709。压缩会有画质取舍，体积变化取决于原视频。结果没有变小时会明确标记。

## 还有这些功能

- 拖入文件或文件夹，递归扫描、搜索、勾选与批量处理。
- 默认保存到源视频旁，也可选择统一输出目录。
- 当前文件与整批进度，暂停、继续、取消、失败重试和结果汇总。
- 覆盖冲突处理、自动重命名、临时文件与输出校验。
- Windows 压缩期间可阻止空闲睡眠；支持本地背景图和外观调整。
- 诊断日志保存在本地；背景图不会写入输出视频。

## 想改进它？

欢迎 Fork、Issue 和 Pull Request。可以改进压缩预设、队列体验、错误提示、平台适配，或者分享你发现的兼容性问题。反馈时请附版本、系统和错误提示；无需上传私人视频。

源码运行建议使用 Python 3.12，并先配置 FFmpeg：

```sh
python -m pip install -r requirements.txt
python video_compress_gui.py
```

开发验证与公开便携包构建：

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -p no:cacheprovider tests -q
python tools/build_public_windows.py
```

核心逻辑在 `video_compressor/` 和 `compressor_core.py`，桌面界面在 `video_compress_gui.py`。原有构建记录见 [开发说明](DEVELOPMENT.md)。公开构建脚本生成不含 FFmpeg 的便携包；离线的完整工具链构建仍保留在 `build_windows.ps1`。

## 开源许可与感谢

原创代码、图标和文档采用 [MIT 许可](LICENSE)，可以使用、修改和分享，请保留许可声明。Qt/PySide6、Python、psutil、PyInstaller 等依赖保留各自许可，见 [第三方声明](THIRD_PARTY_NOTICES.txt) 和 [对应源码入口](licenses/SOURCE_ACCESS.md)。FFmpeg 是独立安装的转码引擎，按所选构建的许可使用和分发。

感谢这些开源项目，让日常视频分享变得更方便。

关键词：视频压缩工具、批量视频压缩、本地视频压缩、Windows 视频压缩、MP4 压缩、FFmpeg 图形界面、免费开源；video compressor, batch video compression, offline video compression, FFmpeg GUI.
