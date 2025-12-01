# Nojoin Companion Installer Build Script
# Run from the companion directory: .\installer\build.ps1

param(
    [switch]$Release,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CompanionDir = Split-Path -Parent $ScriptDir
$DistDir = Join-Path $CompanionDir "dist"

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  Nojoin Companion Installer Builder  " -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# Change to companion directory
Set-Location $CompanionDir

# Step 1: Build the Rust application
if (-not $SkipBuild) {
    Write-Host "[1/4] Building Rust application..." -ForegroundColor Yellow
    
    if ($Release) {
        cargo build --release
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: Cargo build failed!" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "  Skipping release build (use -Release flag for production)" -ForegroundColor Gray
        Write-Host "  Using existing release binary if available..." -ForegroundColor Gray
    }
    
    # Check if release binary exists
    $BinaryPath = Join-Path $CompanionDir "target\release\nojoin-companion.exe"
    if (-not (Test-Path $BinaryPath)) {
        Write-Host "ERROR: Release binary not found at $BinaryPath" -ForegroundColor Red
        Write-Host "       Run with -Release flag to build" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "  Binary ready: $BinaryPath" -ForegroundColor Green
} else {
    Write-Host "[1/4] Skipping Rust build (--SkipBuild)" -ForegroundColor Gray
}

# Step 2: Prepare dist directory
Write-Host "[2/4] Preparing distribution directory..." -ForegroundColor Yellow

if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir | Out-Null
}

Write-Host "  Distribution directory: $DistDir" -ForegroundColor Green

# Step 3: Check for NSIS
Write-Host "[3/4] Checking for NSIS..." -ForegroundColor Yellow

$NSISPath = $null
$PossiblePaths = @(
    "C:\Program Files (x86)\NSIS\makensis.exe",
    "C:\Program Files\NSIS\makensis.exe",
    "$env:ProgramFiles\NSIS\makensis.exe",
    "${env:ProgramFiles(x86)}\NSIS\makensis.exe"
)

foreach ($path in $PossiblePaths) {
    if (Test-Path $path) {
        $NSISPath = $path
        break
    }
}

# Also check PATH
if (-not $NSISPath) {
    $NSISPath = Get-Command makensis -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
}

if (-not $NSISPath) {
    Write-Host "ERROR: NSIS not found!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please install NSIS from: https://nsis.sourceforge.io/Download" -ForegroundColor Yellow
    Write-Host "Or install via Chocolatey: choco install nsis" -ForegroundColor Yellow
    exit 1
}

Write-Host "  NSIS found: $NSISPath" -ForegroundColor Green

# Step 4: Build the installer
Write-Host "[4/4] Building installer..." -ForegroundColor Yellow

$InstallerScript = Join-Path $ScriptDir "installer.nsi"

# Check for required installer assets
$IconPath = Join-Path $ScriptDir "..\src\icon.ico"
if (-not (Test-Path $IconPath)) {
    Write-Host "  WARNING: icon.ico not found, using default" -ForegroundColor Yellow
    Write-Host "  Create $IconPath for custom installer icon" -ForegroundColor Gray
}

# Run NSIS
Set-Location $ScriptDir
& $NSISPath $InstallerScript

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: NSIS build failed!" -ForegroundColor Red
    exit 1
}

$InstallerPath = Join-Path $DistDir "NojoinCompanion-Setup.exe"
if (Test-Path $InstallerPath) {
    $FileInfo = Get-Item $InstallerPath
    Write-Host ""
    Write-Host "======================================" -ForegroundColor Green
    Write-Host "  Build Successful!" -ForegroundColor Green
    Write-Host "======================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Installer: $InstallerPath" -ForegroundColor Cyan
    Write-Host "  Size: $([math]::Round($FileInfo.Length / 1MB, 2)) MB" -ForegroundColor Cyan
    Write-Host ""
} else {
    Write-Host "ERROR: Installer not created!" -ForegroundColor Red
    exit 1
}

Set-Location $CompanionDir

