@echo off
echo ============================================
echo  ScriptScanner v3.0 - Build Script
echo ============================================
echo.

:: Change to agent directory (where this .bat lives)
cd /d "%~dp0"

echo [1/3] Installing dependencies...
pip install pyinstaller pystray pillow imagehash
echo.

echo [2/3] Building ScriptScanner.exe...
python -m PyInstaller scriptscanner.spec --clean -y
echo.

echo [3/3] Done!
if exist "dist\ScriptScanner.exe" (
    echo  SUCCESS: dist\ScriptScanner.exe is ready.
    echo.
    echo  Drop ScriptScanner.exe on any pharmacy PC.
    echo  Config.json will be created next to the exe on first run.
) else (
    echo  ERROR: Build failed - check output above.
)

echo.
pause
