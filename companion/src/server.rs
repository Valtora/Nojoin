use axum::{
    routing::{get, post},
    Router, Json, extract::State, http::StatusCode,
};
use std::sync::Arc;
use tower_http::cors::CorsLayer;
use cpal::traits::{DeviceTrait, HostTrait};
use crate::state::{AppState, AppStatus, AudioCommand};
use crate::notifications;
use crate::config::Config;
use crate::uploader;

pub async fn start_server(state: Arc<AppState>) {
    let app = Router::new()
        .route("/status", get(get_status))
        .route("/config", get(get_config).post(update_config))
        .route("/devices", get(get_devices))
        .route("/levels", get(get_audio_levels))
        .route("/start", post(start_recording))
        .route("/stop", post(stop_recording))
        .route("/pause", post(pause_recording))
        .route("/resume", post(resume_recording))
        .layer(CorsLayer::permissive())
        .with_state(state);

    println!("Server running on http://127.0.0.1:12345");
    let listener = tokio::net::TcpListener::bind("127.0.0.1:12345").await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

use std::time::SystemTime;

#[derive(serde::Serialize)]
struct StatusResponse {
    status: AppStatus,
    duration_seconds: u64,
}

async fn get_status(State(state): State<Arc<AppState>>) -> Json<StatusResponse> {
    let status = state.status.lock().unwrap().clone();
    
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
            },
            _ => acc,
        }
    };

    Json(StatusResponse {
        status,
        duration_seconds: duration.as_secs(),
    })
}

#[derive(serde::Serialize)]
struct AudioLevelsResponse {
    input_level: u32,
    output_level: u32,
    is_recording: bool,
}

async fn get_audio_levels(State(state): State<Arc<AppState>>) -> Json<AudioLevelsResponse> {
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

async fn start_recording(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<StartRequest>,
) -> Result<Json<StartResponse>, StatusCode> {
    // Update token if provided
    if let Some(token) = &payload.token {
        let mut config = state.config.lock().unwrap();
        config.api_token = token.clone();
    }

    // Get config for request
    let (api_url, api_token) = {
        let config = state.config.lock().unwrap();
        (config.api_url.clone(), config.api_token.clone())
    };

    // 1. Call Backend to Init
    let client = reqwest::Client::new();
    let url = format!("{}/recordings/init", api_url);
    let res = client.post(&url)
        .header("Authorization", format!("Bearer {}", api_token))
        .query(&[("name", &payload.name)])
        .send()
        .await
        .map_err(|e| {
            eprintln!("Failed to init recording: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;
        
    if !res.status().is_success() {
        eprintln!("Backend returned error: {}", res.status());
        return Err(StatusCode::INTERNAL_SERVER_ERROR);
    }
    
    let json: serde_json::Value = res.json().await.map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let recording_id = json["id"].as_i64().ok_or(StatusCode::INTERNAL_SERVER_ERROR)?;
    
    // 2. Update State
    {
        let mut status = state.status.lock().unwrap();
        *status = AppStatus::Recording;
        let mut id = state.current_recording_id.lock().unwrap();
        *id = Some(recording_id);
        let mut seq = state.current_sequence.lock().unwrap();
        *seq = 1;
        
        // Reset timing
        let mut start_time = state.recording_start_time.lock().unwrap();
        *start_time = Some(SystemTime::now());
        let mut acc = state.accumulated_duration.lock().unwrap();
        *acc = std::time::Duration::new(0, 0);
    }
    
    // 2. Send Start Command to Audio Thread
    state.audio_command_tx.send(AudioCommand::Start(recording_id)).unwrap();
    
    // Notify Backend of Status
    let config_clone = state.config.lock().unwrap().clone();
    tokio::spawn(async move {
        if let Err(e) = uploader::update_client_status(recording_id, "RECORDING", &config_clone).await {
            eprintln!("Failed to update client status: {}", e);
        }
    });
    
    notifications::show_notification("Recording Started", "Nojoin is now recording.");

    Ok(Json(StartResponse {
        id: recording_id,
        message: "Recording started".to_string(),
    }))
}

#[derive(serde::Deserialize)]
struct StopRequest {
    token: Option<String>,
}

async fn stop_recording(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<Option<StopRequest>>,
) -> Result<Json<String>, StatusCode> {
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
                eprintln!("Failed to update client status: {}", e);
            }
        });
    }

    notifications::show_notification("Recording Stopped", "Processing audio...");
    Ok(Json("Stopped".to_string()))
}

async fn pause_recording(State(state): State<Arc<AppState>>) -> Result<Json<String>, StatusCode> {
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
                eprintln!("Failed to update client status: {}", e);
            }
        });
    }
    
    notifications::show_notification("Recording Paused", "Recording paused.");
    Ok(Json("Paused".to_string()))
}

async fn resume_recording(State(state): State<Arc<AppState>>) -> Result<Json<String>, StatusCode> {
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
                eprintln!("Failed to update client status: {}", e);
            }
        });
    }

    notifications::show_notification("Recording Resumed", "Recording resumed.");
    Ok(Json("Resumed".to_string()))
}

async fn get_config(State(state): State<Arc<AppState>>) -> Json<Config> {
    let config = state.config.lock().unwrap().clone();
    Json(config)
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

async fn get_devices(State(state): State<Arc<AppState>>) -> Json<DevicesResponse> {
    let host = cpal::default_host();
    
    let default_input_name = host.default_input_device()
        .and_then(|d| d.name().ok());
    let default_output_name = host.default_output_device()
        .and_then(|d| d.name().ok());
    
    let input_devices: Vec<AudioDevice> = host.input_devices()
        .map(|devices| {
            devices.filter_map(|d| {
                d.name().ok().map(|name| AudioDevice {
                    is_default: Some(&name) == default_input_name.as_ref(),
                    name,
                })
            }).collect()
        })
        .unwrap_or_default();
    
    let output_devices: Vec<AudioDevice> = host.output_devices()
        .map(|devices| {
            devices.filter_map(|d| {
                d.name().ok().map(|name| AudioDevice {
                    is_default: Some(&name) == default_output_name.as_ref(),
                    name,
                })
            }).collect()
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
    api_url: Option<String>,
    api_token: Option<String>,
    input_device_name: Option<String>,
    output_device_name: Option<String>,
}

async fn update_config(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<ConfigUpdate>,
) -> Result<Json<Config>, StatusCode> {
    let mut config = state.config.lock().unwrap();
    
    if let Some(url) = payload.api_url {
        config.api_url = url;
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
    
    Ok(Json(config.clone()))
}
