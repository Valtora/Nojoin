@echo off
title Nojoin v0.5.2 - Meeting Recording and Transcription
color 0A

echo.
echo ================================================================
echo                    Launching Nojoin v0.5.2
echo ================================================================
echo.

:: Get the directory where this batch file is located
set "SCRIPT_DIR=%~dp0"

:: Check if we're in the right directory
if not exist "%SCRIPT_DIR%Nojoin.py" (
    echo ERROR: Cannot find Nojoin.py in the current directory.
    echo Please make sure you're running this script from the Nojoin folder.
    echo.
    echo Current directory: %SCRIPT_DIR%
    echo.
    pause
    exit /b 1
)

:: Check if virtual environment exists
if not exist "%SCRIPT_DIR%.venv" (
    echo ERROR: Virtual environment not found.
    echo.
    echo It looks like Nojoin hasn't been set up yet.
    echo Please run 'setup_windows.bat' first to install Nojoin.
    echo.
    pause
    exit /b 1
)

:: Check if virtual environment has Python
if not exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    echo ERROR: Virtual environment is incomplete.
    echo.
    echo Please run 'setup_windows.bat' to reinstall Nojoin.
    echo.
    pause
    exit /b 1
)

echo [1/3] Activating virtual environment...
call "%SCRIPT_DIR%.venv\Scripts\activate.bat"
if %errorlevel% neq 0 (
    echo ERROR: Failed to activate virtual environment.
    echo.
    echo Please run 'setup_windows.bat' to fix the installation.
    echo.
    pause
    exit /b 1
)

echo [2/3] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not working in the virtual environment.
    echo.
    echo Please run 'setup_windows.bat' to fix the installation.
    echo.
    pause
    exit /b 1
)

echo [3/3] Starting Nojoin...
echo.

:: Change to the script directory to ensure relative paths work
cd /d "%SCRIPT_DIR%"

:: Launch Nojoin
python Nojoin.py

:: Check if there was an error
if %errorlevel% neq 0 (
    echo.
    echo ================================================================
    echo                    Nojoin Exited with Error
    echo ================================================================
    echo.
    echo Nojoin encountered an error and stopped running.
    echo.
    echo Common solutions:
    echo 1. Check that your audio devices are working
    echo 2. Run 'update_nojoin.bat' to update dependencies
    echo 3. Run 'setup_windows.bat' to reinstall if problems persist
    echo.
    echo For more help, check the logs or visit:
    echo https://github.com/Valtora/Nojoin
    echo.
) else (
    echo.
    echo ================================================================
    echo                    Nojoin Closed Successfully
    echo ================================================================
    echo.
)

pause