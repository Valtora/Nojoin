use crate::notifications;
use crate::state::{AppState, AppStatus, AudioCommand};
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
use tower_http::cors::CorsLayer;

#[derive(Clone)]
pub struct ServerContext {
    pub state: Arc<AppState>,
    pub app_handle: tauri::AppHandle,
}

pub async fn start_server(state: Arc<AppState>, app_handle: tauri::AppHandle) {
    let local_port = {
        let config = state.config.lock().unwrap();
        config.local_port
    };

    let context = ServerContext { state, app_handle };

    let app = Router::new()
        .route("/status", get(get_status))
        .route("/auth", post(authorize))
        .route("/config", get(get_config).post(update_config))
        .route("/devices", get(get_devices))
        .route("/levels", get(get_audio_levels))
        .route("/start", post(start_recording))
        .route("/stop", post(stop_recording))
        .route("/pause", post(pause_recording))
        .route("/resume", post(resume_recording))
        .route("/update", post(trigger_update))
        .layer(CorsLayer::permissive())
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

async fn get_status(State(context): State<ServerContext>) -> Json<StatusResponse> {
    let state = &context.state;
    let status = state.status.lock().unwrap().clone();
    let (authenticated, api_host) = {
        let config = state.config.lock().unwrap();
        (!config.api_token.is_empty(), config.api_host.clone())
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

    Json(StatusResponse {
        status,
        duration_seconds: duration.as_secs(),
        version: env!("CARGO_PKG_VERSION"),
        authenticated,
        api_host,
        update_available,
        latest_version,
    })
}

// Authorization endpoint for web-based device pairing
#[derive(serde::Deserialize)]
struct AuthRequest {
    token: String,
    api_host: Option<String>,
    api_port: Option<u16>,
}

#[derive(serde::Serialize)]
struct AuthResponse {
    success: bool,
    message: String,
}

#[debug_handler]
async fn authorize(
    State(context): State<ServerContext>,
    Json(payload): Json<AuthRequest>,
) -> Json<AuthResponse> {
    let state = &context.state;
    info!("Received authorization request");

    if payload.token.is_empty() {
        return Json(AuthResponse {
            success: false,
            message: "Token cannot be empty".to_string(),
        });
    }

    // Save the token and connection details to config
    {
        let mut config = state.config.lock().unwrap();
        config.api_token = payload.token;

        if let Some(host) = payload.api_host {
            config.api_host = host;
        }

        if let Some(port) = payload.api_port {
            config.api_port = port;
        }

        if let Err(e) = config.save() {
            error!("Failed to save config: {}", e);
            return Json(AuthResponse {
                success: false,
                message: format!("Failed to save config: {}", e),
            });
        }
    }

    info!("Companion app authorized and configured successfully");
    notifications::show_notification(
        &context.app_handle,
        "Connected to Nojoin",
        "Companion app is now connected and configured.",
    );

    Json(AuthResponse {
        success: true,
        message: "Authorization and configuration successful".to_string(),
    })
}

#[derive(serde::Serialize)]
struct AudioLevelsResponse {
    input_level: u32,
    output_level: u32,
    is_recording: bool,
}

async fn get_audio_levels(State(context): State<ServerContext>) -> Json<AudioLevelsResponse> {
    let state = &context.state;
    let status = state.status.lock().unwrap().clone();
    let is_recording = matches!(status, AppStatus::Recording);

    Json(AudioLevelsResponse {
        input_level: state.take_input_level(),
        output_level: state.take_output_level(),
        is_recording,
    })
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
    State(context): State<ServerContext>,
    Json(payload): Json<StartRequest>,
) -> (StatusCode, Json<StartResponse>) {
    let state = &context.state;
    info!("Received start_recording request for '{}'", payload.name);

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

    // Update token if provided (for backward compatibility)
    if let Some(token) = payload.token {
        let mut config = state.config.lock().unwrap();
        config.api_token = token;
        if let Err(e) = config.save() {
            error!("Failed to save config: {}", e);
        }
    }

    // Call backend to create recording
    let client = reqwest::Client::builder()
        .danger_accept_invalid_certs(true)
        .build()
        .unwrap_or_default();

    let (api_url, token) = {
        let config = state.config.lock().unwrap();
        (config.get_api_url(), config.api_token.clone())
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

                    // Start Audio Thread
                    *state.current_recording_id.lock().unwrap() = Some(id);
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
struct StopRequest {
    token: Option<String>,
}

async fn stop_recording(
    State(context): State<ServerContext>,
    Json(payload): Json<Option<StopRequest>>,
) -> Result<Json<String>, StatusCode> {
    let state = &context.state;
    info!("Received stop_recording request");
    // Update token if provided
    if let Some(req) = payload {
        if let Some(token) = req.token {
            let mut config = state.config.lock().unwrap();
            config.api_token = token;
        }
    }

    let recording_id = *state.current_recording_id.lock().unwrap();

    {
        let mut status = state.status.lock().unwrap();
        *status = AppStatus::Uploading;

        // Reset timing
        let mut start_time = state.recording_start_time.lock().unwrap();
        *start_time = None;
        let mut acc = state.accumulated_duration.lock().unwrap();
        *acc = std::time::Duration::new(0, 0);

        // Do NOT clear current_recording_id here. Audio thread needs it.
    }
    state.audio_command_tx.send(AudioCommand::Stop).unwrap();

    if let Some(id) = recording_id {
        let config_clone = state.config.lock().unwrap().clone();
        tokio::spawn(async move {
            if let Err(e) = uploader::update_client_status(id, "UPLOADING", &config_clone).await {
                error!("Failed to update client status: {}", e);
            }
        });
    }

    notifications::show_notification(&context.app_handle, "Recording Stopped", "Processing audio...");
    info!("Stop command processed successfully");
    Ok(Json("Stopped".to_string()))
}

async fn pause_recording(State(context): State<ServerContext>) -> Result<Json<String>, StatusCode> {
    let state = &context.state;
    info!("Received pause_recording request");
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
        tokio::spawn(async move {
            if let Err(e) = uploader::update_client_status(id, "PAUSED", &config_clone).await {
                error!("Failed to update client status: {}", e);
            }
        });
    }

    notifications::show_notification(&context.app_handle, "Recording Paused", "Recording paused.");
    info!("Recording paused");
    Ok(Json("Paused".to_string()))
}

async fn resume_recording(
    State(context): State<ServerContext>,
) -> Result<Json<String>, StatusCode> {
    let state = &context.state;
    info!("Received resume_recording request");
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
        tokio::spawn(async move {
            if let Err(e) = uploader::update_client_status(id, "RECORDING", &config_clone).await {
                error!("Failed to update client status: {}", e);
            }
        });
    }

    notifications::show_notification(&context.app_handle, "Recording Resumed", "Recording resumed.");
    info!("Recording resumed");
    Ok(Json("Resumed".to_string()))
}

#[derive(serde::Serialize)]
struct ConfigResponse {
    api_port: u16,
    local_port: u16,
}

async fn get_config(State(context): State<ServerContext>) -> Json<ConfigResponse> {
    let state = &context.state;
    let config = state.config.lock().unwrap();
    Json(ConfigResponse {
        api_port: config.api_port,
        local_port: config.local_port,
    })
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

async fn get_devices(State(context): State<ServerContext>) -> Json<DevicesResponse> {
    let state = &context.state;
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

    Json(DevicesResponse {
        input_devices,
        output_devices,
        selected_input: config.input_device_name.clone(),
        selected_output: config.output_device_name.clone(),
    })
}

#[derive(serde::Deserialize)]
struct ConfigUpdate {
    api_port: Option<u16>,
    api_token: Option<String>,
    input_device_name: Option<String>,
    output_device_name: Option<String>,
}

async fn update_config(
    State(context): State<ServerContext>,
    Json(payload): Json<ConfigUpdate>,
) -> Result<Json<ConfigResponse>, StatusCode> {
    let state = &context.state;
    let mut config = state.config.lock().unwrap();

    if let Some(port) = payload.api_port {
        config.api_port = port;
    }
    if let Some(token) = payload.api_token {
        config.api_token = token;
    }
    if payload.input_device_name.is_some() {
        config.input_device_name = payload.input_device_name;
    }
    if payload.output_device_name.is_some() {
        config.output_device_name = payload.output_device_name;
    }

    if let Err(e) = config.save() {
        eprintln!("Failed to save config: {}", e);
        return Err(StatusCode::INTERNAL_SERVER_ERROR);
    }

    Ok(Json(ConfigResponse {
        api_port: config.api_port,
        local_port: config.local_port,
    }))
}

async fn trigger_update(State(context): State<ServerContext>) -> StatusCode {
    let state = &context.state;
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
