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
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, TrayIconBuilder, TrayIconEvent},
    LogicalSize, Manager,
};

use semver::Version;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
#[cfg(windows)]
use windows_sys::Win32::UI::Shell::{
    SHChangeNotify, SHCNE_ASSOCCHANGED, SHCNF_DWORD, SHCNF_FLUSH,
};
#[cfg(windows)]
use winreg::enums::HKEY_CURRENT_USER;
#[cfg(windows)]
use winreg::RegKey;

mod audio;
mod backend_url;
mod companion_auth;
mod config;
mod local_https_identity;
mod log_redact;
mod notifications;
mod pairing_link;
mod secret_store;
mod server;
mod state;
mod tls;
mod uploader;
mod win_notifications;

use config::{BackendConnection, Config, MachineLocalUpdate};
use pairing_link::PairingLaunchRequest;
use state::{
    pairing_block_message, recover_mutex_guard, AppState, AppStatus, LocalHttpsHealth,
    LocalHttpsStatus, RecordingRecoveryState,
};
use tauri_plugin_autostart::ManagerExt;

// Define SharedAppState at module level so it's visible to commands
struct SharedAppState(Arc<AppState>);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum LauncherOpenReason {
    ManualStartup,
    ExplicitLaunch,
}

impl LauncherOpenReason {
    fn as_str(self) -> &'static str {
        match self {
            Self::ManualStartup => "manual-startup",
            Self::ExplicitLaunch => "explicit-launch",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum ProcessMode {
    Normal {
        launched_from_autostart: bool,
        pairing_link: Option<String>,
    },
    UninstallCleanup,
}

enum LocalHttpsControllerCommand {
    #[cfg(windows)]
    ServerStopped { result: Result<(), String> },
}

const SETTINGS_WINDOW_LABEL: &str = "settings";
const MAIN_WINDOW_LABEL: &str = "main";
const AUTOSTART_ARG: &str = "--autostart";
const LOCAL_HTTPS_UNINSTALL_CLEANUP_ARG: &str = "--cleanup-local-https-on-uninstall";
const PAIRING_LINK_PREFIX: &str = "nojoin://pair";
#[cfg(windows)]
const NOJOIN_PROTOCOL_REGISTRY_PATH: &str = "Software\\Classes\\nojoin";

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
    #[serde(rename = "connectionState")]
    connection_state: SettingsConnectionState,
    #[serde(rename = "localHttpsStatus")]
    local_https_status: LocalHttpsStatus,
    local_https_message: String,
    local_https_listener_running: bool,
    local_https_current_user_trust_installed: Option<bool>,
    run_on_startup_enabled: bool,
    app_version: String,
    update_available: bool,
    latest_version: Option<String>,
}

#[derive(Clone, Copy, Debug, serde::Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
enum SettingsConnectionState {
    NotPaired,
    PairingCodeActive,
    Connected,
    TemporarilyDisconnected,
    BrowserRepairInProgress,
    BrowserRepairRequired,
}

#[derive(Clone, Copy, Debug, serde::Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
enum LauncherMode {
    FirstRun,
    Unpaired,
    PairingActive,
    PairedHealthy,
    PairedDisconnected,
    LocalHttpsRepairing,
    LocalHttpsNeedsRepair,
}

#[derive(Clone, Copy, Debug, serde::Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
enum LauncherPrimaryAction {
    OpenNojoin,
    OpenSettings,
}

#[derive(serde::Serialize)]
struct LauncherView {
    backend_label: String,
    is_paired: bool,
    is_pairing_active: bool,
    #[serde(rename = "localHttpsStatus")]
    local_https_status: LocalHttpsStatus,
    #[serde(rename = "launcherMode")]
    launcher_mode: LauncherMode,
    #[serde(rename = "primaryAction")]
    primary_action: LauncherPrimaryAction,
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

fn should_show_launcher_intro(config: &Config) -> bool {
    !config.is_authenticated() && !config.launcher_intro_seen().unwrap_or(false)
}

fn derive_launcher_mode(
    is_paired: bool,
    is_pairing_active: bool,
    local_https_status: LocalHttpsStatus,
    status: &AppStatus,
    show_intro: bool,
) -> LauncherMode {
    if local_https_status == LocalHttpsStatus::NeedsRepair {
        LauncherMode::LocalHttpsNeedsRepair
    } else if is_pairing_active {
        LauncherMode::PairingActive
    } else if !is_paired {
        if show_intro {
            LauncherMode::FirstRun
        } else {
            LauncherMode::Unpaired
        }
    } else if matches!(status, AppStatus::BackendOffline | AppStatus::Error(_)) {
        LauncherMode::PairedDisconnected
    } else if local_https_status == LocalHttpsStatus::Repairing {
        LauncherMode::LocalHttpsRepairing
    } else {
        LauncherMode::PairedHealthy
    }
}

fn launcher_primary_action(mode: LauncherMode) -> LauncherPrimaryAction {
    match mode {
        LauncherMode::FirstRun | LauncherMode::Unpaired | LauncherMode::PairingActive => {
            LauncherPrimaryAction::OpenSettings
        }
        LauncherMode::PairedHealthy => {
            LauncherPrimaryAction::OpenNojoin
        }
        LauncherMode::PairedDisconnected | LauncherMode::LocalHttpsRepairing => {
            LauncherPrimaryAction::OpenSettings
        }
        LauncherMode::LocalHttpsNeedsRepair => LauncherPrimaryAction::OpenSettings,
    }
}

fn derive_settings_connection_state(
    state: &Arc<AppState>,
    local_https_status: LocalHttpsStatus,
) -> SettingsConnectionState {
    if local_https_status == LocalHttpsStatus::NeedsRepair {
        SettingsConnectionState::BrowserRepairRequired
    } else if local_https_status == LocalHttpsStatus::Repairing {
        SettingsConnectionState::BrowserRepairInProgress
    } else if state.is_pairing_active() {
        SettingsConnectionState::PairingCodeActive
    } else if !state.is_authenticated() {
        SettingsConnectionState::NotPaired
    } else {
        let status = recover_mutex_guard(state.status.lock(), "status").clone();
        if matches!(status, AppStatus::BackendOffline | AppStatus::Error(_)) {
            SettingsConnectionState::TemporarilyDisconnected
        } else {
            SettingsConnectionState::Connected
        }
    }
}

fn derive_launcher_view(state: &Arc<AppState>) -> LauncherView {
    let (backend_label, show_intro) = {
        let config = recover_mutex_guard(state.config.lock(), "config");
        (
            derive_backend_label(&config),
            should_show_launcher_intro(&config),
        )
    };
    let local_https_status = state.local_https_health().status;
    let status = recover_mutex_guard(state.status.lock(), "status").clone();
    let is_paired = state.is_authenticated();
    let is_pairing_active = state.is_pairing_active();
    let launcher_mode = derive_launcher_mode(
        is_paired,
        is_pairing_active,
        local_https_status,
        &status,
        show_intro,
    );

    LauncherView {
        backend_label,
        is_paired,
        is_pairing_active,
        local_https_status,
        launcher_mode,
        primary_action: launcher_primary_action(launcher_mode),
    }
}

fn focus_webview_window(window: &tauri::WebviewWindow) {
    let _ = window.show();
    let _ = window.set_focus();
}

fn configure_launcher_window(window: &tauri::WebviewWindow) {
    let launcher_window = window.clone();
    window.on_window_event(move |event| {
        if let tauri::WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            let _ = launcher_window.hide();
        }
    });
}

fn mark_launcher_intro_seen(state: &Arc<AppState>) -> Result<bool, String> {
    let mut config = recover_mutex_guard(state.config.lock(), "config");
    if config.is_authenticated() || config.launcher_intro_seen() == Some(true) {
        return Ok(false);
    }

    config
        .update_machine_local_and_save(MachineLocalUpdate {
            launcher_intro_seen: Some(Some(true)),
            ..Default::default()
        })
        .map_err(|err| format!("Failed to persist launcher intro state: {}", err))?;

    Ok(true)
}

fn open_launcher_window(
    app: &tauri::AppHandle,
    state: &Arc<AppState>,
    reason: LauncherOpenReason,
) -> Result<(), String> {
    let launcher_view = derive_launcher_view(state);
    let window = app
        .get_webview_window(MAIN_WINDOW_LABEL)
        .ok_or_else(|| "Launcher window is unavailable.".to_string())?;

    info!(
        "Opening launcher window. reason={} mode={:?} primary_action={:?}",
        reason.as_str(),
        launcher_view.launcher_mode,
        launcher_view.primary_action
    );
    focus_webview_window(&window);

    if launcher_view.launcher_mode == LauncherMode::FirstRun {
        match mark_launcher_intro_seen(state) {
            Ok(true) => info!(
                "Marked launcher intro as seen after opening the launcher. reason={}",
                reason.as_str()
            ),
            Ok(false) => {}
            Err(error) => warn!("{}", error),
        }
    }

    Ok(())
}

fn focus_primary_native_surface_for_launch(
    app: &tauri::AppHandle,
    state: &Arc<AppState>,
    reason: LauncherOpenReason,
) -> Result<(), String> {
    if state.is_pairing_active() {
        return open_settings_window(app).or_else(|error| {
            warn!(
                "{} Falling back to the launcher window instead while pairing approval is active. reason={}",
                error,
                reason.as_str()
            );
            open_launcher_window(app, state, reason)
        });
    }

    open_launcher_window(app, state, reason)
}

fn maybe_open_startup_surface(
    app: &tauri::AppHandle,
    state: &Arc<AppState>,
    launched_from_autostart: bool,
) -> Result<(), String> {
    if launched_from_autostart {
        if state.is_authenticated() {
            info!(
                "Autostart detected with an existing paired backend. Keeping the launcher hidden."
            );
            return Ok(());
        }

        info!(
            "Autostart detected without an active pairing. Staying in the tray and surfacing onboarding via tray notification."
        );
        notifications::show_notification(
            app,
            "Nojoin Companion",
            "Running in the system tray. Open from the tray to finish setup.",
        );
        return Ok(());
    }

    info!("Manual startup detected. Opening the primary native surface.");
    focus_primary_native_surface_for_launch(app, state, LauncherOpenReason::ManualStartup)
}

fn open_paired_web_origin(app: &tauri::AppHandle) -> Result<String, String> {
    let target_url = {
        let state_wrapper = app.state::<SharedAppState>();
        let config = state_wrapper.0.config.lock().unwrap();
        if !config.is_authenticated() {
            return Err(
                "No paired Nojoin deployment is available yet. Start pairing from Nojoin in your browser first."
                    .to_string(),
            );
        }

        config.paired_web_origin().ok_or_else(|| {
            "The paired Nojoin origin is unavailable. Open Companion Settings and pair again if needed."
                .to_string()
        })?
    };

    open::that(&target_url).map_err(|error| format!("Failed to open Nojoin: {}", error))?;
    info!(
        "Opened the paired Nojoin origin from a native surface. target_origin={}",
        target_url
    );

    Ok(target_url)
}

fn tray_has_open_nojoin_target(state: &Arc<AppState>) -> bool {
    let config = recover_mutex_guard(state.config.lock(), "config");
    config.is_authenticated() && config.paired_web_origin().is_some()
}

fn current_tray_status_label(
    status: &AppStatus,
    local_https_status: LocalHttpsStatus,
    is_authenticated: bool,
    recovery_state: RecordingRecoveryState,
) -> String {
    if !matches!(
        status,
        AppStatus::Recording | AppStatus::Paused | AppStatus::Uploading
    ) {
        match local_https_status {
            LocalHttpsStatus::Repairing => {
                return "Browser repair in progress".to_string();
            }
            LocalHttpsStatus::NeedsRepair => {
                return "Browser repair required".to_string();
            }
            LocalHttpsStatus::Ready => {}
        }
    }

    if matches!(status, AppStatus::Error(_)) {
        return "Error".to_string();
    }

    if !is_authenticated {
        return "Not paired".to_string();
    }

    match status {
        AppStatus::Idle => "Connected".to_string(),
        AppStatus::Recording => match recovery_state {
            RecordingRecoveryState::WaitingForReconnect => {
                "Recording while temporarily disconnected".to_string()
            }
            _ => "Recording".to_string(),
        },
        AppStatus::Paused => match recovery_state {
            RecordingRecoveryState::None => "Recording paused".to_string(),
            RecordingRecoveryState::WaitingForReconnect => {
                "Recording paused while temporarily disconnected".to_string()
            }
            RecordingRecoveryState::StopRequested => {
                "Upload queued until reconnect".to_string()
            }
        },
        AppStatus::Uploading => match recovery_state {
            RecordingRecoveryState::StopRequested => {
                "Upload queued until reconnect".to_string()
            }
            _ => "Uploading recording".to_string(),
        },
        AppStatus::BackendOffline => "Temporarily disconnected".to_string(),
        AppStatus::Error(_) => "Error".to_string(),
    }
}

fn current_tray_status_label_for_state(state: &Arc<AppState>) -> String {
    let status = recover_mutex_guard(state.status.lock(), "status").clone();
    let local_https_status = state.local_https_health().status;
    current_tray_status_label(
        &status,
        local_https_status,
        state.is_authenticated(),
        state.recording_recovery_state(),
    )
}

fn current_tray_status_text(state: &Arc<AppState>) -> String {
    format!("Status: {}", current_tray_status_label_for_state(state))
}

fn current_tray_tooltip_text(state: &Arc<AppState>) -> String {
    format!(
        "Nojoin Companion: {}",
        current_tray_status_label_for_state(state)
    )
}

fn update_tray_status_ui(state: &Arc<AppState>) {
    let status_text = current_tray_status_text(state);
    let tooltip_text = current_tray_tooltip_text(state);

    if let Some(item) = recover_mutex_guard(state.tray_status_item.lock(), "tray_status_item").as_ref() {
        let _ = item.set_text(&status_text);
    }

    if let Some(tray) = recover_mutex_guard(state.tray_icon.lock(), "tray_icon").as_ref() {
        let _ = tray.set_tooltip(Some(&tooltip_text));
    }
}

fn build_tray_menu(app: &tauri::AppHandle, state: &Arc<AppState>) -> tauri::Result<Menu<tauri::Wry>> {
    let status = recover_mutex_guard(state.status.lock(), "status").clone();
    let recovery_state = state.recording_recovery_state();
    let has_active_recording = recover_mutex_guard(state.current_recording_id.lock(), "current_recording_id").is_some();
    let can_open_nojoin = tray_has_open_nojoin_target(state);
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
    let open_nojoin = MenuItem::with_id(
        app,
        "open_nojoin",
        "Open Nojoin",
        can_open_nojoin,
        None::<&str>,
    )?;
    let settings = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
    let status_item = MenuItem::with_id(
        app,
        "status",
        &current_tray_status_text(state),
        false,
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

    *recover_mutex_guard(state.tray_status_item.lock(), "tray_status_item") = Some(status_item.clone());

    let separator_after_status = PredefinedMenuItem::separator(app)?;
    let separator_after_controls = PredefinedMenuItem::separator(app)?;
    let separator_before_quit = PredefinedMenuItem::separator(app)?;

    if has_active_recording && matches!(status, AppStatus::Recording | AppStatus::Paused) {
        Menu::with_items(
            app,
            &[
                &status_item,
                &separator_after_status,
                &pause_recording,
                &resume_recording,
                &stop_recording,
                &separator_after_controls,
                &open_nojoin,
                &settings,
                &separator_before_quit,
                &quit,
            ],
        )
    } else {
        Menu::with_items(
            app,
            &[
                &status_item,
                &separator_after_status,
                &open_nojoin,
                &settings,
                &separator_before_quit,
                &quit,
            ],
        )
    }
}

pub fn refresh_tray_menu(app: &tauri::AppHandle, state: &Arc<AppState>) {
    match build_tray_menu(app, state) {
        Ok(menu) => {
            if let Some(tray) = recover_mutex_guard(state.tray_icon.lock(), "tray_icon").as_ref() {
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
    let status = recover_mutex_guard(state.status.lock(), "status").clone();
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
            let mut status = recover_mutex_guard(state.status.lock(), "status");
            *status = AppStatus::BackendOffline;
        }
        _ => {}
    }

    refresh_tray_menu(app, state);
}

fn handle_backend_reconnect(app: &tauri::AppHandle, state: &Arc<AppState>) {
    let status = recover_mutex_guard(state.status.lock(), "status").clone();
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
            let mut status = recover_mutex_guard(state.status.lock(), "status");
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
    .inner_size(660.0, 760.0)
    .min_inner_size(560.0, 520.0)
    .resizable(false)
    .center()
    .build()
    .map(|_| ())
    .map_err(|err| format!("Failed to open settings window: {}", err))
}

fn set_run_on_startup_enabled(
    app: &tauri::AppHandle,
    state: &Arc<AppState>,
    should_run: bool,
) -> Result<String, String> {
    let autostart_manager = app.autolaunch();
    let is_enabled = autostart_manager
        .is_enabled()
        .map_err(|err| format!("Failed to read run on startup preference: {}", err))?;

    if is_enabled != should_run {
        if should_run {
            autostart_manager
                .enable()
                .map_err(|err| format!("Failed to enable run on startup: {}", err))?;
        } else {
            autostart_manager
                .disable()
                .map_err(|err| format!("Failed to disable run on startup: {}", err))?;
        }
    }

    {
        let mut config = recover_mutex_guard(state.config.lock(), "config");
        config
            .update_machine_local_and_save(MachineLocalUpdate {
                run_on_startup: Some(Some(should_run)),
                ..Default::default()
            })
            .map_err(|err| format!("Failed to save run on startup preference: {}", err))?;
    }

    refresh_tray_menu(app, state);

    Ok(if should_run {
        "Run on Startup is enabled. Nojoin Companion will open when you sign in to Windows."
            .to_string()
    } else {
        "Run on Startup is disabled. Open Nojoin Companion manually whenever you need it."
            .to_string()
    })
}

fn open_logs_directory() -> Result<String, String> {
    let log_path = get_log_path();
    let log_dir = log_path
        .parent()
        .ok_or_else(|| "The Companion logs folder is unavailable.".to_string())?;
    open::that(log_dir).map_err(|err| format!("Failed to open the logs folder: {}", err))?;
    Ok("Opened the Companion logs folder.".to_string())
}

#[tauri::command]
fn get_config(state: tauri::State<SharedAppState>) -> ConfigView {
    let config = recover_mutex_guard(state.0.config.lock(), "config");
    ConfigView::from(&*config)
}

#[tauri::command]
fn get_settings_state(app: tauri::AppHandle, state: tauri::State<SharedAppState>) -> SettingsView {
    let backend_label = {
        let config = recover_mutex_guard(state.0.config.lock(), "config");
        derive_backend_label(&config)
    };
    let local_https_health = state.0.local_https_health();
    let latest_version = recover_mutex_guard(state.0.latest_version.lock(), "latest_version").clone();

    SettingsView {
        backend_label,
        is_paired: state.0.is_authenticated(),
        is_pairing_active: state.0.is_pairing_active(),
        connection_state: derive_settings_connection_state(&state.0, local_https_health.status),
        local_https_status: local_https_health.status,
        local_https_message: local_https_health.detail_message,
        local_https_listener_running: local_https_health.listener_running,
        local_https_current_user_trust_installed: local_https_health.current_user_trust_installed,
        run_on_startup_enabled: app.autolaunch().is_enabled().unwrap_or(false),
        app_version: app.package_info().version.to_string(),
        update_available: state.0.update_available.load(Ordering::SeqCst),
        latest_version,
    }
}

#[tauri::command]
fn get_launcher_state(state: tauri::State<SharedAppState>) -> LauncherView {
    derive_launcher_view(&state.0)
}

#[tauri::command]
async fn open_settings(app: tauri::AppHandle) -> Result<(), String> {
    info!("Opening Settings from a native launcher action.");
    let app_handle = app.clone();
    let (tx, rx) = tokio::sync::oneshot::channel();

    app.run_on_main_thread(move || {
        let result = open_settings_window(&app_handle);
        let _ = tx.send(result);
    })
    .map_err(|err| format!("Failed to schedule settings window creation: {}", err))?;

    rx.await
        .map_err(|_| "Failed to receive settings window result.".to_string())?
}

#[tauri::command]
fn open_nojoin(app: tauri::AppHandle) -> Result<String, String> {
    open_paired_web_origin(&app)
}

#[tauri::command]
async fn disconnect_backend(
    app: tauri::AppHandle,
    state: tauri::State<'_, SharedAppState>,
) -> Result<String, String> {
    state.0.is_backend_connected.store(false, Ordering::SeqCst);

    let (backend, secret_bundle, secret_cleanup_error, launcher_intro_reset_error) = {
        let mut config = recover_mutex_guard(state.0.config.lock(), "config");
        let backend = config.backend_connection();
        let secret_bundle = backend
            .as_ref()
            .and_then(|current| secret_store::load_backend_secret_bundle_for_backend(current).ok());
        let mut launcher_intro_reset_error = None;
        if backend.is_some() {
            config
                .clear_backend_and_save()
                .map_err(|err| format!("Failed to save settings: {}", err))?;
            launcher_intro_reset_error = config
                .update_machine_local_and_save(MachineLocalUpdate {
                    launcher_intro_seen: Some(Some(false)),
                    ..Default::default()
                })
                .err();
        }
        let secret_cleanup_error = backend.as_ref().and_then(|current| {
            secret_store::delete_backend_secret_bundle_for_backend(current).err()
        });
        (
            backend,
            secret_bundle,
            secret_cleanup_error,
            launcher_intro_reset_error,
        )
    };

    if let Some(error) = secret_cleanup_error.as_ref() {
        warn!(
            "Failed to delete the local companion secret bundle during disconnect: {}",
            error
        );
    }

    if let Some(error) = launcher_intro_reset_error.as_ref() {
        warn!(
            "Failed to reset launcher intro state during disconnect: {}",
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
                "Disconnected from the current backend and cleared the saved trust state. Start a new pairing request from Nojoin in your browser when you are ready to connect to another deployment.".to_string(),
                "This Companion is no longer paired with a Nojoin backend, and the saved certificate trust has been cleared. Start a new pairing request from Nojoin when you are ready.".to_string(),
            ),
            Ok(count) => (
                format!(
                    "Disconnected from the current backend, cleared the saved trust state, and revoked {} backend pairing{}. Start a new pairing request from Nojoin in your browser when you are ready.",
                    count,
                    if count == 1 { "" } else { "s" }
                ),
                "This Companion is no longer paired with a Nojoin backend, and the saved certificate trust has been cleared. Start a new pairing request from Nojoin when you are ready.".to_string(),
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
            "Restart Firefox, then pair again from Nojoin using a fresh pairing request.",
        );
        Ok(
            "Firefox support was enabled for this Windows device. Restart Firefox, then start a fresh pairing request and try again."
                .to_string(),
        )
    }
}

#[tauri::command]
async fn resize_current_window(
    window: tauri::WebviewWindow,
    width: f64,
    height: f64,
) -> Result<(), String> {
    let window_label = window.label().to_string();
    let (min_width, max_width, min_height, max_height) =
        if window_label == SETTINGS_WINDOW_LABEL {
            (600.0, 920.0, 620.0, 1100.0)
        } else {
            (360.0, 640.0, 220.0, 520.0)
        };
    let clamped_width = width.clamp(min_width, max_width);
    let clamped_height = height.clamp(min_height, max_height);
    let app_handle = window.app_handle().clone();
    let resize_window = window.clone();
    let (tx, rx) = tokio::sync::oneshot::channel();

    app_handle
        .run_on_main_thread(move || {
            let result = resize_window
                .set_size(LogicalSize::new(clamped_width, clamped_height))
                .map_err(|err| format!("Failed to resize window: {}", err));
            let _ = tx.send(result);
        })
        .map_err(|err| format!("Failed to schedule window resize: {}", err))?;

    rx.await
        .map_err(|_| "Failed to receive window resize result.".to_string())?
}

#[tauri::command]
fn set_run_on_startup(
    app: tauri::AppHandle,
    state: tauri::State<'_, SharedAppState>,
    enabled: bool,
) -> Result<String, String> {
    set_run_on_startup_enabled(&app, &state.0, enabled)
}

#[tauri::command]
fn view_logs() -> Result<String, String> {
    open_logs_directory()
}

enum UpdateCheckOutcome {
    UpdateAvailable { version: String, url: String },
    UpToDate,
}

async fn perform_update_check(app: &tauri::AppHandle) -> Result<UpdateCheckOutcome, String> {
    let current_version = app.package_info().version.to_string();
    let state_wrapper = app.state::<SharedAppState>();
    let state = state_wrapper.0.clone();

    match check_github_release(&current_version).await? {
        Some((version, url)) => {
            state.update_available.store(true, Ordering::SeqCst);
            *recover_mutex_guard(state.latest_version.lock(), "latest_version") =
                Some(version.clone());
            *recover_mutex_guard(state.latest_update_url.lock(), "latest_update_url") =
                Some(url.clone());
            Ok(UpdateCheckOutcome::UpdateAvailable { version, url })
        }
        None => {
            state.update_available.store(false, Ordering::SeqCst);
            *recover_mutex_guard(state.latest_version.lock(), "latest_version") = None;
            *recover_mutex_guard(state.latest_update_url.lock(), "latest_update_url") = None;
            Ok(UpdateCheckOutcome::UpToDate)
        }
    }
}

#[tauri::command]
async fn check_for_updates(app: tauri::AppHandle) -> Result<String, String> {
    match perform_update_check(&app).await? {
        UpdateCheckOutcome::UpdateAvailable { version, .. } => Ok(format!(
            "Update available. Nojoin Companion version {} is ready to install from the latest release.",
            version
        )),
        UpdateCheckOutcome::UpToDate => Ok(format!(
            "No updates found. Nojoin Companion is already on version {}.",
            app.package_info().version
        )),
    }
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
    match perform_update_check(app).await {
        Ok(UpdateCheckOutcome::UpdateAvailable { version, url }) => {
            #[cfg(windows)]
            {
                win_notifications::show_update_notification(app.clone(), version, url);
            }

            #[cfg(not(windows))]
            {
                notifications::show_notification(
                    app,
                    "Update Available",
                    &format!(
                        "Nojoin Companion version {} is available. Download it from {}.",
                        version, url
                    ),
                );
            }
        }
        Ok(UpdateCheckOutcome::UpToDate) => {
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
        } else if let Err(reset_error) = config.update_machine_local_and_save(MachineLocalUpdate {
            launcher_intro_seen: Some(Some(false)),
            ..Default::default()
        }) {
            warn!(
                "Failed to reset launcher intro state after clearing the paired backend: {}",
                reset_error
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
            "Firefox can only use Nojoin's local HTTPS certificate after you explicitly enable Firefox support.\n\nThis will install the Nojoin local HTTPS CA and revocation list into the Windows Local Machine trust stores so Firefox can import them when Windows root trust is enabled. Windows will show an administrator approval prompt next. Continue only if you want Firefox on this device to trust the Nojoin Companion local connection.",
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
            "Companion could not restore Windows trust for its secure local connection. Quit and relaunch Companion to try again."
                .to_string()
        }
        Some(local_https_identity::LocalHttpsRepairReason::InvalidCaMaterial) => {
            "Companion's secure local connection identity needs to be rebuilt. Quit and relaunch Companion to continue."
                .to_string()
        }
        Some(local_https_identity::LocalHttpsRepairReason::UnsupportedSchema) => {
            "Companion's secure local connection identity must be upgraded. Quit and relaunch Companion to rebuild it."
                .to_string()
        }
        None => {
            "Companion could not initialize its secure local connection. Quit and relaunch Companion to try again."
                .to_string()
        }
    }
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
                            local_https_identity::LocalHttpsReconcileState::Ready(
                                ready_identity,
                            ) => LocalHttpsReconcileOutcome::Ready(LocalHttpsReadyState {
                                server_identity: ready_identity.server_identity,
                                changes: replacement_result.changes,
                            }),
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
                        repair.reason, repair.message
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
            error!("Local HTTPS reconciliation failed unexpectedly: {}", error);
            LocalHttpsReconcileOutcome::NeedsRepair {
                reason: None,
                message: local_https_repair_message(None),
            }
        }
    }
}

#[cfg(windows)]
fn spawn_local_https_server_task(
    command_tx: tokio::sync::mpsc::UnboundedSender<LocalHttpsControllerCommand>,
    state: Arc<AppState>,
    app: tauri::AppHandle,
    server_identity: local_https_identity::LocalHttpsServerIdentity,
) -> tokio::sync::watch::Sender<bool> {
    let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);
    tokio::spawn(async move {
        let result = server::start_server(state, app, server_identity, shutdown_rx).await;
        let _ = command_tx.send(LocalHttpsControllerCommand::ServerStopped { result });
    });

    shutdown_tx
}

#[cfg(windows)]
async fn run_local_https_controller(
    app: tauri::AppHandle,
    state: Arc<AppState>,
    command_tx: tokio::sync::mpsc::UnboundedSender<LocalHttpsControllerCommand>,
    mut command_rx: tokio::sync::mpsc::UnboundedReceiver<LocalHttpsControllerCommand>,
) {
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

            let shutdown_tx = spawn_local_https_server_task(
                command_tx.clone(),
                state.clone(),
                app.clone(),
                ready_state.server_identity,
            );
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
                "Local Browser Connection Unavailable",
                "Companion could not restore its secure local browser connection automatically. Quit and relaunch Companion to try again.",
            );
        }
    }

    while let Some(command) = command_rx.recv().await {
        match command {
            LocalHttpsControllerCommand::ServerStopped { result } => {
                drop(server_shutdown.take());

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
                        "Companion's secure local listener stopped unexpectedly. Quit and relaunch Companion to restore browser-side local controls.",
                        None,
                        Some(true),
                        false,
                    ),
                );
                notifications::show_notification(
                    &app,
                    "Local Browser Connection Unavailable",
                    "Companion could not keep its secure local listener online. Quit and relaunch Companion to restore browser-side local controls.",
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

    while command_rx.recv().await.is_some() {}
}

/// Maximum size of the active log file before it is rotated, in bytes.
const LOG_ROTATE_MAX_BYTES: u64 = 5 * 1024 * 1024;
/// Number of rotated backups to retain alongside the active log file.
const LOG_ROTATE_BACKUPS: u32 = 5;

fn rotated_log_path(log_path: &std::path::Path, index: u32) -> PathBuf {
    let mut name = log_path
        .file_name()
        .map(|n| n.to_os_string())
        .unwrap_or_else(|| std::ffi::OsString::from("nojoin-companion.log"));
    name.push(format!(".{}", index));
    log_path.with_file_name(name)
}

/// Apply per-user file permissions on Unix. No-op on Windows where the
/// per-user `%APPDATA%` ACL already restricts access.
fn enforce_log_file_permissions(path: &std::path::Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(meta) = std::fs::metadata(path) {
            let mut perms = meta.permissions();
            if perms.mode() & 0o777 != 0o600 {
                perms.set_mode(0o600);
                let _ = std::fs::set_permissions(path, perms);
            }
        }
    }
    #[cfg(not(unix))]
    {
        let _ = path;
    }
}

