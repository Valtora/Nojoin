@echo off
setlocal enabledelayedexpansion

:: Nojoin Setup Script for Windows
:: This script provides a fully automated setup experience for Windows users
:: It assumes minimal technical knowledge and handles dependency installation automatically

echo.
echo ================================================================
echo                  Nojoin v0.5.2 Setup for Windows
echo ================================================================
echo.
echo This script will automatically set up Nojoin on your Windows machine.
echo It will check for and install missing dependencies as needed.
echo.
echo Prerequisites that will be checked and installed if missing:
echo - Python 3.11.9
echo - ffmpeg
echo - Virtual environment and dependencies
echo - CUDA support (if compatible hardware detected)
echo.
pause

:: Function to check if running as administrator
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo WARNING: This script is not running as administrator.
    echo Some installations may fail without administrator privileges.
    echo.
    set /p CONTINUE_NO_ADMIN="Do you want to continue anyway? (y/N): "
    if /i "!CONTINUE_NO_ADMIN!" neq "y" (
        echo.
        echo Please right-click this script and select "Run as administrator"
        pause
        exit /b 1
    )
)

:: Check and install winget if needed
echo [1/8] Checking Windows Package Manager (winget)...
winget --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Windows Package Manager (winget) is not available.
    echo This is required for automatic dependency installation.
    echo.
    echo Please install the "App Installer" from the Microsoft Store:
    echo https://www.microsoft.com/store/productId/9NBLGGH4NNS1
    echo.
    echo After installation, please restart this script.
    pause
    exit /b 1
)
echo Windows Package Manager found.

:: Check if we're in the correct directory
echo [2/8] Checking project directory...
if not exist "Nojoin.py" (
    echo ERROR: This script must be run from the Nojoin project directory.
    echo Please navigate to the directory containing Nojoin.py and run this script again.
    echo.
    pause
    exit /b 1
)
echo Project directory verified.

:: Check and install Python 3.11.9 specifically
echo [3/8] Checking Python 3.11.9 installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not installed or not in PATH.
    echo Installing Python 3.11.9 specifically (required for PyTorch compatibility)...
    winget install Python.Python.3.11 --version 3.11.9 --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo ERROR: Failed to install Python 3.11.9
        echo Please install manually from: https://www.python.org/downloads/release/python-3119/
        pause
        exit /b 1
    )
    echo Python 3.11.9 installed successfully.
    echo Refreshing environment variables...
    :: Refresh PATH for current session
    for /f "tokens=2*" %%i in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH') do set "SYSTEM_PATH=%%j"
    for /f "tokens=2*" %%i in ('reg query "HKCU\Environment" /v PATH 2^>nul') do set "USER_PATH=%%j"
    set "PATH=%SYSTEM_PATH%;%USER_PATH%"
) else (
    for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
    echo Found Python !PYTHON_VERSION!
    
    :: Check if Python version is exactly 3.11.9 (or at least 3.11.x)
    echo !PYTHON_VERSION! | findstr /R "^3\.11\." >nul
    if %errorlevel% neq 0 (
        echo WARNING: Nojoin requires Python 3.11.9 specifically due to PyTorch compatibility.
        echo You have Python !PYTHON_VERSION! installed.
        echo.
        echo Other versions may cause compatibility issues, especially with PyTorch.
        echo.
        set /p INSTALL_CORRECT_PYTHON="Do you want to install Python 3.11.9 alongside your current version? (Y/n): "
        if /i "!INSTALL_CORRECT_PYTHON!" neq "n" (
            echo Installing Python 3.11.9 specifically...
            winget install Python.Python.3.11 --version 3.11.9 --accept-package-agreements --accept-source-agreements
            if %errorlevel% neq 0 (
                echo ERROR: Failed to install Python 3.11.9
                pause
                exit /b 1
            )
                 ) else (
             echo.
             echo WARNING: Continuing with Python !PYTHON_VERSION!
             echo This may cause compatibility issues with PyTorch and other dependencies.
             echo If you experience problems, please install Python 3.11.9 and try again.
             echo.
         )
    )
)

