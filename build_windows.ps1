$ErrorActionPreference = "Stop"

$Version = "1.5.0"
$PackageName = "VideoCompressor-$Version-windows-x64"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root "python\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { $Python = "python" }

& $Python -c "import PySide6, PyInstaller, psutil"
if ($LASTEXITCODE -ne 0) {
    throw "Build dependencies are missing. Provision them from the approved offline environment; this script does not install packages."
}

$env:QT_QPA_PLATFORM = "offscreen"
$env:PYTHONDONTWRITEBYTECODE = "1"
& $Python -m pytest -p no:cacheprovider (Join-Path $Root "tests") -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed; the Windows package was not built." }

$FfmpegBin = Join-Path $Root "ffmpeg\windows-x64\bin"
if (-not (Test-Path -LiteralPath (Join-Path $FfmpegBin "ffmpeg.exe"))) {
    $FfmpegBin = Join-Path $Root "ffmpeg\bin"
}
$Ffmpeg = Join-Path $FfmpegBin "ffmpeg.exe"
$Ffprobe = Join-Path $FfmpegBin "ffprobe.exe"
foreach ($tool in @($Ffmpeg, $Ffprobe)) {
    if (-not (Test-Path -LiteralPath $tool)) { throw "Missing required tool: $tool" }
    & $tool -version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Tool cannot run: $tool" }
}

$FfmpegVersion = ((& $Ffmpeg -version 2>&1 | Select-Object -First 1) | Out-String).Trim()
$Encoders = (& $Ffmpeg -hide_banner -encoders 2>&1 | Out-String)
$Filters = (& $Ffmpeg -hide_banner -filters 2>&1 | Out-String)
foreach ($name in @("libx264", "aac")) {
    if ($Encoders -notmatch [regex]::Escape($name)) { throw "FFmpeg encoder missing: $name" }
}
foreach ($name in @("zscale", "tonemap")) {
    if ($Filters -notmatch [regex]::Escape($name)) { throw "FFmpeg filter missing: $name" }
}

$Icon = Join-Path $Root "assets\app_icon.ico"
if (-not (Test-Path -LiteralPath $Icon)) {
    & $Python (Join-Path $Root "tools\generate_icons.py")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$Dist = Join-Path $Root "dist"
$Work = Join-Path $Root "build"
$Spec = Join-Path $Work "spec"
New-Item -ItemType Directory -Force -Path $Spec | Out-Null
$GuiScript = Join-Path $Root "video_compress_gui.py"
$VersionFile = Join-Path $Root "assets\version_info.txt"
$SvgIcon = Join-Path $Root "assets\app_icon.svg"
$Args = @(
    "-m", "PyInstaller", "--clean", "--noconfirm", "--windowed",
    "--name", "VideoCompressor", "--icon", $Icon, "--version-file", $VersionFile,
    "--distpath", $Dist, "--workpath", (Join-Path $Work "windows-x64"), "--specpath", $Spec,
    "--add-binary", "$Ffmpeg;ffmpeg\bin", "--add-binary", "$Ffprobe;ffmpeg\bin",
    "--add-data", "$SvgIcon;assets",
    "--exclude-module", "PySide6.QtQml", "--exclude-module", "PySide6.QtQuick",
    "--exclude-module", "PySide6.QtQuickWidgets", "--exclude-module", "PySide6.QtWebEngineCore",
    "--exclude-module", "PySide6.QtWebEngineWidgets", "--exclude-module", "PySide6.QtPdf",
    "--exclude-module", "PySide6.QtCharts", "--exclude-module", "PySide6.QtVirtualKeyboard",
    $GuiScript
)
& $Python @Args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$PyInstallerDir = Join-Path $Dist "VideoCompressor"
if (-not (Test-Path -LiteralPath $PyInstallerDir -PathType Container)) {
    throw "PyInstaller output is missing: $PyInstallerDir"
}
$AppDir = Join-Path $Dist $PackageName
if (Test-Path -LiteralPath $AppDir) { Remove-Item -LiteralPath $AppDir -Recurse -Force }
Move-Item -LiteralPath $PyInstallerDir -Destination $AppDir

Copy-Item -LiteralPath (Join-Path $Root "README.txt") -Destination $AppDir -Force
Copy-Item -LiteralPath (Join-Path $Root "THIRD_PARTY_NOTICES.txt") -Destination $AppDir -Force
$LicenseDir = Join-Path $AppDir "licenses"
New-Item -ItemType Directory -Force -Path $LicenseDir | Out-Null
Copy-Item -LiteralPath (Join-Path $Root "licenses\README.txt") -Destination $LicenseDir -Force
Copy-Item -LiteralPath (Join-Path $Root "licenses\FFMPEG_SOURCE.txt") -Destination $LicenseDir -Force

$LicenseCopyScript = @'
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys

destination = Path(sys.argv[1])
packages = ("PySide6_Essentials", "shiboken6", "PyInstaller", "psutil")
inventory = [
    "Python package license inventory",
    "",
    "Files below were copied from the build interpreter's dist-info/licenses directories.",
    "",
]

for package in packages:
    dist = distribution(package)
    copied = []
    folder = destination / f"{package}-{dist.version}"
    for relative in dist.files or ():
        normalized = str(relative).replace("\\", "/")
        marker = ".dist-info/licenses/"
        if marker in normalized:
            license_relative = normalized.split(marker, 1)[1]
        elif ".dist-info/" in normalized:
            candidate = normalized.rsplit("/", 1)[-1]
            if not candidate.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                continue
            license_relative = candidate
        else:
            continue
        source = Path(dist.locate_file(relative))
        if not source.is_file():
            continue
        target = folder / Path(license_relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(license_relative)
    if not copied:
        raise SystemExit(f"No dist-info/licenses files found for {package} {dist.version}")
    license_expression = dist.metadata.get("License-Expression") or dist.metadata.get("License") or "not declared"
    inventory.append(f"{package} {dist.version}")
    inventory.append(f"Declared license: {license_expression}")
    inventory.extend(f"File: {package}-{dist.version}/{name}" for name in sorted(copied))
    inventory.append("")

(destination / "PYTHON_PACKAGE_LICENSES.txt").write_text(
    "\n".join(inventory), encoding="utf-8", newline="\n"
)
'@
$LicenseCopyScript | & $Python - $LicenseDir
if ($LASTEXITCODE -ne 0) { throw "Failed to collect Python package licenses from the build interpreter." }

function Invoke-NativeCapture {
    param([Parameter(Mandatory = $true)][string]$FilePath, [string]$Arguments)
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = $Arguments
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) { throw "Could not start $FilePath" }
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "$FilePath $Arguments exited with code $($process.ExitCode)." }
        return ($stdout + $stderr).Trim()
    }
    finally {
        $process.Dispose()
    }
}

