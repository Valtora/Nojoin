# Nojoin Companion: Tauri Transition Plan

## 1. Executive Summary
We are migrating the Nojoin Companion App from a custom Rust implementation (using `tao`/`wry`/`nsis`) to **Tauri**. 

**Why?**
*   **Native Notifications:** Tauri handles Windows AUMID registration and macOS bundle identifiers automatically, ensuring notifications work reliably.
*   **Cross-Platform Installers:** Tauri generates `.msi`/`.exe` (Windows), `.dmg` (macOS), and `.deb`/`.AppImage` (Linux) out of the box.
*   **Auto-Update:** Built-in updater compatible with GitHub Releases.
*   **Future Proofing:** Easier to add a GUI (Settings, Status) later using web technologies if needed.

## 2. Architecture Changes

| Feature | Old Architecture | New Tauri Architecture |
| :--- | :--- | :--- |
| **Runtime** | Custom `tokio` + `tao` event loop | Tauri Core (manages event loop) |
| **Tray Icon** | `tray-icon` crate | `tauri::SystemTray` |
| **Notifications** | `win32_notif` / `notify-rust` | `tauri::api::notification` |
| **Installer** | Custom NSIS Script (`build.ps1`) | `cargo tauri build` (Wix/NSIS) |
| **Updater** | Custom `updater.rs` | Tauri Updater (Config-based) |
| **Backend Logic** | `src/*.rs` | `src-tauri/src/*.rs` |

## 3. Migration Steps

### Phase 1: Initialization
1.  **Rename** existing `companion` to `companion-legacy` (backup).
2.  **Scaffold** new Tauri project in `companion/`.
    *   Use `cargo-tauri` or `npm create tauri-app`.
    *   Select "Rust" for backend.
    *   Select "HTML/JS" (or Next.js) for frontend (minimal, as we are headless for now).
3.  **Asset Integration**:
    *   Place `NojoinLogo.ico` and `NojoinLogo.png` into `src-tauri/icons/`.
    *   Configure `tauri.conf.json` to use these icons for the binary and installer.

### Phase 2: Porting Core Logic (The "Sidecar")
The "Backend" of a Tauri app is standard Rust. We can reuse 90% of existing code.
1.  **Dependencies**: Copy `[dependencies]` from old `Cargo.toml` to `src-tauri/Cargo.toml`.
    *   *Exclude*: `tao`, `tray-icon`, `notify-rust`, `win32_notif` (Tauri handles these).
    *   *Keep*: `cpal`, `tokio`, `reqwest`, `crossbeam-channel`, etc.
2.  **Modules**: Move `audio.rs`, `server.rs`, `uploader.rs`, `state.rs`, `config.rs` to `src-tauri/src/`.
3.  **Refactor `main.rs`**:
    *   Initialize `tauri::Builder`.
    *   Setup `SystemTray` and `SystemTrayMenu`.
    *   Spawn the `audio` and `server` threads inside the `setup` hook of Tauri.

### Phase 3: Feature Integration
1.  **Notifications**:
    *   Replace `notifications.rs` calls with `tauri::api::notification::Notification`.
    *   Tauri automatically handles the App ID on Windows.
2.  **Headless Mode**:
    *   Configure the main window to be hidden by default in `tauri.conf.json`.
    *   Ensure the app keeps running when the window is closed (if we add one later).
3.  **Auto-Update**:
    *   Configure `plugins.updater` in `tauri.conf.json`.
    *   Add the public key (generated via `tauri signer generate`) to the config.

### Phase 4: CI/CD & Build
1.  **Workflows**: Create `.github/workflows/companion-tauri.yml`.
    *   Use `tauri-apps/tauri-action`.
    *   This action automatically builds for Win/Mac/Linux and uploads artifacts to GitHub Releases.
2.  **Signing**:
    *   **Windows**: Tauri can sign the `.exe` if we provide a certificate (optional but recommended).
    *   **macOS**: Essential for notifications. We will need to configure code signing in the CI pipeline.

## 4. FAQ

### Will we still need CI/CD?
**Yes.**
You cannot build a macOS app on Windows, or a Windows app on Linux (easily).
*   **GitHub Actions** will spin up:
    *   `ubuntu-latest` -> Builds `.deb` / `.AppImage`
    *   `windows-latest` -> Builds `.msi` / `.exe` (NSIS)
    *   `macos-latest` -> Builds `.dmg` / `.app`

### Will we still need an installer?
**Yes.**
Tauri generates them for you.
*   **Windows**: It creates a setup `.exe` (NSIS) or `.msi`. This is crucial because it registers the "AppUserModelID" in the registry, which fixes the notification issue we faced.
*   **Self-Install?**: No, it's a standard installer experience. The user downloads `Nojoin-Setup.exe`, runs it, and it installs to `AppData`.

### What about the "Headless" nature?
Tauri is designed for UI apps, but it supports "System Tray only" apps perfectly.
*   We will define a "main window" in config but set `"visible": false`.
*   This allows us to have a hidden "engine" running.
*   If we ever want to add a "Settings" screen, we just toggle `"visible": true`.

## 5. Immediate Next Tasks
1.  [ ] Rename `companion` folder.
2.  [ ] Initialize Tauri project.
3.  [ ] Copy Rust logic files.
4.  [ ] Implement Tray Menu in Tauri.
