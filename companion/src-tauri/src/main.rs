#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use log::{error, info, warn};
use reqwest;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tauri::{
    menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, TrayIconBuilder, TrayIconEvent},
    LogicalSize,
    Manager,
};

use semver::Version;

mod audio;
mod config;
mod notifications;
mod server;
mod state;
mod tls;
mod uploader;
mod win_notifications;

use config::{Config, MachineLocalUpdate};
use state::{
    pairing_block_message, pairing_code_fingerprint, pairing_code_log_label, AppState,
    AppStatus, RecordingRecoveryState,
    PAIRING_WINDOW_LIFETIME_SECS,
};
use tauri_plugin_autostart::ManagerExt;

// Define SharedAppState at module level so it's visible to commands
struct SharedAppState(Arc<AppState>);

const PAIRING_WINDOW_LABEL: &str = "pairing";
const SETTINGS_WINDOW_LABEL: &str = "settings";

#[derive(serde::Serialize)]
struct ConfigView {
    version: u32,
    api_protocol: String,
    api_host: String,
    api_port: u16,
    api_token: String,
    tls_fingerprint: Option<String>,
    paired_web_origin: Option<String>,
    local_port: u16,
    input_device_name: Option<String>,
    output_device_name: Option<String>,
    last_version: Option<String>,
    min_meeting_length: Option<u32>,
    run_on_startup: Option<bool>,
}

impl From<&Config> for ConfigView {
    fn from(config: &Config) -> Self {
        Self {
            version: config.version,
            api_protocol: config.api_protocol(),
            api_host: config.api_host(),
            api_port: config.api_port(),
            api_token: config.api_token(),
            tls_fingerprint: config.tls_fingerprint(),
            paired_web_origin: config.paired_web_origin(),
            local_port: config.local_port(),
            input_device_name: config.input_device_name().map(|value| value.to_string()),
            output_device_name: config.output_device_name().map(|value| value.to_string()),
            last_version: config.last_version().map(|value| value.to_string()),
            min_meeting_length: config.min_meeting_length(),
            run_on_startup: config.run_on_startup(),
        }
    }
}

#[derive(serde::Serialize)]
struct SettingsView {
    backend_label: String,
    is_paired: bool,
    is_pairing_active: bool,
}

fn derive_backend_label(config: &Config) -> String {
    if !config.is_authenticated() {
        return "Not paired".to_string();
    }

    if let Some(origin) = config.paired_web_origin() {
        return origin;
    }

    let protocol = config.api_protocol();
    let host = config.api_host();
    let port = config.api_port();
    if (protocol == "http" && port == 80) || (protocol == "https" && port == 443) {
        format!("{}://{}", protocol, host)
    } else {
        format!("{}://{}:{}", protocol, host, port)
    }
}

fn current_tray_status_text(state: &Arc<AppState>) -> String {
    if !state.is_authenticated() {
        return "Status: Waiting for connection...".to_string();
    }

    let status = state.status.lock().unwrap().clone();
    let recovery_state = state.recording_recovery_state();
    match status {
        AppStatus::Idle => "Status: Ready to Record".to_string(),
        AppStatus::Recording => match recovery_state {
            RecordingRecoveryState::WaitingForReconnect => {
                "Status: Recording while Nojoin is offline".to_string()
            }
            _ => "Status: Recording".to_string(),
        },
        AppStatus::Paused => match recovery_state {
            RecordingRecoveryState::None => "Status: Recording Paused".to_string(),
            RecordingRecoveryState::WaitingForReconnect => {
                "Status: Recording paused while Nojoin is offline".to_string()
            }
            RecordingRecoveryState::StopRequested => {
                "Status: Upload queued until Nojoin reconnects".to_string()
            }
        },
        AppStatus::Uploading => match recovery_state {
            RecordingRecoveryState::StopRequested => {
                "Status: Upload queued until Nojoin reconnects".to_string()
            }
            _ => "Status: Uploading Recording".to_string(),
        },
        AppStatus::BackendOffline => "Status: Nojoin Offline".to_string(),
        AppStatus::Error(_) => "Status: Error".to_string(),
    }
}

