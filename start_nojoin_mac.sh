#!/bin/bash

# Nojoin Launcher Script for macOS (User Mode)
# This script launches Nojoin with user-directory portable tools

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

# Function to print success
print_success() {
    printf "${GREEN}✓ ${1}${NC}\n"
}

# Function to print warning
print_warning() {
    printf "${YELLOW}⚠ ${1}${NC}\n"
}

# Function to print error
print_error() {
    printf "${RED}✗ ${1}${NC}\n"
}

# Set up user tools directories
USER_TOOLS_DIR="$HOME/Library/Application Support/NojoinTools"
PYTHON_DIR="$USER_TOOLS_DIR/Python311"
FFMPEG_DIR="$USER_TOOLS_DIR/ffmpeg"

clear
print_color $CYAN "================================================================"
print_color $CYAN "               Nojoin v0.5.2 - Starting Application"
print_color $CYAN "================================================================"
echo

# Change to the script's directory
cd "$(dirname "$0")"

# Check if we're in the right directory
if [ ! -f "Nojoin.py" ]; then
    print_error "Could not find Nojoin.py in the current directory."
    print_error "Please make sure this script is in the same folder as Nojoin.py"
    echo
    print_color $BLUE "Current directory: $(pwd)"
    echo
    read -p "Press Enter to exit..."
    exit 1
fi

# Update PATH to include portable tools if they exist
if [ -d "$PYTHON_DIR/bin" ]; then
    export PATH="$PYTHON_DIR/bin:$PATH"
    print_color $GREEN "Using portable Python from user directory..."
fi

if [ -d "$FFMPEG_DIR/bin" ]; then
    export PATH="$FFMPEG_DIR/bin:$PATH"
    print_color $GREEN "Using portable ffmpeg from user directory..."
fi

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    print_error "Python virtual environment not found."
    echo
    print_color $YELLOW "This usually means Nojoin hasn't been set up yet."
    print_color $YELLOW "Please run the setup script first:"
    echo
    print_color $BLUE "  1. Run './setup_mac.sh' to set up Nojoin"
    print_color $BLUE "  2. Or follow the manual setup instructions in README.md"
    echo
    read -p "Press Enter to exit..."
    exit 1
fi

# Check if virtual environment activation script exists
if [ ! -f ".venv/bin/activate" ]; then
    print_error "Virtual environment appears to be corrupted."
    print_error "The activation script is missing."
    echo
    print_color $YELLOW "Please run the setup script again to recreate the environment:"
    print_color $BLUE "  ./setup_mac.sh"
    echo
    read -p "Press Enter to exit..."
    exit 1
fi

# Activate virtual environment
print_color $YELLOW "Activating Python virtual environment..."
source .venv/bin/activate
if [ $? -ne 0 ]; then
    print_error "Failed to activate virtual environment."
    echo
    print_color $YELLOW "This might indicate a corrupted Python installation."
    print_color $YELLOW "Please try running the setup script again:"
    print_color $BLUE "  ./setup_mac.sh"
    echo
    read -p "Press Enter to exit..."
    exit 1
fi

# Verify Python is working
if ! python3 --version &> /dev/null; then
    print_error "Python is not working correctly in the virtual environment."
    echo
    print_color $YELLOW "This might indicate a corrupted installation."
    print_color $YELLOW "Please try running the setup script again:"
    print_color $BLUE "  ./setup_mac.sh"
    echo
    read -p "Press Enter to exit..."
    exit 1
fi

# Check if main dependencies are installed
print_color $YELLOW "Checking core dependencies..."
if ! python3 -c "import sys; sys.path.insert(0, '.'); from nojoin.utils.config_manager import config_manager" 2>/dev/null; then
    print_warning "Core dependencies appear to be missing or corrupted."
    echo
    print_color $YELLOW "Attempting to install/update dependencies..."
    if ! pip install -r requirements.txt; then
        print_error "Failed to install dependencies."
        echo
        print_color $YELLOW "Please check your internet connection and try again, or"
        print_color $YELLOW "run the setup script to reinstall everything:"
        print_color $BLUE "  ./setup_mac.sh"
        echo
        read -p "Press Enter to exit..."
        exit 1
    fi
    print_success "Dependencies updated successfully."
fi

# Final verification
print_color $YELLOW "Performing final system check..."
if ! python3 -c "import torch; import whisper; import pyannote.audio; print('✓ All core libraries available')" 2>/dev/null; then
    print_warning "Some advanced features may not work correctly."
    print_warning "The application will still start, but you may experience issues."
    echo
    print_color $YELLOW "For best results, consider running the setup script again:"
    print_color $BLUE "  ./setup_mac.sh"
    echo
    sleep 3
fi

echo
print_success "Environment ready"
print_success "Starting Nojoin..."
echo
print_color $CYAN "================================================================"
print_color $CYAN "             Welcome to Nojoin - Ready for Recording!"
print_color $CYAN "================================================================"
echo

# Launch Nojoin
python3 Nojoin.py
EXIT_CODE=$?

# Handle exit status
if [ $EXIT_CODE -eq 0 ]; then
    echo
    print_success "Nojoin closed successfully."
else
    echo
    print_color $RED "================================================================"
    print_color $RED "                    Nojoin encountered an error"
    print_color $RED "================================================================"
    echo
    print_color $YELLOW "Exit code: $EXIT_CODE"
    echo
    print_color $BLUE "Common solutions:"
    print_color $BLUE "1. Check the logs in the 'logs' folder for detailed error information"
    print_color $BLUE "2. Ensure your microphone/audio devices are properly connected"
    print_color $BLUE "3. Try running the setup script again: ./setup_mac.sh"
    print_color $BLUE "4. Check the GitHub repository for known issues and solutions"
    echo
    print_color $BLUE "For support, visit: https://github.com/Valtora/Nojoin"
    echo
    print_color $YELLOW "The terminal window will remain open for troubleshooting."
    print_color $YELLOW "Close this window when you're done reviewing the information."
    echo
    read -p "Press Enter to exit..."
fi

# Deactivate virtual environment
deactivate 2>/dev/null

echo
print_color $CYAN "Thank you for using Nojoin!"
sleep 2 