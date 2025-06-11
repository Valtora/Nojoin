@echo off
setlocal enabledelayedexpansion

:: Nojoin Setup Script for Windows (User Mode - No Admin Required)
:: This script installs everything to user directories for maximum security

echo.
echo ================================================================
echo                 Nojoin v0.5.2 Setup for Windows
echo                        (User Mode - No Admin)
echo ================================================================
echo.
echo This script will set up Nojoin in your user directories.
echo No administrator privileges required for maximum security.
echo.
echo Prerequisites that will be installed if missing:
echo - Python 3.11.9 (portable, user directory)
echo - ffmpeg (portable, user directory)
echo - Virtual environment and dependencies
echo - CUDA support (if compatible hardware detected)
echo.
pause

:: Set up user directories
set "USER_TOOLS_DIR=%APPDATA%\NojoinTools"
set "PYTHON_DIR=%USER_TOOLS_DIR%\Python311"
set "FFMPEG_DIR=%USER_TOOLS_DIR%\ffmpeg"

:: Create tools directory
if not exist "%USER_TOOLS_DIR%" mkdir "%USER_TOOLS_DIR%"

:: Check if we're in the correct directory
echo [1/8] Checking project directory...
if not exist "Nojoin.py" (
    echo ERROR: This script must be run from the Nojoin project directory.
    echo Please navigate to the directory containing Nojoin.py and run this script again.
    echo.
    pause
    exit /b 1
)
echo Project directory verified.

:: Check if we have curl for downloads
echo [2/8] Checking download capabilities...
curl --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: curl is not available for downloading dependencies.
    echo Please ensure you have Windows 10 version 1803 or later with curl available.
    echo Alternatively, you can install curl or use the manual setup instructions.
    echo.
    pause
    exit /b 1
)
echo Download capabilities verified.

:: Check and install Python 3.11.9 to user directory
echo [3/8] Checking Python 3.11.9 installation...

:: First check if we already have Python in our user tools directory
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
if exist "%PYTHON_EXE%" (
    "%PYTHON_EXE%" --version >nul 2>&1
    if %errorlevel% equ 0 (
        for /f "tokens=2" %%i in ('"%PYTHON_EXE%" --version 2^>^&1') do set PYTHON_VERSION=%%i
        echo Found portable Python !PYTHON_VERSION! in user directory.
        goto python_ready
    )
)

:: Check system Python
python --version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
    echo Found system Python !PYTHON_VERSION!
    
    :: Check if Python version is 3.11.x
    echo !PYTHON_VERSION! | findstr /R "^3\.11\." >nul
    if %errorlevel% equ 0 (
        echo System Python 3.11.x detected. You can use this or install portable version.
        set /p USE_SYSTEM="Use system Python 3.11.x? (Y/n): "
        if /i "!USE_SYSTEM!" neq "n" (
            set "PYTHON_EXE=python"
            goto python_ready
        )
    ) else (
        echo WARNING: System Python !PYTHON_VERSION! found, but Nojoin requires Python 3.11.9.
        echo We'll install a portable Python 3.11.9 to your user directory.
        echo.
    )
) else (
    echo No system Python found. Installing portable Python 3.11.9...
)

:: Download and install portable Python 3.11.9
echo Installing portable Python 3.11.9 to user directory...
echo This may take a few minutes...

set "PYTHON_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
set "PYTHON_ZIP=%TEMP%\python3119.zip"

echo Downloading Python 3.11.9...
curl -L -o "%PYTHON_ZIP%" "%PYTHON_URL%"
if %errorlevel% neq 0 (
    echo ERROR: Failed to download Python 3.11.9
    echo Please check your internet connection and try again.
    pause
    exit /b 1
)

echo Extracting Python to %PYTHON_DIR%...
if exist "%PYTHON_DIR%" rmdir /s /q "%PYTHON_DIR%"
mkdir "%PYTHON_DIR%"

