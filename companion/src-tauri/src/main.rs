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
    LogicalSize, Manager,
};

use semver::Version;
#[cfg(windows)]
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

mod audio;
mod companion_auth;
mod config;
mod local_https_identity;
mod notifications;
mod secret_store;
mod server;
mod state;
mod tls;
mod uploader;
mod win_notifications;

use config::{Config, MachineLocalUpdate};
use state::{
    pairing_block_message, pairing_code_fingerprint, pairing_code_log_label, AppState, AppStatus,
    LocalHttpsHealth, LocalHttpsStatus, RecordingRecoveryState,
    PAIRING_WINDOW_LIFETIME_SECS,
};
use tauri_plugin_autostart::ManagerExt;

// Define SharedAppState at module level so it's visible to commands
struct SharedAppState(Arc<AppState>);

struct SharedLocalHttpsController(LocalHttpsControllerHandle);

#[derive(Clone)]
struct LocalHttpsControllerHandle {
    command_tx: tokio::sync::mpsc::UnboundedSender<LocalHttpsControllerCommand>,
}

enum LocalHttpsControllerCommand {
    Repair {
        #[cfg(windows)]
        allow_identity_reset: bool,
        response_tx: tokio::sync::oneshot::Sender<Result<String, String>>,
    },
    #[cfg(windows)]
    ServerStopped {
        generation: u64,
        result: Result<(), String>,
    },
}

impl LocalHttpsControllerHandle {
    async fn repair_local_https(&self, allow_identity_reset: bool) -> Result<String, String> {
        let (response_tx, response_rx) = tokio::sync::oneshot::channel();
        #[cfg(not(windows))]
        let _ = allow_identity_reset;
        self.command_tx
            .send(LocalHttpsControllerCommand::Repair {
                #[cfg(windows)]
                allow_identity_reset,
                response_tx,
            })
            .map_err(|_| "Local HTTPS repair is unavailable because the controller is offline.".to_string())?;

        response_rx
            .await
            .map_err(|_| "Local HTTPS repair did not return a result.".to_string())?
    }
}

const PAIRING_WINDOW_LABEL: &str = "pairing";
const SETTINGS_WINDOW_LABEL: &str = "settings";
const LOCAL_HTTPS_UNINSTALL_CLEANUP_ARG: &str = "--cleanup-local-https-on-uninstall";

#[derive(serde::Serialize)]
struct ConfigView {
    version: u32,
    api_protocol: String,
    api_host: String,
    api_port: u16,
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
    #[serde(rename = "localHttpsStatus")]
    local_https_status: LocalHttpsStatus,
    local_https_message: String,
    local_https_listener_running: bool,
    local_https_current_user_trust_installed: Option<bool>,
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
    let status = state.status.lock().unwrap().clone();
    if let AppStatus::Error(message) = &status {
        let trimmed = message.trim();
        return if trimmed.is_empty() {
            "Status: Error".to_string()
        } else {
            format!("Status: {}", trimmed)
        };
    }

    let local_https_health = state.local_https_health();
    if !matches!(status, AppStatus::Recording | AppStatus::Paused | AppStatus::Uploading) {
        match local_https_health.status {
            LocalHttpsStatus::Repairing => {
                return "Status: Repairing local HTTPS".to_string();
            }
            LocalHttpsStatus::NeedsRepair => {
                return "Status: Local HTTPS needs repair".to_string();
            }
            LocalHttpsStatus::Ready => {}
        }
    }

    if !state.is_authenticated() {
        return "Status: Waiting for connection...".to_string();
    }

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
    let check_updates = MenuItem::with_id(
        app,
        "check_updates",
        "Check for Updates",
        true,
        None::<&str>,
    )?;
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
    notifications::show_notification(
        app,
        "Recording Paused",
        "Recording paused from the Companion tray.",
    );
    refresh_tray_menu(app, state);
    Ok(())
}

