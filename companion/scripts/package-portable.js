const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// Configuration
const APP_NAME = 'Nojoin';
const TAURI_DIR = path.join(__dirname, '../src-tauri');
const TARGET_DIR = path.join(TAURI_DIR, 'target/release');
const OUTPUT_DIR = path.join(__dirname, '../dist-portable');

// Detect platform
const platform = process.platform;
const isWin = platform === 'win32';
const isLinux = platform === 'linux';
const isMac = platform === 'darwin';

// Ensure output directory exists
if (!fs.existsSync(OUTPUT_DIR)) {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
}

// Get version from package.json
const packageJson = require('../package.json');
const version = packageJson.version;

console.log(`📦 Packaging portable version ${version} for ${platform}...`);

// 1. Build the app
const skipBuild = process.argv.includes('--no-build');

if (!skipBuild) {
  console.log('🔨 Building Tauri app...');
  try {
    execSync('npm run tauri build', { stdio: 'inherit', cwd: path.join(__dirname, '..') });
  } catch (error) {
    console.error('❌ Build failed.');
    process.exit(1);
  }
} else {
  console.log('⏩ Skipping build step...');
}

// 2. Identify Source and Destination
let sourcePath;
let destFilename;

if (isWin) {
    sourcePath = path.join(TARGET_DIR, `${APP_NAME}.exe`);
    destFilename = `${APP_NAME}-Companion-Portable.exe`;
} else if (isLinux) {
    // Try to find AppImage first as it is the true portable format for Linux
    const bundleDir = path.join(TARGET_DIR, 'bundle/appimage');
    if (fs.existsSync(bundleDir)) {
        const files = fs.readdirSync(bundleDir);
        const appImage = files.find(f => f.endsWith('.AppImage'));
        if (appImage) {
            sourcePath = path.join(bundleDir, appImage);
            destFilename = `${APP_NAME}-Companion-Portable.AppImage`;
        }
    }
    
    // Fallback to raw binary if AppImage not found (or if user prefers? No, AppImage is better)
    if (!sourcePath) {
        // Note: The raw binary might be named 'nojoin-companion' (from package.json name) or 'Nojoin' (from tauri.conf.json product name)
        // tauri.conf.json says "mainBinaryName": "Nojoin"
        // But let's check both
        const possibleNames = ['Nojoin', 'nojoin-companion'];
        for (const name of possibleNames) {
            const p = path.join(TARGET_DIR, name);
            if (fs.existsSync(p)) {
                sourcePath = p;
                destFilename = `${APP_NAME}-Companion-Portable`; // No extension
                break;
            }
        }
    }
} else if (isMac) {
    console.log('ℹ️  Skipping portable build for macOS (DMG is already portable).');
    process.exit(0);
}

// 3. Copy the file
if (sourcePath && fs.existsSync(sourcePath)) {
    const destPath = path.join(OUTPUT_DIR, destFilename);
    console.log(`📋 Copying ${sourcePath} -> ${destPath}`);
    fs.copyFileSync(sourcePath, destPath);
    
    // Make executable on Unix
    if (!isWin) {
        try {
            execSync(`chmod +x "${destPath}"`);
        } catch (e) {
            // ignore
        }
    }

    console.log(`✅ Portable binary created: ${destPath}`);
    
    // Check for WebView2Loader.dll on Windows
    if (isWin) {
        const dllName = 'WebView2Loader.dll';
        const dllPath = path.join(TARGET_DIR, dllName);
        if (fs.existsSync(dllPath)) {
            const destDll = path.join(OUTPUT_DIR, dllName);
            console.log(`📋 Copying ${dllName} (required for some setups)...`);
            fs.copyFileSync(dllPath, destDll);
        }
    }

} else {
    console.error(`❌ Could not find binary to package.`);
    if (sourcePath) console.error(`   Looked at: ${sourcePath}`);
    process.exit(1);
}
