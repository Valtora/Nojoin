#!/bin/bash

# Nojoin Setup Script for macOS (User Mode - No Admin Required)
# This script installs everything to user directories for maximum security

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to print colored output
print_color() {
    printf "${1}${2}${NC}\n"
}

# Function to print step headers
print_step() {
    printf "\n${BLUE}[${1}] ${2}${NC}\n"
}

# Function to print success
print_success() {
    printf "${GREEN}[✓] ${1}${NC}\n"
}

# Function to print warning
print_warning() {
    printf "${YELLOW}⚠ ${1}${NC}\n"
}

# Function to print error
print_error() {
    printf "${RED}✗ ${1}${NC}\n"
}

# Set up user directories
USER_TOOLS_DIR="$HOME/Library/Application Support/NojoinTools"
PYTHON_DIR="$USER_TOOLS_DIR/Python311"
FFMPEG_DIR="$USER_TOOLS_DIR/ffmpeg"

# Create tools directory
mkdir -p "$USER_TOOLS_DIR"

print_color $CYAN "================================================================"
print_color $CYAN "                        Nojoin Setup for macOS"
print_color $CYAN "================================================================"
echo
print_color $PURPLE "This script will set up Nojoin in your user directories."
print_color $PURPLE "No administrator privileges required for maximum security."
echo
print_color $YELLOW "Prerequisites that will be installed if missing:"
print_color $YELLOW "- Python 3.11.9 (portable, user directory)"
print_color $YELLOW "- ffmpeg (portable, user directory)"
print_color $YELLOW "- Virtual environment and dependencies"
print_color $YELLOW "- Metal Performance Shaders support (if Apple Silicon detected)"
echo

read -p "Press Enter to continue with the setup..."

# Check if we're in the correct directory
print_step "1" "Checking project directory..."
if [ ! -f "Nojoin.py" ]; then
    print_error "This script must be run from the Nojoin project directory."
    print_error "Please navigate to the directory containing Nojoin.py and run this script again."
    echo
    exit 1
fi
print_success "Project directory verified."

# Check for required tools
print_step "2" "Checking download capabilities..."
if ! command -v curl &> /dev/null; then
    print_error "curl is required for downloading dependencies but is not installed."
    print_error "Please install curl and try again."
    exit 1
fi
print_success "Download capabilities verified."

# Check and install Python 3.11.9 to user directory
print_step "3/8" "Checking Python 3.11.9 installation..."

PYTHON_EXE="$PYTHON_DIR/bin/python3"

# First check if we already have Python in our user tools directory
if [ -f "$PYTHON_EXE" ]; then
    if PYTHON_VERSION=$("$PYTHON_EXE" --version 2>&1 | cut -d' ' -f2); then
        print_success "Found portable Python $PYTHON_VERSION in user directory."
        PYTHON_READY=true
    fi
fi

if [ -z "$PYTHON_READY" ]; then
    # Check system Python
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
        print_color $GREEN "Found system Python $PYTHON_VERSION"
        
        # Check if Python version is 3.11+ (including 3.12, 3.13, etc.)
        MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
        MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
        
        if [ "$MAJOR" -ge 3 ] && { [ "$MAJOR" -gt 3 ] || [ "$MINOR" -ge 11 ]; }; then
            print_color $GREEN "System Python $PYTHON_VERSION is compatible (3.11+)."
            read -p "Use system Python $PYTHON_VERSION? (Y/n): " USE_SYSTEM
            if [[ ! "$USE_SYSTEM" =~ ^[Nn]$ ]]; then
                PYTHON_EXE="python3"
                USING_SYSTEM_PYTHON=true
                PYTHON_READY=true
            fi
        else
            print_warning "System Python $PYTHON_VERSION found, but Nojoin requires Python 3.11+."
            print_warning "We'll install a portable Python 3.11.9 to your user directory."
            echo
        fi
    else
        print_color $YELLOW "No system Python found. Installing portable Python 3.11.9..."
    fi
fi