fn resume_recording_from_tray(app: &tauri::AppHandle, state: &Arc<AppState>) -> Result<(), String> {
    if !state.is_backend_connected.load(Ordering::SeqCst) {
        return Err(
            "Nojoin is still offline. Wait for the connection to recover before resuming."
                .to_string(),
        );
    }

    let status_update = server::resume_recording_locally(state)?;
    server::spawn_recording_status_update(status_update);
    notifications::show_notification(
        app,
        "Recording Resumed",
        "Recording resumed from the Companion tray.",
    );
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
    let local_https_health = state.0.local_https_health();

    SettingsView {
        backend_label,
        is_paired: state.0.is_authenticated(),
        is_pairing_active: state.0.is_pairing_active(),
        local_https_status: local_https_health.status,
        local_https_message: local_https_health.detail_message,
        local_https_listener_running: local_https_health.listener_running,
        local_https_current_user_trust_installed: local_https_health
            .current_user_trust_installed,
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
        Some(backend) => match companion_auth::cancel_pending_pairing_for_backend(&backend).await {
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
    state.0.is_backend_connected.store(false, Ordering::SeqCst);

    let (backend, secret_bundle, secret_cleanup_error) = {
        let mut config = state.0.config.lock().unwrap();
        let backend = config.backend_connection();
        let secret_bundle = backend
            .as_ref()
            .and_then(|current| secret_store::load_backend_secret_bundle_for_backend(current).ok());
        if backend.is_some() {
            config
                .clear_backend_and_save()
                .map_err(|err| format!("Failed to save settings: {}", err))?;
        }
        let secret_cleanup_error = backend.as_ref().and_then(|current| {
            secret_store::delete_backend_secret_bundle_for_backend(current).err()
        });
        (backend, secret_bundle, secret_cleanup_error)
    };

    if let Some(error) = secret_cleanup_error.as_ref() {
        warn!(
            "Failed to delete the local companion secret bundle during disconnect: {}",
            error
        );
    }

    refresh_tray_menu(&app, &state.0);

    let Some(backend) = backend else {
        return Ok("No backend is currently paired.".to_string());
    };

    let (response_message, notification_body) = match secret_bundle {
        Some(bundle) => match companion_auth::signal_explicit_backend_disconnect_with_bundle(
            &backend,
            &bundle,
        )
        .await
        {
            Ok(0) => (
                "Disconnected from the current backend and cleared the saved trust state. Start pairing again from Companion Settings when you are ready to connect to another Nojoin deployment.".to_string(),
                "This Companion is no longer paired with a Nojoin backend, and the saved certificate trust has been cleared. Start pairing again from Settings when you are ready.".to_string(),
            ),
            Ok(count) => (
                format!(
                    "Disconnected from the current backend, cleared the saved trust state, and revoked {} backend pairing{}. Start pairing again from Companion Settings when you are ready.",
                    count,
                    if count == 1 { "" } else { "s" }
                ),
                "This Companion is no longer paired with a Nojoin backend, and the saved certificate trust has been cleared. Start pairing again from Settings when you are ready.".to_string(),
            ),
            Err(err) => (
                format!(
                    "Disconnected locally from the current backend and cleared the saved trust state, but backend cleanup could not be confirmed: {}.",
                    err
                ),
                "This Companion is no longer paired locally and the saved certificate trust has been cleared. Backend cleanup could not be confirmed, so verify the old backend before reconnecting elsewhere.".to_string(),
            ),
        },
        None => (
            "Disconnected locally from the current backend and cleared the saved trust state, but backend cleanup could not be confirmed because the stored companion credential was unavailable.".to_string(),
            "This Companion is no longer paired locally and the saved certificate trust has been cleared. Backend cleanup could not be confirmed, so verify the old backend before reconnecting elsewhere.".to_string(),
        ),
    };

    notifications::show_notification(&app, "Companion Unpaired", &notification_body);
    Ok(response_message)
}

#[tauri::command]
async fn enable_firefox_support(app: tauri::AppHandle) -> Result<String, String> {
    #[cfg(not(windows))]
    {
        let _ = app;
        warn!("Firefox support setup was requested on a non-Windows platform.");
        Err("Firefox support setup is only available on Windows.".to_string())
    }

    #[cfg(windows)]
    {
        info!("Firefox support setup requested from Companion Settings.");
        if !confirm_firefox_machine_root_install(&app) {
            warn!("Firefox support setup was canceled in the Companion confirmation dialog.");
            return Err("Firefox support setup was canceled.".to_string());
        }

        info!("Firefox support setup confirmed; launching elevated installer task.");
        match tauri::async_runtime::spawn_blocking(
            local_https_identity::install_firefox_machine_root_support,
        )
        .await
        {
            Ok(Ok(())) => {
                info!("Firefox support setup completed successfully.");
            }
            Ok(Err(error)) => {
                error!("Firefox support setup failed: {}", error);
                return Err(error);
            }
            Err(error) => {
                error!(
                    "Firefox support setup task failed before completion: {}",
                    error
                );
                return Err(format!("Firefox support setup task failed: {}", error));
            }
        }

        notifications::show_notification(
            &app,
            "Firefox Support Enabled",
            "Restart Firefox, then pair again from Nojoin using a fresh Companion code.",
        );
        Ok(
            "Firefox support was enabled for this Windows device. Restart Firefox, then generate a fresh pairing code and try pairing again."
                .to_string(),
        )
    }
}

#[tauri::command]
async fn repair_local_https(
    app: tauri::AppHandle,
    state: tauri::State<'_, SharedAppState>,
    controller: tauri::State<'_, SharedLocalHttpsController>,
) -> Result<String, String> {
    let allow_identity_reset = {
        let local_https_health = state.0.local_https_health();
        matches!(
            local_https_health.repair_reason,
            Some(local_https_identity::LocalHttpsRepairReason::InvalidCaMaterial)
                | Some(local_https_identity::LocalHttpsRepairReason::UnsupportedSchema)
        )
    };

    if allow_identity_reset && !confirm_local_https_identity_replace(&app) {
        return Err("Local HTTPS repair was canceled before replacing the current identity.".to_string());
    }

    controller.0.repair_local_https(allow_identity_reset).await
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
        if let AppStatus::Error(message) = &status {
            return Err(message.clone());
        }
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

fn reconcile_backend_secret_state(config: &mut Config) {
    let Some(backend) = config.backend_connection() else {
        return;
    };

    if !backend.has_complete_pairing_state() {
        return;
    }

    if let Err(error) = secret_store::load_backend_secret_bundle_for_backend(&backend) {
        warn!(
            "Clearing paired backend because the local companion secret bundle could not be loaded: {}",
            error
        );
        if let Err(clear_error) = config.clear_backend_and_save() {
            error!(
                "Failed to clear paired backend after companion secret bundle load failure: {}",
                clear_error
            );
        }
        if let Err(delete_error) = secret_store::delete_backend_secret_bundle_for_backend(&backend)
        {
            warn!(
                "Failed to delete stale companion secret bundle after clearing the paired backend: {}",
                delete_error
            );
        }
    }
}

#[cfg(windows)]
struct PromptingLocalCaTrustStore {
    app: tauri::AppHandle,
    inner: local_https_identity::SystemLocalCaTrustStore,
}

#[cfg(windows)]
impl PromptingLocalCaTrustStore {
    fn new(app: tauri::AppHandle) -> Self {
        Self {
            app,
            inner: local_https_identity::SystemLocalCaTrustStore,
        }
    }
}

#[cfg(windows)]
impl local_https_identity::LocalCaTrustStore for PromptingLocalCaTrustStore {
    fn is_ca_trusted(&self, ca_certificate_der: &[u8]) -> Result<bool, String> {
        self.inner.is_ca_trusted(ca_certificate_der)
    }

    fn install_ca(&self, ca_certificate_der: &[u8]) -> Result<(), String> {
        if !confirm_local_https_trust_install(&self.app) {
            return Err(
                "The local HTTPS trust installation was canceled before the Windows confirmation dialog."
                    .to_string(),
            );
        }

        self.inner.install_ca(ca_certificate_der)
    }

    fn install_crl(&self, crl_der: &[u8]) -> Result<(), String> {
        self.inner.install_crl(crl_der)
    }

    fn remove_ca(&self, ca_certificate_der: &[u8]) -> Result<bool, String> {
        self.inner.remove_ca(ca_certificate_der)
    }

    fn remove_crl(&self, crl_der: &[u8]) -> Result<bool, String> {
        self.inner.remove_crl(crl_der)
    }
}

#[cfg(windows)]
fn confirm_local_https_trust_install(app: &tauri::AppHandle) -> bool {
    app.dialog()
        .message(
            "Nojoin Companion needs to add a local Windows certificate so your browser can securely connect to the Companion on this device.\n\nThis applies only to secure local Companion communication, not general internet traffic. Windows will show its own confirmation dialog next. Click Continue here, then click Yes in the Windows dialog.",
        )
        .title("Approve Secure Local Connection")
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Continue".to_string(),
            "Cancel".to_string(),
        ))
        .blocking_show()
}

#[cfg(windows)]
fn confirm_firefox_machine_root_install(app: &tauri::AppHandle) -> bool {
    app.dialog()
        .message(
            "Firefox can only use Nojoin's local HTTPS certificate after you explicitly enable Firefox support.\n\nThis will install the Nojoin local HTTPS CA into the Windows Local Machine trusted root store so Firefox can import it when Windows root trust is enabled. Windows will show an administrator approval prompt next. Continue only if you want Firefox on this device to trust the Nojoin Companion local connection.",
        )
        .title("Enable Firefox Support")
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Continue".to_string(),
            "Cancel".to_string(),
        ))
        .blocking_show()
}