:: Check and install ffmpeg
echo [4/8] Checking ffmpeg installation...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo ffmpeg is not installed or not in PATH.
    echo Installing ffmpeg...
    winget install ffmpeg --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo ERROR: Failed to install ffmpeg automatically.
        echo Please install manually from: https://ffmpeg.org/download.html
        echo Make sure to add ffmpeg to your system PATH.
        pause
        exit /b 1
    )
    echo ffmpeg installed successfully.
    echo Refreshing environment variables...
    for /f "tokens=2*" %%i in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH') do set "SYSTEM_PATH=%%j"
    for /f "tokens=2*" %%i in ('reg query "HKCU\Environment" /v PATH 2^>nul') do set "USER_PATH=%%j"
    set "PATH=%SYSTEM_PATH%;%USER_PATH%"
) else (
    echo ffmpeg found and working.
)

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
python -m venv .venv
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
        echo This requires:
        echo 1. NVIDIA GPU (which you have)
        echo 2. NVIDIA CUDA Toolkit 12.8
        echo 3. Compatible NVIDIA drivers
        echo.
        set /p INSTALL_CUDA="Would you like to install CUDA support for faster processing? (Y/n): "
        if /i "!INSTALL_CUDA!" neq "n" (
            echo.
            echo Please follow these steps:
            echo 1. Download NVIDIA CUDA Toolkit 12.8 from:
            echo    https://developer.nvidia.com/cuda-12-8-1-download-archive
            echo 2. Install it with default settings
            echo 3. Restart your computer if prompted
            echo 4. Come back and press any key to continue this setup
            echo.
            pause
            
            echo Checking if CUDA 12.8 is now installed...
            nvcc --version >nul 2>&1
            if %errorlevel% equ 0 (
                echo CUDA Toolkit detected! Installing CUDA-enabled PyTorch...
                pip uninstall torch torchvision torchaudio -y --quiet
                pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128 --quiet
                if %errorlevel% equ 0 (
                    echo CUDA-enabled PyTorch installed successfully!
                    set CUDA_INSTALLED=true
                ) else (
                    echo Failed to install CUDA PyTorch, falling back to CPU version...
                    pip install torch torchvision torchaudio --quiet
                )
            ) else (
                echo CUDA Toolkit not detected. Continuing with CPU-only version.
                echo You can always install CUDA later and reinstall PyTorch.
            )
        ) else (
            echo Continuing with CPU-only processing.
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

:: Test CUDA if installed
if defined CUDA_INSTALLED (
    echo Testing CUDA support...
    python -c "import torch; print('✓ CUDA Available:', torch.cuda.is_available()); print('✓ GPU Count:', torch.cuda.device_count())" 2>nul
    if %errorlevel% equ 0 (
        echo ✓ CUDA support verified!
    ) else (
        echo WARNING: CUDA installation may have issues.
    )
)

:: Create convenience scripts
echo.
echo Creating convenience scripts...

:: Create desktop shortcut script
echo Creating desktop shortcut...
set "CURRENT_DIR=%CD%"
set "SHORTCUT_PATH=%USERPROFILE%\Desktop\Nojoin.lnk"

:: Create PowerShell script to create shortcut
echo $WshShell = New-Object -comObject WScript.Shell > create_shortcut.ps1
echo $Shortcut = $WshShell.CreateShortcut("!SHORTCUT_PATH!") >> create_shortcut.ps1
echo $Shortcut.TargetPath = "!CURRENT_DIR!\run_nojoin.bat" >> create_shortcut.ps1
echo $Shortcut.WorkingDirectory = "!CURRENT_DIR!" >> create_shortcut.ps1
echo $Shortcut.IconLocation = "!CURRENT_DIR!\assets\NojoinLogo.png" >> create_shortcut.ps1
echo $Shortcut.Description = "Nojoin - Meeting Recording and Transcription" >> create_shortcut.ps1
echo $Shortcut.Save() >> create_shortcut.ps1

powershell -ExecutionPolicy Bypass -File create_shortcut.ps1 >nul 2>&1
del create_shortcut.ps1 >nul 2>&1

:: Create run script
echo @echo off > run_nojoin.bat
echo title Nojoin - Meeting Recording and Transcription >> run_nojoin.bat
echo cd /d "%%~dp0" >> run_nojoin.bat
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
echo call .venv\Scripts\activate.bat >> update_nojoin.bat
echo echo Updating Nojoin dependencies... >> update_nojoin.bat
echo pip install --upgrade -r requirements.txt >> update_nojoin.bat
echo echo Update complete! >> update_nojoin.bat
echo pause >> update_nojoin.bat

echo.
echo ================================================================
echo                    Setup Complete!
echo ================================================================
echo.
echo ✓ Nojoin has been successfully set up on your Windows system!
echo.
if defined CUDA_INSTALLED (
    echo ✓ CUDA support enabled for faster processing
) else (
    echo ℹ CPU-only processing configured
)
echo.
echo How to run Nojoin:
echo   1. Double-click the "Nojoin" shortcut on your desktop
echo   2. Double-click "run_nojoin.bat" in this folder
echo   3. Use the Start Menu to search for "Nojoin"
echo.
echo Additional utilities created:
echo   • run_nojoin.bat - Start Nojoin
echo   • update_nojoin.bat - Update dependencies
echo   • Desktop shortcut - Quick access to Nojoin
echo.
echo Important notes:
echo - Your recordings will be saved in the 'recordings' folder
echo - Settings and data are stored in the 'nojoin' folder
echo - Keep this folder intact - it contains your installation
echo.
if not defined CUDA_INSTALLED (
    echo To add CUDA support later:
    echo 1. Install NVIDIA CUDA Toolkit 12.8
    echo 2. Run update_nojoin.bat to reinstall with CUDA support
    echo.
)
echo For support, visit: https://github.com/Valtora/Nojoin
echo.
echo Press any key to launch Nojoin now...
pause >nul

:: Launch Nojoin
echo Launching Nojoin...
call run_nojoin.bat 