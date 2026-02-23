#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use log::{error, info};
use reqwest;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tauri::{
    menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, TrayIconBuilder, TrayIconEvent},
    Manager,
};

use semver::Version;

mod audio;
mod config;
mod notifications;
mod server;
mod state;
mod uploader;
mod win_notifications;

use config::Config;
use state::{AppState, AppStatus};
use tauri_plugin_autostart::ManagerExt;

// Define SharedAppState at module level so it's visible to commands
struct SharedAppState(Arc<AppState>);

#[tauri::command]
fn get_config(state: tauri::State<SharedAppState>) -> Config {
    // Access the inner Arc<AppState> via .0 on the dereferenced state
    let config = state.0.config.lock().unwrap();
    config.clone()
}

#[tauri::command]
fn resolve_pairing(state: tauri::State<SharedAppState>, window: tauri::Window, allowed: bool) {
    let mut pending = state.0.pending_pairing.lock().unwrap();
    if let Some(tx) = pending.take() {
        let _ = tx.send(allowed);
    }
    let _ = window.close();
}

#[tauri::command]
fn save_config(state: tauri::State<SharedAppState>, server_url: String) -> Result<(), String> {
    let mut config = state.0.config.lock().unwrap();

    // Parse URL
    // If it doesn't start with http:// or https://, assume https://
    let url_str = if !server_url.contains("://") {
        format!("https://{}", server_url)
    } else {
        server_url.clone()
    };

    let url = reqwest::Url::parse(&url_str).map_err(|e| format!("Invalid URL: {}", e))?;

    config.api_protocol = url.scheme().to_string();
    config.api_host = url.host_str().unwrap_or("localhost").to_string();
    config.api_port = url.port().unwrap_or_else(|| {
        if config.api_protocol == "http" {
            80
        } else {
            443
        }
    });

    config.save().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn close_update_prompt(window: tauri::Window) {
    let _ = window.close();
}

#[derive(serde::Deserialize)]
struct GitHubRelease {
    tag_name: String,
    html_url: String,
}

async fn check_github_release(current_version: &str) -> Result<Option<(String, String)>, String> {
    let client = reqwest::Client::builder()
        .user_agent("Nojoin-Companion")
        .build()
        .map_err(|e| e.to_string())?;

    let resp = client
        .get("https://api.github.com/repos/Valtora/Nojoin/releases/latest")
        .send()
        .await
        .map_err(|e| e.to_string())?;

    if resp.status().is_success() {
        let release: GitHubRelease = resp.json().await.map_err(|e| e.to_string())?;
        // tag_name is usually "v0.1.4"
        let version_str = release.tag_name.trim_start_matches('v');

        let remote_version = Version::parse(version_str)
            .map_err(|e| format!("Failed to parse remote version: {}", e))?;

        let current = Version::parse(current_version)
            .map_err(|e| format!("Failed to parse current version: {}", e))?;

        if remote_version > current {
            return Ok(Some((version_str.to_string(), release.html_url)));
        }
        Ok(None)
    } else {
        Err(format!("Failed to fetch releases: {}", resp.status()))
    }
}

async fn check_and_prompt_update(app: &tauri::AppHandle, silent: bool) {
    let current_version = app.package_info().version.to_string();

    match check_github_release(&current_version).await {
        Ok(Some((version, url))) => {
            let state_wrapper = app.state::<SharedAppState>();
            let state = &state_wrapper.0;

            state.update_available.store(true, Ordering::SeqCst);
            *state.latest_version.lock().unwrap() = Some(version.clone());
            *state.latest_update_url.lock().unwrap() = Some(url.clone());

            #[cfg(windows)]
            {
                win_notifications::show_update_notification(app.clone(), version, url);
            }
        }
        Ok(None) => {
            if !silent {
                notifications::show_notification(
                    app,
                    "No Update",
                    "You are on the latest version.",
                );
            }
        }
        Err(e) => {
            if !silent {
                notifications::show_notification(app, "Update Error", &e.to_string());
            }
        }
    }
}

fn get_log_path() -> PathBuf {
    let app_data = Config::get_app_data_dir();
    // Ensure directory exists
    if let Err(e) = std::fs::create_dir_all(&app_data) {
        eprintln!("Failed to create app data directory: {}", e);
    }
    app_data.join("nojoin-companion.log")
}

fn setup_logging() -> Result<(), fern::InitError> {
    let log_path = get_log_path();

    fern::Dispatch::new()
        .format(|out, message, record| {
            out.finish(format_args!(
                "[{} {} {}] {}",
                chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
                record.level(),
                record.target(),
                message
            ))
        })
        .level(log::LevelFilter::Info)
        .chain(fern::log_file(&log_path)?)
        .apply()?;

    Ok(())
}

fn open_web_interface(app: &tauri::AppHandle) {
    let state_wrapper = app.state::<SharedAppState>();
    let state = &state_wrapper.0;

    let url = {
        let dynamic_url = state.web_url.lock().unwrap().clone();
        if let Some(d_url) = dynamic_url {
            Some(d_url)
        } else {
            let config = state.config.lock().unwrap();
            Some(config.get_web_url())
        }
    };

    if let Some(target_url) = url {
        let _ = open::that(target_url);
    } else {
        notifications::show_notification(app, "Error", "Backend URL not found.");
    }
}

fn main() {
    if let Err(e) = setup_logging() {
        eprintln!("Failed to initialize logging: {}", e);
    }
    info!("Starting Nojoin Companion (Tauri)...");

    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            notifications::show_notification(app, "Nojoin Companion", "An instance is already running.");
        }))
        .plugin(tauri_plugin_autostart::init(tauri_plugin_autostart::MacosLauncher::LaunchAgent, Some(vec![])))
        .invoke_handler(tauri::generate_handler![get_config, save_config, close_update_prompt, resolve_pairing])
        .setup(|app| {
            let (audio_tx, audio_rx) = crossbeam_channel::unbounded();
            let mut config = Config::load();

            let autostart_manager = app.autolaunch();
            let mut is_enabled = autostart_manager.is_enabled().unwrap_or(false);
            if let Some(should_run) = config.run_on_startup {
                if should_run && !is_enabled {
                    if let Err(e) = autostart_manager.enable() {
                        error!("Failed to enable autostart on load: {}", e);
                    } else {
                        is_enabled = true;
                    }
                } else if !should_run && is_enabled {
                    if let Err(e) = autostart_manager.disable() {
                        error!("Failed to disable autostart on load: {}", e);
                    } else {
                        is_enabled = false;
                    }
                }
            } else {
                config.run_on_startup = Some(is_enabled);
                let _ = config.save();
            }

            let state = Arc::new(AppState {
                status: Mutex::new(AppStatus::Idle),
                current_recording_id: Mutex::new(None),
                current_sequence: Mutex::new(1),
                audio_command_tx: audio_tx.clone(),
                config: Mutex::new(config),
                recording_start_time: Mutex::new(None),
                accumulated_duration: Mutex::new(Duration::new(0, 0)),
                input_level: AtomicU32::new(0),
                output_level: AtomicU32::new(0),
                web_url: Mutex::new(None),
                is_backend_connected: AtomicBool::new(false),
                update_available: AtomicBool::new(false),
                latest_version: Mutex::new(None),
                latest_update_url: Mutex::new(None),
                tray_status_item: Mutex::new(None),
                tray_run_on_startup_item: Mutex::new(None),
                tray_open_web_item: Mutex::new(None),
                tray_icon: Mutex::new(None),
                pending_pairing: Mutex::new(None),
            });

            app.manage(SharedAppState(state.clone()));

            // Create Menu Items
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let about = MenuItem::with_id(app, "about", "About", true, None::<&str>)?;
            let help = MenuItem::with_id(app, "help", "Help", true, None::<&str>)?;
            let view_logs = MenuItem::with_id(app, "view_logs", "View Logs", true, None::<&str>)?;
            let check_updates = MenuItem::with_id(app, "check_updates", "Check for Updates", true, None::<&str>)?;

            // Initialize run_on_startup checkmark using enforced is_enabled
            let run_on_startup = CheckMenuItem::with_id(app, "run_on_startup", "Run on Startup", true, is_enabled, None::<&str>)?;

            let open_web = MenuItem::with_id(app, "open_web", "Open Nojoin", true, None::<&str>)?;
            let settings = MenuItem::with_id(app, "settings", "Connection Settings", true, None::<&str>)?;
            // Enables status item without attaching action.
            let status_item = MenuItem::with_id(app, "status", "Status: Waiting for connection...", true, None::<&str>)?;

            // Store items in state
            *state.tray_status_item.lock().unwrap() = Some(status_item.clone());
            *state.tray_run_on_startup_item.lock().unwrap() = Some(run_on_startup.clone());
            *state.tray_open_web_item.lock().unwrap() = Some(open_web.clone());

            let menu = Menu::with_items(app, &[
                &status_item,
                &PredefinedMenuItem::separator(app)?,
                &open_web,
                &settings,
                &run_on_startup,
                &check_updates,
                &view_logs,
                &help,
                &about,
                &PredefinedMenuItem::separator(app)?,
                &quit,
            ])?;

            let tray = TrayIconBuilder::new()
                .tooltip("Nojoin Companion")
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .on_menu_event(move |app, event| {
                    match event.id.as_ref() {
                        "quit" => {
                            std::process::exit(0);
                        }
                        "settings" => {
                            let window = app.get_webview_window("settings");
                            if let Some(window) = window {
                                let _ = window.show();
                                let _ = window.set_focus();
                            } else {
                                let _ = tauri::WebviewWindowBuilder::new(
                                    app,
                                    "settings",
                                    tauri::WebviewUrl::App("settings.html".into())
                                )
                                .title("Nojoin Companion Settings")
                                .inner_size(400.0, 350.0)
                                .build();
                            }
                        }
                        "open_web" => {
                            open_web_interface(app);
                        }
                        "about" => {
                             notifications::show_notification(
                                 app,
                                 "About Nojoin Companion",
                                 &format!("This is the Nojoin Companion App that let's Nojoin listen in on your meetings.\n\nVersion {}", app.package_info().version)
                             );
                        }
                        "help" => {
                            let _ = open::that("https://github.com/Valtora/Nojoin");
                        }
                        "view_logs" => {
                            let log_path = get_log_path();
                            if let Some(log_dir) = log_path.parent() {
                                let _ = open::that(log_dir);
                            }
                        }
                        "check_updates" => {
                            let handle = app.clone();
                            tauri::async_runtime::spawn(async move {
                                check_and_prompt_update(&handle, false).await;
                            });
                        }
                        "run_on_startup" => {
                            let autostart_manager = app.autolaunch();
                            let state_wrapper = app.state::<SharedAppState>();
                            let state = &state_wrapper.0;
                            let item_guard = state.tray_run_on_startup_item.lock().unwrap();

                            if autostart_manager.is_enabled().unwrap_or(false) {
                                if let Err(e) = autostart_manager.disable() {
                                    error!("Failed to disable autostart: {}", e);
                                } else {
                                    if let Some(item) = item_guard.as_ref() {
                                        let _ = item.set_checked(false);
                                    }
                                    let mut config = state.config.lock().unwrap();
                                    config.run_on_startup = Some(false);
                                    let _ = config.save();
                                }
                            } else {
                                if let Err(e) = autostart_manager.enable() {
                                    error!("Failed to enable autostart: {}", e);
                                    notifications::show_notification(app, "Error", "Could not enable run on startup");
                                } else {
                                    if let Some(item) = item_guard.as_ref() {
                                        let _ = item.set_checked(true);
                                    }
                                    let mut config = state.config.lock().unwrap();
                                    config.run_on_startup = Some(true);
                                    let _ = config.save();
                                }
                            }
                        }
                        _ => {}
                    }
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::DoubleClick { button: MouseButton::Left, .. } = event {
                        open_web_interface(tray.app_handle());
                    }
                })
                .build(app)?;

            *state.tray_icon.lock().unwrap() = Some(tray);

            // Post-update check
            {
                let mut config = state.config.lock().unwrap();
                let current_version = app.package_info().version.to_string();

                if let Some(last_ver) = &config.last_version {
                    if last_ver != &current_version {
                        notifications::show_notification(app.handle(), "Updated", &format!("Nojoin Companion App Updated v{}", current_version));
                    }
                }

                if config.last_version.as_ref() != Some(&current_version) {
                    config.last_version = Some(current_version);
                    let _ = config.save();
                }
            }

            // Start update check loop
            let app_handle_update = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                // Initial delay
                tokio::time::sleep(Duration::from_secs(10)).await;
                check_and_prompt_update(&app_handle_update, true).await;

                loop {
                    tokio::time::sleep(Duration::from_secs(6 * 60 * 60)).await; // 6 hours
                    check_and_prompt_update(&app_handle_update, true).await;
                }
            });

            let state_audio = state.clone();
            // audio_tx resides in state; audio_rx retained locally for loop.
            let app_handle_audio = app.handle().clone();

            thread::spawn(move || {
                audio::run_audio_loop(state_audio, audio_rx, app_handle_audio);
            });

            let state_server = state.clone();
            let app_handle = app.handle().clone();

            thread::spawn(move || {
                let rt = tokio::runtime::Runtime::new().unwrap();

                // Health Check & Status Update Loop
                let state_fetch = state_server.clone();

                rt.spawn(async move {
                    let client = reqwest::Client::builder()
                        .danger_accept_invalid_certs(true)
                        .timeout(Duration::from_secs(5))
                        .build()
                        .unwrap_or_default();

                    loop {
                        // 1. Perform Health Check
                        let api_url = {
                            let config = state_fetch.config.lock().unwrap();
                            config.get_api_url()
                        };
                        let status_url = format!("{}/system/status", api_url);

                        match client.get(&status_url).send().await {
                            Ok(resp) => {
                                if resp.status().is_success() {
                                    state_fetch.is_backend_connected.store(true, Ordering::SeqCst);
                                    let mut status = state_fetch.status.lock().unwrap();
                                    if *status == AppStatus::BackendOffline {
                                        *status = AppStatus::Idle;
                                    }
                                } else {
                                    state_fetch.is_backend_connected.store(false, Ordering::SeqCst);
                                    let mut status = state_fetch.status.lock().unwrap();
                                    if *status == AppStatus::Idle {
                                        *status = AppStatus::BackendOffline;
                                    }
                                }
                            }
                            Err(_) => {
                                state_fetch.is_backend_connected.store(false, Ordering::SeqCst);
                                let mut status = state_fetch.status.lock().unwrap();
                                if *status == AppStatus::Idle {
                                    *status = AppStatus::BackendOffline;
                                }
                            }
                        }

                        // 2. Update Tray Icon Text
                        if let Ok(status) = state_fetch.status.try_lock() {
                             let status_text = if !state_fetch.is_authenticated() {
                                 "Status: Waiting for connection..."
                             } else {
                                 match *status {
                                     AppStatus::Idle => "Status: Ready to Record",
                                     AppStatus::Recording => "Status: Recording",
                                     AppStatus::Paused => "Status: Recording Paused",
                                     AppStatus::Uploading => "Status: Uploading Recording",
                                     AppStatus::BackendOffline => "Status: Backend Not Found...",
                                     AppStatus::Error(_) => "Status: Error",
                                 }
                             };

                             if let Some(item) = state_fetch.tray_status_item.lock().unwrap().as_ref() {
                                 let _ = item.set_text(status_text);
                             }

                             if let Some(tray) = state_fetch.tray_icon.lock().unwrap().as_ref() {
                                 let _ = tray.set_tooltip(Some(status_text));
                             }

                             let is_connected = state_fetch.is_backend_connected.load(Ordering::SeqCst);
                             if let Some(item) = state_fetch.tray_open_web_item.lock().unwrap().as_ref() {
                                 let _ = item.set_enabled(is_connected);
                             }
                        }

                        tokio::time::sleep(Duration::from_secs(5)).await;
                    }
                });

                rt.block_on(server::start_server(state_server, app_handle));
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
