@echo off
setlocal enabledelayedexpansion
title Nojoin v0.5.2 - Meeting Recording and Transcription

:: Set up user tools directories
set "USER_TOOLS_DIR=%APPDATA%\NojoinTools"
set "PYTHON_DIR=%USER_TOOLS_DIR%\Python311"
set "FFMPEG_DIR=%USER_TOOLS_DIR%\ffmpeg"

echo.
echo ================================================================
echo                       Starting Application
echo ================================================================
echo.

:: Change to the script's directory
cd /d "%~dp0"

:: Check if we're in the right directory
if not exist "Nojoin.py" (
    echo ERROR: Could not find Nojoin.py in the current directory.
    echo Please make sure this script is in the same folder as Nojoin.py
    echo.
    echo Current directory: %CD%
    echo.
    pause
    exit /b 1
)

:: Update PATH to include portable tools if they exist
if exist "%PYTHON_DIR%" (
    set "PATH=%PYTHON_DIR%;%PATH%"
    echo Using portable Python from user directory...
)

if exist "%FFMPEG_DIR%\bin" (
    set "PATH=%FFMPEG_DIR%\bin;%PATH%"
    echo Using portable ffmpeg from user directory...
)

:: Check if virtual environment exists
if not exist ".venv" (
    echo ERROR: Python virtual environment not found.
    echo.
    echo This usually means Nojoin hasn't been set up yet.
    echo Please run the setup script first:
    echo.
    echo   1. Double-click 'setup_windows.bat' to set up Nojoin
    echo   2. Or follow the manual setup instructions in README.md
    echo.
    pause
    exit /b 1
)

:: Check if virtual environment activation script exists
if not exist ".venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment appears to be corrupted.
    echo The activation script is missing.
    echo.
    echo Please run the setup script again to recreate the environment:
    echo   Double-click 'setup_windows.bat'
    echo.
    pause
    exit /b 1
)

:: Activate virtual environment
echo Activating Python virtual environment...
call .venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo ERROR: Failed to activate virtual environment.
    echo.
    echo This might indicate a corrupted Python installation.
    echo Please try running the setup script again:
    echo   Double-click 'setup_windows.bat'
    echo.
    pause
    exit /b 1
)

:: Verify Python is working
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not working correctly in the virtual environment.
    echo.
    echo This might indicate a corrupted installation.
    echo Please try running the setup script again:
    echo   Double-click 'setup_windows.bat'
    echo.
    pause
    exit /b 1
)

:: Check if main dependencies are installed
echo Checking core dependencies...
python -c "import sys; sys.path.insert(0, '.'); from nojoin.utils.config_manager import config_manager" 2>nul
if %errorlevel% neq 0 (
    echo WARNING: Core dependencies appear to be missing or corrupted.
    echo.
    echo Attempting to install/update dependencies...
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo ERROR: Failed to install dependencies.
        echo.
        echo Please check your internet connection and try again, or
        echo run the setup script to reinstall everything:
        echo   Double-click 'setup_windows.bat'
        echo.
        pause
        exit /b 1
    )
    echo Dependencies updated successfully.
)

:: Final verification
echo Performing final system check...
python -c "import torch; import whisper; import pyannote.audio; print('✓ All core libraries available')" 2>nul
if %errorlevel% neq 0 (
    echo WARNING: Some advanced features may not work correctly.
    echo The application will still start, but you may experience issues.
    echo.
    echo For best results, consider running the setup script again:
    echo   Double-click 'setup_windows.bat'
    echo.
    timeout /t 3 >nul
)

echo.
echo ✓ Environment ready
echo ✓ Starting Nojoin...
echo.
echo ================================================================
echo             Welcome to Nojoin - Ready for Recording!
echo ================================================================
echo.

:: Launch Nojoin
python Nojoin.py

:: Handle exit status
if %errorlevel% equ 0 (
    echo.
    echo Nojoin closed successfully.
) else (
    echo.
    echo ================================================================
    echo                    Nojoin encountered an error
    echo ================================================================
    echo.
    echo Exit code: %errorlevel%
    echo.
    echo Common solutions:
    echo 1. Check the logs in the 'logs' folder for detailed error information
    echo 2. Ensure your microphone/audio devices are properly connected
    echo 3. Try running the setup script again: setup_windows.bat
    echo 4. Check the GitHub repository for known issues and solutions
    echo.
    echo For support, visit: https://github.com/Valtora/Nojoin
    echo.
    echo The application window will remain open for troubleshooting.
    echo Close this window when you're done reviewing the information.
    echo.
    pause
)

:: Deactivate virtual environment
call .venv\Scripts\deactivate.bat 2>nul

echo.
echo Thank you for using Nojoin!
timeout /t 2 >nul