# Corresponding source and build information

This record describes the public Windows v1.5.0 portable release. Original application source is at [Marquis-Chan/video-compressor](https://github.com/Marquis-Chan/video-compressor), with the release's source archive alongside its binary download. Build using Python 3.12.8, the exact packages in `requirements-dev.txt`, and `python tools/build_public_windows.py` on Windows x64. The application and its dependency code have not been modified to restrict library replacement.

## Component source downloads

The following official upstream archives are available without a paid account. They correspond to the versions in this release. This project does not modify the upstream libraries. Package build inputs, versions and final binary hashes are recorded in the delivered manifest and license inventory.

| Component | Version | Corresponding source |
|---|---|---|
| Qt Core / GUI / Widgets / Network and base plugins | 6.11.1 | [qtbase source archive](https://download.qt.io/official_releases/qt/6.11/6.11.1/submodules/qtbase-everywhere-src-6.11.1.tar.xz) |
| Qt SVG and its plugin | 6.11.1 | [qtsvg source archive](https://download.qt.io/official_releases/qt/6.11/6.11.1/submodules/qtsvg-everywhere-src-6.11.1.tar.xz) |
| Additional Qt image-format plugins | 6.11.1 | [qtimageformats source archive](https://download.qt.io/official_releases/qt/6.11/6.11.1/submodules/qtimageformats-everywhere-src-6.11.1.tar.xz) |
| PySide6 Essentials / shiboken6 | 6.11.1 | [Qt for Python source archive](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.1-src/pyside-setup-everywhere-src-6.11.1.tar.xz) |
| CPython | 3.12.8 | [Python source archive](https://www.python.org/ftp/python/3.12.8/Python-3.12.8.tar.xz) |
| psutil | 7.2.2 | [psutil tagged source](https://github.com/giampaolo/psutil/tree/release-7.2.2) |
| PyInstaller | 6.21.0 | [PyInstaller tagged source](https://github.com/pyinstaller/pyinstaller/tree/v6.21.0) |

Qt/PySide open-source package licensing is documented by the [Qt project](https://doc.qt.io/qtforpython-6/licenses.html) and the [official PyPI package](https://pypi.org/project/PySide6-Essentials/6.11.1/). The upstream PyPI wheels are offered for open-source and commercial licensing; this application selects LGPLv3. Unmodified upstream libraries and the shipped license notices retain their copyrights and licenses.

Qt is dynamically loaded from the portable application's `_internal/PySide6/` directory. The application source and build instructions are available to relink/rebuild it with a modified compatible library. No installation authorization key or signature is required by this project to use such a rebuilt version. Library modifications must preserve ABI compatibility with the Python extension modules.

If an upstream source link becomes unavailable, please [open an issue](https://github.com/Marquis-Chan/video-compressor/issues) so its access can be restored. Builders who change dependency versions must update these source references and notices for their own distribution.

## FFmpeg is external

The public portable ZIP does not distribute FFmpeg or its component libraries. Obtain a separate build from the [FFmpeg project download page](https://ffmpeg.org/download.html). The selected engine must provide libx264, AAC, zscale and tonemap. The existing developer-only FFmpeg provenance records are historical records for other bundled builds and do not describe a binary in this public ZIP.
