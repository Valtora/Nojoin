#![windows_subsystem = "windows"]

use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicU32, AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};
use std::path::PathBuf;
use tray_icon::{TrayIconBuilder, menu::{Menu, MenuItem, MenuEvent, PredefinedMenuItem}, Icon};
use tao::event_loop::{EventLoop, ControlFlow};
use reqwest;
use serde_json;
use log::{info, warn, error};

mod server;
mod audio;
mod state;
mod uploader;
mod config;
mod notifications;
mod updater;

use state::{AppState, AppStatus};
use config::Config;

fn get_log_path() -> PathBuf {
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

fn load_icon() -> Icon {
    let (icon_rgba, icon_width, icon_height) = {
        let image = image::load_from_memory(include_bytes!("icon.png"))
            .expect("Failed to open icon path")
            .into_rgba8();
        let (width, height) = image.dimensions();
        let rgba = image.into_raw();
        (rgba, width, height)
    };
    Icon::from_rgba(icon_rgba, icon_width, icon_height).expect("Failed to open icon")
}

fn main() {
    // Initialize file-based logger
    if let Err(e) = setup_logging() {
        eprintln!("Failed to initialize logging: {}", e);
    }
    info!("Starting Nojoin Companion v{}...", env!("CARGO_PKG_VERSION"));
    info!("Log file: {}", get_log_path().display());

    let event_loop = EventLoop::new();
    
    // Tray Setup
    let tray_menu = Menu::new();
    
    let status_i = MenuItem::new("Status: Ready to Record", false, None);
    let open_web_i = MenuItem::new("Open Nojoin", true, None);
    let check_updates_i = MenuItem::new("Check for Updates", true, None);
    let view_logs_i = MenuItem::new("View Logs", true, None);
    let help_i = MenuItem::new("Help", true, None);
    let about_i = MenuItem::new("About", true, None);
    let restart_i = MenuItem::new("Restart", true, None);
    let quit_i = MenuItem::new("Quit", true, None);

    tray_menu.append(&status_i).unwrap();
    tray_menu.append(&PredefinedMenuItem::separator()).unwrap();
    tray_menu.append(&open_web_i).unwrap();
    tray_menu.append(&check_updates_i).unwrap();
    tray_menu.append(&view_logs_i).unwrap();
    tray_menu.append(&help_i).unwrap();
    tray_menu.append(&about_i).unwrap();
    tray_menu.append(&PredefinedMenuItem::separator()).unwrap();
    tray_menu.append(&restart_i).unwrap();
    tray_menu.append(&quit_i).unwrap();
    
    let _tray_icon = TrayIconBuilder::new()
        .with_menu(Box::new(tray_menu))
        .with_tooltip("Nojoin Companion")
        .with_icon(load_icon())
        .build()
        .unwrap();

    // App State
    let (audio_tx, audio_rx) = crossbeam_channel::unbounded();
    let config = Config::load();
    info!("Configuration loaded. API URL: {}", config.get_api_url());

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

    // Audio Thread
    let state_audio = state.clone();
    thread::spawn(move || {
        info!("Starting audio thread...");
        audio::run_audio_loop(state_audio, audio_rx);
    });

    // Server Thread (Tokio)
    let state_server = state.clone();
    thread::spawn(move || {
        let rt = tokio::runtime::Runtime::new().unwrap();
        info!("Starting server thread...");
        
        // Persistent Health Check Loop
        let state_fetch = state_server.clone();
        rt.spawn(async move {
            let client = reqwest::Client::builder()
                .danger_accept_invalid_certs(true)
                .timeout(Duration::from_secs(5))
                .build()
                .unwrap_or_default();
            
            let mut attempt = 0;
            let max_wait = Duration::from_secs(60); // Max wait between retries
            
            info!("Starting backend health check loop...");

            loop {
                let api_url = {
                    let config = state_fetch.config.lock().unwrap();
                    config.get_api_url()
                };
                
                let status_url = format!("{}/system/status", api_url);
                
                match client.get(&status_url).send().await {
                    Ok(resp) => {
                        if let Ok(json) = resp.json::<serde_json::Value>().await {
                            // Success!
                            let was_connected = state_fetch.is_backend_connected.load(Ordering::SeqCst);
                            if !was_connected {
                                info!("Backend connected successfully!");
                            }
                            state_fetch.is_backend_connected.store(true, Ordering::SeqCst);
                            
                            // Update Web URL from config (always localhost)
                            {
                                let config = state_fetch.config.lock().unwrap();
                                let web_url = config.get_web_url();
                                let mut stored_url = state_fetch.web_url.lock().unwrap();
                                if stored_url.as_deref() != Some(&web_url) {
                                    info!("Web App URL: {}", web_url);
                                    *stored_url = Some(web_url);
                                }
                            }
                            
                            // Check if backend returned a different web_app_url (for future flexibility)
                            if let Some(url) = json.get("web_app_url").and_then(|v| v.as_str()) {
                                let mut web_url = state_fetch.web_url.lock().unwrap();
                                if web_url.as_deref() != Some(url) {
                                    info!("Web App URL updated from backend: {}", url);
                                    *web_url = Some(url.to_string());
                                }
                            }
                            
                            // Reset status if it was offline
                            {
                                let mut status = state_fetch.status.lock().unwrap();
                                if *status == AppStatus::BackendOffline {
                                    info!("Status changed to Idle (Backend Online)");
                                    *status = AppStatus::Idle;
                                }
                            }
                            
                            // Reset backoff
                            attempt = 0;
                        } else {
                            // Response parse error
                            warn!("Backend reachable but response invalid.");
                            state_fetch.is_backend_connected.store(false, Ordering::SeqCst);
                            let mut status = state_fetch.status.lock().unwrap();
                            if *status == AppStatus::Idle {
                                warn!("Status changed to BackendOffline (Invalid Response)");
                                *status = AppStatus::BackendOffline;
                            }
                        }
                    },
                    Err(e) => {
                        // Connection error
                        let was_connected = state_fetch.is_backend_connected.load(Ordering::SeqCst);
                        if was_connected {
                            warn!("Backend connection lost: {}", e);
                        } else if attempt == 0 {
                            warn!("Could not connect to backend: {}", e);
                        }

                        state_fetch.is_backend_connected.store(false, Ordering::SeqCst);
                        let mut status = state_fetch.status.lock().unwrap();
                        if *status == AppStatus::Idle {
                            warn!("Status changed to BackendOffline (Connection Error)");
                            *status = AppStatus::BackendOffline;
                        }
                    }
                }
                
                // Calculate wait time with exponential backoff
                let wait_secs = if state_fetch.is_backend_connected.load(Ordering::SeqCst) {
                    30 // Check every 30s if connected
                } else {
                    attempt += 1;
                    let backoff = 2u64.pow(attempt.min(6)); // 2, 4, 8, 16, 32, 64
                    let wait = std::cmp::min(backoff, max_wait.as_secs());
                    if !state_fetch.is_backend_connected.load(Ordering::SeqCst) {
                         info!("Retrying backend connection in {}s...", wait);
                    }
                    wait
                };
                
                tokio::time::sleep(Duration::from_secs(wait_secs)).await;
            }
        });

        // Auto-update check (runs once at startup, then daily)
        rt.spawn(async move {
            // Initial delay to let the app settle
            tokio::time::sleep(Duration::from_secs(5)).await;
            updater::check_for_updates().await;
            
            // Check daily
            loop {
                tokio::time::sleep(Duration::from_secs(24 * 60 * 60)).await;
                updater::check_for_updates().await;
            }
        });

        rt.block_on(server::start_server(state_server));
    });

    // Event Loop
    let menu_channel = MenuEvent::receiver();
    
    event_loop.run(move |_event, _, control_flow| {
        *control_flow = ControlFlow::WaitUntil(Instant::now() + Duration::from_millis(100));

        // Update Status
        if let Ok(status) = state.status.try_lock() {
             let status_text = match *status {
                 AppStatus::Idle => "Status: Ready to Record",
                 AppStatus::Recording => "Status: Recording",
                 AppStatus::Paused => "Status: Recording Paused",
                 AppStatus::Uploading => "Status: Uploading Recording",
                 AppStatus::BackendOffline => "Status: Backend Not Found...",
                 AppStatus::Error(_) => "Status: Error",
             };
             let _ = status_i.set_text(status_text);
             
             // Disable "Open Nojoin" if offline
             let is_connected = state.is_backend_connected.load(Ordering::SeqCst);
             let _ = open_web_i.set_enabled(is_connected);
        }

        if let Ok(event) = menu_channel.try_recv() {
            if event.id == quit_i.id() {
                *control_flow = ControlFlow::Exit;
            } else if event.id == open_web_i.id() {
                 let url = {
                     // Prefer dynamic URL from backend, fallback to config
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
                     notifications::show_notification("Error", "Backend URL not found. Please check connection.");
                 }
            } else if event.id == check_updates_i.id() {
                 // Trigger manual update check
                 std::thread::spawn(|| {
                     let rt = tokio::runtime::Runtime::new().unwrap();
                     rt.block_on(updater::check_for_updates_interactive());
                 });
            } else if event.id == view_logs_i.id() {
                 let log_path = get_log_path();
                 // Open the directory containing the log file
                 if let Some(log_dir) = log_path.parent() {
                     if let Err(e) = open::that(log_dir) {
                         error!("Failed to open log directory: {}", e);
                         notifications::show_notification("Error", "Failed to open log directory.");
                     }
                 } else {
                     notifications::show_notification("Error", "Could not determine log directory.");
                 }
            } else if event.id == help_i.id() {
                 let _ = open::that("https://github.com/Valtora/Nojoin"); 
            } else if event.id == about_i.id() {
                 notifications::show_notification(
                     "About Nojoin Companion", 
                     &format!("Version {}\n\nA meeting intelligence companion.", env!("CARGO_PKG_VERSION"))
                 );
            } else if event.id == restart_i.id() {
                 notifications::show_notification("Restart", "Please restart the application manually.");
            }
        }
    });
}
