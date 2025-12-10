const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

// Paths
const projectRoot = path.resolve(__dirname, '..');
const srcTauri = path.join(projectRoot, 'src-tauri');
const targetRelease = path.join(srcTauri, 'target', 'release');
const bundleMacos = path.join(targetRelease, 'bundle', 'macos');
const bundleDmg = path.join(targetRelease, 'bundle', 'dmg');
const appName = 'Nojoin.app';
const appPath = path.join(bundleMacos, appName);
const packageJson = require('../package.json');
const version = packageJson.version;
// Assuming aarch64 for now as we are on Apple Silicon, but ideally should detect
const arch = process.arch === 'arm64' ? 'aarch64' : 'x64'; 
const dmgName = `Nojoin_${version}_${arch}.dmg`;
const dmgPath = path.join(bundleDmg, dmgName);
const createDmgScript = path.join(__dirname, 'create-dmg.sh');
const licenseFile = path.join(srcTauri, 'LICENSE_DISCLAIMER.txt');
const iconFile = path.join(srcTauri, 'icons', 'icon.icns');

console.log(`Running build on platform: ${process.platform}`);

console.log('Building Tauri App...');
try {
    execSync('npm run tauri build', { stdio: 'inherit', cwd: projectRoot });
} catch (e) {
    console.error('Build failed');
    process.exit(1);
}

// Ad-hoc sign the app to prevent "App is damaged" error on macOS
if (process.platform === 'darwin') {
    console.log('Ad-hoc signing the app...');
    try {
        execSync(`codesign --force --deep --sign - "${appPath}"`, { stdio: 'inherit' });
    } catch (e) {
        console.error('Signing failed:', e);
        process.exit(1);
    }
}

// Only run DMG creation on macOS
if (process.platform === 'darwin') {
    console.log('macOS detected. Creating DMG with License...');

    // Ensure bundle/dmg directory exists
    if (!fs.existsSync(bundleDmg)) {
        fs.mkdirSync(bundleDmg, { recursive: true });
    }

    // Clean previous DMG
    if (fs.existsSync(dmgPath)) {
        console.log(`Removing existing DMG: ${dmgPath}`);
        fs.unlinkSync(dmgPath);
    }

    // Command arguments for create-dmg
    // Note: We point to our local support directory for the EULA template
    const supportDir = path.join(__dirname, 'support');
    // We need to set the sentinel file or just set CDMG_SUPPORT_DIR env var if the script supports it.
    // Looking at the script, it checks for .this-is-the-create-dmg-repo in the script dir.
    // Let's create that sentinel file temporarily.
    const sentinelFile = path.join(__dirname, '.this-is-the-create-dmg-repo');
    fs.writeFileSync(sentinelFile, '');

    try {
        const cmd = `"${createDmgScript}" \
            --volname "Nojoin" \
            --volicon "${iconFile}" \
            --icon "${appName}" 180 170 \
            --app-drop-link 480 170 \
            --window-size 660 400 \
            --hide-extension "${appName}" \
            "${dmgPath}" \
            "${appPath}"`;

        console.log(`Running: ${cmd}`);
        execSync(cmd, { stdio: 'inherit', cwd: __dirname });
        console.log(`DMG created successfully: ${dmgPath}`);
    } catch (e) {
        console.error('DMG creation failed:', e);
        process.exit(1);
    } finally {
        // Cleanup sentinel
        if (fs.existsSync(sentinelFile)) {
            fs.unlinkSync(sentinelFile);
        }
    }
} else {
    console.log('Not on macOS. Skipping DMG creation.');
}