$FfmpegSourceText = Get-Content -LiteralPath (Join-Path $Root "licenses\FFMPEG_SOURCE.txt") -Raw
$FfmpegLicenseOutput = Invoke-NativeCapture -FilePath $Ffmpeg -Arguments "-L"
$FfprobeLicenseOutput = Invoke-NativeCapture -FilePath $Ffprobe -Arguments "-L"
$FfmpegHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Ffmpeg).Hash.ToLowerInvariant()
$FfprobeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Ffprobe).Hash.ToLowerInvariant()
$FfmpegDeliveryInfo = @(
    $FfmpegSourceText.TrimEnd(),
    "",
    "Exact bundled binaries",
    "ffmpeg SHA256: $FfmpegHash",
    "ffprobe SHA256: $FfprobeHash",
    "",
    "ffmpeg -L output",
    $FfmpegLicenseOutput,
    "",
    "ffprobe -L output",
    $FfprobeLicenseOutput
) -join "`r`n"
[IO.File]::WriteAllText(
    (Join-Path $LicenseDir "FFMPEG_LICENSE_AND_SOURCE.txt"),
    $FfmpegDeliveryInfo + "`r`n",
    [Text.UTF8Encoding]::new($false)
)

$RequiredDeliveryFiles = @(
    "README.txt",
    "THIRD_PARTY_NOTICES.txt",
    "licenses\README.txt",
    "licenses\FFMPEG_SOURCE.txt",
    "licenses\FFMPEG_LICENSE_AND_SOURCE.txt",
    "licenses\PYTHON_PACKAGE_LICENSES.txt"
)
foreach ($relativePath in $RequiredDeliveryFiles) {
    $path = Join-Path $AppDir $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required delivery file is missing: $relativePath"
    }
}
foreach ($packagePrefix in @("PySide6_Essentials-", "shiboken6-", "PyInstaller-", "psutil-")) {
    $packageLicenseFiles = Get-ChildItem -LiteralPath $LicenseDir -Recurse -File |
        Where-Object { $_.FullName.Substring($LicenseDir.Length + 1).StartsWith($packagePrefix, [StringComparison]::OrdinalIgnoreCase) }
    if (-not $packageLicenseFiles) { throw "No delivered license text found for $packagePrefix" }
}