fn update_tray_status_ui(state: &Arc<AppState>) {
    let status_text = current_tray_status_text(state);

    if let Some(item) = state.tray_status_item.lock().unwrap().as_ref() {
        let _ = item.set_text(&status_text);
    }

    if let Some(tray) = state.tray_icon.lock().unwrap().as_ref() {
        let _ = tray.set_tooltip(Some(&status_text));
    }
}

fn build_tray_menu(
    app: &tauri::AppHandle,
    state: &Arc<AppState>,
    is_autostart_enabled: bool,
) -> tauri::Result<Menu<tauri::Wry>> {
    let status = state.status.lock().unwrap().clone();
    let recovery_state = state.recording_recovery_state();
    let has_active_recording = state.current_recording_id.lock().unwrap().is_some();
    let can_pause_recording = has_active_recording
        && matches!(status, AppStatus::Recording)
        && recovery_state == RecordingRecoveryState::None;
    let can_resume_recording = has_active_recording
        && matches!(status, AppStatus::Paused)
        && recovery_state == RecordingRecoveryState::None
        && state.is_backend_connected.load(Ordering::SeqCst);
    let stop_recording_label = match recovery_state {
        RecordingRecoveryState::StopRequested => "Upload Queued Until Reconnect",
        RecordingRecoveryState::WaitingForReconnect => "Stop Recording and Queue Upload",
        RecordingRecoveryState::None => "Stop Recording",
    };
    let can_stop_recording = has_active_recording
        && matches!(status, AppStatus::Recording | AppStatus::Paused)
        && recovery_state != RecordingRecoveryState::StopRequested;

    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let about = MenuItem::with_id(app, "about", "About", true, None::<&str>)?;
    let view_logs = MenuItem::with_id(app, "view_logs", "View Logs", true, None::<&str>)?;
    let check_updates =
        MenuItem::with_id(app, "check_updates", "Check for Updates", true, None::<&str>)?;
    let run_on_startup = CheckMenuItem::with_id(
        app,
        "run_on_startup",
        "Run on Startup",
        true,
        is_autostart_enabled,
        None::<&str>,
    )?;
    let settings = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
    let status_item = MenuItem::with_id(
        app,
        "status",
        &current_tray_status_text(state),
        true,
        None::<&str>,
    )?;
    let pause_recording = MenuItem::with_id(
        app,
        "pause_recording",
        "Pause Recording",
        can_pause_recording,
        None::<&str>,
    )?;
    let resume_recording = MenuItem::with_id(
        app,
        "resume_recording",
        "Resume Recording",
        can_resume_recording,
        None::<&str>,
    )?;
    let stop_recording = MenuItem::with_id(
        app,
        "stop_recording",
        stop_recording_label,
        can_stop_recording,
        None::<&str>,
    )?;

    *state.tray_status_item.lock().unwrap() = Some(status_item.clone());
    *state.tray_run_on_startup_item.lock().unwrap() = Some(run_on_startup.clone());

    let separator_one = PredefinedMenuItem::separator(app)?;
    let separator_two = PredefinedMenuItem::separator(app)?;
    let separator_three = PredefinedMenuItem::separator(app)?;

    if has_active_recording && matches!(status, AppStatus::Recording | AppStatus::Paused) {
        Menu::with_items(
            app,
            &[
                &status_item,
                &separator_one,
                &pause_recording,
                &resume_recording,
                &stop_recording,
                &separator_three,
                &settings,
                &run_on_startup,
                &check_updates,
                &view_logs,
                &about,
                &separator_two,
                &quit,
            ],
        )
    } else {
        Menu::with_items(
            app,
            &[
                &status_item,
                &separator_one,
                &settings,
                &run_on_startup,
                &check_updates,
                &view_logs,
                &about,
                &separator_two,
                &quit,
            ],
        )
    }
}