#[cfg(windows)]
fn confirm_local_https_identity_replace(app: &tauri::AppHandle) -> bool {
    app.dialog()
        .message(
            "Nojoin Companion needs to replace its current secure local identity before browser control can resume.\n\nThis will generate a new local HTTPS certificate authority for this Windows user profile and ask Windows to trust it again. Continue only if you want to rebuild the Companion's secure local browser connection on this device.",
        )
        .title("Replace Secure Local Identity")
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Continue".to_string(),
            "Cancel".to_string(),
        ))
        .blocking_show()
}

#[cfg(not(windows))]
fn confirm_local_https_identity_replace(_app: &tauri::AppHandle) -> bool {
    true
}

#[cfg(windows)]
struct LocalHttpsReadyState {
    server_identity: local_https_identity::LocalHttpsServerIdentity,
    changes: local_https_identity::LocalHttpsReconcileChanges,
}

#[cfg(windows)]
enum LocalHttpsReconcileOutcome {
    Ready(LocalHttpsReadyState),
    NeedsRepair {
        reason: Option<local_https_identity::LocalHttpsRepairReason>,
        message: String,
    },
}

#[cfg(windows)]
fn set_local_https_health_and_refresh(
    app: &tauri::AppHandle,
    state: &Arc<AppState>,
    health: LocalHttpsHealth,
) {
    state.set_local_https_health(health);
    refresh_tray_menu(app, state);
}

