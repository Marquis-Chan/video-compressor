# 视频压缩工具箱 v1.5.0

面向粉丝团体的本地批量视频压缩工具。视频只在电脑本地处理，原文件不会被修改；默认结果保存在源视频旁的 `compressed` 文件夹。

## 成品使用

此目录和其中的 `windows-x64.zip` 只适用于 Windows。Windows 与 macOS 发布包不能混用；M 系列 Mac 必须使用在 Apple Silicon Mac 上单独构建并验收的 `macos-arm64.zip`。

### Windows 10/11 x64

1. 解压 `VideoCompressor-1.5.0-windows-x64.zip`。
2. 双击 `VideoCompressor.exe`。
3. 将视频或整个活动文件夹拖入窗口，选择档位后开始压缩。

内部未签名版本可能出现 Windows 安全提示。请只使用团队负责人提供、且 SHA256 已由负责人核验的压缩包。

## 压缩档位

- 高清分享：CRF 25、fast、AAC 128k，保留原分辨率；默认推荐。
- 清晰优先：CRF 23、medium、AAC 160k，保留原分辨率。
- 体积优先：CRF 28、fast、AAC 96k，最高 1080p。

分享输出统一使用 H.264、8-bit `yuv420p` 和 faststart。HDR 素材会转换为兼容性更好的 SDR BT.709。若结果没有变小，应用会明确标记，不把它计入节省空间。

## 主要功能

- 拖入视频或文件夹，递归扫描常见视频格式
- 批量勾选、搜索、取消、失败重试
- 输出位置常驻可见；默认保存到源视频旁，也可直接输入或选择统一输出文件夹
- 原子覆盖、自动重命名、唯一临时文件
- 当前文件与整批连续进度、取消任务、结果汇总
- Windows 编码期间防止空闲睡眠，并支持当前视频即时暂停、继续和暂停中取消
- 本地明星背景图、暗化与柔化设置，不上传也不写入视频
- 本地诊断日志，不记录或上传视频内容

## 源码运行

Windows 源码运行使用原有依赖文件：

```bash
python -m pip install -r requirements.txt
python video_compress_gui.py
```

Windows 工作区也可双击 `run.bat`。命令行模式：`run.bat --cli`。

## 开发、测试与构建

Windows 安装精确锁定的开发依赖：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -p no:cacheprovider tests -q
```

Windows 构建：

```powershell
.\build_windows.ps1
```

脚本只打包 `ffmpeg.exe` 与 `ffprobe.exe`，不会联网安装依赖。ZIP 只有一个同名顶层目录，其中包含 `README.txt`、`THIRD_PARTY_NOTICES.txt`、`licenses/` 与 `BUILD_MANIFEST.txt`；脚本会逐项断言 ZIP 未漏文件，并在成功后删除解压式构建目录以节省空间。清单记录版本、平台架构、UTC 构建时间、FFmpeg 版本，以及除清单自身外所有文件的 SHA256。解压体积超过 320 MB 或 ZIP 超过 180 MB 会直接失败。

## 第三方许可

分发时必须携带 `README.txt`、`THIRD_PARTY_NOTICES.txt`、`licenses/`、`BUILD_MANIFEST.txt`，并保存发布 ZIP 的 SHA256。构建脚本通过当前构建解释器的 `importlib.metadata` 动态定位并复制 PySide6 Essentials、shiboken6、PyInstaller 与 psutil 的许可证文件，不依赖硬编码的 `python\` 路径；FFmpeg 的精确版本、`-L` 许可证输出、输入 SHA256 与来源地址会写入许可证目录。正式公开发布还需完成许可证复核和 Windows 代码签名。
