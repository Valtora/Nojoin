use crate::config::{BackendConnection, Config, MachineLocalUpdate};
use crate::notifications;
use crate::state::{AppState, AppStatus, AudioCommand, PairingValidationError};
use crate::uploader;
use axum::debug_handler;
use axum::{
    extract::State,
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use cpal::traits::{DeviceTrait, HostTrait};
use log::{error, info};
use std::sync::Arc;
use std::time::Duration;
use tauri::Manager;
use tower_http::cors::CorsLayer;

#[derive(Clone)]
pub struct ServerContext {
    pub state: Arc<AppState>,
    pub app_handle: tauri::AppHandle,
}

pub async fn start_server(state: Arc<AppState>, app_handle: tauri::AppHandle) {
    let local_port = {
        let config = state.config.lock().unwrap();
        config.local_port()
    };

    let context = ServerContext {
        state: state.clone(),
        app_handle,
    };

    let cors_state = state.clone();

    let cors = CorsLayer::new()
        .allow_origin(tower_http::cors::AllowOrigin::predicate(
            move |origin: &axum::http::HeaderValue, request_parts: &axum::http::request::Parts| {
                if let Ok(origin_str) = origin.to_str() {
                    let config = cors_state.config.lock().unwrap();
                    if !is_allowed_loopback_host(request_parts.headers.get("host"), &config) {
                        return false;
                    }

                    if request_parts.uri.path() == "/pair/complete" {
                        return cors_state.is_pairing_active() && !origin_str.is_empty();
                    }

                    return is_allowed_origin_value(origin_str, &config);
                }
                false
            },
        ))
        .allow_methods(tower_http::cors::Any)
        .allow_headers(tower_http::cors::Any);

    let app = Router::new()
        .route("/status", get(get_status))
        .route("/auth", post(deprecated_authorize))
        .route("/pair/complete", post(complete_pairing))
        .route("/config", get(get_config).post(update_config))
        .route("/devices", get(get_devices))
        .route("/levels", get(get_audio_levels))
        .route("/levels/live", get(get_live_audio_levels))
        .route("/start", post(start_recording))
        .route("/stop", post(stop_recording))
        .route("/pause", post(pause_recording))
        .route("/resume", post(resume_recording))
        .route("/update", post(trigger_update))
        .layer(cors)
        .with_state(context);

    let bind_addr = format!("127.0.0.1:{}", local_port);
    info!("Server running on http://{}", bind_addr);
    let listener = tokio::net::TcpListener::bind(&bind_addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

use std::time::SystemTime;

#[derive(serde::Serialize)]
struct StatusResponse {
    status: AppStatus,
    duration_seconds: u64,
    version: &'static str,
    authenticated: bool,
    api_host: String,
    update_available: bool,
    latest_version: Option<String>,
}

fn is_allowed_origin_value(origin: &str, config: &Config) -> bool {
    if origin == "http://localhost:14141"
        || origin == "https://localhost:14141"
        || origin == "http://localhost:3000"
    {
        return true;
    }

    let expected_origin = config.get_web_url();
    if !expected_origin.is_empty() && origin.eq_ignore_ascii_case(&expected_origin) {
        return true;
    }

    false
}

fn is_allowed_origin(origin_header: Option<&axum::http::HeaderValue>, config: &Config) -> bool {
    let origin = match origin_header {
        Some(origin) => match origin.to_str() {
            Ok(value) => value,
            Err(_) => return false,
        },
        None => return false,
    };

    is_allowed_origin_value(origin, config)
}

fn is_allowed_loopback_host(
    host_header: Option<&axum::http::HeaderValue>,
    config: &Config,
) -> bool {
    let host = match host_header {
        Some(host) => match host.to_str() {
            Ok(value) => value,
            Err(_) => return false,
        },
        None => return false,
    };

    let localhost = format!("localhost:{}", config.local_port());
    let ipv4_loopback = format!("127.0.0.1:{}", config.local_port());
    let ipv6_loopback = format!("[::1]:{}", config.local_port());

    host.eq_ignore_ascii_case(&localhost)
        || host.eq_ignore_ascii_case(&ipv4_loopback)
        || host.eq_ignore_ascii_case(&ipv6_loopback)
}

fn ensure_loopback_request(
    headers: &axum::http::HeaderMap,
    config: &Config,
) -> Result<(), StatusCode> {
    if is_allowed_loopback_host(headers.get("host"), config) {
        Ok(())
    } else {
        Err(StatusCode::FORBIDDEN)
    }
}

fn ensure_authenticated_origin(
    headers: &axum::http::HeaderMap,
    config: &Config,
) -> Result<(), StatusCode> {
    if is_allowed_origin(headers.get("origin"), config) {
        Ok(())
    } else {
        Err(StatusCode::FORBIDDEN)
    }
}

async fn get_status(
    headers: axum::http::HeaderMap,
    State(context): State<ServerContext>,
) -> Result<Json<StatusResponse>, StatusCode> {
    let state = &context.state;

    {
        let config = state.config.lock().unwrap();
        ensure_loopback_request(&headers, &config)?;
        ensure_authenticated_origin(&headers, &config)?;
    }

    let status = state.status.lock().unwrap().clone();
    let (authenticated, api_host) = {
        let config = state.config.lock().unwrap();
        (config.is_authenticated(), config.api_host())
    };

    let duration = {
        let acc = *state.accumulated_duration.lock().unwrap();
        let start = *state.recording_start_time.lock().unwrap();

        match status {
            AppStatus::Recording => {
                if let Some(s) = start {
                    if let Ok(elapsed) = s.elapsed() {
                        acc + elapsed
                    } else {
                        acc
                    }
                } else {
                    acc
                }
            }
            _ => acc,
        }
    };

    let update_available = state
        .update_available
        .load(std::sync::atomic::Ordering::Relaxed);
    let latest_version = state.latest_version.lock().unwrap().clone();

    Ok(Json(StatusResponse {
        status,
        duration_seconds: duration.as_secs(),
        version: env!("CARGO_PKG_VERSION"),
        authenticated,
        api_host,
        update_available,
        latest_version,
    }))
}

#[derive(serde::Deserialize)]
struct PairingCompleteRequest {
    pairing_code: String,
    bootstrap_token: String,
    api_host: Option<String>,
    api_port: Option<u16>,
    api_protocol: Option<String>,
    tls_fingerprint: Option<String>,
}

#[derive(serde::Serialize)]
struct PairingCompleteResponse {
    success: bool,
    message: String,
}

async fn validate_bootstrap_token(payload: &PairingCompleteRequest) -> Result<(), String> {
    let protocol = payload
        .api_protocol
        .as_deref()
        .ok_or_else(|| "Missing API protocol".to_string())?;
    let host = payload
        .api_host
        .as_deref()
        .ok_or_else(|| "Missing API host".to_string())?;
    let port = payload
        .api_port
        .ok_or_else(|| "Missing API port".to_string())?;

    let validation_url = format!(
        "{}://{}:{}/api/v1/login/companion-token/validate",
        protocol, host, port
    );
    let client = crate::tls::create_client(payload.tls_fingerprint.clone())
        .map_err(|err| err.to_string())?;

    let response = client
        .get(validation_url)
        .header(
            "Authorization",
            format!("Bearer {}", payload.bootstrap_token),
        )
        .send()
        .await
        .map_err(|err| err.to_string())?;

    if response.status().is_success() {
        Ok(())
    } else {
        Err(format!(
            "Bootstrap token validation failed with status {}",
            response.status()
        ))
    }
}

#[debug_handler]
async fn complete_pairing(
    State(context): State<ServerContext>,
    headers: axum::http::HeaderMap,
    Json(payload): Json<PairingCompleteRequest>,
) -> (StatusCode, Json<PairingCompleteResponse>) {
    let state = &context.state;
    info!("Received pairing completion request");

    {
        let config = state.config.lock().unwrap();
        if let Err(status) = ensure_loopback_request(&headers, &config) {
            return (
                status,
                Json(PairingCompleteResponse {
                    success: false,
                    message: "Pairing requests must target the local loopback server.".to_string(),
                }),
            );
        }
    }

    let origin = match headers.get("origin").and_then(|value| value.to_str().ok()) {
        Some(origin) if !origin.is_empty() => origin.to_string(),
        _ => {
            return (
                StatusCode::FORBIDDEN,
                Json(PairingCompleteResponse {
                    success: false,
                    message: "Pairing requests must include a valid Origin header.".to_string(),
                }),
            );
        }
    };

    if payload.bootstrap_token.trim().is_empty()
        || payload.api_host.as_deref().unwrap_or("").trim().is_empty()
        || payload
            .api_protocol
            .as_deref()
            .unwrap_or("")
            .trim()
            .is_empty()
        || payload.api_port.is_none()
        || payload.pairing_code.trim().is_empty()
    {
        return (
            StatusCode::BAD_REQUEST,
            Json(PairingCompleteResponse {
                success: false,
                message: "Pairing code, bootstrap token, protocol, host, and port are required."
                    .to_string(),
            }),
        );
    }

    {
        let status = state.status.lock().unwrap().clone();
        if !matches!(status, AppStatus::Idle | AppStatus::BackendOffline) {
            return (
                StatusCode::CONFLICT,
                Json(PairingCompleteResponse {
                    success: false,
                    message: "Companion pairing is blocked while a recording is active."
                        .to_string(),
                }),
            );
        }
    }

    match state.validate_pairing_code(&payload.pairing_code) {
        Ok(()) => {
            state.clear_pairing_session();
            if let Some(window) = context.app_handle.get_webview_window("pairing") {
                let _ = window.close();
            }
        }
        Err(PairingValidationError::NotActive) => {
            return (
                StatusCode::FORBIDDEN,
                Json(PairingCompleteResponse {
                    success: false,
                    message:
                        "Pairing mode is not active. Start pairing from the Companion app first."
                            .to_string(),
                }),
            );
        }
        Err(PairingValidationError::Expired) => {
            if let Some(window) = context.app_handle.get_webview_window("pairing") {
                let _ = window.close();
            }
            return (
                StatusCode::GONE,
                Json(PairingCompleteResponse {
                    success: false,
                    message:
                        "The pairing code has expired. Start pairing again in the Companion app."
                            .to_string(),
                }),
            );
        }
        Err(PairingValidationError::Invalid) => {
            return (
                StatusCode::FORBIDDEN,
                Json(PairingCompleteResponse {
                    success: false,
                    message: "The pairing code is invalid.".to_string(),
                }),
            );
        }
        Err(PairingValidationError::LockedOut) => {
            if let Some(window) = context.app_handle.get_webview_window("pairing") {
                let _ = window.close();
            }
            return (
                StatusCode::TOO_MANY_REQUESTS,
                Json(PairingCompleteResponse {
                    success: false,
                    message: "Too many invalid pairing attempts. Start pairing again in the Companion app.".to_string(),
                }),
            );
        }
    }

    if let Err(err) = validate_bootstrap_token(&payload).await {
        error!("Bootstrap token validation failed: {}", err);
        return (
            StatusCode::UNAUTHORIZED,
            Json(PairingCompleteResponse {
                success: false,
                message: "The backend bootstrap token is invalid or expired. Start pairing again from Nojoin.".to_string(),
            }),
        );
    }

    let backend = BackendConnection {
        api_protocol: payload.api_protocol.unwrap_or_default(),
        api_host: payload.api_host.unwrap_or_default(),
        api_port: payload.api_port.unwrap_or_default(),
        api_token: payload.bootstrap_token,
        tls_fingerprint: payload.tls_fingerprint,
        paired_web_origin: Some(origin.clone()),
        local_control_secret: None,
    };

    {
        let mut config = state.config.lock().unwrap();
        if let Err(e) = config.replace_backend_and_save(backend) {
            error!("Failed to save config: {}", e);
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(PairingCompleteResponse {
                    success: false,
                    message: format!("Failed to save pairing config: {}", e),
                }),
            );
        }
    }

    *state.current_recording_id.lock().unwrap() = None;
    *state.current_recording_token.lock().unwrap() = None;
    *state.current_sequence.lock().unwrap() = 1;

    info!(
        "Companion pairing completed successfully for origin {}",
        origin
    );
    notifications::show_notification(
        &context.app_handle,
        "Connected to Nojoin",
        "Companion app is now paired with this Nojoin deployment.",
    );

    (
        StatusCode::OK,
        Json(PairingCompleteResponse {
            success: true,
            message: "Pairing completed successfully.".to_string(),
        }),
    )
}

async fn deprecated_authorize() -> (StatusCode, Json<PairingCompleteResponse>) {
    (
        StatusCode::GONE,
        Json(PairingCompleteResponse {
            success: false,
            message: "Manual pairing now requires /pair/complete with a Companion-generated code."
                .to_string(),
        }),
    )
}

#[derive(serde::Serialize)]
struct AudioLevelsResponse {
    input_level: u32,
    output_level: u32,
    is_recording: bool,
}

async fn get_audio_levels(
    headers: axum::http::HeaderMap,
    State(context): State<ServerContext>,
) -> Result<Json<AudioLevelsResponse>, StatusCode> {
    let state = &context.state;

    {
        let config = state.config.lock().unwrap();
        ensure_loopback_request(&headers, &config)?;
        ensure_authenticated_origin(&headers, &config)?;
    }

    let status = state.status.lock().unwrap().clone();
    let is_recording = matches!(status, AppStatus::Recording);

    Ok(Json(AudioLevelsResponse {
        input_level: state.take_input_level(),
        output_level: state.take_output_level(),
        is_recording,
    }))
}

async fn get_live_audio_levels(
    headers: axum::http::HeaderMap,
    State(context): State<ServerContext>,
) -> Result<Json<AudioLevelsResponse>, StatusCode> {
    let state = &context.state;

    {
        let config = state.config.lock().unwrap();
        ensure_loopback_request(&headers, &config)?;
        ensure_authenticated_origin(&headers, &config)?;
    }

    let status = state.status.lock().unwrap().clone();
    let is_recording = matches!(status, AppStatus::Recording | AppStatus::Paused);

    Ok(Json(AudioLevelsResponse {
        input_level: state.peek_live_input_level(),
        output_level: state.peek_live_output_level(),
        is_recording,
    }))
}

#[derive(serde::Deserialize)]
struct StartRequest {
    name: String,
    token: Option<String>,
}

#[derive(serde::Serialize)]
struct StartResponse {
    id: i64,
    message: String,
}

#[debug_handler]
async fn start_recording(
    headers: axum::http::HeaderMap,
    State(context): State<ServerContext>,
    Json(payload): Json<StartRequest>,
) -> (StatusCode, Json<StartResponse>) {
    let state = &context.state;
    info!("Received start_recording request for '{}'", payload.name);

    {
        let config = state.config.lock().unwrap();
        if let Err(status) = ensure_loopback_request(&headers, &config) {
            return (
                status,
                Json(StartResponse {
                    id: 0,
                    message: "Requests must target the Companion loopback address.".to_string(),
                }),
            );
        }
        if let Err(status) = ensure_authenticated_origin(&headers, &config) {
            return (
                status,
                Json(StartResponse {
                    id: 0,
                    message: "This origin is not allowed to control the Companion app.".to_string(),
                }),
            );
        }
    }

    // Check status (and drop lock immediately)
    {
        let status = state.status.lock().unwrap();
        if *status != AppStatus::Idle && *status != AppStatus::BackendOffline {
            return (
                StatusCode::BAD_REQUEST,
                Json(StartResponse {
                    id: 0,
                    message: "Already recording".to_string(),
                }),
            );
        }
    }

    // Call backend to create recording
    let fingerprint = {
        let config = state.config.lock().unwrap();
        config.tls_fingerprint()
    };
    let client = crate::tls::create_client(fingerprint).unwrap_or_default();

    let (api_url, token) = {
        let config = state.config.lock().unwrap();
        (
            config.get_api_url(),
            payload.token.clone().unwrap_or_else(|| config.api_token()),
        )
    };

    let res = client
        .post(format!("{}/recordings/init", api_url))
        .header("Authorization", format!("Bearer {}", token))
        .query(&[("name", &payload.name)])
        .send()
        .await;

    match res {
        Ok(response) => {
            if let Ok(json) = response.json::<serde_json::Value>().await {
                if let Some(id) = json.get("id").and_then(|v| v.as_i64()) {
                    let recording_name = json
                        .get("name")
                        .and_then(|v| v.as_str())
                        .unwrap_or(&payload.name);
                    let upload_token = json
                        .get("upload_token")
                        .and_then(|v| v.as_str())
                        .map(|value| value.to_string());

                    if upload_token.is_none() {
                        error!("Backend did not return a recording upload token.");
                        return (
                            StatusCode::INTERNAL_SERVER_ERROR,
                            Json(StartResponse {
                                id: 0,
                                message: "Recording upload token missing".to_string(),
                            }),
                        );
                    }

                    // Start Audio Thread
                    *state.current_recording_id.lock().unwrap() = Some(id);
                    *state.current_recording_token.lock().unwrap() = upload_token;
                    *state.current_sequence.lock().unwrap() = 1;
                    *state.recording_start_time.lock().unwrap() = Some(SystemTime::now());
                    *state.accumulated_duration.lock().unwrap() = Duration::new(0, 0);

                    state
                        .audio_command_tx
                        .send(AudioCommand::Start(id))
                        .unwrap();

                    // Re-acquire lock to update status
                    let mut status = state.status.lock().unwrap();
                    *status = AppStatus::Recording;

                    notifications::show_notification(
                        &context.app_handle,
                        "Recording Started",
                        &format!("Recording '{}' started.", recording_name),
                    );
                    info!("Recording started successfully. ID: {}", id);

                    return (
                        StatusCode::OK,
                        Json(StartResponse {
                            id,
                            message: "Recording started".to_string(),
                        }),
                    );
                }
            }
        }
        Err(e) => {
            error!("Failed to start recording on backend: {}", e);
        }
    }

    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(StartResponse {
            id: 0,
            message: "Failed to start recording".to_string(),
        }),
    )
}

#[derive(serde::Deserialize)]
struct StopRequest {}

async fn stop_recording(
    headers: axum::http::HeaderMap,
    State(context): State<ServerContext>,
    Json(_payload): Json<Option<StopRequest>>,
) -> Result<Json<String>, StatusCode> {
    let state = &context.state;
    info!("Received stop_recording request");

    {
        let config = state.config.lock().unwrap();
        ensure_loopback_request(&headers, &config)?;
        ensure_authenticated_origin(&headers, &config)?;
    }

    let recording_id = *state.current_recording_id.lock().unwrap();

    {
        let mut status = state.status.lock().unwrap();
        *status = AppStatus::Uploading;

        // Do NOT clear current_recording_id here. Audio thread needs it.
    }
    state.audio_command_tx.send(AudioCommand::Stop).unwrap();

    if let Some(id) = recording_id {
        let config_clone = state.config.lock().unwrap().clone();
        let token = state.current_recording_token.lock().unwrap().clone();
        tokio::spawn(async move {
            if let Some(token) = token {
                if let Err(e) =
                    uploader::update_client_status(id, "UPLOADING", &config_clone, &token).await
                {
                    error!("Failed to update client status: {}", e);
                }
            } else {
                error!(
                    "Missing recording upload token while stopping recording {}",
                    id
                );
            }
        });
    }

    notifications::show_notification(
        &context.app_handle,
        "Recording Stopped",
        "Processing audio...",
    );
    info!("Stop command processed successfully");
    Ok(Json("Stopped".to_string()))
}

async fn pause_recording(
    headers: axum::http::HeaderMap,
    State(context): State<ServerContext>,
) -> Result<Json<String>, StatusCode> {
    let state = &context.state;
    info!("Received pause_recording request");

    {
        let config = state.config.lock().unwrap();
        ensure_loopback_request(&headers, &config)?;
        ensure_authenticated_origin(&headers, &config)?;
    }

    let recording_id = *state.current_recording_id.lock().unwrap();
    {
        let mut status = state.status.lock().unwrap();
        *status = AppStatus::Paused;

        // Accumulate time
        let mut start_time = state.recording_start_time.lock().unwrap();
        if let Some(s) = *start_time {
            if let Ok(elapsed) = s.elapsed() {
                let mut acc = state.accumulated_duration.lock().unwrap();
                *acc += elapsed;
            }
        }
        *start_time = None;
    }
    state.audio_command_tx.send(AudioCommand::Pause).unwrap();

    if let Some(id) = recording_id {
        let config_clone = state.config.lock().unwrap().clone();
        let token = state.current_recording_token.lock().unwrap().clone();
        tokio::spawn(async move {
            if let Some(token) = token {
                if let Err(e) =
                    uploader::update_client_status(id, "PAUSED", &config_clone, &token).await
                {
                    error!("Failed to update client status: {}", e);
                }
            } else {
                error!(
                    "Missing recording upload token while pausing recording {}",
                    id
                );
            }
        });
    }

    notifications::show_notification(&context.app_handle, "Recording Paused", "Recording paused.");
    info!("Recording paused");
    Ok(Json("Paused".to_string()))
}

async fn resume_recording(
    headers: axum::http::HeaderMap,
    State(context): State<ServerContext>,
) -> Result<Json<String>, StatusCode> {
    let state = &context.state;
    info!("Received resume_recording request");

    {
        let config = state.config.lock().unwrap();
        ensure_loopback_request(&headers, &config)?;
        ensure_authenticated_origin(&headers, &config)?;
    }

    let recording_id = *state.current_recording_id.lock().unwrap();
    {
        let mut status = state.status.lock().unwrap();
        *status = AppStatus::Recording;
        let mut seq = state.current_sequence.lock().unwrap();
        *seq += 1;

        // Resume timing
        let mut start_time = state.recording_start_time.lock().unwrap();
        *start_time = Some(SystemTime::now());
    }
    state.audio_command_tx.send(AudioCommand::Resume).unwrap();

    if let Some(id) = recording_id {
        let config_clone = state.config.lock().unwrap().clone();
        let token = state.current_recording_token.lock().unwrap().clone();
        tokio::spawn(async move {
            if let Some(token) = token {
                if let Err(e) =
                    uploader::update_client_status(id, "RECORDING", &config_clone, &token).await
                {
                    error!("Failed to update client status: {}", e);
                }
            } else {
                error!(
                    "Missing recording upload token while resuming recording {}",
                    id
                );
            }
        });
    }

    notifications::show_notification(
        &context.app_handle,
        "Recording Resumed",
        "Recording resumed.",
    );
    info!("Recording resumed");
    Ok(Json("Resumed".to_string()))
}

#[derive(serde::Serialize)]
struct ConfigResponse {
    api_port: u16,
    local_port: u16,
    min_meeting_length: Option<u32>,
}

async fn get_config(
    headers: axum::http::HeaderMap,
    State(context): State<ServerContext>,
) -> Result<Json<ConfigResponse>, StatusCode> {
    let state = &context.state;

    {
        let config = state.config.lock().unwrap();
        ensure_loopback_request(&headers, &config)?;
        ensure_authenticated_origin(&headers, &config)?;

        return Ok(Json(ConfigResponse {
            api_port: config.api_port(),
            local_port: config.local_port(),
            min_meeting_length: config.min_meeting_length(),
        }));
    }
}

#[derive(serde::Serialize)]
struct AudioDevice {
    name: String,
    is_default: bool,
}

#[derive(serde::Serialize)]
struct DevicesResponse {
    input_devices: Vec<AudioDevice>,
    output_devices: Vec<AudioDevice>,
    selected_input: Option<String>,
    selected_output: Option<String>,
}

async fn get_devices(
    headers: axum::http::HeaderMap,
    State(context): State<ServerContext>,
) -> Result<Json<DevicesResponse>, StatusCode> {
    let state = &context.state;

    {
        let config = state.config.lock().unwrap();
        ensure_loopback_request(&headers, &config)?;
        ensure_authenticated_origin(&headers, &config)?;
    }

    let host = cpal::default_host();

    let default_input_name = host.default_input_device().and_then(|d| d.name().ok());
    let default_output_name = host.default_output_device().and_then(|d| d.name().ok());

    let input_devices: Vec<AudioDevice> = host
        .input_devices()
        .map(|devices| {
            devices
                .filter_map(|d| {
                    d.name().ok().map(|name| AudioDevice {
                        is_default: Some(&name) == default_input_name.as_ref(),
                        name,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let output_devices: Vec<AudioDevice> = host
        .output_devices()
        .map(|devices| {
            devices
                .filter_map(|d| {
                    d.name().ok().map(|name| AudioDevice {
                        is_default: Some(&name) == default_output_name.as_ref(),
                        name,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let config = state.config.lock().unwrap();

    Ok(Json(DevicesResponse {
        input_devices,
        output_devices,
        selected_input: config.input_device_name().map(|value| value.to_string()),
        selected_output: config.output_device_name().map(|value| value.to_string()),
    }))
}

#[derive(serde::Deserialize)]
struct ConfigUpdate {
    api_port: Option<u16>,
    api_token: Option<String>,
    input_device_name: Option<String>,
    output_device_name: Option<String>,
    min_meeting_length: Option<u32>,
}

async fn update_config(
    headers: axum::http::HeaderMap,
    State(context): State<ServerContext>,
    Json(payload): Json<ConfigUpdate>,
) -> Result<Json<ConfigResponse>, StatusCode> {
    let state = &context.state;
    let mut config = state.config.lock().unwrap();

    ensure_loopback_request(&headers, &config)?;
    ensure_authenticated_origin(&headers, &config)?;

    let mut updated = config.clone();
    let mut should_save = false;

    if payload.api_port.is_some() || payload.api_token.is_some() {
        let mut backend = updated.backend_or_default();
        if let Some(port) = payload.api_port {
            backend.api_port = port;
            should_save = true;
        }
        if let Some(token) = payload.api_token {
            backend.api_token = token;
            should_save = true;
        }
        updated.replace_backend(backend);
    }

    let mut machine_local_update = MachineLocalUpdate::default();
    let mut machine_local_changed = false;
    if let Some(input_device_name) = payload.input_device_name {
        machine_local_update.input_device_name = Some(Some(input_device_name));
        machine_local_changed = true;
    }
    if let Some(output_device_name) = payload.output_device_name {
        machine_local_update.output_device_name = Some(Some(output_device_name));
        machine_local_changed = true;
    }
    if let Some(min_len) = payload.min_meeting_length {
        machine_local_update.min_meeting_length = Some(Some(min_len));
        machine_local_changed = true;
    }
    if machine_local_changed {
        updated.apply_machine_local_update(machine_local_update);
        should_save = true;
    }

    if should_save {
        if let Err(e) = updated.save() {
            eprintln!("Failed to save config: {}", e);
            return Err(StatusCode::INTERNAL_SERVER_ERROR);
        }
        *config = updated;
    }

    Ok(Json(ConfigResponse {
        api_port: config.api_port(),
        local_port: config.local_port(),
        min_meeting_length: config.min_meeting_length(),
    }))
}

async fn trigger_update(
    headers: axum::http::HeaderMap,
    State(context): State<ServerContext>,
) -> StatusCode {
    let state = &context.state;

    {
        let config = state.config.lock().unwrap();
        if ensure_loopback_request(&headers, &config).is_err()
            || ensure_authenticated_origin(&headers, &config).is_err()
        {
            return StatusCode::FORBIDDEN;
        }
    }

    let url = state.latest_update_url.lock().unwrap().clone();

    if let Some(target_url) = url {
        if let Err(e) = open::that(target_url) {
            error!("Failed to open update URL: {}", e);
            return StatusCode::INTERNAL_SERVER_ERROR;
        }
        StatusCode::OK
    } else {
        StatusCode::NOT_FOUND
    }
}