$ManifestPath = Join-Path $AppDir "BUILD_MANIFEST.txt"
function Get-DeliveryRelativePath {
    param([Parameter(Mandatory = $true)][string]$BasePath, [Parameter(Mandatory = $true)][string]$TargetPath)
    $base = [IO.Path]::GetFullPath($BasePath).TrimEnd("\", "/") + [IO.Path]::DirectorySeparatorChar
    $target = [IO.Path]::GetFullPath($TargetPath)
    if (-not $target.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Delivery file is outside the application directory: $target"
    }
    return $target.Substring($base.Length).Replace("\", "/")
}

$BuildTime = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
$ManifestLines = [Collections.Generic.List[string]]::new()
$ManifestLines.Add("Video Compressor build manifest")
$ManifestLines.Add("Version: $Version")
$ManifestLines.Add("Platform: Windows")
$ManifestLines.Add("Architecture: x64")
$ManifestLines.Add("Build-Time-UTC: $BuildTime")
$ManifestLines.Add("FFmpeg-Version: $FfmpegVersion")
$ManifestLines.Add("Hash-Algorithm: SHA256")
$ManifestLines.Add("Hash-Scope: every regular delivery file except BUILD_MANIFEST.txt (self-reference)")
$ManifestLines.Add("")
$ManifestLines.Add("SHA256  Bytes  Path")
$ManifestFiles = Get-ChildItem -LiteralPath $AppDir -Recurse -File |
    Where-Object { $_.FullName -ne $ManifestPath } |
    Sort-Object FullName
foreach ($file in $ManifestFiles) {
    $relativePath = Get-DeliveryRelativePath -BasePath $AppDir -TargetPath $file.FullName
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
    $ManifestLines.Add("$hash  $($file.Length)  $relativePath")
}
[IO.File]::WriteAllLines($ManifestPath, $ManifestLines, [Text.UTF8Encoding]::new($false))
if (-not (Select-String -LiteralPath $ManifestPath -SimpleMatch "licenses/FFMPEG_LICENSE_AND_SOURCE.txt" -Quiet)) {
    throw "BUILD_MANIFEST.txt does not include the FFmpeg license/source record."
}
foreach ($binaryName in @("ffmpeg.exe", "ffprobe.exe")) {
    if (-not (Select-String -LiteralPath $ManifestPath -SimpleMatch $binaryName -Quiet)) {
        throw "BUILD_MANIFEST.txt does not include the delivered $binaryName."
    }
}

$AppBytes = (Get-ChildItem -LiteralPath $AppDir -Recurse -File | Measure-Object Length -Sum).Sum
$AppMB = [math]::Round($AppBytes / 1MB, 1)
if ($AppMB -gt 320) { throw "Extracted build is $AppMB MB; budget is 320 MB." }

$Archive = Join-Path $Dist "VideoCompressor-$Version-windows-x64.zip"
if (Test-Path -LiteralPath $Archive) { Remove-Item -LiteralPath $Archive -Force }
Compress-Archive -Path $AppDir -DestinationPath $Archive -CompressionLevel Optimal

Add-Type -AssemblyName System.IO.Compression.FileSystem
$ExpectedEntries = Get-ChildItem -LiteralPath $AppDir -Recurse -File | ForEach-Object {
    "$PackageName/$(Get-DeliveryRelativePath -BasePath $AppDir -TargetPath $_.FullName)"
}
$Zip = [IO.Compression.ZipFile]::OpenRead($Archive)
try {
    $ArchiveEntries = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($entry in $Zip.Entries) { [void]$ArchiveEntries.Add($entry.FullName.Replace("\", "/")) }
    $MissingEntries = @($ExpectedEntries | Where-Object { -not $ArchiveEntries.Contains($_) })
    if ($MissingEntries.Count -gt 0) {
        throw "Portable ZIP is missing delivery files: $($MissingEntries -join ', ')"
    }
} finally {
    $Zip.Dispose()
}

$ZipMB = [math]::Round((Get-Item -LiteralPath $Archive).Length / 1MB, 1)
if ($ZipMB -gt 180) { throw "Portable ZIP is $ZipMB MB; budget is 180 MB." }
$Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Archive

Remove-Item -LiteralPath $AppDir -Recurse -Force

Write-Host "Verified extracted build: $AppMB MB"
Write-Host "Portable ZIP: $Archive ($ZipMB MB)"
Write-Host "SHA256: $($Hash.Hash.ToLower())"