:: Extract using PowerShell
powershell -command "Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%PYTHON_DIR%' -Force"
if %errorlevel% neq 0 (
    echo ERROR: Failed to extract Python
    pause
    exit /b 1
)

:: Download get-pip.py
echo Setting up pip for portable Python...
curl -L -o "%PYTHON_DIR%\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
if %errorlevel% neq 0 (
    echo ERROR: Failed to download pip installer
    pause
    exit /b 1
)

:: Configure portable Python
echo python311._pth > "%PYTHON_DIR%\python311._pth"
echo .>> "%PYTHON_DIR%\python311._pth"
echo import site>> "%PYTHON_DIR%\python311._pth"

:: Install pip
"%PYTHON_EXE%" "%PYTHON_DIR%\get-pip.py" --user
if %errorlevel% neq 0 (
    echo ERROR: Failed to install pip
    pause
    exit /b 1
)

echo Portable Python 3.11.9 installed successfully.

:python_ready
echo ✓ Python 3.11.9 ready

:: Check and install ffmpeg to user directory
echo [4/8] Checking ffmpeg installation...

set "FFMPEG_EXE=%FFMPEG_DIR%\bin\ffmpeg.exe"
if exist "%FFMPEG_EXE%" (
    "%FFMPEG_EXE%" -version >nul 2>&1
    if %errorlevel% equ 0 (
        echo Found portable ffmpeg in user directory.
        goto ffmpeg_ready
    )
)

:: Check system ffmpeg
ffmpeg -version >nul 2>&1
if %errorlevel% equ 0 (
    echo Found system ffmpeg.
    set /p USE_SYSTEM_FFMPEG="Use system ffmpeg? (Y/n): "
    if /i "!USE_SYSTEM_FFMPEG!" neq "n" (
        goto ffmpeg_ready
    )
)

:: Download and install portable ffmpeg
echo Installing portable ffmpeg to user directory...
set "FFMPEG_URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
set "FFMPEG_ZIP=%TEMP%\ffmpeg.zip"

echo Downloading ffmpeg...
curl -L -o "%FFMPEG_ZIP%" "%FFMPEG_URL%"
if %errorlevel% neq 0 (
    echo ERROR: Failed to download ffmpeg
    echo Please check your internet connection and try again.
    pause
    exit /b 1
)

echo Extracting ffmpeg to %FFMPEG_DIR%...
if exist "%FFMPEG_DIR%" rmdir /s /q "%FFMPEG_DIR%"
mkdir "%FFMPEG_DIR%"

:: Extract using PowerShell and find the actual directory
powershell -command "Expand-Archive -Path '%FFMPEG_ZIP%' -DestinationPath '%TEMP%\ffmpeg_extract' -Force"
if %errorlevel% neq 0 (
    echo ERROR: Failed to extract ffmpeg
    pause
    exit /b 1
)

:: Find the extracted directory and move contents
for /d %%i in ("%TEMP%\ffmpeg_extract\ffmpeg-*") do (
    xcopy "%%i\*" "%FFMPEG_DIR%\" /E /I /Y >nul
    rmdir /s /q "%%i" >nul 2>&1
)
rmdir /s /q "%TEMP%\ffmpeg_extract" >nul 2>&1

echo Portable ffmpeg installed successfully.

:ffmpeg_ready
echo ✓ ffmpeg ready

:: Update PATH for this session to include our tools
set "PATH=%PYTHON_DIR%;%FFMPEG_DIR%\bin;%PATH%"

:: Handle virtual environment
echo [5/8] Setting up Python virtual environment...
if exist ".venv" (
    echo Virtual environment already exists.
    set /p RECREATE="Do you want to recreate it? This will delete existing dependencies. (y/N): "
    if /i "!RECREATE!" equ "y" (
        echo Removing existing virtual environment...
        rmdir /s /q .venv
        goto create_venv
    ) else (
        echo Using existing virtual environment.
        goto activate_venv
    )
) else (
    goto create_venv
)

