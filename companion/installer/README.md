# Nojoin Companion Installer

This directory contains the NSIS installer script for building the Windows installer.

## Prerequisites

1. **NSIS (Nullsoft Scriptable Install System)**
   - Download from: https://nsis.sourceforge.io/Download
   - Or install via Chocolatey: `choco install nsis`

2. **Rust toolchain** (for building the companion app)
   - Install from: https://rustup.rs/

## Building the Installer

### Quick Build

From the `companion` directory, run:

```powershell
.\installer\build.ps1 -Release
```

### Build Options

- `-Release`: Build the Rust application in release mode
- `-SkipBuild`: Skip Rust build, use existing binary

### Manual Build

1. Build the Rust application:
   ```powershell
   cargo build --release
   ```

2. Run NSIS (from the `companion` directory):
   ```powershell
   & "C:\Program Files (x86)\NSIS\makensis.exe" installer\installer.nsi
   ```

## Output

The installer will be created at:
```
companion/dist/Nojoin-Companion-Setup-v{VERSION}.exe
```

For example, version 0.1.0 produces:
```
companion/dist/Nojoin-Companion-Setup-v0.1.0.exe
```

## Installer Features

- **Installation Path**: `%LOCALAPPDATA%\Nojoin`
- **Start Menu Shortcuts**: Optional
- **Desktop Shortcut**: Optional  
- **Run on Startup**: Optional (adds to Windows startup)
- **Config Preservation**: Existing `config.json` is preserved during updates
- **Auto-termination**: Running instances are closed before update
- **Custom Icon**: Uses `icon.ico` from the installer directory

## Customization

### Icons

Place the following files in the `installer` directory for custom branding:
- `icon.ico` - Application and installer icon (required)
- `welcome.bmp` - Welcome page image (164x314 pixels, optional)
- `header.bmp` - Header image (150x57 pixels, optional)

### Version

When releasing a new version, update BOTH files:

1. `installer.nsi`:
   ```nsi
   !define PRODUCT_VERSION "X.Y.Z"
   ```

2. `Cargo.toml`:
   ```toml
   version = "X.Y.Z"
   ```

The installer filename automatically includes the version from `installer.nsi`.

## Uninstaller

The installer creates an uninstaller at:
```
%LOCALAPPDATA%\Nojoin\Uninstall.exe
```

Users can also uninstall via Windows Settings > Apps.