fn rotate_logs_if_needed(log_path: &std::path::Path) {
    let size = std::fs::metadata(log_path).map(|m| m.len()).unwrap_or(0);
    if size < LOG_ROTATE_MAX_BYTES {
        return;
    }
    let oldest = rotated_log_path(log_path, LOG_ROTATE_BACKUPS);
    let _ = std::fs::remove_file(&oldest);
    for i in (1..LOG_ROTATE_BACKUPS).rev() {
        let from = rotated_log_path(log_path, i);
        let to = rotated_log_path(log_path, i + 1);
        if from.exists() {
            let _ = std::fs::rename(&from, &to);
            enforce_log_file_permissions(&to);
        }
    }
    let first = rotated_log_path(log_path, 1);
    let _ = std::fs::rename(log_path, &first);
    enforce_log_file_permissions(&first);
}

fn open_log_file(log_path: &std::path::Path) -> std::io::Result<std::fs::File> {
    let mut opts = std::fs::OpenOptions::new();
    opts.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let file = opts.open(log_path)?;
    enforce_log_file_permissions(log_path);
    Ok(file)
}

fn install_panic_hook() {
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let payload = info.to_string();
        error!(
            "Panic in nojoin-companion: {}",
            crate::log_redact::sanitize_for_log(&payload)
        );
        default_hook(info);
    }));
}