# Download and install portable Python 3.11.9 if needed
if [ -z "$PYTHON_READY" ]; then
    print_color $YELLOW "Installing portable Python 3.11.9 to user directory..."
    print_color $YELLOW "This may take a few minutes..."
    
    # Detect architecture
    ARCH=$(uname -m)
    if [[ "$ARCH" == "arm64" ]]; then
        PYTHON_URL="https://www.python.org/ftp/python/3.11.9/python-3.11.9-macos11.pkg"
        PYTHON_PKG="/tmp/python3119.pkg"
    else
        PYTHON_URL="https://www.python.org/ftp/python/3.11.9/python-3.11.9-macos11.pkg"
        PYTHON_PKG="/tmp/python3119.pkg"
    fi
    
    print_color $YELLOW "Downloading Python 3.11.9..."
    if ! curl -L -o "$PYTHON_PKG" "$PYTHON_URL"; then
        print_error "Failed to download Python 3.11.9"
        print_error "Please check your internet connection and try again."
        exit 1
    fi
    
    print_color $YELLOW "Extracting Python to user directory..."
    rm -rf "$PYTHON_DIR"
    mkdir -p "$PYTHON_DIR"
    
    # Extract the package contents to our user directory
    cd /tmp
    if ! xar -xf "$PYTHON_PKG"; then
        print_error "Failed to extract Python package"
        exit 1
    fi
    
    # Find the Python framework and copy it to user directory
    if [ -f "Python_Framework.pkg/Payload" ]; then
        cd "$PYTHON_DIR"
        if ! cat /tmp/Python_Framework.pkg/Payload | gunzip -dc | cpio -i; then
            print_error "Failed to install Python framework"
            exit 1
        fi
        
        # Create symlinks for easier access
        mkdir -p bin
        ln -sf ../Library/Frameworks/Python.framework/Versions/3.11/bin/python3 bin/python3
        ln -sf ../Library/Frameworks/Python.framework/Versions/3.11/bin/pip3 bin/pip3
        
        PYTHON_EXE="$PYTHON_DIR/bin/python3"
    else
        print_error "Failed to find Python framework in package"
        exit 1
    fi
    
    # Clean up
    rm -f "$PYTHON_PKG"
    rm -rf /tmp/Python_Framework.pkg
    
    print_success "Portable Python 3.11.9 installed successfully."
fi

print_success "Python 3.11.9 ready"

# Check and install ffmpeg to user directory
print_step "4" "Checking ffmpeg installation..."

FFMPEG_EXE="$FFMPEG_DIR/bin/ffmpeg"
if [ -f "$FFMPEG_EXE" ]; then
    if "$FFMPEG_EXE" -version &> /dev/null; then
        print_success "Found portable ffmpeg in user directory."
        FFMPEG_READY=true
    fi
fi

if [ -z "$FFMPEG_READY" ]; then
    # Check system ffmpeg
    if command -v ffmpeg &> /dev/null; then
        FFMPEG_VERSION=$(ffmpeg -version 2>&1 | head -n1 | cut -d' ' -f3)
        print_color $GREEN "Found system ffmpeg $FFMPEG_VERSION."
        read -p "Use system ffmpeg? (Y/n): " USE_SYSTEM_FFMPEG
        if [[ ! "$USE_SYSTEM_FFMPEG" =~ ^[Nn]$ ]]; then
            USING_SYSTEM_FFMPEG=true
            FFMPEG_READY=true
        fi
    fi
fi

# Download and install portable ffmpeg if needed
if [ -z "$FFMPEG_READY" ]; then
    print_color $YELLOW "Installing portable ffmpeg to user directory..."
    
    # Detect architecture for ffmpeg
    ARCH=$(uname -m)
    if [[ "$ARCH" == "arm64" ]]; then
        FFMPEG_URL="https://evermeet.cx/ffmpeg/ffmpeg-6.1.zip"
    else
        FFMPEG_URL="https://evermeet.cx/ffmpeg/ffmpeg-6.1.zip"
    fi
    
    FFMPEG_ZIP="/tmp/ffmpeg.zip"
    
    print_color $YELLOW "Downloading ffmpeg..."
    if ! curl -L -o "$FFMPEG_ZIP" "$FFMPEG_URL"; then
        print_error "Failed to download ffmpeg"
        print_error "Please check your internet connection and try again."
        exit 1
    fi
    
    print_color $YELLOW "Extracting ffmpeg to $FFMPEG_DIR..."
    rm -rf "$FFMPEG_DIR"
    mkdir -p "$FFMPEG_DIR/bin"
    
    cd "$FFMPEG_DIR/bin"
    if ! unzip -q "$FFMPEG_ZIP"; then
        print_error "Failed to extract ffmpeg"
        exit 1
    fi
    
    # Make executable
    chmod +x ffmpeg
    
    # Clean up
    rm -f "$FFMPEG_ZIP"
    
    print_success "Portable ffmpeg installed successfully."