pub fn refresh_tray_menu(app: &tauri::AppHandle, state: &Arc<AppState>) {
    let is_autostart_enabled = app.autolaunch().is_enabled().unwrap_or(false);
    match build_tray_menu(app, state, is_autostart_enabled) {
        Ok(menu) => {
            if let Some(tray) = state.tray_icon.lock().unwrap().as_ref() {
                if let Err(err) = tray.set_menu(Some(menu)) {
                    error!("Failed to refresh tray menu: {}", err);
                }
            }
            update_tray_status_ui(state);
        }
        Err(err) => error!("Failed to build tray menu: {}", err),
    }
}

fn handle_backend_disconnect(app: &tauri::AppHandle, state: &Arc<AppState>) {
    let status = state.status.lock().unwrap().clone();
    match status {
        AppStatus::Recording | AppStatus::Paused | AppStatus::Uploading => {
            let previous_recovery_state = state.recording_recovery_state();
            match server::mark_recording_waiting_for_reconnect(state) {
                Ok(changed) => {
                    if changed && previous_recovery_state == RecordingRecoveryState::None {
                        let message = match status {
                            AppStatus::Recording => "Connection to Nojoin was lost. Recording will continue locally. Use the tray menu to stop it if you want to queue the upload until reconnect.",
                            AppStatus::Paused => "Connection to Nojoin was lost while the recording was paused. Resume stays blocked until reconnect, but you can still stop it from the tray to queue the upload.",
                            AppStatus::Uploading => "Connection to Nojoin was lost while audio was uploading. The upload will stay queued until reconnect.",
                            _ => unreachable!(),
                        };
                        notifications::show_notification(app, "Nojoin Connection Lost", message);
                    }
                }
                Err(message) => error!("Failed to mark recording recovery state: {}", message),
            }
        }
        AppStatus::Idle => {
            let mut status = state.status.lock().unwrap();
            *status = AppStatus::BackendOffline;
        }
        _ => {}
    }

    refresh_tray_menu(app, state);
}

fn handle_backend_reconnect(app: &tauri::AppHandle, state: &Arc<AppState>) {
    let status = state.status.lock().unwrap().clone();
    let previous_recovery_state = server::restore_recording_after_reconnect(state);
    match previous_recovery_state {
        RecordingRecoveryState::WaitingForReconnect => {
            let message = match status {
                AppStatus::Recording => "Connection restored. The active recording continued locally and can now be stopped normally from Nojoin or the tray menu.",
                AppStatus::Paused => "Connection restored. Recording is still paused. Resume or stop it from Nojoin or the tray menu.",
                _ => "Connection restored.",
            };
            notifications::show_notification(app, "Nojoin Reconnected", message);
        }
        RecordingRecoveryState::StopRequested => {
            notifications::show_notification(
                app,
                "Nojoin Reconnected",
                "Connection restored. Queued uploads will resume automatically.",
            );
        }
        RecordingRecoveryState::None => {
            let mut status = state.status.lock().unwrap();
            if *status == AppStatus::BackendOffline {
                *status = AppStatus::Idle;
            }
        }
    }

    refresh_tray_menu(app, state);
}

fn pause_recording_from_tray(app: &tauri::AppHandle, state: &Arc<AppState>) -> Result<(), String> {
    let status_update = server::pause_recording_locally(state)?;
    server::spawn_recording_status_update(status_update);
    notifications::show_notification(app, "Recording Paused", "Recording paused from the Companion tray.");
    refresh_tray_menu(app, state);
    Ok(())
}

