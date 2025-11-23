use axum::{
    routing::{get, post},
    Router, Json, extract::State, http::StatusCode,
};
use std::sync::Arc;
use tower_http::cors::CorsLayer;
use crate::state::{AppState, AppStatus, AudioCommand};

pub async fn start_server(state: Arc<AppState>) {
    let app = Router::new()
        .route("/status", get(get_status))
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

async fn get_status(State(state): State<Arc<AppState>>) -> Json<AppStatus> {
    let status = state.status.lock().unwrap().clone();
    Json(status)
}

#[derive(serde::Deserialize)]
struct StartRequest {
    name: String,
    token: Option<String>,
}

#[derive(serde::Serialize)]
struct StartResponse {
    id: i32,
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
    let recording_id = json["id"].as_i64().ok_or(StatusCode::INTERNAL_SERVER_ERROR)? as i32;
    
    // 2. Update State
    {
        let mut status = state.status.lock().unwrap();
        *status = AppStatus::Recording;
        let mut id = state.current_recording_id.lock().unwrap();
        *id = Some(recording_id);
        let mut seq = state.current_sequence.lock().unwrap();
        *seq = 1;
    }
    
    // 3. Send Command to Audio Thread
    state.audio_command_tx.send(AudioCommand::Start(recording_id)).unwrap();
    
    Ok(Json(StartResponse {
        id: recording_id,
        message: "Started".to_string(),
    }))
}

async fn stop_recording(State(state): State<Arc<AppState>>) -> Result<Json<String>, StatusCode> {
    {
        let mut status = state.status.lock().unwrap();
        *status = AppStatus::Idle;
    }
    state.audio_command_tx.send(AudioCommand::Stop).unwrap();
    Ok(Json("Stopped".to_string()))
}

async fn pause_recording(State(state): State<Arc<AppState>>) -> Result<Json<String>, StatusCode> {
    {
        let mut status = state.status.lock().unwrap();
        *status = AppStatus::Paused;
    }
    state.audio_command_tx.send(AudioCommand::Pause).unwrap();
    Ok(Json("Paused".to_string()))
}

async fn resume_recording(State(state): State<Arc<AppState>>) -> Result<Json<String>, StatusCode> {
    {
        let mut status = state.status.lock().unwrap();
        *status = AppStatus::Recording;
        let mut seq = state.current_sequence.lock().unwrap();
        *seq += 1;
    }
    state.audio_command_tx.send(AudioCommand::Resume).unwrap();
    Ok(Json("Resumed".to_string()))
}