fn setup_logging() -> Result<(), fern::InitError> {
    let log_path = get_log_path();
    rotate_logs_if_needed(&log_path);
    let file = open_log_file(&log_path)?;

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
        // Strict hardcoded level. Even if a future caller re-enables debug
        // logging, the noisy network crates below cannot dump request bodies.
        .level(log::LevelFilter::Info)
        .filter(|metadata| {
            let target = metadata.target();
            let noisy = target.starts_with("reqwest")
                || target.starts_with("hyper")
                || target.starts_with("h2")
                || target.starts_with("rustls")
                || target.starts_with("tokio_rustls")
                || target.starts_with("tower")
                || target.starts_with("axum");
            if noisy && metadata.level() > log::Level::Warn {
                return false;
            }
            true
        })
        .chain(file)
        .apply()?;

    install_panic_hook();
    Ok(())
}

fn open_web_interface(app: &tauri::AppHandle) {
    if let Err(message) = open_paired_web_origin(app) {
        notifications::show_notification(app, "Open Nojoin Unavailable", &message);
    }
}

fn handle_tray_double_click(app: &tauri::AppHandle, state: &Arc<AppState>) {
    if tray_has_open_nojoin_target(state) {
        open_web_interface(app);
        return;
    }

    if let Err(message) = focus_primary_native_surface_for_launch(
        app,
        state,
        LauncherOpenReason::ExplicitLaunch,
    ) {
        notifications::show_notification(app, "Launcher Error", &message);
    }
}

