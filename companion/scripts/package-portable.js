const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// Configuration
const APP_NAME = 'Nojoin';
const TAURI_DIR = path.join(__dirname, '../src-tauri');
const TARGET_DIR = path.join(TAURI_DIR, 'target/release');
const OUTPUT_DIR = path.join(__dirname, '../dist-portable');

// Detect platform - only Windows supported
const platform = process.platform;
const isWin = platform === 'win32';

if (!isWin) {
  console.error('❌ Portable packaging is only supported on Windows.');
  console.error('   macOS and Linux companion apps are not currently supported.');
  process.exit(1);
}

// Ensure output directory exists
if (!fs.existsSync(OUTPUT_DIR)) {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
}

// Get version from package.json
const packageJson = require('../package.json');
const version = packageJson.version;

console.log(`📦 Packaging portable version ${version} for Windows...`);

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

// 2. Package Windows executable
const sourcePath = path.join(TARGET_DIR, `${APP_NAME}.exe`);
const destFilename = `Nojoin_${version}_windows_portable.exe`;
const destPath = path.join(OUTPUT_DIR, destFilename);

if (fs.existsSync(sourcePath)) {
  console.log(`📋 Packaging ${sourcePath} -> ${destPath}`);
  fs.copyFileSync(sourcePath, destPath);
  console.log(`✅ Portable executable created: ${destPath}`);
  
  // Check for WebView2Loader.dll on Windows
  const dllName = 'WebView2Loader.dll';
  const dllPath = path.join(TARGET_DIR, dllName);
  if (fs.existsSync(dllPath)) {
    const destDll = path.join(OUTPUT_DIR, dllName);
    console.log(`📋 Copying ${dllName} (required for some setups)...`);
    fs.copyFileSync(dllPath, destDll);
  }
  
  console.log(`\n✅ Packaging complete!`);
  console.log(`   Output: ${destPath}`);
} else {
  console.error(`❌ Source executable not found: ${sourcePath}`);
  process.exit(1);
}