fn resume_recording_from_tray(app: &tauri::AppHandle, state: &Arc<AppState>) -> Result<(), String> {
    if !state.is_backend_connected.load(Ordering::SeqCst) {
        return Err("Nojoin is still offline. Wait for the connection to recover before resuming.".to_string());
    }

    let status_update = server::resume_recording_locally(state)?;
    server::spawn_recording_status_update(status_update);
    notifications::show_notification(app, "Recording Resumed", "Recording resumed from the Companion tray.");
    refresh_tray_menu(app, state);
    Ok(())
}

fn stop_recording_from_tray(app: &tauri::AppHandle, state: &Arc<AppState>) -> Result<(), String> {
    let is_connected = state.is_backend_connected.load(Ordering::SeqCst);
    let recovery_state = state.recording_recovery_state();

    if is_connected && recovery_state == RecordingRecoveryState::None {
        let status_update = server::stop_recording_locally(state, false)?;
        server::spawn_recording_status_update(status_update);
        notifications::show_notification(app, "Recording Stopped", "Processing audio...");
    } else {
        let _status_update = server::stop_recording_locally(state, true)?;
        notifications::show_notification(
            app,
            "Recording Stopped",
            "Recording stopped locally. Audio is saved and queued for upload when Nojoin reconnects.",
        );
    }

    refresh_tray_menu(app, state);
    Ok(())
}

fn open_settings_window(app: &tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(SETTINGS_WINDOW_LABEL) {
        let _ = window.show();
        let _ = window.set_focus();
        return Ok(());
    }

    tauri::WebviewWindowBuilder::new(
        app,
        SETTINGS_WINDOW_LABEL,
        tauri::WebviewUrl::App("settings.html".into()),
    )
    .title("Settings")
    .inner_size(540.0, 420.0)
    .min_inner_size(380.0, 280.0)
    .resizable(false)
    .center()
    .build()
    .map(|_| ())
    .map_err(|err| format!("Failed to open settings window: {}", err))
}

#[tauri::command]
fn get_config(state: tauri::State<SharedAppState>) -> ConfigView {
    let config = state.0.config.lock().unwrap();
    ConfigView::from(&*config)
}

#[tauri::command]
fn get_settings_state(state: tauri::State<SharedAppState>) -> SettingsView {
    let backend_label = {
        let config = state.0.config.lock().unwrap();
        derive_backend_label(&config)
    };

    SettingsView {
        backend_label,
        is_paired: state.0.is_authenticated(),
        is_pairing_active: state.0.is_pairing_active(),
    }
}

#[tauri::command]
async fn start_pairing_mode(
    app: tauri::AppHandle,
    state: tauri::State<'_, SharedAppState>,
) -> Result<String, String> {
    let (tx, rx) = tokio::sync::oneshot::channel();
    let app_handle = app.clone();
    let shared_state = state.0.clone();

    app.run_on_main_thread(move || {
        let result = start_pairing_mode_internal(&app_handle, &shared_state);
        let _ = tx.send(result);
    })
    .map_err(|err| format!("Failed to schedule pairing window creation: {}", err))?;

    rx.await
        .map_err(|_| "Failed to receive pairing window result.".to_string())?
}

#[tauri::command]
async fn cancel_pairing_request(
    app: tauri::AppHandle,
    state: tauri::State<'_, SharedAppState>,
) -> Result<String, String> {
    let had_local_pairing = state.0.is_pairing_active();
    state.0.clear_pairing_session();
    close_pairing_window(&app);

    let backend = {
        let config = state.0.config.lock().unwrap();
        config.backend_connection()
    };
    if !had_local_pairing {
        return Ok("No local pairing session is currently active.".to_string());
    }

    let backend_cleanup = match backend {
        Some(backend) => match server::cancel_pending_pairing_for_backend(&backend).await {
            Ok(0) => None,
            Ok(count) => Some(format!(
                "Cleared {} pending backend pairing request{} for the current backend.",
                count,
                if count == 1 { "" } else { "s" }
            )),
            Err(err) => Some(format!(
                "The local pairing session was cancelled, but backend cleanup could not be confirmed: {}.",
                err
            )),
        },
        None => None,
    };

    Ok(match backend_cleanup {
        Some(message) => format!("Cancelled the active local pairing session. {}", message),
        None => "Cancelled the active local pairing session.".to_string(),
    })
}

