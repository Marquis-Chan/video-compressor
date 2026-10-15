"""Build the public portable app. FFmpeg is supplied separately by the user."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from importlib.metadata import distribution
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Build the Windows portable release on Windows x64.")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from compressor_core import APP_VERSION

    package = f"VideoCompressor-{APP_VERSION}-windows-x64-public"
    build_root = root / "build"
    build_root.mkdir(exist_ok=True)
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    archive = dist / f"{package}.zip"
    if archive.exists():
        raise SystemExit(f"Refusing to overwrite existing release: {archive}")
    required = ["README.txt", "LICENSE", "THIRD_PARTY_NOTICES.txt", "licenses/LGPL-3.0.txt", "licenses/GPL-3.0.txt", "licenses/PYTHON-LICENSE.txt", "licenses/SOURCE_ACCESS.md"]
    for item in required:
        if not (root / item).is_file():
            raise SystemExit(f"Missing public delivery document: {item}")
    with tempfile.TemporaryDirectory(prefix="public-windows-", dir=build_root) as tmp:
        temp = Path(tmp).resolve()
        if not temp.is_relative_to(build_root.resolve()):
            raise SystemExit("Temporary build escaped its designated build directory.")
        args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--name", "VideoCompressor", "--icon", str(root / "assets/app_icon.ico"), "--version-file", str(root / "assets/version_info.txt"), "--distpath", str(temp / "dist"), "--workpath", str(temp / "work"), "--specpath", str(temp), "--add-data", f"{root / 'assets/app_icon.svg'};assets"]
        for module in ("PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtPdf", "PySide6.QtCharts", "PySide6.QtVirtualKeyboard"):
            args.extend(["--exclude-module", module])
        args.append(str(root / "video_compress_gui.py"))
        subprocess.run(args, cwd=root, check=True)
        app = temp / "dist/VideoCompressor"
        for item in required:
            target = app / item
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / item, target)
        inventory = {"python": sys.version, "build_time_utc": datetime.now(timezone.utc).isoformat(), "ffmpeg_bundled": False, "dependencies": {}}
        for name in ("PySide6_Essentials", "shiboken6", "PyInstaller", "psutil"):
            package_dist = distribution(name)
            inventory["dependencies"][name] = {"version": package_dist.version, "license": package_dist.metadata.get("License-Expression") or package_dist.metadata.get("License")}
            for item in package_dist.files or ():
                normalized = str(item).replace("\\", "/")
                marker = ".dist-info/licenses/"
                if marker in normalized:
                    relative = normalized.split(marker, 1)[1]
                elif ".dist-info/" in normalized and Path(normalized).name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                    relative = Path(normalized).name
                else:
                    continue
                source = Path(package_dist.locate_file(item))
                if source.is_file():
                    target = app / "licenses" / f"{name}-{package_dist.version}" / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        (app / "licenses/BUILD_INPUTS.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        forbidden = {"ffmpeg.exe", "ffprobe.exe", "ffplay.exe"}
        files = sorted(p for p in app.rglob("*") if p.is_file())
        if any(p.name.lower() in forbidden for p in files):
            raise SystemExit("The public app must not bundle FFmpeg executables.")
        lines = [f"Video Compressor {APP_VERSION} public Windows x64", "FFmpeg: external; not included", "Hash scope: all files except this manifest", "", "SHA256  Bytes  Path"]
        records = {}
        for file in files:
            relative = file.relative_to(app).as_posix()
            records[relative] = (sha256(file), file.stat().st_size)
            lines.append(f"{records[relative][0]}  {records[relative][1]}  {relative}")
        (app / "BUILD_MANIFEST.txt").write_text("\n".join(lines)+"\n", encoding="utf-8")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for file in sorted(p for p in app.rglob("*") if p.is_file()):
                z.write(file, f"{package}/{file.relative_to(app).as_posix()}")
        with zipfile.ZipFile(archive) as z:
            assert {n.split("/", 1)[0] for n in z.namelist()} == {package}
            for relative, (digest, size) in records.items():
                content = z.read(f"{package}/{relative}")
                assert len(content) == size and hashlib.sha256(content).hexdigest() == digest, relative
            assert all(Path(n).name.lower() not in forbidden for n in z.namelist())
            assert f"{package}/VideoCompressor.exe" in z.namelist()
    digest = sha256(archive)
    (dist / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size, "sha256": digest, "verified_files": len(records), "ffmpeg_bundled": False}))


if __name__ == "__main__":
    main()
