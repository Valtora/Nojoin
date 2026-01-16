const fs = require('fs');
const path = require('path');

// Paths
const ROOT_DIR = path.resolve(__dirname, '..');
const VERSION_FILE = path.join(ROOT_DIR, 'docs', 'VERSION');
const PACKAGE_JSON = path.join(ROOT_DIR, 'companion', 'package.json');
const TAURI_CONF = path.join(ROOT_DIR, 'companion', 'src-tauri', 'tauri.conf.json');
const CARGO_TOML = path.join(ROOT_DIR, 'companion', 'src-tauri', 'Cargo.toml');

// Read Source of Truth
if (!fs.existsSync(VERSION_FILE)) {
    console.error(`Error: Version file not found at ${VERSION_FILE}`);
    process.exit(1);
}

const version = fs.readFileSync(VERSION_FILE, 'utf8').trim();
if (!version.match(/^\d+\.\d+\.\d+$/)) {
    console.error(`Error: Invalid version format in docs/VERSION: "${version}". Expected X.Y.Z`);
    process.exit(1);
}

console.log(`Syncing version ${version} to companion app files...`);

// 1. Update companion/package.json
if (fs.existsSync(PACKAGE_JSON)) {
    const pkg = JSON.parse(fs.readFileSync(PACKAGE_JSON, 'utf8'));
    const oldVer = pkg.version;
    pkg.version = version;
    fs.writeFileSync(PACKAGE_JSON, JSON.stringify(pkg, null, 2) + '\n');
    console.log(`[UPDATED] package.json: ${oldVer} -> ${version}`);
} else {
    console.error(`[ERROR] companion/package.json not found!`);
}

// 2. Update companion/src-tauri/tauri.conf.json
if (fs.existsSync(TAURI_CONF)) {
    const conf = JSON.parse(fs.readFileSync(TAURI_CONF, 'utf8'));
    const oldVer = conf.version;
    conf.version = version;
    fs.writeFileSync(TAURI_CONF, JSON.stringify(conf, null, 2) + '\n');
    console.log(`[UPDATED] tauri.conf.json: ${oldVer} -> ${version}`);
} else {
    console.error(`[ERROR] tauri.conf.json not found!`);
}

// 3. Update companion/src-tauri/Cargo.toml
if (fs.existsSync(CARGO_TOML)) {
    let cargo = fs.readFileSync(CARGO_TOML, 'utf8');
    // Regex to find 'version = "X.Y.Z"' specifically under [package] which is usually at the top
    // Robust regex to match 'version = "..."' but be careful not to match dependencies
    // Since [package] is usually first, we can replace the first occurrence of version = "..."
    
    // A safer way for TOML without a parser dependency (to keep script simple):
    // Look for the line `version = "..."`
    // We assume the first `version = "..."` is the package version.
    
    const versionRegex = /^version\s*=\s*"\d+\.\d+\.\d+"/m;
    const match = cargo.match(versionRegex);
    
    if (match) {
        const oldLine = match[0];
        const newLine = `version = "${version}"`;
        if (oldLine !== newLine) {
            cargo = cargo.replace(versionRegex, newLine);
            fs.writeFileSync(CARGO_TOML, cargo);
            console.log(`[UPDATED] Cargo.toml: ${oldLine} -> ${newLine}`);
        } else {
            console.log(`[SKIPPED] Cargo.toml already has version ${version}`);
        }
    } else {
        console.error(`[ERROR] Could not find version field in Cargo.toml`);
    }
} else {
    console.error(`[ERROR] Cargo.toml not found!`);
}

console.log('Version sync complete.');