fi

print_success "ffmpeg ready"

# Update PATH for this session to include our tools
if [ "$USING_SYSTEM_PYTHON" != "true" ]; then
    export PATH="$PYTHON_DIR/bin:$PATH"
fi
if [ "$USING_SYSTEM_FFMPEG" != "true" ]; then
    export PATH="$FFMPEG_DIR/bin:$PATH"
fi

# Handle virtual environment
print_step "5" "Setting up Python virtual environment..."
if [ -d ".venv" ]; then
    print_color $GREEN "Virtual environment already exists."
    read -p "Do you want to recreate it? This will delete existing dependencies. (y/N): " RECREATE
    if [[ "$RECREATE" =~ ^[Yy]$ ]]; then
        print_color $YELLOW "Removing existing virtual environment..."
        rm -rf .venv
        CREATE_VENV=true
    else
        print_color $GREEN "Using existing virtual environment."
        CREATE_VENV=false
    fi
else
    CREATE_VENV=true
fi

if [ "$CREATE_VENV" = true ]; then
    print_color $YELLOW "Creating virtual environment..."
    if ! "$PYTHON_EXE" -m venv .venv; then
        print_error "Failed to create virtual environment."
        print_error "This might be due to Python installation issues."
        exit 1
    fi
fi

print_color $YELLOW "Activating virtual environment..."
source .venv/bin/activate
if [ $? -ne 0 ]; then
    print_error "Failed to activate virtual environment."
    exit 1
fi

# Choose PyTorch installation type
print_step "6" "Choosing PyTorch installation..."
echo
print_color $CYAN "========================================"
print_color $CYAN "    PyTorch Installation Options"
print_color $CYAN "========================================"
echo
print_color $WHITE "Nojoin can use different processing modes:"
echo
ARCH=$(uname -m)
if [[ "$ARCH" == "arm64" ]]; then
    print_color $GREEN "1. CPU-only: Works on all Macs but slower processing"
    print_color $GREEN "2. MPS (Metal): GPU acceleration for Apple Silicon Macs (Recommended)"
    echo
    print_color $YELLOW "Apple Silicon Information:"
    print_color $YELLOW "- MPS uses your Mac's GPU for faster processing"
    print_color $YELLOW "- Your Mac appears to be Apple Silicon (recommended: MPS)"
    print_color $YELLOW "- You can always switch later by reinstalling PyTorch"
    echo
    read -p "Choose installation type (1=CPU-only, 2=MPS): " PYTORCH_CHOICE
    
    if [[ "$PYTORCH_CHOICE" == "2" ]]; then
        echo
        print_color $CYAN "========================================"
        print_color $CYAN "     MPS PyTorch Installation"
        print_color $CYAN "========================================"
        echo
        print_color $YELLOW "Installing MPS-enabled PyTorch (this may take several minutes)..."
        if pip install torch torchvision torchaudio --quiet; then
            print_success "MPS PyTorch installed successfully!"
            
            # Test MPS availability
            if python3 -c "import torch; print('MPS_AVAILABLE' if torch.backends.mps.is_available() else 'MPS_NOT_AVAILABLE')" 2>/dev/null | grep -q "MPS_AVAILABLE"; then
                print_color $GREEN "🚀 Metal Performance Shaders (MPS) is available for GPU acceleration!"
                print_color $GREEN "This will provide significantly faster transcription and processing."
            else
                print_warning "MPS support not detected. Will use CPU processing."
            fi
        else
            print_warning "MPS PyTorch installation failed. Falling back to CPU-only..."
            if ! pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu --quiet; then
                print_error "Failed to install CPU PyTorch."
                print_error "Please check your internet connection."
                exit 1
            fi
            print_success "CPU PyTorch installed successfully!"
        fi
    else
        echo
        print_color $CYAN "========================================"
        print_color $CYAN "     CPU-only PyTorch Installation"
        print_color $CYAN "========================================"
        echo
        print_color $YELLOW "Installing CPU-only PyTorch (this may take several minutes)..."
        if ! pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu --quiet; then
            print_error "Failed to install CPU PyTorch."
            print_error "Please check your internet connection."
            exit 1
        fi
        print_success "CPU PyTorch installed successfully!"
    fi