#[cfg(windows)]
fn windows_protocol_handler_exe_path(exe_path: &std::path::Path) -> String {
    let path = exe_path.to_string_lossy().replace('/', "\\");

    if let Some(stripped) = path.strip_prefix("\\\\?\\UNC\\") {
        return format!("\\\\{}", stripped);
    }

    if let Some(stripped) = path.strip_prefix("\\\\?\\") {
        return stripped.to_string();
    }

    path
}

#[cfg(windows)]
fn set_registry_string_value_if_changed(
    key: &RegKey,
    name: &str,
    value: &str,
    description: &str,
) -> Result<bool, String> {
    if key.get_value::<String, _>(name).ok().as_deref() == Some(value) {
        return Ok(false);
    }

    key.set_value(name, &value)
        .map_err(|error| format!("Failed to set {}: {}", description, error))?;

    Ok(true)
}

#[cfg(windows)]
fn ensure_nojoin_protocol_handler_registered() -> Result<(), String> {
    let exe_path = std::env::current_exe()
        .map_err(|error| format!("Failed to locate the current executable: {}", error))?;
    let exe_path_string = windows_protocol_handler_exe_path(&exe_path);
    let command_value = format!("\"{}\" \"%1\"", exe_path_string);
    let icon_value = format!("\"{}\",0", exe_path_string);
    let mut association_changed = false;

    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let (protocol_key, _) = hkcu
        .create_subkey(NOJOIN_PROTOCOL_REGISTRY_PATH)
        .map_err(|error| format!("Failed to open the nojoin protocol registry key: {}", error))?;
    association_changed |= set_registry_string_value_if_changed(
        &protocol_key,
        "",
        "URL:Nojoin Protocol",
        "the nojoin protocol description",
    )?;
    association_changed |= set_registry_string_value_if_changed(
        &protocol_key,
        "URL Protocol",
        "",
        "the nojoin protocol URL marker",
    )?;
    association_changed |= set_registry_string_value_if_changed(
        &protocol_key,
        "FriendlyTypeName",
        "Nojoin Companion Pairing Link",
        "the nojoin protocol friendly name",
    )?;

    let (default_icon_key, _) = protocol_key
        .create_subkey("DefaultIcon")
        .map_err(|error| format!("Failed to open the nojoin protocol icon key: {}", error))?;
    association_changed |= set_registry_string_value_if_changed(
        &default_icon_key,
        "",
        &icon_value,
        "the nojoin protocol icon",
    )?;

    let (command_key, _) = protocol_key
        .create_subkey("shell\\open\\command")
        .map_err(|error| format!("Failed to open the nojoin protocol command key: {}", error))?;
    association_changed |= set_registry_string_value_if_changed(
        &command_key,
        "",
        &command_value,
        "the nojoin protocol open command",
    )?;

    if association_changed {
        unsafe {
            SHChangeNotify(
                SHCNE_ASSOCCHANGED as i32,
                SHCNF_DWORD | SHCNF_FLUSH,
                std::ptr::null(),
                std::ptr::null(),
            );
        }
        thread::sleep(Duration::from_millis(250));
    }

    Ok(())
}

