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
let isFolder = false;

// Map platform to user-friendly name
const platformName = isWin ? 'windows' : (isMac ? 'macos' : 'linux');
const ext = isWin ? 'exe' : (isMac ? 'zip' : 'AppImage');

destFilename = `Nojoin_${version}_${platformName}_portable.${ext}`;

if (isWin) {
    sourcePath = path.join(TARGET_DIR, `${APP_NAME}.exe`);
} else if (isLinux) {
    // Try to find AppImage first as it is the true portable format for Linux
    const bundleDir = path.join(TARGET_DIR, 'bundle/appimage');
    if (fs.existsSync(bundleDir)) {
        const files = fs.readdirSync(bundleDir);
        const appImage = files.find(f => f.endsWith('.AppImage'));
        if (appImage) {
            sourcePath = path.join(bundleDir, appImage);
        }
    }
    
    // Fallback to raw binary if AppImage not found
    if (!sourcePath) {
        const possibleNames = ['Nojoin', 'nojoin-companion'];
        for (const name of possibleNames) {
            const p = path.join(TARGET_DIR, name);
            if (fs.existsSync(p)) {
                sourcePath = p;
                // If falling back to raw binary, no extension
                destFilename = `Nojoin_${version}_${platformName}_portable`;
                break;
            }
        }
    }
} else if (isMac) {
    // For macOS, we zip the .app bundle
    const bundleMacos = path.join(TARGET_DIR, 'bundle/macos');
    sourcePath = path.join(bundleMacos, `${APP_NAME}.app`);
    isFolder = true;
}

// 3. Copy/Package the file
if (sourcePath && fs.existsSync(sourcePath)) {
    const destPath = path.join(OUTPUT_DIR, destFilename);
    console.log(`📋 Packaging ${sourcePath} -> ${destPath}`);
    
    if (isMac && isFolder) {
        // Zip the .app bundle
        try {
            // -r for recursive, -y to store symlinks as symlinks (important for .app)
            // We need to run zip from the parent directory of the .app to get the structure right
            const parentDir = path.dirname(sourcePath);
            const appName = path.basename(sourcePath);
            execSync(`zip -r -y "${destPath}" "${appName}"`, { cwd: parentDir, stdio: 'inherit' });
            console.log(`✅ Portable zip created: ${destPath}`);
        } catch (e) {
            console.error('❌ Failed to zip .app bundle:', e);
            process.exit(1);
        }
    } else {
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
    }
    
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