else
    # Intel Mac
    print_color $GREEN "1. CPU-only: Standard processing for Intel Macs (Recommended)"
    print_color $GREEN "2. Custom: Advanced users only"
    echo
    print_color $YELLOW "Intel Mac Information:"
    print_color $YELLOW "- Your Mac appears to be Intel-based"
    print_color $YELLOW "- CPU-only mode is recommended for Intel Macs"
    print_color $YELLOW "- GPU acceleration is not available on Intel Macs"
    echo
    read -p "Choose installation type (1=CPU-only, 2=Custom): " PYTORCH_CHOICE
    
    if [[ "$PYTORCH_CHOICE" == "2" ]]; then
        echo
        print_color $YELLOW "Installing standard PyTorch..."
        if ! pip install torch torchvision torchaudio --quiet; then
            print_error "Failed to install PyTorch."
            print_error "Please check your internet connection."
            exit 1
        fi
        print_success "PyTorch installed successfully!"
    else
        echo
        print_color $CYAN "========================================"
        print_color $CYAN "     CPU-only PyTorch Installation"
        print_color $CYAN "========================================"
        echo
        print_color $YELLOW "Installing CPU-only PyTorch (this may take several minutes)..."
        if ! pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu --quiet; then
            print_error "Failed to install CPU PyTorch."
            print_error "Please check your internet connection."
            exit 1
        fi
        print_success "CPU PyTorch installed successfully!"
    fi
fi

# Upgrade pip first
echo
print_color $YELLOW "Upgrading pip to latest version..."
pip install --upgrade pip --quiet

# Install remaining dependencies
print_step "7" "Installing remaining dependencies..."
print_color $YELLOW "This may take several minutes..."
if ! pip install -r requirements.txt --quiet; then
    print_error "Failed to install some dependencies."
    print_error "Please check your internet connection and try again."
    exit 1
fi

# Test the installation
echo
print_color $YELLOW "Testing installation..."
if python3 -c "import sys; sys.path.insert(0, '.'); from nojoin.utils.config_manager import config_manager; print('[✓] Configuration system working')" 2>/dev/null; then
    print_success "Installation test passed!"
else
    print_warning "Installation test failed. The application may not work correctly."
    print_warning "You may need to restart your computer and try again."
fi

# Create convenience scripts
echo
print_color $YELLOW "Creating convenience scripts..."

# Create run script with PATH updates
cat > run_nojoin.sh << EOF
#!/bin/bash
cd "\$(dirname "\$0")"
$(if [ "$USING_SYSTEM_PYTHON" != "true" ] && [ "$USING_SYSTEM_FFMPEG" != "true" ]; then
    echo "export PATH=\"$PYTHON_DIR/bin:$FFMPEG_DIR/bin:\$PATH\""
elif [ "$USING_SYSTEM_PYTHON" != "true" ]; then
    echo "export PATH=\"$PYTHON_DIR/bin:\$PATH\""
elif [ "$USING_SYSTEM_FFMPEG" != "true" ]; then
    echo "export PATH=\"$FFMPEG_DIR/bin:\$PATH\""
fi)
source .venv/bin/activate
python3 Nojoin.py
EOF
chmod +x run_nojoin.sh

# Create update script
cat > update_nojoin.sh << EOF
#!/bin/bash
cd "\$(dirname "\$0")"
$(if [ "$USING_SYSTEM_PYTHON" != "true" ] && [ "$USING_SYSTEM_FFMPEG" != "true" ]; then
    echo "export PATH=\"$PYTHON_DIR/bin:$FFMPEG_DIR/bin:\$PATH\""
elif [ "$USING_SYSTEM_PYTHON" != "true" ]; then
    echo "export PATH=\"$PYTHON_DIR/bin:\$PATH\""
elif [ "$USING_SYSTEM_FFMPEG" != "true" ]; then
    echo "export PATH=\"$FFMPEG_DIR/bin:\$PATH\""
fi)
source .venv/bin/activate
echo "Updating Nojoin dependencies..."
pip install --upgrade -r requirements.txt
echo "Update complete!"
read -p "Press Enter to continue..."
EOF
chmod +x update_nojoin.sh

# Create macOS app bundle
print_color $YELLOW "Creating macOS app bundle..."
APP_DIR="$HOME/Applications/Nojoin.app"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