#[cfg(not(windows))]
fn ensure_nojoin_protocol_handler_registered() -> Result<(), String> {
    Ok(())
}

struct PairingRequestGuard {
    app: tauri::AppHandle,
    state: Arc<AppState>,
    request_id: String,
}

impl PairingRequestGuard {
    fn acquire(
        app: &tauri::AppHandle,
        state: Arc<AppState>,
        request_id: String,
    ) -> Result<Self, String> {
        state.begin_pairing_request(&request_id)?;
        refresh_tray_menu(app, &state);
        Ok(Self {
            app: app.clone(),
            state,
            request_id,
        })
    }
}

impl Drop for PairingRequestGuard {
    fn drop(&mut self) {
        self.state.finish_pairing_request(&self.request_id);
        refresh_tray_menu(&self.app, &self.state);
    }
}

fn extract_pairing_link_arg(args: &[String]) -> Option<String> {
    args.iter()
        .map(|arg| arg.trim().trim_matches('"').to_string())
        .find(|arg| arg.starts_with(PAIRING_LINK_PREFIX))
}

fn backend_label_from_connection(backend: &BackendConnection) -> String {
    backend
        .paired_web_origin
        .clone()
        .unwrap_or_else(|| backend.derived_web_origin())
}

async fn confirm_pairing_request(
    app: &tauri::AppHandle,
    launch_request: &PairingLaunchRequest,
    previous_backend: Option<&BackendConnection>,
) -> Result<bool, String> {
    let app_handle = app.clone();
    let prompt_message = if let Some(previous_backend) = previous_backend {
        let current_origin = backend_label_from_connection(previous_backend);
        if current_origin == launch_request.backend_origin {
            format!(
                "Approve pairing refresh for {} as {}?\n\nThis keeps the same Nojoin deployment but rotates this device's pairing.",
                launch_request.backend_origin, launch_request.username
            )
        } else {
            format!(
                "Approve switching this device from {} to {} as {}?\n\nFuture recordings and browser-side controls will use the new Nojoin deployment after approval.",
                current_origin, launch_request.backend_origin, launch_request.username
            )
        }
    } else {
        format!(
            "Approve pairing this device with {} as {}?\n\nNojoin requested that this Companion connect to that deployment.",
            launch_request.backend_origin, launch_request.username
        )
    };
    let (tx, rx) = tokio::sync::oneshot::channel();

    app.run_on_main_thread(move || {
        if let Err(error) = open_settings_window(&app_handle) {
            warn!(
                "Failed to surface the settings window before showing the pairing prompt: {}",
                error
            );
        }

        let decision = app_handle
            .dialog()
            .message(prompt_message)
            .title("Approve Pairing Request")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom(
                "Approve".to_string(),
                "Decline".to_string(),
            ))
            .blocking_show();
        let _ = tx.send(decision);
    })
    .map_err(|error| format!("Failed to schedule the pairing approval prompt: {}", error))?;

    rx.await
        .map_err(|_| "Failed to receive the pairing approval decision.".to_string())
}