#[tauri::command]
async fn disconnect_backend(
    app: tauri::AppHandle,
    state: tauri::State<'_, SharedAppState>,
) -> Result<String, String> {
    state.0.clear_pairing_session();
    close_pairing_window(&app);

    let backend = {
        let mut config = state.0.config.lock().unwrap();
        let backend = config.backend_connection();
        if backend.is_some() {
            config
                .clear_backend_and_save()
                .map_err(|err| format!("Failed to save settings: {}", err))?;
        }
        backend
    };

    refresh_tray_menu(&app, &state.0);

    let Some(backend) = backend else {
        return Ok("No backend is currently paired.".to_string());
    };

    let (response_message, notification_body) = match server::signal_explicit_backend_disconnect(&backend).await {
        Ok(0) => (
            "Disconnected from the current backend. Start pairing again from Companion Settings when you are ready to connect to another Nojoin deployment.".to_string(),
            "This Companion is no longer paired with a Nojoin backend. Start pairing again from Settings when you are ready.".to_string(),
        ),
        Ok(count) => (
            format!(
                "Disconnected from the current backend and revoked {} backend pairing{}. Start pairing again from Companion Settings when you are ready.",
                count,
                if count == 1 { "" } else { "s" }
            ),
            "This Companion is no longer paired with a Nojoin backend. Start pairing again from Settings when you are ready.".to_string(),
        ),
        Err(err) => (
            format!(
                "Disconnected locally from the current backend, but backend cleanup could not be confirmed: {}.",
                err
            ),
            "This Companion is no longer paired locally. Backend cleanup could not be confirmed, so verify the old backend before reconnecting elsewhere.".to_string(),
        ),
    };

    notifications::show_notification(&app, "Companion Unpaired", &notification_body);
    Ok(response_message)
}

#[tauri::command]
fn resize_current_window(
    window: tauri::WebviewWindow,
    width: f64,
    height: f64,
) -> Result<(), String> {
    let clamped_width = width.clamp(360.0, 720.0);
    let clamped_height = height.clamp(220.0, 760.0);

    window
        .set_size(LogicalSize::new(clamped_width, clamped_height))
        .map_err(|err| format!("Failed to resize window: {}", err))
}

fn close_pairing_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window(PAIRING_WINDOW_LABEL) {
        let _ = window.close();
    }
}

