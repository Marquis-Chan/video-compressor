@echo off
chcp 65001 >nul 2>&1
title Video Compressor

set "SCRIPT_DIR=%~dp0"
set "PATH=%SCRIPT_DIR%ffmpeg\bin;%PATH%"

set "PYTHON_DIR=%SCRIPT_DIR%python"
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
set "PYTHONW_EXE=%PYTHON_DIR%\pythonw.exe"

if exist "%PYTHON_EXE%" goto :check_python

python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=python"
    goto :run_app
)

where python >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=python"
    goto :run_app
)

echo [ERROR] Python not found. Please install Python 3.x or keep the bundled python folder.
pause
exit /b 1

:check_python
set "PTH_FILE=%PYTHON_DIR%\python312._pth"
if exist "%PTH_FILE%" (
    findstr /C:"import site" "%PTH_FILE%" >nul 2>&1
    if errorlevel 1 (
        echo [INFO] Enabling third-party packages...
        (
            echo python312.zip
            echo .
            echo import site
        ) > "%PYTHON_DIR%\python312._pth"
    )
)

"%PYTHON_EXE%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not available.
    pause
    exit /b 1
)

:run_app
if /I "%~1"=="--cli" goto :cli_mode

"%PYTHON_EXE%" -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PySide6 is not installed, so the GUI cannot start.
    echo Run this once:
    echo   "%SCRIPT_DIR%install_gui_deps.bat"
    echo.
    echo Starting CLI fallback...
    "%PYTHON_EXE%" "%SCRIPT_DIR%video_compress_cli.py" %*
    exit /b %ERRORLEVEL%
)

if not exist "%PYTHONW_EXE%" set "PYTHONW_EXE=%PYTHON_EXE%"
start "" "%PYTHONW_EXE%" "%SCRIPT_DIR%video_compress_gui.py" %*
exit /b 0

:cli_mode
shift
set "CLI_ARGS="
:collect_cli_args
if "%~1"=="" goto :run_cli_mode
set CLI_ARGS=%CLI_ARGS% "%~1"
shift
goto :collect_cli_args
:run_cli_mode
"%PYTHON_EXE%" "%SCRIPT_DIR%video_compress_cli.py" %CLI_ARGS%
exit /b %ERRORLEVEL%