async fn best_effort_reject_pairing_request(
    backend_target: &crate::backend_url::ValidatedBackendTarget,
    launch_request: &PairingLaunchRequest,
    status: &str,
    detail: &str,
    failure_reason: &str,
) {
    if let Err(error) = companion_auth::reject_pairing_request(
        backend_target,
        &launch_request.request_id,
        &launch_request.request_secret,
        status,
        detail,
        failure_reason,
    )
    .await
    {
        warn!(
            "Failed to update backend pairing request state (request_id={}): {}",
            &launch_request.request_id,
            error
        );
    }
}

fn pairing_request_identity_conflict(
    existing_backend: &BackendConnection,
    launch_request: &PairingLaunchRequest,
) -> bool {
    if backend_label_from_connection(existing_backend) != launch_request.backend_origin {
        return false;
    }

    existing_backend
        .backend_identity_public_key
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(|value| value != launch_request.backend_identity_public_key)
        .unwrap_or(false)
}

fn is_same_backend_target(previous: &BackendConnection, replacement: &BackendConnection) -> bool {
    previous.api_protocol.eq_ignore_ascii_case(&replacement.api_protocol)
        && previous.api_host.eq_ignore_ascii_case(&replacement.api_host)
        && previous.api_port == replacement.api_port
        && previous.paired_web_origin == replacement.paired_web_origin
}