fn start_pairing_mode_internal(
    app: &tauri::AppHandle,
    state: &Arc<AppState>,
) -> Result<String, String> {
    {
        let status = state.status.lock().unwrap().clone();
        if let Some(message) = pairing_block_message(&status) {
            return Err(message.to_string());
        }
    }

    state.clear_pairing_session();
    close_pairing_window(app);

    let was_previously_paired = state.is_authenticated();
    let session = state.begin_pairing_session();
    info!(
        "Started pairing mode: code={} code_hash={} expires_in={}s previously_paired={}",
        pairing_code_log_label(&session.canonical_code),
        pairing_code_fingerprint(&session.canonical_code),
        session.remaining_seconds(),
        was_previously_paired
    );
    let window_url = format!(
        "pairing.html?code={}&expires_in={}",
        session.display_code,
        session.remaining_seconds()
    );

    let window = tauri::WebviewWindowBuilder::new(
        app,
        PAIRING_WINDOW_LABEL,
        tauri::WebviewUrl::App(window_url.into()),
    )
    .title("Pair Nojoin Companion")
    .inner_size(480.0, 340.0)
    .min_inner_size(360.0, 240.0)
    .always_on_top(true)
    .resizable(false)
    .center()
    .build()
    .map_err(|e| {
        state.clear_pairing_session();
        format!("Failed to open pairing window: {}", e)
    })?;

    let state_on_close = state.clone();
    let code_on_close = session.canonical_code.clone();
    window.on_window_event(move |event| {
        if let tauri::WindowEvent::CloseRequested { .. } = event {
            let should_clear_active_session = state_on_close
                .current_pairing_session()
                .map(|active| active.canonical_code == code_on_close)
                .unwrap_or(false);

            if should_clear_active_session {
                info!(
                    "Pairing window closed before completion: code={} code_hash={}",
                    pairing_code_log_label(&code_on_close),
                    pairing_code_fingerprint(&code_on_close)
                );
                state_on_close.clear_pairing_session();
            }
        }
    });

    let expected_code = session.canonical_code.clone();
    let state_on_expiry = state.clone();
    let app_on_expiry = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_secs(PAIRING_WINDOW_LIFETIME_SECS)).await;

        let should_expire = state_on_expiry
            .current_pairing_session()
            .map(|active| active.canonical_code == expected_code)
            .unwrap_or(false);

        if should_expire {
            warn!(
                "Pairing session expired before completion: code={} code_hash={} previously_paired={}",
                pairing_code_log_label(&expected_code),
                pairing_code_fingerprint(&expected_code),
                was_previously_paired
            );
            state_on_expiry.clear_pairing_session();
            close_pairing_window(&app_on_expiry);
            notifications::show_notification(
                &app_on_expiry,
                "Pairing Expired",
                if was_previously_paired {
                    "The replacement pairing code expired. The current backend stays connected. Start re-pairing again if you still want to switch backends."
                } else {
                    "The pairing code expired. Start pairing again from Companion Settings if needed."
                },
            );
        }
    });

    Ok(session.display_code)
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

    let target_url = {
        let config = state.config.lock().unwrap();
        config.get_web_url()
    };

    if !target_url.is_empty() {
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
        .invoke_handler(tauri::generate_handler![
            get_config,
            get_settings_state,
            start_pairing_mode,
            cancel_pairing_request,
            disconnect_backend,
            resize_current_window,
            close_update_prompt
        ])
        .setup(|app| {
            let (audio_tx, audio_rx) = crossbeam_channel::unbounded();
            let mut config = Config::load();

            let autostart_manager = app.autolaunch();
            let mut is_enabled = autostart_manager.is_enabled().unwrap_or(false);
            if let Some(should_run) = config.run_on_startup() {
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
                let _ = config.update_machine_local_and_save(MachineLocalUpdate {
                    run_on_startup: Some(Some(is_enabled)),
                    ..Default::default()
                });
            }

            let state = Arc::new(AppState {
                status: Mutex::new(AppStatus::Idle),
                current_recording_id: Mutex::new(None),
                current_recording_token: Mutex::new(None),
                current_recording_owner: Mutex::new(None),
                recording_recovery_state: Mutex::new(RecordingRecoveryState::None),
                current_sequence: Mutex::new(1),
                audio_command_tx: audio_tx.clone(),
                config: Mutex::new(config),
                recording_start_time: Mutex::new(None),
                accumulated_duration: Mutex::new(Duration::new(0, 0)),
                input_level: AtomicU32::new(0),
                output_level: AtomicU32::new(0),
                live_input_level: AtomicU32::new(0),
                live_output_level: AtomicU32::new(0),
                is_backend_connected: AtomicBool::new(false),
                update_available: AtomicBool::new(false),
                latest_version: Mutex::new(None),
                latest_update_url: Mutex::new(None),
                tray_status_item: Mutex::new(None),
                tray_run_on_startup_item: Mutex::new(None),
                tray_icon: Mutex::new(None),
                pairing_session: Mutex::new(None),
            });

            app.manage(SharedAppState(state.clone()));

            let app_handle = app.handle();
            let menu = build_tray_menu(&app_handle, &state, is_enabled)?;

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
                            if let Err(message) = open_settings_window(app) {
                                notifications::show_notification(app, "Settings Error", &message);
                            }
                        }
                        "pause_recording" => {
                            let state_wrapper = app.state::<SharedAppState>();
                            let state = state_wrapper.0.clone();
                            if let Err(message) = pause_recording_from_tray(app, &state) {
                                notifications::show_notification(app, "Recording Pause Error", &message);
                                refresh_tray_menu(app, &state);
                            }
                        }
                        "resume_recording" => {
                            let state_wrapper = app.state::<SharedAppState>();
                            let state = state_wrapper.0.clone();
                            if let Err(message) = resume_recording_from_tray(app, &state) {
                                notifications::show_notification(app, "Recording Resume Error", &message);
                                refresh_tray_menu(app, &state);
                            }
                        }
                        "stop_recording" => {
                            let state_wrapper = app.state::<SharedAppState>();
                            let state = state_wrapper.0.clone();
                            if let Err(message) = stop_recording_from_tray(app, &state) {
                                notifications::show_notification(app, "Recording Stop Error", &message);
                                refresh_tray_menu(app, &state);
                            }
                        }
                        "about" => {
                             notifications::show_notification(
                                 app,
                                 "About Nojoin Companion",
                                 &format!("This is the Nojoin Companion App that let's Nojoin listen in on your meetings.\n\nVersion {}", app.package_info().version)
                             );
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
                                    let _ = config.update_machine_local_and_save(MachineLocalUpdate {
                                        run_on_startup: Some(Some(false)),
                                        ..Default::default()
                                    });
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
                                    let _ = config.update_machine_local_and_save(MachineLocalUpdate {
                                        run_on_startup: Some(Some(true)),
                                        ..Default::default()
                                    });
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

                if let Some(last_ver) = config.last_version() {
                    if last_ver != current_version {
                        notifications::show_notification(app.handle(), "Updated", &format!("Nojoin Companion App Updated v{}", current_version));
                    }
                }

                if config.last_version() != Some(current_version.as_str()) {
                    let _ = config.update_machine_local_and_save(MachineLocalUpdate {
                        last_version: Some(Some(current_version)),
                        ..Default::default()
                    });
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
                let app_handle_status = app_handle.clone();
                rt.spawn(async move {
                    let fingerprint = {
                        let config = state_fetch.config.lock().unwrap();
                        config.tls_fingerprint()
                    };

                    let mut client = crate::tls::create_client_builder(fingerprint.clone())
                        .timeout(Duration::from_secs(5))
                        .build()
                        .unwrap_or_default();

                    let mut current_fingerprint = fingerprint;

                    loop {
                        // 1. Perform Health Check
                        let (status_origin, fingerprint) = {
                            let config = state_fetch.config.lock().unwrap();
                            (config.get_web_url(), config.tls_fingerprint())
                        };

                        // Recreate client if fingerprint changed
                        if current_fingerprint != fingerprint {
                            client = crate::tls::create_client_builder(fingerprint.clone())
                                .timeout(Duration::from_secs(5))
                                .build()
                                .unwrap_or_default();
                            current_fingerprint = fingerprint;
                        }

                        let status_url = format!("{}/api/health", status_origin);

                        match client.get(&status_url).send().await {
                            Ok(resp) => {
                                if resp.status().is_success() {
                                    state_fetch.is_backend_connected.store(true, Ordering::SeqCst);
                                    handle_backend_reconnect(&app_handle_status, &state_fetch);
                                } else {
                                    state_fetch.is_backend_connected.store(false, Ordering::SeqCst);
                                    handle_backend_disconnect(&app_handle_status, &state_fetch);
                                }
                            }
                            Err(_) => {
                                state_fetch.is_backend_connected.store(false, Ordering::SeqCst);
                                handle_backend_disconnect(&app_handle_status, &state_fetch);
                            }
                        }

                        update_tray_status_ui(&state_fetch);

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