:create_venv
echo Creating virtual environment...
"%PYTHON_EXE%" -m venv .venv
if %errorlevel% neq 0 (
    echo ERROR: Failed to create virtual environment.
    echo This might be due to Python installation issues.
    pause
    exit /b 1
)

:activate_venv
echo Activating virtual environment...
call .venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo ERROR: Failed to activate virtual environment.
    pause
    exit /b 1
)

:: Install basic PyTorch first for CUDA detection
echo [6/8] Installing basic PyTorch for system analysis...
echo This may take a few minutes...
pip install torch torchvision torchaudio --quiet
if %errorlevel% neq 0 (
    echo ERROR: Failed to install basic PyTorch.
    echo Please check your internet connection.
    pause
    exit /b 1
)

:: Check for CUDA-capable GPU
echo [7/8] Checking for CUDA-capable hardware...
python -c "import torch; gpu_available = torch.cuda.is_available(); gpu_count = torch.cuda.device_count() if gpu_available else 0; print(f'CUDA Available: {gpu_available}'); print(f'GPU Count: {gpu_count}'); [print(f'GPU {i}: {torch.cuda.get_device_name(i)}') for i in range(gpu_count)]" 2>nul
set CUDA_CHECK_RESULT=%errorlevel%

if %CUDA_CHECK_RESULT% equ 0 (
    :: Run a more detailed CUDA check
    for /f "tokens=*" %%i in ('python -c "import torch; print('CUDA_AVAILABLE' if torch.cuda.is_available() else 'CUDA_NOT_AVAILABLE')" 2^>nul') do set CUDA_RESULT=%%i
    
    if "!CUDA_RESULT!" == "CUDA_AVAILABLE" (
        echo.
        echo ========================================
        echo  CUDA-Compatible GPU Detected!
        echo ========================================
        python -c "import torch; [print(f'  GPU {i}: {torch.cuda.get_device_name(i)}') for i in range(torch.cuda.device_count())]"
        echo.
        echo To get significantly faster transcription and processing,
        echo you can install NVIDIA CUDA Toolkit 12.8.
        echo.
        echo Note: CUDA Toolkit installation may require administrator privileges.
        echo You can install it later if preferred.
        echo.
        set /p INSTALL_CUDA="Would you like guidance for CUDA installation? (Y/n): "
        if /i "!INSTALL_CUDA!" neq "n" (
            echo.
            echo CUDA Installation Instructions:
            echo 1. Download NVIDIA CUDA Toolkit 12.8 from:
            echo    https://developer.nvidia.com/cuda-12-8-1-download-archive
            echo 2. Run the installer (may require administrator privileges)
            echo 3. Restart your computer if prompted
            echo 4. Run this setup script again to configure CUDA PyTorch
            echo.
            set /p CONTINUE_SETUP="Continue setup with CPU-only PyTorch for now? (Y/n): "
            if /i "!CONTINUE_SETUP!" equ "n" (
                echo Setup paused for CUDA installation.
                echo Run this script again after installing CUDA.
                pause
                exit /b 0
            )
        )
    ) else (
        echo No CUDA-capable GPU detected. Using CPU-only processing.
    )
) else (
    echo Could not check CUDA status. Using CPU-only processing.
)

:: Install remaining dependencies
echo [8/8] Installing remaining dependencies...
echo This may take several minutes...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo ERROR: Failed to install some dependencies.
    echo Please check your internet connection and try again.
    pause
    exit /b 1
)

:: Test the installation
echo.
echo Testing installation...
python -c "import sys; sys.path.insert(0, '.'); from nojoin.utils.config_manager import config_manager; print('✓ Configuration system working')" 2>nul
if %errorlevel% neq 0 (
    echo WARNING: Installation test failed. The application may not work correctly.
    echo You may need to restart your computer and try again.
    pause
) else (
    echo ✓ Installation test passed!
)

:: Create convenience scripts
echo.
echo Creating convenience scripts...

