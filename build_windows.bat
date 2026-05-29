@echo off
REM Build the Windows distributable.
REM Requirements before running:
REM   1. Python 3.10+ with pip on PATH
REM   2. vendor\ffmpeg.exe and vendor\ffprobe.exe in place
REM      (Download from https://www.gyan.dev/ffmpeg/builds/ -> "release essentials")

setlocal

cd /d "%~dp0"

if not exist "vendor\ffmpeg.exe" (
    echo [ERROR] vendor\ffmpeg.exe is missing.
    echo Download from https://www.gyan.dev/ffmpeg/builds/ -^> release essentials,
    echo then place ffmpeg.exe and ffprobe.exe into the vendor\ directory.
    exit /b 1
)
if not exist "vendor\ffprobe.exe" (
    echo [ERROR] vendor\ffprobe.exe is missing.
    exit /b 1
)

echo [1/3] Installing build dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [ERROR] pip install failed.
    exit /b 1
)

echo [2/3] Cleaning previous build...
if exist "build" rmdir /s /q build
if exist "dist" rmdir /s /q dist

echo [3/3] Running PyInstaller...
python -m PyInstaller video_label_tool.spec --noconfirm
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

echo.
echo === Build complete ===
echo Output: dist\video_label_tool\
echo Launch: dist\video_label_tool\video_label_tool.exe

endlocal