#[cfg(not(windows))]
fn set_local_https_health_and_refresh(
    app: &tauri::AppHandle,
    state: &Arc<AppState>,
    health: LocalHttpsHealth,
) {
    state.set_local_https_health(health);
    refresh_tray_menu(app, state);
}

#[cfg(windows)]
fn local_https_repair_message(
    reason: Option<local_https_identity::LocalHttpsRepairReason>,
) -> String {
    match reason {
        Some(local_https_identity::LocalHttpsRepairReason::TrustStoreFailure) => {
            "Companion could not restore Windows trust for its secure local connection. Use Repair Local HTTPS to try again."
                .to_string()
        }
        Some(local_https_identity::LocalHttpsRepairReason::InvalidCaMaterial) => {
            "Companion's secure local connection identity needs to be rebuilt. Use Repair Local HTTPS to continue."
                .to_string()
        }
        Some(local_https_identity::LocalHttpsRepairReason::UnsupportedSchema) => {
            "Companion's secure local connection identity must be upgraded. Use Repair Local HTTPS to rebuild it."
                .to_string()
        }
        None => {
            "Companion could not initialize its secure local connection. Use Repair Local HTTPS to try again."
                .to_string()
        }
    }
}

#[cfg(windows)]
fn local_https_success_message(
    changes: &local_https_identity::LocalHttpsReconcileChanges,
    listener_restarted: bool,
) -> String {
    if changes.bootstrapped_identity {
        return "Local HTTPS was rebuilt and secure local browser connections are ready again."
            .to_string();
    }
    if changes.leaf_regenerated && listener_restarted {
        return "Local HTTPS renewed its browser certificate and restarted the local listener."
            .to_string();
    }
    if changes.trust_installed {
        return "Local HTTPS trust was repaired and secure local browser connections are ready again."
            .to_string();
    }
    if listener_restarted {
        return "Local HTTPS restarted its local listener and secure browser connections are ready again."
            .to_string();
    }

    "Local HTTPS is already ready.".to_string()
}

