#!/bin/bash

# Nojoin Setup Script for macOS
# This script provides a fully automated setup experience for macOS users
# It assumes minimal technical knowledge and handles dependency installation automatically

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Function to print colored output
print_step() {
    echo -e "${BLUE}[$1/7]${NC} ${BOLD}$2${NC}"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

# Check if running on macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    print_error "This script is designed for macOS only."
    echo "For Linux users: You likely have the technical knowledge to set this up manually."
    echo "Please refer to the README.md for manual installation instructions."
    exit 1
fi

clear
echo
echo "================================================================"
echo "               Nojoin v0.5.2 Setup for macOS"
echo "================================================================"
echo
echo "This script will automatically set up Nojoin on your Mac."
echo "It will check for and install missing dependencies as needed."
echo
echo "Prerequisites that will be checked and installed if missing:"
echo "• Homebrew package manager"
echo "• Python 3.11.9 (specifically required for PyTorch compatibility)"
echo "• ffmpeg"
echo "• Virtual environment and dependencies"
echo "• Metal Performance Shaders (MPS) support for Apple Silicon"
echo
read -p "Press Enter to begin automatic setup..."

# Check if we're in the correct directory
print_step 1 "Checking project directory"
if [[ ! -f "Nojoin.py" ]]; then
    print_error "This script must be run from the Nojoin project directory."
    echo "Please navigate to the directory containing Nojoin.py and run this script again."
    exit 1
fi
print_success "Project directory verified"

# Check and install Homebrew
print_step 2 "Checking Homebrew package manager"
if ! command -v brew &> /dev/null; then
    print_warning "Homebrew is not installed. Installing Homebrew..."
    echo "Homebrew is required for automatic dependency installation on macOS."
    echo
    read -p "Install Homebrew now? (Y/n): " INSTALL_HOMEBREW
    if [[ ! "$INSTALL_HOMEBREW" =~ ^[Nn]$ ]]; then
        echo "Installing Homebrew (this may take several minutes)..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        
        # Add Homebrew to PATH for current session
        if [[ -f "/opt/homebrew/bin/brew" ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -f "/usr/local/bin/brew" ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
        
        if command -v brew &> /dev/null; then
            print_success "Homebrew installed successfully"
        else
            print_error "Failed to install Homebrew"
            echo "Please install Homebrew manually from: https://brew.sh"
            exit 1
        fi
    else
        print_error "Homebrew is required for automatic setup"
        echo "Please install Homebrew from https://brew.sh and run this script again"
        exit 1
    fi
else
    print_success "Homebrew found"
    # Update Homebrew to latest version
    echo "Updating Homebrew..."
    brew update &> /dev/null || true
fi

# Check and install Python 3.11.9 specifically
print_step 3 "Checking Python 3.11.9 installation"
PYTHON_CMD=""

# Check for python3.11 first
if command -v python3.11 &> /dev/null; then
    PYTHON_CMD="python3.11"
    PYTHON_VERSION=$(python3.11 --version 2>&1 | cut -d' ' -f2)
    print_success "Found Python $PYTHON_VERSION (python3.11)"
elif command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
    if [[ "$PYTHON_VERSION" =~ ^3\.11\. ]]; then
        PYTHON_CMD="python3"
        print_success "Found Python $PYTHON_VERSION (python3)"
    else
        print_warning "Found Python $PYTHON_VERSION, but Nojoin requires Python 3.11.9 specifically"
        echo "Other versions may cause PyTorch compatibility issues."
        echo "Installing Python 3.11..."
        brew install python@3.11
        PYTHON_CMD="python3.11"
    fi
else
    print_warning "Python is not installed"
    echo "Installing Python 3.11..."
    brew install python@3.11
    PYTHON_CMD="python3.11"
fi

# Verify Python installation
if ! command -v $PYTHON_CMD &> /dev/null; then
    print_error "Failed to install Python 3.11"
    echo "Please install Python 3.11 manually and run this script again"
    exit 1
fi

FINAL_PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | cut -d' ' -f2)
print_success "Using Python $FINAL_PYTHON_VERSION ($PYTHON_CMD)"

# Check and install ffmpeg
print_step 4 "Checking ffmpeg installation"
if ! command -v ffmpeg &> /dev/null; then
    print_warning "ffmpeg is not installed"
    echo "Installing ffmpeg..."
    brew install ffmpeg
    if ! command -v ffmpeg &> /dev/null; then
        print_error "Failed to install ffmpeg"
        echo "Please install ffmpeg manually and run this script again"
        exit 1
    fi
    print_success "ffmpeg installed successfully"
else
    print_success "ffmpeg found and working"
fi

# Handle virtual environment
print_step 5 "Setting up Python virtual environment"
if [[ -d ".venv" ]]; then
    print_warning "Virtual environment already exists"
    read -p "Do you want to recreate it? This will delete existing dependencies. (y/N): " RECREATE
    if [[ "$RECREATE" =~ ^[Yy]$ ]]; then
        echo "Removing existing virtual environment..."
        rm -rf .venv
        create_venv=true
    else
        print_success "Using existing virtual environment"
        create_venv=false
    fi
else
    create_venv=true
fi

if [[ "$create_venv" == "true" ]]; then
    echo "Creating virtual environment..."
    $PYTHON_CMD -m venv .venv
    if [[ $? -ne 0 ]]; then
        print_error "Failed to create virtual environment"
        echo "This might be due to Python installation issues"
        exit 1
    fi
    print_success "Virtual environment created"
fi

echo "Activating virtual environment..."
source .venv/bin/activate
if [[ $? -ne 0 ]]; then
    print_error "Failed to activate virtual environment"
    exit 1
fi
print_success "Virtual environment activated"

# Install basic PyTorch first for hardware detection
print_step 6 "Installing basic PyTorch for system analysis"
echo "This may take a few minutes..."
pip install torch torchvision torchaudio --quiet
if [[ $? -ne 0 ]]; then
    print_error "Failed to install basic PyTorch"
    echo "Please check your internet connection"
    exit 1
fi

# Check for Apple Silicon and MPS support
echo "Checking for Apple Silicon and MPS support..."
ARCH=$(uname -m)
MPS_AVAILABLE=$(python -c "import torch; print('YES' if torch.backends.mps.is_available() else 'NO')" 2>/dev/null || echo "NO")

if [[ "$ARCH" == "arm64" ]]; then
    print_success "Apple Silicon Mac detected ($ARCH architecture)"
    if [[ "$MPS_AVAILABLE" == "YES" ]]; then
        print_success "Metal Performance Shaders (MPS) support available"
        print_info "PyTorch will automatically use your GPU for faster processing"
    else
        print_warning "MPS support not available (macOS 12.3+ required)"
        print_info "Will use CPU-only processing"
    fi
else
    print_info "Intel Mac detected - using CPU processing"
fi

# Install remaining dependencies
print_step 7 "Installing remaining dependencies"
echo "This may take several minutes..."
pip install -r requirements.txt --quiet
if [[ $? -ne 0 ]]; then
    print_error "Failed to install some dependencies"
    echo "Please check your internet connection and try again"
    exit 1
fi

# Test the installation
echo
echo "Testing installation..."
python -c "import sys; sys.path.insert(0, '.'); from nojoin.utils.config_manager import config_manager; print('✓ Configuration system working')" 2>/dev/null
if [[ $? -ne 0 ]]; then
    print_warning "Installation test failed. The application may not work correctly"
    read -p "Press Enter to continue..."
else
    print_success "Installation test passed!"
fi

# Test MPS if available
if [[ "$MPS_AVAILABLE" == "YES" ]]; then
    echo "Testing MPS support..."
    python -c "import torch; print('✓ MPS Available:', torch.backends.mps.is_available()); print('✓ Using device:', 'mps' if torch.backends.mps.is_available() else 'cpu')" 2>/dev/null
    if [[ $? -eq 0 ]]; then
        print_success "MPS support verified!"
    else
        print_warning "MPS support test failed"
    fi
fi

# Create convenience scripts
echo
echo "Creating convenience scripts..."

# Create run script
cat > run_nojoin.sh << 'EOF'
#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
python Nojoin.py
if [[ $? -ne 0 ]]; then
    echo
    echo "Nojoin encountered an error. Check the logs for details."
    read -p "Press Enter to exit..."
fi
EOF

chmod +x run_nojoin.sh

# Create update script
cat > update_nojoin.sh << 'EOF'
#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
echo "Updating Nojoin dependencies..."
pip install --upgrade -r requirements.txt
echo "Update complete!"
read -p "Press Enter to exit..."
EOF

chmod +x update_nojoin.sh

# Create Automator application for better macOS integration
create_automator_app() {
    CURRENT_DIR=$(pwd)
    APP_NAME="Nojoin.app"
    
    if [[ -d "$HOME/Applications/$APP_NAME" ]]; then
        rm -rf "$HOME/Applications/$APP_NAME"
    fi
    
    # Create the app bundle structure
    mkdir -p "$HOME/Applications/$APP_NAME/Contents/MacOS"
    mkdir -p "$HOME/Applications/$APP_NAME/Contents/Resources"
    
    # Create Info.plist
    cat > "$HOME/Applications/$APP_NAME/Contents/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>Nojoin</string>
    <key>CFBundleIdentifier</key>
    <string>com.nojoin.app</string>
    <key>CFBundleName</key>
    <string>Nojoin</string>
    <key>CFBundleDisplayName</key>
    <string>Nojoin</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.14</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

    # Create the executable
    cat > "$HOME/Applications/$APP_NAME/Contents/MacOS/Nojoin" << EOF
#!/bin/bash
cd "$CURRENT_DIR"
source .venv/bin/activate
python Nojoin.py
EOF

    chmod +x "$HOME/Applications/$APP_NAME/Contents/MacOS/Nojoin"
    
    # Copy icon if it exists
    if [[ -f "assets/NojoinLogo.png" ]]; then
        cp "assets/NojoinLogo.png" "$HOME/Applications/$APP_NAME/Contents/Resources/"
    fi
    
    print_success "Nojoin.app created in ~/Applications"
}

echo
read -p "Would you like to create a Nojoin.app for easy access? (Y/n): " CREATE_APP
if [[ ! "$CREATE_APP" =~ ^[Nn]$ ]]; then
    create_automator_app
fi

echo
echo "================================================================"
echo "                    Setup Complete!"
echo "================================================================"
echo
print_success "Nojoin has been successfully set up on your Mac!"
echo

if [[ "$MPS_AVAILABLE" == "YES" ]]; then
    print_success "Metal Performance Shaders (MPS) enabled for faster processing"
else
    print_info "CPU-only processing configured"
fi

echo
echo "How to run Nojoin:"
if [[ -d "$HOME/Applications/Nojoin.app" ]]; then
    echo "  1. Open Nojoin.app from your Applications folder"
    echo "  2. Search for 'Nojoin' in Spotlight"
fi
echo "  3. Run './run_nojoin.sh' from this directory"
echo "  4. Or manually activate the virtual environment and run:"
echo "     source .venv/bin/activate"
echo "     python Nojoin.py"
echo
echo "Additional utilities created:"
echo "  • run_nojoin.sh - Start Nojoin"
echo "  • update_nojoin.sh - Update dependencies"
if [[ -d "$HOME/Applications/Nojoin.app" ]]; then
    echo "  • Nojoin.app - macOS application bundle"
fi
echo
echo "Important notes:"
echo "• Your recordings will be saved in the 'recordings' folder"
echo "• Settings and data are stored in the 'nojoin' folder"
echo "• Keep this folder intact - it contains your installation"
if [[ "$ARCH" == "arm64" ]] && [[ "$MPS_AVAILABLE" == "NO" ]]; then
    echo "• Update to macOS 12.3+ to enable Metal Performance Shaders"
fi
echo
echo "For support, visit: https://github.com/Valtora/Nojoin"
echo
read -p "Press Enter to launch Nojoin now..."

# Launch Nojoin
echo "Launching Nojoin..."
./run_nojoin.sh 