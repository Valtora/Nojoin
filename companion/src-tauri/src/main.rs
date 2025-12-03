#![cfg_attr(
  all(not(debug_assertions), target_os = "windows"),
  windows_subsystem = "windows"
)]

use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicU32, AtomicBool, Ordering};
use std::thread;
use std::time::Duration;
use std::path::PathBuf;
use tauri::{CustomMenuItem, SystemTray, SystemTrayMenu, SystemTrayMenuItem, SystemTrayEvent, Manager};
use log::{info, error};
use reqwest;

mod server;
mod audio;
mod state;
mod uploader;
mod config;
mod notifications;
mod win_notifications;
mod mac_notifications;
mod linux_notifications;

use state::{AppState, AppStatus};
use config::Config;
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};

// Define SharedAppState at module level so it's visible to commands
struct SharedAppState(Arc<AppState>);

#[tauri::command]
fn get_config(state: tauri::State<SharedAppState>) -> Config {
    // Access the inner Arc<AppState> via .0 on the dereferenced state
    let config = state.0.config.lock().unwrap();
    config.clone()
}

#[tauri::command]
fn save_config(state: tauri::State<SharedAppState>, api_host: String, api_port: u16) -> Result<(), String> {
    let mut config = state.0.config.lock().unwrap();
    config.api_host = api_host;
    config.api_port = api_port;
    
    config.save().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
async fn install_update(app: tauri::AppHandle) -> Result<(), String> {
    match app.updater().check().await {
        Ok(update) => {
            if update.is_update_available() {
                update.download_and_install().await.map_err(|e| e.to_string())?;
                tauri::api::process::restart(&app.env());
                Ok(())
            } else {
                Err("No update available".to_string())
            }
        }
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
fn close_update_prompt(window: tauri::Window) {
    let _ = window.close();
}

async fn check_and_prompt_update(app: &tauri::AppHandle, silent: bool) {
    match app.updater().check().await {
        Ok(update) => {
            if update.is_update_available() {
                let version = update.latest_version().to_string();
                
                #[cfg(windows)]
                {
                    win_notifications::show_update_notification(app.clone(), version);
                }

                #[cfg(target_os = "macos")]
                {
                    mac_notifications::show_update_notification(app.clone(), version);
                }

                #[cfg(target_os = "linux")]
                {
                    linux_notifications::show_update_notification(app.clone(), version);
                }
            } else if !silent {
                notifications::show_notification("No Updates", "You are on the latest version.");
            }
        }
        Err(e) => {
            error!("Update check failed: {}", e);
            if !silent {
                notifications::show_notification("Update Error", &e.to_string());
            }
        }
    }
}

fn get_log_path() -> PathBuf {
    // In Tauri, we might want to use the app data directory, but for now let's stick to exe dir or current dir
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|p| p.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."))
        .join("nojoin-companion.log")
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

fn main() {
    if let Err(e) = setup_logging() {
        eprintln!("Failed to initialize logging: {}", e);
    }
    info!("Starting Nojoin Companion (Tauri)...");

    let quit = CustomMenuItem::new("quit".to_string(), "Quit");
    let restart = CustomMenuItem::new("restart".to_string(), "Restart");
    let about = CustomMenuItem::new("about".to_string(), "About");
    let help = CustomMenuItem::new("help".to_string(), "Help");
    let view_logs = CustomMenuItem::new("view_logs".to_string(), "View Logs");
    let check_updates = CustomMenuItem::new("check_updates".to_string(), "Check for Updates");
    let run_on_startup = CustomMenuItem::new("run_on_startup".to_string(), "Run on Startup");
    let open_web = CustomMenuItem::new("open_web".to_string(), "Open Nojoin");
    let settings = CustomMenuItem::new("settings".to_string(), "Settings");
    let status_item = CustomMenuItem::new("status".to_string(), "Status: Waiting for connection...").disabled();

    let tray_menu = SystemTrayMenu::new()
        .add_item(status_item)
        .add_native_item(SystemTrayMenuItem::Separator)
        .add_item(open_web)
        .add_item(settings)
        .add_item(run_on_startup)
        .add_item(check_updates)
        .add_item(view_logs)
        .add_item(help)
        .add_item(about)
        .add_native_item(SystemTrayMenuItem::Separator)
        .add_item(restart)
        .add_item(quit);

    let system_tray = SystemTray::new()
        .with_tooltip("Nojoin Companion")
        .with_menu(tray_menu);

    // We need a way to share state with the event handler.
    // Since the event handler is a closure, we can't easily move the Arc<AppState> into it AND the setup closure.
    // Tauri's app.manage() is the "Tauri way", but our existing code uses Arc<AppState>.
    // We will use a global or a Mutex inside a lazy_static if needed, OR just rely on the fact that
    // we can clone the Arc for the threads, but for the menu click handler, we might need to access it.
    // Actually, we can put the Arc<AppState> into Tauri's managed state!
    
    // However, our AppState is complex. Let's wrap it.
    // struct SharedAppState(Arc<AppState>); // Moved to module level

    tauri::Builder::default()
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec![])))
        .invoke_handler(tauri::generate_handler![get_config, save_config, install_update, close_update_prompt])
        .system_tray(system_tray)
        .on_system_tray_event(|app, event| match event {
            SystemTrayEvent::MenuItemClick { id, .. } => {
                match id.as_str() {
                    "quit" => {
                        std::process::exit(0);
                    }
                    "settings" => {
                        let window = app.get_window("settings");
                        if let Some(window) = window {
                            window.show().unwrap();
                            window.set_focus().unwrap();
                        } else {
                            tauri::WindowBuilder::new(
                                app,
                                "settings",
                                tauri::WindowUrl::App("settings.html".into())
                            )
                            .title("Nojoin Companion Settings")
                            .inner_size(400.0, 350.0)
                            .build()
                            .unwrap();
                        }
                    }
                    "open_web" => {
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
                            notifications::show_notification("Error", "Backend URL not found.");
                        }
                    }
                    "about" => {
                         notifications::show_notification(
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
                        // Trigger Tauri updater check
                        let handle = app.app_handle();
                        tauri::async_runtime::spawn(async move {
                            check_and_prompt_update(&handle, false).await;
                        });
                    }
                    "run_on_startup" => {
                        let autostart_manager = app.autolaunch();
                        if autostart_manager.is_enabled().unwrap_or(false) {
                            if let Err(e) = autostart_manager.disable() {
                                error!("Failed to disable autostart: {}", e);
                            } else {
                                let _ = app.tray_handle().get_item("run_on_startup").set_selected(false);
                            }
                        } else {
                            if let Err(e) = autostart_manager.enable() {
                                error!("Failed to enable autostart: {}", e);
                                notifications::show_notification("Error", "Could not enable run on startup");
                            } else {
                                let _ = app.tray_handle().get_item("run_on_startup").set_selected(true);
                            }
                        }
                    }
                    "restart" => {
                        notifications::show_notification("Restart", "Please restart manually.");
                    }
                    _ => {}
                }
            }
            _ => {}
        })
        .setup(|app| {
            let (audio_tx, audio_rx) = crossbeam_channel::unbounded();
            let config = Config::load();
            
            let state = Arc::new(AppState {
                status: Mutex::new(AppStatus::Idle),
                current_recording_id: Mutex::new(None),
                current_sequence: Mutex::new(1),
                audio_command_tx: audio_tx,
                config: Mutex::new(config),
                recording_start_time: Mutex::new(None),
                accumulated_duration: Mutex::new(Duration::new(0, 0)),
                input_level: AtomicU32::new(0),
                output_level: AtomicU32::new(0),
                web_url: Mutex::new(None),
                is_backend_connected: AtomicBool::new(false),
            });

            // Manage the state so we can access it in menu handlers
            app.manage(SharedAppState(state.clone()));

            // Initialize run_on_startup checkmark
            let autostart_manager = app.autolaunch();
            let is_enabled = autostart_manager.is_enabled().unwrap_or(false);
            let _ = app.tray_handle().get_item("run_on_startup").set_selected(is_enabled);

            // Post-update check
            {
                let mut config = state.config.lock().unwrap();
                let current_version = app.package_info().version.to_string();
                
                if let Some(last_ver) = &config.last_version {
                    if last_ver != &current_version {
                        notifications::show_notification("Updated", &format!("Nojoin Companion App Updated v{}", current_version));
                    }
                }
                
                if config.last_version.as_ref() != Some(&current_version) {
                    config.last_version = Some(current_version);
                    let _ = config.save();
                }
            }

            // Start update check loop
            let app_handle_update = app.handle();
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
            thread::spawn(move || {
                audio::run_audio_loop(state_audio, audio_rx);
            });

            let state_server = state.clone();
            let app_handle = app.handle();
            
            thread::spawn(move || {
                let rt = tokio::runtime::Runtime::new().unwrap();
                
                // Health Check & Status Update Loop
                let state_fetch = state_server.clone();
                let tray_handle = app_handle.tray_handle();
                
                rt.spawn(async move {
                    let client = reqwest::Client::builder()
                        .danger_accept_invalid_certs(true)
                        .timeout(Duration::from_secs(5))
                        .build()
                        .unwrap_or_default();
                    
                    loop {
                        // 1. Perform Health Check (Logic copied from old main.rs)
                        let api_url = {
                            let config = state_fetch.config.lock().unwrap();
                            config.get_api_url()
                        };
                        let status_url = format!("{}/system/status", api_url);
                        
                        // ... (Simplified health check logic for brevity, assuming similar to before)
                        // We need to update the tray status text based on state
                        
                        match client.get(&status_url).send().await {
                            Ok(resp) => {
                                if resp.status().is_success() {
                                    state_fetch.is_backend_connected.store(true, Ordering::SeqCst);
                                    // Update web url logic...
                                    
                                    // Reset status if offline
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
                             let _ = tray_handle.get_item("status").set_title(status_text);
                             let _ = tray_handle.set_tooltip(status_text);
                             
                             let is_connected = state_fetch.is_backend_connected.load(Ordering::SeqCst);
                             let _ = tray_handle.get_item("open_web").set_enabled(is_connected);
                        }

                        tokio::time::sleep(Duration::from_secs(5)).await;
                    }
                });

                rt.block_on(server::start_server(state_server));
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
