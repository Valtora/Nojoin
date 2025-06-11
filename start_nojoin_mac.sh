#!/bin/bash

# Nojoin v0.5.2 Launcher for macOS
# This script launches Nojoin with proper error checking and user guidance

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Function to print colored output
print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗ ERROR:${NC} $1"
}

print_step() {
    echo -e "${BLUE}[$1/3]${NC} $2"
}

print_warning() {
    echo -e "${YELLOW}⚠ WARNING:${NC} $1"
}

# Clear screen and show header
clear
echo
echo "================================================================"
echo "                   Launching Nojoin v0.5.2"
echo "================================================================"
echo

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check if we're in the right directory
if [[ ! -f "$SCRIPT_DIR/Nojoin.py" ]]; then
    print_error "Cannot find Nojoin.py in the current directory."
    echo "Please make sure you're running this script from the Nojoin folder."
    echo
    echo "Current directory: $SCRIPT_DIR"
    echo
    read -p "Press Enter to exit..."
    exit 1
fi

# Check if virtual environment exists
if [[ ! -d "$SCRIPT_DIR/.venv" ]]; then
    print_error "Virtual environment not found."
    echo
    echo "It looks like Nojoin hasn't been set up yet."
    echo "Please run './setup_mac.sh' first to install Nojoin."
    echo
    read -p "Press Enter to exit..."
    exit 1
fi

# Check if virtual environment has Python
if [[ ! -f "$SCRIPT_DIR/.venv/bin/python" ]]; then
    print_error "Virtual environment is incomplete."
    echo
    echo "Please run './setup_mac.sh' to reinstall Nojoin."
    echo
    read -p "Press Enter to exit..."
    exit 1
fi

print_step 1 "Activating virtual environment"
source "$SCRIPT_DIR/.venv/bin/activate"
if [[ $? -ne 0 ]]; then
    print_error "Failed to activate virtual environment."
    echo
    echo "Please run './setup_mac.sh' to fix the installation."
    echo
    read -p "Press Enter to exit..."
    exit 1
fi

print_step 2 "Checking Python installation"
python --version &> /dev/null
if [[ $? -ne 0 ]]; then
    print_error "Python is not working in the virtual environment."
    echo
    echo "Please run './setup_mac.sh' to fix the installation."
    echo
    read -p "Press Enter to exit..."
    exit 1
fi

print_step 3 "Starting Nojoin"
echo

# Change to the script directory to ensure relative paths work
cd "$SCRIPT_DIR"

# Launch Nojoin
python Nojoin.py

# Check if there was an error
EXIT_CODE=$?
if [[ $EXIT_CODE -ne 0 ]]; then
    echo
    echo "================================================================"
    echo "                   Nojoin Exited with Error"
    echo "================================================================"
    echo
    echo "Nojoin encountered an error and stopped running."
    echo
    echo "Common solutions:"
    echo "1. Check that your audio devices are working"
    echo "2. Run './update_nojoin.sh' to update dependencies"
    echo "3. Run './setup_mac.sh' to reinstall if problems persist"
    echo
    echo "For more help, check the logs or visit:"
    echo "https://github.com/Valtora/Nojoin"
    echo
else
    echo
    echo "================================================================"
    echo "                   Nojoin Closed Successfully"
    echo "================================================================"
    echo
fi

read -p "Press Enter to exit..." 