#[cfg(windows)]
fn run_local_https_reconcile(
    app: &tauri::AppHandle,
    allow_identity_reset: bool,
) -> LocalHttpsReconcileOutcome {
    let paths = local_https_identity::LocalHttpsPaths::current();
    let trust_store = PromptingLocalCaTrustStore::new(app.clone());

    match local_https_identity::ensure_local_https_identity_with(
        &paths,
        &trust_store,
        time::OffsetDateTime::now_utc(),
    ) {
        Ok(result) => match result.state {
            local_https_identity::LocalHttpsReconcileState::Ready(ready_identity) => {
                LocalHttpsReconcileOutcome::Ready(LocalHttpsReadyState {
                    server_identity: ready_identity.server_identity,
                    changes: result.changes,
                })
            }
            local_https_identity::LocalHttpsReconcileState::RepairRequired(repair) => {
                if allow_identity_reset
                    && matches!(
                        repair.reason,
                        local_https_identity::LocalHttpsRepairReason::InvalidCaMaterial
                            | local_https_identity::LocalHttpsRepairReason::UnsupportedSchema
                    )
                {
                    info!(
                        "Replacing the persisted local HTTPS identity after explicit user confirmation. previous_reason={:?} previous_message={}",
                        repair.reason,
                        repair.message
                    );
                    match local_https_identity::replace_local_https_identity_with(
                        &paths,
                        &trust_store,
                        time::OffsetDateTime::now_utc(),
                    ) {
                        Ok(replacement_result) => match replacement_result.state {
                            local_https_identity::LocalHttpsReconcileState::Ready(ready_identity) => {
                                LocalHttpsReconcileOutcome::Ready(LocalHttpsReadyState {
                                    server_identity: ready_identity.server_identity,
                                    changes: replacement_result.changes,
                                })
                            }
                            local_https_identity::LocalHttpsReconcileState::RepairRequired(
                                replacement_repair,
                            ) => {
                                error!(
                                    "Local HTTPS identity replacement still requires repair: reason={:?} message={}",
                                    replacement_repair.reason,
                                    replacement_repair.message
                                );
                                let repair_reason = replacement_repair.reason.clone();
                                LocalHttpsReconcileOutcome::NeedsRepair {
                                    reason: Some(repair_reason.clone()),
                                    message: local_https_repair_message(Some(repair_reason)),
                                }
                            }
                        },
                        Err(error) => {
                            error!(
                                "Local HTTPS identity replacement failed unexpectedly: {}",
                                error
                            );
                            LocalHttpsReconcileOutcome::NeedsRepair {
                                reason: None,
                                message: local_https_repair_message(None),
                            }
                        }
                    }
                } else {
                    error!(
                        "Local HTTPS requires repair: reason={:?} message={}",
                        repair.reason,
                        repair.message
                    );
                    let repair_reason = repair.reason.clone();
                    LocalHttpsReconcileOutcome::NeedsRepair {
                        reason: Some(repair_reason.clone()),
                        message: local_https_repair_message(Some(repair_reason)),
                    }
                }
            }
        },
        Err(error) => {
            error!(
                "Local HTTPS reconciliation failed unexpectedly: {}",
                error
            );
            LocalHttpsReconcileOutcome::NeedsRepair {
                reason: None,
                message: local_https_repair_message(None),
            }
        }
    }
}

#[cfg(windows)]
async fn stop_local_https_server(
    server_task: &mut Option<tokio::task::JoinHandle<()>>,
    server_shutdown: &mut Option<tokio::sync::watch::Sender<bool>>,
    server_generation: &mut Option<u64>,
    expected_stopped_generation: &mut Option<u64>,
) {
    if server_task.is_some() {
        *expected_stopped_generation = *server_generation;
    }

    if let Some(shutdown_tx) = server_shutdown.take() {
        let _ = shutdown_tx.send(true);
    }

    if let Some(task) = server_task.take() {
        let _ = task.await;
    }

    *server_generation = None;
}