# Create Info.plist
cat > "$APP_DIR/Contents/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>Nojoin</string>
    <key>CFBundleIdentifier</key>
    <string>com.valtora.nojoin</string>
    <key>CFBundleName</key>
    <string>Nojoin</string>
    <key>CFBundleVersion</key>
    <string>0.5.2</string>
    <key>CFBundleShortVersionString</key>
    <string>0.5.2</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleSignature</key>
    <string>NOJN</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSRequiresAquaSystemAppearance</key>
    <false/>
</dict>
</plist>
EOF

# Create launcher script
CURRENT_DIR=$(pwd)
cat > "$APP_DIR/Contents/MacOS/Nojoin" << EOF
#!/bin/bash
cd "$CURRENT_DIR"
$(if [ "$USING_SYSTEM_PYTHON" != "true" ] && [ "$USING_SYSTEM_FFMPEG" != "true" ]; then
    echo "export PATH=\"$PYTHON_DIR/bin:$FFMPEG_DIR/bin:\$PATH\""
elif [ "$USING_SYSTEM_PYTHON" != "true" ]; then
    echo "export PATH=\"$PYTHON_DIR/bin:\$PATH\""
elif [ "$USING_SYSTEM_FFMPEG" != "true" ]; then
    echo "export PATH=\"$FFMPEG_DIR/bin:\$PATH\""
fi)
source .venv/bin/activate
python3 Nojoin.py
EOF
chmod +x "$APP_DIR/Contents/MacOS/Nojoin"

# Copy icon if available
if [ -f "assets/favicon.ico" ]; then
    cp "assets/favicon.ico" "$APP_DIR/Contents/Resources/"
elif [ -f "assets/icons/NojoinLogo.png" ]; then
    cp "assets/icons/NojoinLogo.png" "$APP_DIR/Contents/Resources/"
elif [ -f "assets/NojoinLogo.png" ]; then
    cp "assets/NojoinLogo.png" "$APP_DIR/Contents/Resources/"
fi

# Clean up temporary files
rm -f /tmp/python3119.pkg /tmp/ffmpeg.zip

echo
print_color $CYAN "================================================================"
print_color $CYAN "                    Setup Complete!"
print_color $CYAN "================================================================"
echo
print_success "Nojoin has been successfully set up on your macOS system!"
print_success "All tools installed to user directories (no admin required)"
echo
print_color $BLUE "Installation details:"
if [ "$USING_SYSTEM_PYTHON" = "true" ]; then
    print_color $BLUE "- Python: System installation ($PYTHON_VERSION)"
else
    print_color $BLUE "- Python 3.11.9: $PYTHON_DIR (portable)"
fi
if [ "$USING_SYSTEM_FFMPEG" = "true" ]; then
    print_color $BLUE "- ffmpeg: System installation ($FFMPEG_VERSION)"
else
    print_color $BLUE "- ffmpeg: $FFMPEG_DIR (portable)"
fi
print_color $BLUE "- Virtual environment: $(pwd)/.venv"
echo
print_color $GREEN "How to run Nojoin:"
print_color $GREEN "  1. Open the 'Nojoin' app from your Applications folder"
print_color $GREEN "  2. Run './run_nojoin.sh' from this directory"
print_color $GREEN "  3. Double-click 'run_nojoin.sh' in Finder"
echo
print_color $YELLOW "Additional utilities created:"
print_color $YELLOW "  - run_nojoin.sh - Start Nojoin"
print_color $YELLOW "  - update_nojoin.sh - Update dependencies"
print_color $YELLOW "  - Nojoin.app - Native macOS application"
echo
print_color $PURPLE "Important notes:"
print_color $PURPLE "- Your recordings will be saved in the 'recordings' folder"
print_color $PURPLE "- Settings and data are stored in the 'nojoin' folder"
print_color $PURPLE "- All tools are in $USER_TOOLS_DIR"

if [[ "$(uname -m)" == "arm64" ]]; then
    echo
    print_color $GREEN "🚀 Apple Silicon optimizations enabled for faster performance!"
fi

echo
print_color $BLUE "For support, visit: https://github.com/Valtora/Nojoin"
echo
read -p "Press Enter to launch Nojoin now..."

# Launch Nojoin
print_color $YELLOW "Launching Nojoin..."
./run_nojoin.sh 