async fn process_pairing_link(
    app: tauri::AppHandle,
    state: Arc<AppState>,
    raw_url: String,
) {
    let (launch_request, backend_target) = match PairingLaunchRequest::parse(&raw_url) {
        Ok(value) => value,
        Err(error) => {
            notifications::show_notification(&app, "Pairing Request Invalid", &error);
            return;
        }
    };

    if launch_request.is_expired() {
        notifications::show_notification(
            &app,
            "Pairing Request Expired",
            "This Nojoin pairing request has already expired. Start again from Nojoin in the browser.",
        );
        return;
    }

    let _guard = match PairingRequestGuard::acquire(
        &app,
        state.clone(),
        launch_request.request_id.clone(),
    ) {
        Ok(guard) => guard,
        Err(message) => {
            notifications::show_notification(&app, "Pairing Already Open", &message);
            best_effort_reject_pairing_request(
                &backend_target,
                &launch_request,
                "failed",
                &message,
                "another_request_in_progress",
            )
            .await;
            return;
        }
    };

    if let Err(error) = companion_auth::mark_pairing_request_opened(
        &backend_target,
        &launch_request.request_id,
        &launch_request.request_secret,
    )
    .await
    {
        notifications::show_notification(
            &app,
            "Pairing Request Failed",
            "Nojoin Companion could not acknowledge the pairing request. Start again from Nojoin in the browser.",
        );
        warn!(
            "Failed to mark pairing request opened (request_id={}): {}",
            &launch_request.request_id,
            error
        );
        return;
    }

    let current_status = recover_mutex_guard(state.status.lock(), "status").clone();
    if let Some(message) = pairing_block_message(&current_status) {
        best_effort_reject_pairing_request(
            &backend_target,
            &launch_request,
            "failed",
            message,
            "companion_busy",
        )
        .await;
        notifications::show_notification(&app, "Pairing Unavailable", message);
        return;
    }

    let previous_backend = {
        let config = recover_mutex_guard(state.config.lock(), "config");
        config.backend_connection()
    };

    if previous_backend
        .as_ref()
        .map(|backend| pairing_request_identity_conflict(backend, &launch_request))
        .unwrap_or(false)
    {
        let message = "This Nojoin deployment presented a different saved backend identity than the one already trusted on this device. Open Nojoin from the currently paired deployment and review the backend before approving another pairing.";
        best_effort_reject_pairing_request(
            &backend_target,
            &launch_request,
            "failed",
            message,
            "backend_identity_mismatch",
        )
        .await;
        notifications::show_notification(&app, "Pairing Identity Mismatch", message);
        return;
    }

    let approved = match confirm_pairing_request(&app, &launch_request, previous_backend.as_ref()).await {
        Ok(approved) => approved,
        Err(error) => {
            best_effort_reject_pairing_request(
                &backend_target,
                &launch_request,
                "failed",
                "Nojoin Companion could not show the approval prompt. Start again from Nojoin.",
                "prompt_unavailable",
            )
            .await;
            notifications::show_notification(&app, "Pairing Failed", &error);
            return;
        }
    };

    if !approved {
        best_effort_reject_pairing_request(
            &backend_target,
            &launch_request,
            "declined",
            "Pairing was declined in the Companion app.",
            "user_declined",
        )
        .await;
        notifications::show_notification(
            &app,
            "Pairing Declined",
            "This device was not paired because the request was declined.",
        );
        return;
    }

    let captured_tls_fingerprint = match crate::tls::capture_tls_fingerprint(
        &backend_target.host,
        backend_target.port,
    )
    .await
    {
        Ok(fingerprint) => fingerprint,
        Err(error) => {
            best_effort_reject_pairing_request(
                &backend_target,
                &launch_request,
                "failed",
                "Nojoin Companion could not capture the backend TLS certificate. Start pairing again from Nojoin.",
                "tls_capture_failed",
            )
            .await;
            notifications::show_notification(&app, "Pairing Failed", &error);
            return;
        }
    };

    let completion = match companion_auth::complete_pairing_request(
        &backend_target,
        &launch_request.request_id,
        &launch_request.request_secret,
        &captured_tls_fingerprint,
    )
    .await
    {
        Ok(completion) => completion,
        Err(error) => {
            best_effort_reject_pairing_request(
                &backend_target,
                &launch_request,
                "failed",
                "Nojoin Companion could not complete the pairing request. Start pairing again from Nojoin.",
                "pairing_completion_failed",
            )
            .await;
            notifications::show_notification(&app, "Pairing Failed", &error);
            return;
        }
    };

    let backend = BackendConnection {
        api_protocol: completion.api_protocol.clone(),
        api_host: completion.api_host.clone(),
        api_port: completion.api_port,
        tls_fingerprint: Some(captured_tls_fingerprint.clone()),
        paired_web_origin: Some(completion.paired_web_origin.clone()),
        backend_pairing_id: Some(completion.backend_pairing_id.clone()),
        local_control_secret_version: Some(completion.local_control_secret_version),
        backend_identity_key_id: Some(completion.backend_identity_key_id.clone()),
        backend_identity_public_key: Some(completion.backend_identity_public_key.clone()),
    };
    let new_secret_bundle = secret_store::BackendSecretBundle {
        companion_credential_secret: completion.companion_credential_secret.clone(),
        local_control_secret: completion.local_control_secret.clone(),
    };

    let previous_secret_bundle = previous_backend
        .as_ref()
        .and_then(|existing| secret_store::load_backend_secret_bundle_for_backend(existing).ok());
    let had_existing_backend = previous_backend.is_some();
    let should_revoke_previous_backend = previous_backend
        .as_ref()
        .map(|existing| !is_same_backend_target(existing, &backend))
        .unwrap_or(false);

    if let Err(error) = secret_store::save_backend_secret_bundle_for_backend(&backend, &new_secret_bundle) {
        let _ = companion_auth::revoke_backend_pairings_with_bundle(&backend, &new_secret_bundle).await;
        notifications::show_notification(&app, "Pairing Failed", &error);
        return;
    }

    let save_result = {
        let mut config = recover_mutex_guard(state.config.lock(), "config");
        config.replace_backend_and_save(backend.clone())
    };
    if let Err(error) = save_result {
        let _ = secret_store::delete_backend_secret_bundle_for_backend(&backend);
        let _ = companion_auth::revoke_backend_pairings_with_bundle(&backend, &new_secret_bundle).await;
        notifications::show_notification(&app, "Pairing Failed", &format!("Failed to save pairing config: {}", error));
        return;
    }

    *recover_mutex_guard(state.current_recording_id.lock(), "current_recording_id") = None;
    *recover_mutex_guard(state.current_recording_token.lock(), "current_recording_token") = None;
    state.clear_current_recording_owner();
    state.clear_recording_recovery_state();
    *recover_mutex_guard(state.current_sequence.lock(), "current_sequence") = 1;

    if should_revoke_previous_backend {
        if let Some(previous_backend) = previous_backend.as_ref() {
            if let Some(bundle) = previous_secret_bundle.as_ref() {
                if let Err(error) =
                    companion_auth::revoke_backend_pairings_with_bundle(previous_backend, bundle).await
                {
                    warn!("Failed to revoke pairing state on the previous backend: {}", error);
                }
            }
        }
    }

    if let Some(previous_backend) = previous_backend.as_ref() {
        if previous_backend.backend_pairing_id != backend.backend_pairing_id {
            if let Err(error) = secret_store::delete_backend_secret_bundle_for_backend(previous_backend)
            {
                warn!(
                    "Failed to delete the previous backend companion secret bundle after successful pairing: {}",
                    error
                );
            }
        }
    }

    let (notification_title, notification_body) = if should_revoke_previous_backend {
        (
            "Backend Switch Complete",
            "Companion is now paired with the new Nojoin deployment.",
        )
    } else if had_existing_backend {
        (
            "Pairing Refreshed",
            "Companion pairing was refreshed for this Nojoin deployment.",
        )
    } else {
        (
            "Pairing Complete",
            "Companion is now paired with this Nojoin deployment.",
        )
    };
    notifications::show_notification(&app, notification_title, notification_body);
    refresh_tray_menu(&app, &state);
}