#[cfg(windows)]
fn spawn_local_https_server_task(
    command_tx: tokio::sync::mpsc::UnboundedSender<LocalHttpsControllerCommand>,
    state: Arc<AppState>,
    app: tauri::AppHandle,
    generation: u64,
    server_identity: local_https_identity::LocalHttpsServerIdentity,
) -> (
    tokio::task::JoinHandle<()>,
    tokio::sync::watch::Sender<bool>,
) {
    let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(async move {
        let result = server::start_server(state, app, server_identity, shutdown_rx).await;
        let _ = command_tx.send(LocalHttpsControllerCommand::ServerStopped { generation, result });
    });

    (task, shutdown_tx)
}

#[cfg(windows)]
async fn run_local_https_controller(
    app: tauri::AppHandle,
    state: Arc<AppState>,
    command_tx: tokio::sync::mpsc::UnboundedSender<LocalHttpsControllerCommand>,
    mut command_rx: tokio::sync::mpsc::UnboundedReceiver<LocalHttpsControllerCommand>,
) {
    let mut next_generation: u64 = 1;
    let mut server_generation: Option<u64> = None;
    let mut expected_stopped_generation: Option<u64> = None;
    let mut server_task: Option<tokio::task::JoinHandle<()>> = None;
    let mut server_shutdown: Option<tokio::sync::watch::Sender<bool>> = None;

    set_local_https_health_and_refresh(
        &app,
        &state,
        LocalHttpsHealth::repairing("Companion is reconciling its secure local connection."),
    );

    match run_local_https_reconcile(&app, false) {
        LocalHttpsReconcileOutcome::Ready(ready_state) => {
            if ready_state.changes.bootstrapped_identity {
                info!(
                    "Bootstrapped the local HTTPS identity for the Companion local API. trust_installed={} leaf_regenerated={}",
                    ready_state.changes.trust_installed,
                    ready_state.changes.leaf_regenerated
                );
                notifications::show_notification(
                    &app,
                    "Local HTTPS Enabled",
                    "Companion created and trusted its local HTTPS identity for secure browser connections.",
                );
            } else {
                if ready_state.changes.trust_installed {
                    info!(
                        "Reinstalled the local HTTPS CA trust in the current-user store during startup reconciliation."
                    );
                    notifications::show_notification(
                        &app,
                        "Local HTTPS Repaired",
                        "Companion repaired its local HTTPS trust so secure browser connections can resume.",
                    );
                }
                if ready_state.changes.leaf_regenerated {
                    info!(
                        "Regenerated the local HTTPS leaf certificate during startup reconciliation."
                    );
                }
            }

            let generation = next_generation;
            next_generation += 1;
            let (task, shutdown_tx) = spawn_local_https_server_task(
                command_tx.clone(),
                state.clone(),
                app.clone(),
                generation,
                ready_state.server_identity,
            );
            server_generation = Some(generation);
            server_task = Some(task);
            server_shutdown = Some(shutdown_tx);
            set_local_https_health_and_refresh(&app, &state, LocalHttpsHealth::ready(true));
        }
        LocalHttpsReconcileOutcome::NeedsRepair { reason, message } => {
            set_local_https_health_and_refresh(
                &app,
                &state,
                LocalHttpsHealth::needs_repair(message.clone(), reason, None, false),
            );
            notifications::show_notification(
                &app,
                "Local HTTPS Repair Required",
                "Companion local HTTPS needs repair. Open Companion Settings and use Repair Local HTTPS. The local browser connection will stay offline until this is fixed.",
            );
        }
    }

    while let Some(command) = command_rx.recv().await {
        match command {
            LocalHttpsControllerCommand::Repair {
                allow_identity_reset,
                response_tx,
            } => {
                let listener_running = server_task.is_some();
                let current_health = state.local_https_health();
                set_local_https_health_and_refresh(
                    &app,
                    &state,
                    LocalHttpsHealth {
                        status: LocalHttpsStatus::Repairing,
                        detail_message: "Companion is repairing its secure local connection. Browser status will refresh automatically when this finishes.".to_string(),
                        repair_reason: None,
                        current_user_trust_installed: current_health.current_user_trust_installed,
                        listener_running,
                    },
                );

                match run_local_https_reconcile(&app, allow_identity_reset) {
                    LocalHttpsReconcileOutcome::Ready(ready_state) => {
                        let listener_restarted =
                            ready_state.changes.bootstrapped_identity
                                || ready_state.changes.leaf_regenerated
                                || server_task.is_none();

                        if listener_restarted {
                            stop_local_https_server(
                                &mut server_task,
                                &mut server_shutdown,
                                &mut server_generation,
                                &mut expected_stopped_generation,
                            )
                            .await;

                            let generation = next_generation;
                            next_generation += 1;
                            let (task, shutdown_tx) = spawn_local_https_server_task(
                                command_tx.clone(),
                                state.clone(),
                                app.clone(),
                                generation,
                                ready_state.server_identity,
                            );
                            server_generation = Some(generation);
                            server_task = Some(task);
                            server_shutdown = Some(shutdown_tx);
                        }

                        let success_message =
                            local_https_success_message(&ready_state.changes, listener_restarted);
                        set_local_https_health_and_refresh(
                            &app,
                            &state,
                            LocalHttpsHealth::ready(server_task.is_some()),
                        );
                        notifications::show_notification(
                            &app,
                            "Local HTTPS Repaired",
                            &success_message,
                        );
                        let _ = response_tx.send(Ok(success_message));
                    }
                    LocalHttpsReconcileOutcome::NeedsRepair { reason, message } => {
                        set_local_https_health_and_refresh(
                            &app,
                            &state,
                            LocalHttpsHealth::needs_repair(
                                message.clone(),
                                reason,
                                current_health.current_user_trust_installed,
                                server_task.is_some(),
                            ),
                        );
                        notifications::show_notification(
                            &app,
                            "Local HTTPS Repair Required",
                            "Companion local HTTPS still needs repair. Open Companion Settings and use Repair Local HTTPS again if needed.",
                        );
                        let _ = response_tx.send(Err(message));
                    }
                }
            }
            LocalHttpsControllerCommand::ServerStopped { generation, result } => {
                if expected_stopped_generation == Some(generation) {
                    expected_stopped_generation = None;
                    continue;
                }
                if server_generation != Some(generation) {
                    continue;
                }

                server_task = None;
                server_shutdown = None;
                server_generation = None;

                match result {
                    Ok(()) => {
                        warn!(
                            "Companion local HTTPS listener stopped without an explicit restart request."
                        );
                    }
                    Err(error) => {
                        error!("Companion local HTTPS server stopped: {}", error);
                    }
                }

                set_local_https_health_and_refresh(
                    &app,
                    &state,
                    LocalHttpsHealth::needs_repair(
                        "Companion's secure local listener stopped unexpectedly. Use Repair Local HTTPS to restart it.",
                        None,
                        Some(true),
                        false,
                    ),
                );
                notifications::show_notification(
                    &app,
                    "Local HTTPS Server Failed",
                    "Companion could not keep its secure local listener online. Open Companion Settings and use Repair Local HTTPS.",
                );
            }
        }
    }
}