:: Create run script with PATH updates
echo @echo off > run_nojoin.bat
echo title Nojoin - Meeting Recording and Transcription >> run_nojoin.bat
echo cd /d "%%~dp0" >> run_nojoin.bat
echo set "PATH=%PYTHON_DIR%;%FFMPEG_DIR%\bin;%%PATH%%" >> run_nojoin.bat
echo call .venv\Scripts\activate.bat >> run_nojoin.bat
echo python Nojoin.py >> run_nojoin.bat
echo if %%errorlevel%% neq 0 ( >> run_nojoin.bat
echo     echo. >> run_nojoin.bat
echo     echo Nojoin encountered an error. Check the logs for details. >> run_nojoin.bat
echo     pause >> run_nojoin.bat
echo ^) >> run_nojoin.bat

:: Create update script
echo @echo off > update_nojoin.bat
echo title Update Nojoin Dependencies >> update_nojoin.bat
echo cd /d "%%~dp0" >> update_nojoin.bat
echo set "PATH=%PYTHON_DIR%;%FFMPEG_DIR%\bin;%%PATH%%" >> update_nojoin.bat
echo call .venv\Scripts\activate.bat >> update_nojoin.bat
echo echo Updating Nojoin dependencies... >> update_nojoin.bat
echo pip install --upgrade -r requirements.txt >> update_nojoin.bat
echo echo Update complete! >> update_nojoin.bat
echo pause >> update_nojoin.bat

:: Create desktop shortcut script
echo Creating desktop shortcut...
set "CURRENT_DIR=%CD%"
set "SHORTCUT_PATH=%USERPROFILE%\Desktop\Nojoin.lnk"

:: Create PowerShell script to create shortcut
echo $WshShell = New-Object -comObject WScript.Shell > create_shortcut.ps1
echo $Shortcut = $WshShell.CreateShortcut("!SHORTCUT_PATH!") >> create_shortcut.ps1
echo $Shortcut.TargetPath = "!CURRENT_DIR!\run_nojoin.bat" >> create_shortcut.ps1
echo $Shortcut.WorkingDirectory = "!CURRENT_DIR!" >> create_shortcut.ps1
echo $Shortcut.IconLocation = "!CURRENT_DIR!\assets\favicon.ico" >> create_shortcut.ps1
echo $Shortcut.Description = "Nojoin - Meeting Recording and Transcription" >> create_shortcut.ps1
echo $Shortcut.Save() >> create_shortcut.ps1

powershell -ExecutionPolicy Bypass -File create_shortcut.ps1 >nul 2>&1
del create_shortcut.ps1 >nul 2>&1

:: Clean up temporary files
if exist "%PYTHON_ZIP%" del "%PYTHON_ZIP%" >nul 2>&1
if exist "%FFMPEG_ZIP%" del "%FFMPEG_ZIP%" >nul 2>&1

echo.
echo ================================================================
echo                    Setup Complete!
echo ================================================================
echo.
echo ✓ Nojoin has been successfully set up on your Windows system!
echo ✓ All tools installed to user directories (no admin required)
echo.
echo Installation locations:
echo • Python 3.11.9: %PYTHON_DIR%
echo • ffmpeg: %FFMPEG_DIR%
echo • Virtual environment: %CD%\.venv
echo.
echo How to run Nojoin:
echo   1. Double-click the "Nojoin" shortcut on your desktop
echo   2. Double-click "run_nojoin.bat" in this folder
echo.
echo Additional utilities created:
echo   • run_nojoin.bat - Start Nojoin
echo   • update_nojoin.bat - Update dependencies
echo   • Desktop shortcut - Quick access to Nojoin
echo.
echo Important notes:
echo - Your recordings will be saved in the 'recordings' folder
echo - Settings and data are stored in the 'nojoin' folder
echo - All tools are in %USER_TOOLS_DIR%
echo.
echo For support, visit: https://github.com/Valtora/Nojoin
echo.
echo Press any key to launch Nojoin now...
pause >nul

:: Launch Nojoin
echo Launching Nojoin...
call run_nojoin.bat 