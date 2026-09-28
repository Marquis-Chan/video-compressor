@echo off
chcp 65001 >nul 2>&1
title Install Video Compressor Runtime

set "SCRIPT_DIR=%~dp0"
set "PYTHON_DIR=%SCRIPT_DIR%python"
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"

if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [INFO] pip is not available. Downloading get-pip.py...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%TEMP%\get-pip.py'"
    if errorlevel 1 (
        echo [ERROR] Could not download get-pip.py.
        pause
        exit /b 1
    )
    "%PYTHON_EXE%" "%TEMP%\get-pip.py"
    if errorlevel 1 (
        echo [ERROR] pip installation failed.
        pause
        exit /b 1
    )
)

"%PYTHON_EXE%" -m pip install -r "%SCRIPT_DIR%requirements.txt"
if errorlevel 1 (
    echo [ERROR] GUI dependency installation failed.
    pause
    exit /b 1
)

echo [OK] Runtime dependencies installed from the pinned requirements file.
pause