#[cfg(not(windows))]
async fn run_local_https_controller(
    app: tauri::AppHandle,
    state: Arc<AppState>,
    _command_tx: tokio::sync::mpsc::UnboundedSender<LocalHttpsControllerCommand>,
    mut command_rx: tokio::sync::mpsc::UnboundedReceiver<LocalHttpsControllerCommand>,
) {
    warn!(
        "Companion local HTTPS listener startup is only supported on Windows; leaving the local server offline on this platform."
    );
    set_local_https_health_and_refresh(
        &app,
        &state,
        LocalHttpsHealth::needs_repair(
            "Companion local HTTPS is only available on Windows.",
            None,
            None,
            false,
        ),
    );

    while let Some(command) = command_rx.recv().await {
        let LocalHttpsControllerCommand::Repair { response_tx, .. } = command;
        let _ = response_tx.send(Err("Local HTTPS repair is only available on Windows.".to_string()));
    }
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

fn handle_process_mode() -> bool {
    let mut args = std::env::args();
    let _ = args.next();

    match args.next().as_deref() {
        Some(LOCAL_HTTPS_UNINSTALL_CLEANUP_ARG) => {
            run_local_https_uninstall_cleanup();
            true
        }
        _ => false,
    }
}

fn run_local_https_uninstall_cleanup() {
    #[cfg(windows)]
    {
        info!("Running local HTTPS uninstall cleanup before delete-app-data removal.");
        let cleanup = local_https_identity::cleanup_local_https_for_uninstall();

        if cleanup.public_identity_found {
            info!(
                "Local HTTPS uninstall cleanup checked the persisted CA metadata. ca_removed={}",
                cleanup.ca_removed
            );
        } else {
            info!("Local HTTPS uninstall cleanup did not find persisted CA metadata on disk.");
        }

        if cleanup.revocation_list_found {
            info!(
                "Local HTTPS uninstall cleanup checked the persisted CRL. crl_removed={}",
                cleanup.crl_removed
            );
        } else {
            info!("Local HTTPS uninstall cleanup did not find a persisted CRL on disk.");
        }

        for issue in cleanup.issues {
            warn!("Local HTTPS uninstall cleanup issue: {}", issue);
        }
    }

    #[cfg(not(windows))]
    {
        warn!("Local HTTPS uninstall cleanup was requested on a non-Windows platform.");
    }
}

fn main() {
    if let Err(e) = setup_logging() {
        eprintln!("Failed to initialize logging: {}", e);
    }

    if handle_process_mode() {
        return;
    }

    info!("Starting Nojoin Companion (Tauri)...");

    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
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
            enable_firefox_support,
            repair_local_https,
            resize_current_window,
            close_update_prompt
        ])
        .setup(|app| {
            let (audio_tx, audio_rx) = crossbeam_channel::unbounded();
            let mut config = Config::load();
            reconcile_backend_secret_state(&mut config);

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
                local_https_health: Mutex::new(LocalHttpsHealth::default()),
                tray_status_item: Mutex::new(None),
                tray_run_on_startup_item: Mutex::new(None),
                tray_icon: Mutex::new(None),
                pairing_session: Mutex::new(None),
            });

            let (local_https_command_tx, local_https_command_rx) =
                tokio::sync::mpsc::unbounded_channel();

            app.manage(SharedAppState(state.clone()));
            app.manage(SharedLocalHttpsController(LocalHttpsControllerHandle {
                command_tx: local_https_command_tx.clone(),
            }));

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
            let local_https_command_tx_server = local_https_command_tx.clone();

            thread::spawn(move || {
                let rt = tokio::runtime::Runtime::new().unwrap();

                // Health Check & Status Update Loop
                let state_fetch = state_server.clone();
                let app_handle_status = app_handle.clone();
                rt.spawn(async move {
                    let mut client: Option<reqwest::Client> = None;
                    let mut current_fingerprint: Option<String> = None;

                    loop {
                        // 1. Perform Health Check
                        let (status_origin, fingerprint, is_authenticated) = {
                            let config = state_fetch.config.lock().unwrap();
                            (
                                config.get_web_url(),
                                config.tls_fingerprint(),
                                config.is_authenticated(),
                            )
                        };

                        if !is_authenticated {
                            client = None;
                            current_fingerprint = None;
                            state_fetch.is_backend_connected.store(false, Ordering::SeqCst);
                            update_tray_status_ui(&state_fetch);
                            tokio::time::sleep(Duration::from_secs(5)).await;
                            continue;
                        }

                        // Recreate client if fingerprint changed
                        if client.is_none() || current_fingerprint != fingerprint {
                            match crate::tls::create_client_builder(fingerprint.clone())
                                .timeout(Duration::from_secs(5))
                                .build()
                            {
                                Ok(new_client) => {
                                    client = Some(new_client);
                                    current_fingerprint = fingerprint.clone();
                                }
                                Err(err) => {
                                    error!("Failed to build backend status client: {}", err);
                                    state_fetch
                                        .is_backend_connected
                                        .store(false, Ordering::SeqCst);
                                    handle_backend_disconnect(&app_handle_status, &state_fetch);
                                    update_tray_status_ui(&state_fetch);
                                    tokio::time::sleep(Duration::from_secs(5)).await;
                                    continue;
                                }
                            }
                        }

                        let status_url = format!("{}/api/health", status_origin);
                        let Some(client) = client.as_ref() else {
                            tokio::time::sleep(Duration::from_secs(5)).await;
                            continue;
                        };

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

                rt.block_on(run_local_https_controller(
                    app_handle,
                    state_server,
                    local_https_command_tx_server,
                    local_https_command_rx,
                ));
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
