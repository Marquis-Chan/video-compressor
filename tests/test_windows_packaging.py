# -*- coding: utf-8 -*-

import os
import sys
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "VideoCompressor-1.5.0-windows-x64"
ARCHIVE = ROOT / "dist" / f"{PACKAGE_NAME}.zip"


class WindowsPackagingContractTests(unittest.TestCase):
    def test_windows_dependencies_are_exactly_locked(self) -> None:
        self.assertEqual(
            (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines(),
            ["PySide6-Essentials==6.11.1", "psutil==7.2.2"],
        )
        self.assertEqual(
            (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines(),
            ["-r requirements.txt", "pyinstaller==6.21.0", "pytest==9.1.1"],
        )

    def test_build_script_enforces_a_single_clean_delivery(self) -> None:
        script = (ROOT / "build_windows.ps1").read_text(encoding="utf-8")

        self.assertIn('import PySide6, PyInstaller, psutil', script)
        self.assertIn('-m pytest -p no:cacheprovider', script)
        self.assertIn(
            '("PySide6_Essentials", "shiboken6", "PyInstaller", "psutil")',
            script,
        )
        self.assertNotIn('"PySide6_Essentials", "PySide6"', script)
        self.assertIn('Compress-Archive -Path $AppDir', script)
        self.assertIn('"$PackageName/$(Get-DeliveryRelativePath', script)
        self.assertIn('Remove-Item -LiteralPath $AppDir -Recurse -Force', script)
        self.assertNotIn('"$Archive.sha256"', script)


@unittest.skipUnless(sys.platform == "win32", "requires Windows")
@unittest.skipUnless(
    os.environ.get("VIDEO_COMPRESSOR_VERIFY_WINDOWS_ARCHIVE") == "1",
    "set VIDEO_COMPRESSOR_VERIFY_WINDOWS_ARCHIVE=1 after building",
)
class WindowsArchiveTests(unittest.TestCase):
    def test_portable_zip_has_one_top_level_folder_and_required_files(self) -> None:
        self.assertTrue(ARCHIVE.is_file(), ARCHIVE)
        with zipfile.ZipFile(ARCHIVE) as archive:
            names = {name.replace("\\", "/") for name in archive.namelist()}

        top_levels = {name.split("/", 1)[0] for name in names if name}
        self.assertEqual(top_levels, {PACKAGE_NAME})
        required = {
            f"{PACKAGE_NAME}/VideoCompressor.exe",
            f"{PACKAGE_NAME}/README.txt",
            f"{PACKAGE_NAME}/THIRD_PARTY_NOTICES.txt",
            f"{PACKAGE_NAME}/BUILD_MANIFEST.txt",
        }
        self.assertTrue(required.issubset(names), sorted(required - names))
        self.assertTrue(any(name.endswith("/ffmpeg.exe") for name in names))
        self.assertTrue(any(name.endswith("/ffprobe.exe") for name in names))
        self.assertFalse(any(name.endswith("/ffplay.exe") for name in names))


if __name__ == "__main__":
    unittest.main()
