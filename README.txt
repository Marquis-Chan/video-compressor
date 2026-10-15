视频压缩工具箱 v1.5.0 / Video Compressor Toolbox

Windows 10/11 x64 公开便携版 / Public portable edition

1. 先安装 FFmpeg（含 ffmpeg.exe 和 ffprobe.exe），并确保两个命令可在终端运行。
   Windows 终端安装命令：winget install --id Gyan.FFmpeg --exact
   官方 Windows 构建入口：https://www.gyan.dev/ffmpeg/builds/
2. 完整解压此 ZIP，双击 VideoCompressor.exe。
3. 拖入视频或文件夹，选择档位、确认输出位置后开始。

安装 FFmpeg 后，请重新打开终端和应用。如果仍找不到它，可将 ffmpeg.exe、
ffprobe.exe 放进 VideoCompressor.exe 旁的 ffmpeg/bin/ 目录。
公开包不含 FFmpeg，需要 libx264、AAC、zscale 与 tonemap 支持。

视频仅在本地处理，原视频保留，默认输出到源视频旁的 compressed 文件夹。
当前成品未进行 Windows 代码签名；可在 GitHub 发布页核对 SHA256。

English:
Install FFmpeg once (including ffprobe), extract the entire ZIP, and open
VideoCompressor.exe. Drop in videos or folders, choose a preset, confirm the
output location, and start. No Python installation is needed. The UI is Chinese.
Videos are processed locally. This public package does not bundle FFmpeg.
The executable is not code-signed; release SHA256 checksums are provided.

源码与双语说明 / Source and bilingual guide:
https://github.com/Marquis-Chan/video-compressor

请保留 LICENSE、THIRD_PARTY_NOTICES.txt、licenses/ 和 BUILD_MANIFEST.txt。
Keep LICENSE, THIRD_PARTY_NOTICES.txt, licenses/, and BUILD_MANIFEST.txt together.