fn dispatch_pairing_link(app: &tauri::AppHandle, state: &Arc<AppState>, raw_url: String) {
    tauri::async_runtime::spawn(process_pairing_link(
        app.clone(),
        state.clone(),
        raw_url,
    ));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tray_status_uses_browser_repair_vocabulary() {
        assert_eq!(
            current_tray_status_label(
                &AppStatus::Idle,
                LocalHttpsStatus::Repairing,
                true,
                RecordingRecoveryState::None,
            ),
            "Browser repair in progress"
        );
        assert_eq!(
            current_tray_status_label(
                &AppStatus::Idle,
                LocalHttpsStatus::NeedsRepair,
                true,
                RecordingRecoveryState::None,
            ),
            "Browser repair required"
        );
    }

    #[test]
    fn tray_status_uses_frozen_idle_and_disconnect_vocabulary() {
        assert_eq!(
            current_tray_status_label(
                &AppStatus::Idle,
                LocalHttpsStatus::Ready,
                false,
                RecordingRecoveryState::None,
            ),
            "Not paired"
        );
        assert_eq!(
            current_tray_status_label(
                &AppStatus::Idle,
                LocalHttpsStatus::Ready,
                true,
                RecordingRecoveryState::None,
            ),
            "Connected"
        );
        assert_eq!(
            current_tray_status_label(
                &AppStatus::BackendOffline,
                LocalHttpsStatus::Ready,
                true,
                RecordingRecoveryState::None,
            ),
            "Temporarily disconnected"
        );
    }

    #[test]
    fn tray_status_preserves_recording_reconnect_wording() {
        assert_eq!(
            current_tray_status_label(
                &AppStatus::Recording,
                LocalHttpsStatus::Ready,
                true,
                RecordingRecoveryState::WaitingForReconnect,
            ),
            "Recording while temporarily disconnected"
        );
        assert_eq!(
            current_tray_status_label(
                &AppStatus::Paused,
                LocalHttpsStatus::Ready,
                true,
                RecordingRecoveryState::WaitingForReconnect,
            ),
            "Recording paused while temporarily disconnected"
        );
        assert_eq!(
            current_tray_status_label(
                &AppStatus::Uploading,
                LocalHttpsStatus::Ready,
                true,
                RecordingRecoveryState::StopRequested,
            ),
            "Upload queued until reconnect"
        );
    }

}

fn handle_process_mode() -> ProcessMode {
    let args: Vec<String> = std::env::args().skip(1).collect();

    if args
        .iter()
        .any(|arg| arg == LOCAL_HTTPS_UNINSTALL_CLEANUP_ARG)
    {
        run_local_https_uninstall_cleanup();
        return ProcessMode::UninstallCleanup;
    }

    ProcessMode::Normal {
        launched_from_autostart: args.iter().any(|arg| arg == AUTOSTART_ARG),
        pairing_link: extract_pairing_link_arg(&args),
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

    let (launched_from_autostart, startup_pairing_link) = match handle_process_mode() {
        ProcessMode::UninstallCleanup => return,
        ProcessMode::Normal {
            launched_from_autostart,
            pairing_link,
        } => (launched_from_autostart, pairing_link),
    };

    info!("Starting Nojoin Companion (Tauri)...");

    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            let state = app.state::<SharedAppState>().0.clone();
            if let Some(pairing_link) = extract_pairing_link_arg(&args) {
                dispatch_pairing_link(app, &state, pairing_link);
                return;
            }
            if let Err(message) =
                focus_primary_native_surface_for_launch(app, &state, LauncherOpenReason::ExplicitLaunch)
            {
                notifications::show_notification(app, "Launcher Error", &message);
            }
        }))
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![AUTOSTART_ARG]),
        ))
        .invoke_handler(tauri::generate_handler![
            get_config,
            get_settings_state,
            get_launcher_state,
            open_settings,
            open_nojoin,
            disconnect_backend,
            enable_firefox_support,
            resize_current_window,
            set_run_on_startup,
            view_logs,
            check_for_updates,
            close_update_prompt
        ])
        .setup(move |app| {
            if let Err(error) = ensure_nojoin_protocol_handler_registered() {
                warn!(
                    "Failed to ensure that the nojoin:// protocol handler is registered for this Companion binary: {}",
                    error
                );
                notifications::show_notification(
                    app.handle(),
                    "Pairing Links Unavailable",
                    "Nojoin Companion could not register the local nojoin:// pairing link handler. Browser pairing will fail until the app can repair that Windows association.",
                );
            }

            let (audio_tx, audio_rx) = crossbeam_channel::unbounded();
            let mut config = Config::load();
            reconcile_backend_secret_state(&mut config);

            let autostart_manager = app.autolaunch();
            let is_enabled = autostart_manager.is_enabled().unwrap_or(false);
            if let Some(should_run) = config.run_on_startup() {
                if should_run && !is_enabled {
                    if let Err(e) = autostart_manager.enable() {
                        error!("Failed to enable autostart on load: {}", e);
                    }
                } else if !should_run && is_enabled {
                    if let Err(e) = autostart_manager.disable() {
                        error!("Failed to disable autostart on load: {}", e);
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
                tray_icon: Mutex::new(None),
                #[cfg(test)]
                pairing_session: Mutex::new(None),
                pairing_request_in_progress: Mutex::new(None),
            });

            let (local_https_command_tx, local_https_command_rx) =
                tokio::sync::mpsc::unbounded_channel();

            app.manage(SharedAppState(state.clone()));

            if let Some(window) = app.get_webview_window(MAIN_WINDOW_LABEL) {
                configure_launcher_window(&window);
                let _ = window.hide();
            }

            let app_handle = app.handle();
            let menu = build_tray_menu(&app_handle, &state)?;

            let tray = TrayIconBuilder::new()
                .tooltip("Nojoin Companion")
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .on_menu_event(move |app, event| {
                    match event.id.as_ref() {
                        "quit" => {
                            std::process::exit(0);
                        }
                        "open_nojoin" => {
                            open_web_interface(app);
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
                        _ => {}
                    }
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::DoubleClick { button: MouseButton::Left, .. } = event {
                        let state = tray.app_handle().state::<SharedAppState>().0.clone();
                        handle_tray_double_click(tray.app_handle(), &state);
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

            if let Some(pairing_link) = startup_pairing_link.clone() {
                dispatch_pairing_link(app.handle(), &state, pairing_link);
            } else if let Err(message) =
                maybe_open_startup_surface(app.handle(), &state, launched_from_autostart)
            {
                notifications::show_notification(app.handle(), "Launcher Error", &message);
            }

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
