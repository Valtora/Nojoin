use crate::config::{BackendConnection, Config, MachineLocalUpdate};
use crate::notifications;
use crate::state::{
    pairing_block_message, ActiveRecordingOwner, AppState, AppStatus, AudioCommand,
    PairingValidationError, RecordingRecoveryState,
};
use crate::uploader;
use axum::debug_handler;
use axum::{
    extract::State,
    http::{header, HeaderMap, HeaderValue, StatusCode, uri::Authority},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use cpal::traits::{DeviceTrait, HostTrait};
use jsonwebtoken::{decode, errors::ErrorKind, Algorithm, DecodingKey, Validation};
use log::{error, info};
use reqwest::{Method, Url};
use std::collections::HashSet;
use std::str::FromStr;
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
                    if canonicalize_loopback_host(
                        request_parts.headers.get("host"),
                        config.local_port(),
                    )
                    .is_none()
                    {
                        return false;
                    }

                    if request_parts.uri.path() == "/pair/complete" {
                        return cors_state.is_pairing_active()
                            && canonicalize_origin_value(origin_str).is_some();
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

const LOCAL_CONTROL_TOKEN_TYPE: &str = "companion_local_control";
const LOCAL_CONTROL_AUDIENCE: &str = "nojoin-companion-local";
const LOCAL_CONTROL_STATUS_READ_ACTION: &str = "status:read";
const LOCAL_CONTROL_SETTINGS_READ_ACTION: &str = "settings:read";
const LOCAL_CONTROL_SETTINGS_WRITE_ACTION: &str = "settings:write";
const LOCAL_CONTROL_DEVICES_READ_ACTION: &str = "devices:read";
const LOCAL_CONTROL_WAVEFORM_READ_ACTION: &str = "waveform:read";
const LOCAL_CONTROL_RECORDING_START_ACTION: &str = "recording:start";
const LOCAL_CONTROL_RECORDING_STOP_ACTION: &str = "recording:stop";
const LOCAL_CONTROL_RECORDING_PAUSE_ACTION: &str = "recording:pause";
const LOCAL_CONTROL_RECORDING_RESUME_ACTION: &str = "recording:resume";
const LOCAL_CONTROL_UPDATE_TRIGGER_ACTION: &str = "update:trigger";

#[derive(Debug, Clone, serde::Deserialize, serde::Serialize)]
struct LocalControlClaims {
    aud: String,
    sub: String,
    user_id: i64,
    username: String,
    origin: String,
    actions: Vec<String>,
    exp: usize,
    iat: usize,
    token_type: String,
    #[serde(rename = "companion_pairing_id")]
    companion_pairing_id: String,
    secret_version: u32,
}

#[derive(Debug, Clone)]
struct LocalRequestGuard {
    #[allow(dead_code)]
    claims: LocalControlClaims,
    #[allow(dead_code)]
    origin: String,
    #[allow(dead_code)]
    host: String,
}

#[derive(Debug, serde::Serialize)]
struct LocalGuardErrorResponse {
    error: &'static str,
    message: String,
}

#[derive(Debug)]
struct LocalGuardRejection {
    status: StatusCode,
    error: &'static str,
    message: String,
}

type LocalApiResult<T> = Result<T, LocalGuardRejection>;

impl LocalGuardRejection {
    fn unauthenticated(error: &'static str, message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::UNAUTHORIZED,
            error,
            message: message.into(),
        }
    }

    fn forbidden(error: &'static str, message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::FORBIDDEN,
            error,
            message: message.into(),
        }
    }

    fn conflict(error: &'static str, message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::CONFLICT,
            error,
            message: message.into(),
        }
    }

    fn internal(error: &'static str, message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            error,
            message: message.into(),
        }
    }
}

impl IntoResponse for LocalGuardRejection {
    fn into_response(self) -> Response {
        let mut response = (
            self.status,
            Json(LocalGuardErrorResponse {
                error: self.error,
                message: self.message,
            }),
        )
            .into_response();

        if self.status == StatusCode::UNAUTHORIZED {
            response.headers_mut().insert(
                header::WWW_AUTHENTICATE,
                HeaderValue::from_static("Bearer"),
            );
        }

        response
    }
}

fn is_standard_origin_port(protocol: &str, port: u16) -> bool {
    (protocol.eq_ignore_ascii_case("https") && port == 443)
        || (protocol.eq_ignore_ascii_case("http") && port == 80)
}

fn format_host_for_origin(host: &str) -> String {
    if host.contains(':') && !host.starts_with('[') && !host.ends_with(']') {
        format!("[{}]", host)
    } else {
        host.to_string()
    }
}

fn canonicalize_origin_value(origin: &str) -> Option<String> {
    let url = Url::parse(origin.trim()).ok()?;
    let scheme = url.scheme().trim().to_ascii_lowercase();
    if scheme != "http" && scheme != "https" {
        return None;
    }

    let host = url.host_str()?.to_string();
    let port = url.port_or_known_default()? as u16;
    let formatted_host = format_host_for_origin(&host);

    if is_standard_origin_port(&scheme, port) {
        Some(format!("{}://{}", scheme, formatted_host))
    } else {
        Some(format!("{}://{}:{}", scheme, formatted_host, port))
    }
}

fn canonicalize_loopback_host(
    host_header: Option<&HeaderValue>,
    expected_port: u16,
) -> Option<String> {
    let host = host_header?.to_str().ok()?.trim();
    let authority = Authority::from_str(host).ok()?;
    let port = authority.port_u16()?;
    if port != expected_port {
        return None;
    }

    let normalized_host = authority
        .host()
        .trim_matches(|value| value == '[' || value == ']')
        .to_ascii_lowercase();

    let canonical_host = match normalized_host.as_str() {
        "localhost" => "localhost".to_string(),
        "127.0.0.1" => "127.0.0.1".to_string(),
        "::1" => "::1".to_string(),
        _ => return None,
    };

    if canonical_host.contains(':') {
        Some(format!("[{}]:{}", canonical_host, port))
    } else {
        Some(format!("{}:{}", canonical_host, port))
    }
}

fn is_allowed_origin_value(origin: &str, config: &Config) -> bool {
    let expected_origin = match config.paired_web_origin() {
        Some(origin) => origin,
        None => return false,
    };

    canonicalize_origin_value(origin)
        .map(|value| value == expected_origin)
        .unwrap_or(false)
}

fn ensure_loopback_request(headers: &HeaderMap, config: &Config) -> Result<String, LocalGuardRejection> {
    canonicalize_loopback_host(headers.get("host"), config.local_port()).ok_or_else(|| {
        LocalGuardRejection::forbidden(
            "invalid_local_host",
            "Requests must target the configured loopback Companion host.",
        )
    })
}

fn ensure_authenticated_origin(
    headers: &HeaderMap,
    config: &Config,
) -> Result<String, LocalGuardRejection> {
    let expected_origin = config.paired_web_origin().ok_or_else(|| {
        LocalGuardRejection::conflict(
            "local_pairing_conflict",
            "Companion pairing state is missing or expired. Pair again from Nojoin.",
        )
    })?;

    let origin = headers
        .get("origin")
        .ok_or_else(|| {
            LocalGuardRejection::forbidden(
                "invalid_local_origin",
                "Requests must include the paired web Origin header.",
            )
        })?
        .to_str()
        .ok()
        .and_then(canonicalize_origin_value)
        .ok_or_else(|| {
            LocalGuardRejection::forbidden(
                "invalid_local_origin",
                "Requests must include a valid paired web Origin header.",
            )
        })?;

    if origin != expected_origin {
        return Err(LocalGuardRejection::forbidden(
            "invalid_local_origin",
            "This origin is not allowed to control the Companion app.",
        ));
    }

    Ok(origin)
}

fn validate_local_control_token(
    headers: &HeaderMap,
    config: &Config,
    required_action: &'static str,
) -> Result<LocalControlClaims, LocalGuardRejection> {
    let backend = config.backend_connection().ok_or_else(|| {
        LocalGuardRejection::conflict(
            "local_pairing_conflict",
            "Companion pairing state is missing or expired. Pair again from Nojoin.",
        )
    })?;

    let paired_origin = backend.paired_web_origin.clone().ok_or_else(|| {
        LocalGuardRejection::conflict(
            "local_pairing_conflict",
            "Companion pairing state is missing or expired. Pair again from Nojoin.",
        )
    })?;
    let local_control_secret = backend.local_control_secret.clone().ok_or_else(|| {
        LocalGuardRejection::conflict(
            "local_pairing_conflict",
            "Companion pairing state is missing or expired. Pair again from Nojoin.",
        )
    })?;
    let pairing_id = backend.backend_pairing_id.clone().ok_or_else(|| {
        LocalGuardRejection::conflict(
            "local_pairing_conflict",
            "Companion pairing state is missing or expired. Pair again from Nojoin.",
        )
    })?;
    let secret_version = backend.local_control_secret_version.ok_or_else(|| {
        LocalGuardRejection::conflict(
            "local_pairing_conflict",
            "Companion pairing state is missing or expired. Pair again from Nojoin.",
        )
    })?;

    let auth_header = headers.get(header::AUTHORIZATION).ok_or_else(|| {
        LocalGuardRejection::unauthenticated(
            "missing_local_control_token",
            "Local control bearer token is required.",
        )
    })?;
    let auth_value = auth_header.to_str().map_err(|_| {
        LocalGuardRejection::forbidden(
            "invalid_local_control_token",
            "Local control token header is malformed.",
        )
    })?;
    let (scheme, token) = auth_value.split_once(' ').ok_or_else(|| {
        LocalGuardRejection::unauthenticated(
            "missing_local_control_token",
            "Local control bearer token is required.",
        )
    })?;
    if !scheme.eq_ignore_ascii_case("Bearer") || token.trim().is_empty() {
        return Err(LocalGuardRejection::unauthenticated(
            "missing_local_control_token",
            "Local control bearer token is required.",
        ));
    }

    let mut validation = Validation::new(Algorithm::HS256);
    validation.set_audience(&[LOCAL_CONTROL_AUDIENCE]);
    validation.leeway = 0;
    validation.required_spec_claims = HashSet::from([
        "aud".to_string(),
        "exp".to_string(),
        "sub".to_string(),
        "token_type".to_string(),
    ]);

    let claims = match decode::<LocalControlClaims>(
        token.trim(),
        &DecodingKey::from_secret(local_control_secret.as_bytes()),
        &validation,
    ) {
        Ok(decoded) => decoded.claims,
        Err(error) => match error.kind() {
            ErrorKind::ExpiredSignature => {
                return Err(LocalGuardRejection::unauthenticated(
                    "expired_local_control_token",
                    "Local control token has expired.",
                ));
            }
            _ => {
                return Err(LocalGuardRejection::forbidden(
                    "invalid_local_control_token",
                    "Local control token is invalid.",
                ));
            }
        },
    };

    if claims.token_type != LOCAL_CONTROL_TOKEN_TYPE {
        return Err(LocalGuardRejection::forbidden(
            "invalid_local_control_token",
            "Local control token type is invalid.",
        ));
    }
    if claims.origin != paired_origin {
        return Err(LocalGuardRejection::forbidden(
            "wrong_local_control_origin",
            "Local control token origin does not match the paired backend origin.",
        ));
    }
    if claims.companion_pairing_id != pairing_id || claims.secret_version != secret_version {
        return Err(LocalGuardRejection::conflict(
            "local_pairing_conflict",
            "Companion pairing state is stale or rotated. Pair again from Nojoin.",
        ));
    }
    if !claims.actions.iter().any(|action| action == required_action) {
        return Err(LocalGuardRejection::forbidden(
            "invalid_local_control_token",
            "Local control token does not allow this route.",
        ));
    }

    Ok(claims)
}

fn guard_steady_state_request(
    headers: &HeaderMap,
    config: &Config,
    required_action: &'static str,
) -> Result<LocalRequestGuard, LocalGuardRejection> {
    let host = ensure_loopback_request(headers, config)?;
    let origin = ensure_authenticated_origin(headers, config)?;
    let claims = validate_local_control_token(headers, config, required_action)?;

    Ok(LocalRequestGuard {
        claims,
        origin,
        host,
    })
}

fn ensure_same_recording_owner(
    owner: Option<&ActiveRecordingOwner>,
    claims: &LocalControlClaims,
    action: &'static str,
) -> Result<(), LocalGuardRejection> {
    let owner = owner.ok_or_else(|| {
        LocalGuardRejection::conflict(
            "recording_owner_missing",
            "Active recording ownership metadata is missing. Start a new recording before retrying recording controls.",
        )
    })?;

    if owner.companion_pairing_id != claims.companion_pairing_id {
        return Err(LocalGuardRejection::conflict(
            "recording_owner_conflict",
            "Active recording ownership no longer matches the current Companion pairing.",
        ));
    }

    if owner.user_id != claims.user_id {
        return Err(LocalGuardRejection::forbidden(
            "recording_owner_mismatch",
            format!(
                "Only the user who started this recording can {} it.",
                action
            ),
        ));
    }

    Ok(())
}

#[derive(Clone)]
pub struct RecordingStatusUpdate {
    recording_id: i64,
    status: &'static str,
    config: Config,
    token: Option<String>,
    state: Arc<AppState>,
}

fn active_recording_id(state: &Arc<AppState>) -> Result<i64, String> {
    (*state.current_recording_id.lock().unwrap())
        .ok_or_else(|| "No active recording is currently running.".to_string())
}

fn build_recording_status_update(
    state: &Arc<AppState>,
    recording_id: i64,
    status: &'static str,
) -> RecordingStatusUpdate {
    RecordingStatusUpdate {
        recording_id,
        status,
        config: state.config.lock().unwrap().clone(),
        token: state.current_recording_token.lock().unwrap().clone(),
        state: state.clone(),
    }
}

pub fn spawn_recording_status_update(update: RecordingStatusUpdate) {
    tauri::async_runtime::spawn(async move {
        if let Some(token) = update.token {
            match uploader::update_client_status(
                update.recording_id,
                update.status,
                &update.config,
                &token,
            )
            .await
            {
                Ok(Some(new_token)) => {
                    *update.state.current_recording_token.lock().unwrap() = Some(new_token);
                }
                Ok(None) => {}
                Err(error) => {
                    error!(
                        "Failed to update client status {} for recording {}: {}",
                        update.status,
                        update.recording_id,
                        error
                    );
                }
            }
        } else {
            error!(
                "Missing recording upload token while reporting {} for recording {}",
                update.status,
                update.recording_id
            );
        }
    });
}

pub fn pause_recording_locally(state: &Arc<AppState>) -> Result<RecordingStatusUpdate, String> {
    let recording_id = active_recording_id(state)?;

    {
        let mut status = state.status.lock().unwrap();
        if *status != AppStatus::Recording {
            return Err(match *status {
                AppStatus::Paused => "Recording is already paused.".to_string(),
                _ => "No active recording is currently running.".to_string(),
            });
        }

        *status = AppStatus::Paused;

        let mut start_time = state.recording_start_time.lock().unwrap();
        if let Some(started_at) = *start_time {
            if let Ok(elapsed) = started_at.elapsed() {
                let mut accumulated = state.accumulated_duration.lock().unwrap();
                *accumulated += elapsed;
            }
        }
        *start_time = None;
    }

    state
        .audio_command_tx
        .send(AudioCommand::Pause)
        .map_err(|err| format!("Failed to pause recording: {}", err))?;
    state.clear_recording_recovery_state();

    Ok(build_recording_status_update(state, recording_id, "PAUSED"))
}

pub fn resume_recording_locally(state: &Arc<AppState>) -> Result<RecordingStatusUpdate, String> {
    let recording_id = active_recording_id(state)?;

    if state.recording_recovery_state() != RecordingRecoveryState::None {
        return Err("Recording cannot resume until Nojoin reconnects.".to_string());
    }

    {
        let mut status = state.status.lock().unwrap();
        if *status != AppStatus::Paused {
            return Err(match *status {
                AppStatus::Recording => "Recording is already running.".to_string(),
                _ => "No paused recording is currently available.".to_string(),
            });
        }

        *status = AppStatus::Recording;
        let mut sequence = state.current_sequence.lock().unwrap();
        *sequence += 1;
        *state.recording_start_time.lock().unwrap() = Some(SystemTime::now());
    }

    state
        .audio_command_tx
        .send(AudioCommand::Resume)
        .map_err(|err| format!("Failed to resume recording: {}", err))?;
    state.clear_recording_recovery_state();

    Ok(build_recording_status_update(state, recording_id, "RECORDING"))
}

pub fn stop_recording_locally(
    state: &Arc<AppState>,
    queue_upload_until_reconnect: bool,
) -> Result<RecordingStatusUpdate, String> {
    let recording_id = active_recording_id(state)?;

    {
        let mut status = state.status.lock().unwrap();
        if *status != AppStatus::Recording && *status != AppStatus::Paused {
            return Err("No active recording is currently running.".to_string());
        }

        *status = AppStatus::Uploading;
    }

    state
        .audio_command_tx
        .send(AudioCommand::Stop)
        .map_err(|err| format!("Failed to stop recording: {}", err))?;
    if queue_upload_until_reconnect {
        state.set_recording_recovery_state(RecordingRecoveryState::StopRequested);
    } else {
        state.clear_recording_recovery_state();
    }

    Ok(build_recording_status_update(state, recording_id, "UPLOADING"))
}

pub fn mark_recording_waiting_for_reconnect(state: &Arc<AppState>) -> Result<bool, String> {
    if state.current_recording_id.lock().unwrap().is_none() {
        return Err("No active recording is currently running.".to_string());
    }

    let status = state.status.lock().unwrap().clone();
    let next_recovery_state = match status {
        AppStatus::Recording | AppStatus::Paused => RecordingRecoveryState::WaitingForReconnect,
        AppStatus::Uploading => RecordingRecoveryState::StopRequested,
        _ => return Err("No active recording is currently running.".to_string()),
    };

    if state.recording_recovery_state() == next_recovery_state {
        return Ok(false);
    }

    state.set_recording_recovery_state(next_recovery_state);
    Ok(true)
}

pub fn restore_recording_after_reconnect(state: &Arc<AppState>) -> RecordingRecoveryState {
    let previous_state = state.recording_recovery_state();
    if previous_state != RecordingRecoveryState::None {
        state.clear_recording_recovery_state();
    }

    previous_state
}

async fn get_status(
    headers: HeaderMap,
    State(context): State<ServerContext>,
) -> LocalApiResult<Json<StatusResponse>> {
    let state = &context.state;

    {
        let config = state.config.lock().unwrap();
        let _guard = guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_STATUS_READ_ACTION,
        )?;
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
    local_control_secret: Option<String>,
    backend_pairing_id: Option<String>,
    local_control_secret_version: Option<u32>,
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

#[derive(serde::Deserialize, Default)]
struct PairingManagementResponse {
    revoked_count: Option<u64>,
    cancelled_count: Option<u64>,
}

async fn send_pairing_management_request(
    method: Method,
    protocol: &str,
    host: &str,
    port: u16,
    token: &str,
    tls_fingerprint: Option<String>,
    path: &str,
) -> Result<u64, String> {
    let url = format!("{}://{}:{}/api/v1{}", protocol, host, port, path);
    let client = crate::tls::create_client(tls_fingerprint).map_err(|err| err.to_string())?;

    let response = client
        .request(method, url)
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .map_err(|err| err.to_string())?;

    let status = response.status();
    let body = response.text().await.map_err(|err| err.to_string())?;
    if !status.is_success() {
        return Err(format!("Pairing management request failed with status {}", status));
    }

    let parsed: PairingManagementResponse = serde_json::from_str(&body).unwrap_or_default();
    Ok(parsed
        .revoked_count
        .or(parsed.cancelled_count)
        .unwrap_or_default())
}

async fn cancel_pending_pairing_from_request(
    payload: &PairingCompleteRequest,
) -> Result<u64, String> {
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

    send_pairing_management_request(
        Method::DELETE,
        protocol,
        host,
        port,
        &payload.bootstrap_token,
        payload.tls_fingerprint.clone(),
        "/login/companion-pairing/pending",
    )
    .await
}

pub async fn cancel_pending_pairing_for_backend(
    backend: &BackendConnection,
) -> Result<u64, String> {
    send_pairing_management_request(
        Method::DELETE,
        &backend.api_protocol,
        &backend.api_host,
        backend.api_port,
        &backend.api_token,
        backend.tls_fingerprint.clone(),
        "/login/companion-pairing/pending",
    )
    .await
}

pub async fn revoke_backend_pairings(backend: &BackendConnection) -> Result<u64, String> {
    send_pairing_management_request(
        Method::DELETE,
        &backend.api_protocol,
        &backend.api_host,
        backend.api_port,
        &backend.api_token,
        backend.tls_fingerprint.clone(),
        "/login/companion-pairing",
    )
    .await
}

pub async fn signal_explicit_backend_disconnect(
    backend: &BackendConnection,
) -> Result<u64, String> {
    send_pairing_management_request(
        Method::POST,
        &backend.api_protocol,
        &backend.api_host,
        backend.api_port,
        &backend.api_token,
        backend.tls_fingerprint.clone(),
        "/login/companion-pairing/disconnect",
    )
    .await
}

fn is_same_backend_target(previous: &BackendConnection, replacement: &BackendConnection) -> bool {
    previous.api_protocol.eq_ignore_ascii_case(&replacement.api_protocol)
        && previous.api_host.eq_ignore_ascii_case(&replacement.api_host)
        && previous.api_port == replacement.api_port
        && previous.paired_web_origin == replacement.paired_web_origin
}

#[debug_handler]
async fn complete_pairing(
    State(context): State<ServerContext>,
    headers: HeaderMap,
    Json(payload): Json<PairingCompleteRequest>,
) -> (StatusCode, Json<PairingCompleteResponse>) {
    let state = &context.state;
    info!("Received pairing completion request");

    {
        let config = state.config.lock().unwrap();
        if ensure_loopback_request(&headers, &config).is_err() {
            return (
                StatusCode::FORBIDDEN,
                Json(PairingCompleteResponse {
                    success: false,
                    message: "Pairing requests must target the local loopback server.".to_string(),
                }),
            );
        }
    }

    let origin = match headers
        .get("origin")
        .and_then(|value| value.to_str().ok())
        .and_then(canonicalize_origin_value)
    {
        Some(origin) => origin,
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
        if let Some(message) = pairing_block_message(&status) {
            return (
                StatusCode::CONFLICT,
                Json(PairingCompleteResponse {
                    success: false,
                    message: message.to_string(),
                }),
            );
        }
    }

    match state.begin_pairing_completion(&payload.pairing_code) {
        Ok(()) => {}
        Err(PairingValidationError::NotActive) => {
            if let Err(err) = cancel_pending_pairing_from_request(&payload).await {
                error!("Failed to cancel pending pairing after inactive request: {}", err);
            }
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
            if let Err(err) = cancel_pending_pairing_from_request(&payload).await {
                error!("Failed to cancel pending pairing after expiry: {}", err);
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
            if let Err(err) = cancel_pending_pairing_from_request(&payload).await {
                error!("Failed to cancel pending pairing after invalid code: {}", err);
            }
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
            if let Err(err) = cancel_pending_pairing_from_request(&payload).await {
                error!("Failed to cancel pending pairing after lockout: {}", err);
            }
            notifications::show_notification(
                &context.app_handle,
                "Pairing Failed",
                "Too many invalid pairing attempts were made. Start pairing again from Companion Settings.",
            );
            return (
                StatusCode::TOO_MANY_REQUESTS,
                Json(PairingCompleteResponse {
                    success: false,
                    message: "Too many invalid pairing attempts. Start pairing again in the Companion app.".to_string(),
                }),
            );
        }
        Err(PairingValidationError::InProgress) => {
            return (
                StatusCode::CONFLICT,
                Json(PairingCompleteResponse {
                    success: false,
                    message: "A pairing completion is already in progress.".to_string(),
                }),
            );
        }
    }

    if let Err(err) = validate_bootstrap_token(&payload).await {
        error!("Bootstrap token validation failed: {}", err);
        state.release_pairing_completion();
        if let Err(cancel_err) = cancel_pending_pairing_from_request(&payload).await {
            error!(
                "Failed to cancel pending pairing after bootstrap validation failure: {}",
                cancel_err
            );
        }
        return (
            StatusCode::UNAUTHORIZED,
            Json(PairingCompleteResponse {
                success: false,
                message: "The backend bootstrap token is invalid or expired. Start pairing again from Nojoin.".to_string(),
            }),
        );
    }

    let backend = BackendConnection {
        api_protocol: payload.api_protocol.clone().unwrap_or_default(),
        api_host: payload.api_host.clone().unwrap_or_default(),
        api_port: payload.api_port.unwrap_or_default(),
        api_token: payload.bootstrap_token.clone(),
        tls_fingerprint: payload.tls_fingerprint.clone(),
        paired_web_origin: Some(origin.clone()),
        local_control_secret: payload.local_control_secret.clone(),
        backend_pairing_id: payload.backend_pairing_id.clone(),
        local_control_secret_version: payload.local_control_secret_version,
    };

    let previous_backend = {
        let config = state.config.lock().unwrap();
        config.backend_connection()
    };
    let had_existing_backend = previous_backend.is_some();
    let should_revoke_previous_backend = previous_backend
        .as_ref()
        .map(|existing| !is_same_backend_target(existing, &backend))
        .unwrap_or(false);

    let save_result = {
        let mut config = state.config.lock().unwrap();
        config.replace_backend_and_save(backend.clone())
    };

    if let Err(e) = save_result {
        error!("Failed to save config: {}", e);
        state.release_pairing_completion();
        if let Err(cancel_err) = cancel_pending_pairing_from_request(&payload).await {
            error!(
                "Failed to cancel pending pairing after local save failure: {}",
                cancel_err
            );
        }
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(PairingCompleteResponse {
                success: false,
                message: format!("Failed to save pairing config: {}", e),
            }),
        );
    }

    state.complete_pairing_session();
    if let Some(window) = context.app_handle.get_webview_window("pairing") {
        let _ = window.close();
    }

    *state.current_recording_id.lock().unwrap() = None;
    *state.current_recording_token.lock().unwrap() = None;
    state.clear_current_recording_owner();
    state.clear_recording_recovery_state();
    *state.current_sequence.lock().unwrap() = 1;

    if should_revoke_previous_backend {
        if let Some(previous_backend) = previous_backend {
            if let Err(revoke_err) = revoke_backend_pairings(&previous_backend).await {
                error!(
                    "Failed to revoke pairing state on the previous backend after successful re-pair: {}",
                    revoke_err
                );
            }
        }
    }

    info!(
        "Companion pairing completed successfully for origin {}",
        origin
    );
    let (notification_title, notification_body, success_message) = if should_revoke_previous_backend {
        (
            "Backend Switch Complete",
            "Companion is now paired with this Nojoin deployment. Future recordings and local controls will use this backend.",
            "Backend switch completed successfully.",
        )
    } else if had_existing_backend {
        (
            "Pairing Refreshed",
            "Companion pairing was refreshed for this Nojoin deployment.",
            "Pairing refreshed successfully.",
        )
    } else {
        (
            "Pairing Complete",
            "Companion is now paired with this Nojoin deployment.",
            "Pairing completed successfully.",
        )
    };
    notifications::show_notification(
        &context.app_handle,
        notification_title,
        notification_body,
    );
    crate::refresh_tray_menu(&context.app_handle, state);

    (
        StatusCode::OK,
        Json(PairingCompleteResponse {
            success: true,
            message: success_message.to_string(),
        }),
    )
}

async fn deprecated_authorize(
    headers: HeaderMap,
    State(context): State<ServerContext>,
) -> LocalApiResult<(StatusCode, Json<PairingCompleteResponse>)> {
    {
        let config = context.state.config.lock().unwrap();
        let _host = ensure_loopback_request(&headers, &config)?;
    }

    Ok((
        StatusCode::GONE,
        Json(PairingCompleteResponse {
            success: false,
            message: "Manual pairing now requires /pair/complete with a Companion-generated code."
                .to_string(),
        }),
    ))
}

#[derive(serde::Serialize)]
struct AudioLevelsResponse {
    input_level: u32,
    output_level: u32,
    is_recording: bool,
}

async fn get_audio_levels(
    headers: HeaderMap,
    State(context): State<ServerContext>,
) -> LocalApiResult<Json<AudioLevelsResponse>> {
    let state = &context.state;

    {
        let config = state.config.lock().unwrap();
        let _guard = guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_WAVEFORM_READ_ACTION,
        )?;
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
    headers: HeaderMap,
    State(context): State<ServerContext>,
) -> LocalApiResult<Json<AudioLevelsResponse>> {
    let state = &context.state;

    {
        let config = state.config.lock().unwrap();
        let _guard = guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_WAVEFORM_READ_ACTION,
        )?;
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
    headers: HeaderMap,
    State(context): State<ServerContext>,
    Json(payload): Json<StartRequest>,
) -> LocalApiResult<(StatusCode, Json<StartResponse>)> {
    let state = &context.state;
    info!("Received start_recording request for '{}'", payload.name);

    let guard = {
        let config = state.config.lock().unwrap();
        guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_RECORDING_START_ACTION,
        )?
    };

    // Check status (and drop lock immediately)
    {
        let status = state.status.lock().unwrap();
        if *status != AppStatus::Idle && *status != AppStatus::BackendOffline {
            return Ok((
                StatusCode::CONFLICT,
                Json(StartResponse {
                    id: 0,
                    message: "Recording is already active or busy.".to_string(),
                }),
            ));
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
                        return Ok((
                            StatusCode::INTERNAL_SERVER_ERROR,
                            Json(StartResponse {
                                id: 0,
                                message: "Recording upload token missing".to_string(),
                            }),
                        ));
                    }

                    // Start Audio Thread
                    *state.current_recording_id.lock().unwrap() = Some(id);
                    *state.current_recording_token.lock().unwrap() = upload_token;
                    state.set_current_recording_owner(ActiveRecordingOwner {
                        user_id: guard.claims.user_id,
                        username: guard.claims.username.clone(),
                        companion_pairing_id: guard.claims.companion_pairing_id.clone(),
                    });
                    state.clear_recording_recovery_state();
                    *state.current_sequence.lock().unwrap() = 1;
                    *state.recording_start_time.lock().unwrap() = Some(SystemTime::now());
                    *state.accumulated_duration.lock().unwrap() = Duration::new(0, 0);

                    state
                        .audio_command_tx
                        .send(AudioCommand::Start(id))
                        .unwrap();

                    {
                        let mut status = state.status.lock().unwrap();
                        *status = AppStatus::Recording;
                    }

                    notifications::show_notification(
                        &context.app_handle,
                        "Recording Started",
                        &format!("Recording '{}' started.", recording_name),
                    );
                    crate::refresh_tray_menu(&context.app_handle, state);
                    info!("Recording started successfully. ID: {}", id);

                    return Ok((
                        StatusCode::OK,
                        Json(StartResponse {
                            id,
                            message: "Recording started".to_string(),
                        }),
                    ));
                }
            }
        }
        Err(e) => {
            error!("Failed to start recording on backend: {}", e);
        }
    }

    Ok((
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(StartResponse {
            id: 0,
            message: "Failed to start recording".to_string(),
        }),
    ))
}

async fn stop_recording(
    headers: HeaderMap,
    State(context): State<ServerContext>,
) -> LocalApiResult<Json<String>> {
    let state = &context.state;
    info!("Received stop_recording request");

    let guard = {
        let config = state.config.lock().unwrap();
        guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_RECORDING_STOP_ACTION,
        )?
    };

    if state.current_recording_id.lock().unwrap().is_none() {
        return Err(LocalGuardRejection::conflict(
            "recording_not_active",
            "No active recording is currently running.",
        ));
    }
    let owner = state.current_recording_owner();
    ensure_same_recording_owner(owner.as_ref(), &guard.claims, "stop")?;

    let status_update = stop_recording_locally(state, false).map_err(|message| {
        LocalGuardRejection::conflict("recording_not_active", message)
    })?;
    spawn_recording_status_update(status_update);
    crate::refresh_tray_menu(&context.app_handle, state);

    notifications::show_notification(
        &context.app_handle,
        "Recording Stopped",
        "Processing audio...",
    );
    info!("Stop command processed successfully");
    Ok(Json("Stopped".to_string()))
}

async fn pause_recording(
    headers: HeaderMap,
    State(context): State<ServerContext>,
) -> LocalApiResult<Json<String>> {
    let state = &context.state;
    info!("Received pause_recording request");

    let guard = {
        let config = state.config.lock().unwrap();
        guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_RECORDING_PAUSE_ACTION,
        )?
    };

    if state.current_recording_id.lock().unwrap().is_none() {
        return Err(LocalGuardRejection::conflict(
            "recording_not_active",
            "No active recording is currently running.",
        ));
    }
    let owner = state.current_recording_owner();
    ensure_same_recording_owner(owner.as_ref(), &guard.claims, "pause")?;

    let status_update = pause_recording_locally(state).map_err(|message| {
        LocalGuardRejection::conflict("recording_not_active", message)
    })?;
    spawn_recording_status_update(status_update);
    crate::refresh_tray_menu(&context.app_handle, state);

    notifications::show_notification(&context.app_handle, "Recording Paused", "Recording paused.");
    info!("Recording paused");
    Ok(Json("Paused".to_string()))
}

async fn resume_recording(
    headers: HeaderMap,
    State(context): State<ServerContext>,
) -> LocalApiResult<Json<String>> {
    let state = &context.state;
    info!("Received resume_recording request");

    let guard = {
        let config = state.config.lock().unwrap();
        guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_RECORDING_RESUME_ACTION,
        )?
    };

    if state.current_recording_id.lock().unwrap().is_none() {
        return Err(LocalGuardRejection::conflict(
            "recording_not_active",
            "No active recording is currently running.",
        ));
    }
    let owner = state.current_recording_owner();
    ensure_same_recording_owner(owner.as_ref(), &guard.claims, "resume")?;

    let status_update = resume_recording_locally(state).map_err(|message| {
        LocalGuardRejection::conflict("recording_not_active", message)
    })?;
    spawn_recording_status_update(status_update);
    crate::refresh_tray_menu(&context.app_handle, state);

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
    headers: HeaderMap,
    State(context): State<ServerContext>,
) -> LocalApiResult<Json<ConfigResponse>> {
    let state = &context.state;

    {
        let config = state.config.lock().unwrap();
        let _guard = guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_SETTINGS_READ_ACTION,
        )?;

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
    headers: HeaderMap,
    State(context): State<ServerContext>,
) -> LocalApiResult<Json<DevicesResponse>> {
    let state = &context.state;

    {
        let config = state.config.lock().unwrap();
        let _guard = guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_DEVICES_READ_ACTION,
        )?;
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
    headers: HeaderMap,
    State(context): State<ServerContext>,
    Json(payload): Json<ConfigUpdate>,
) -> LocalApiResult<Json<ConfigResponse>> {
    let state = &context.state;
    let mut config = state.config.lock().unwrap();

    let _guard = guard_steady_state_request(
        &headers,
        &config,
        LOCAL_CONTROL_SETTINGS_WRITE_ACTION,
    )?;

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
            return Err(LocalGuardRejection::internal(
                "local_config_save_failed",
                "Failed to save Companion settings.",
            ));
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
    headers: HeaderMap,
    State(context): State<ServerContext>,
) -> LocalApiResult<StatusCode> {
    let state = &context.state;

    {
        let config = state.config.lock().unwrap();
        let _guard = guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_UPDATE_TRIGGER_ACTION,
        )?;
    }

    let url = state.latest_update_url.lock().unwrap().clone();

    if let Some(target_url) = url {
        if let Err(e) = open::that(target_url) {
            error!("Failed to open update URL: {}", e);
            return Ok(StatusCode::INTERNAL_SERVER_ERROR);
        }
        Ok(StatusCode::OK)
    } else {
        Ok(StatusCode::NOT_FOUND)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderMap;
    use crossbeam_channel::unbounded;
    use jsonwebtoken::{encode, EncodingKey, Header};
    use std::sync::atomic::{AtomicBool, AtomicU32};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

    fn build_test_config() -> Config {
        Config {
            version: 1,
            machine_local: crate::config::MachineLocalSettings {
                local_port: 12345,
                input_device_name: None,
                output_device_name: None,
                last_version: None,
                min_meeting_length: None,
                run_on_startup: None,
            },
            backend: Some(BackendConnection {
                api_protocol: "https".to_string(),
                api_host: "localhost".to_string(),
                api_port: 14443,
                api_token: "bootstrap-token".to_string(),
                tls_fingerprint: None,
                paired_web_origin: Some("https://paired.example.com".to_string()),
                local_control_secret: Some("test-local-control-secret".to_string()),
                backend_pairing_id: Some("pairing-123".to_string()),
                local_control_secret_version: Some(3),
            }),
        }
    }

    fn now_timestamp() -> usize {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs() as usize
    }

    fn build_claims() -> LocalControlClaims {
        let now = now_timestamp();
        LocalControlClaims {
            aud: LOCAL_CONTROL_AUDIENCE.to_string(),
            sub: "alice".to_string(),
            user_id: 1,
            username: "alice".to_string(),
            origin: "https://paired.example.com".to_string(),
            actions: vec![LOCAL_CONTROL_STATUS_READ_ACTION.to_string()],
            exp: now + 120,
            iat: now,
            token_type: LOCAL_CONTROL_TOKEN_TYPE.to_string(),
            companion_pairing_id: "pairing-123".to_string(),
            secret_version: 3,
        }
    }

    fn build_recording_owner() -> ActiveRecordingOwner {
        ActiveRecordingOwner {
            user_id: 1,
            username: "alice".to_string(),
            companion_pairing_id: "pairing-123".to_string(),
        }
    }

    fn build_test_state() -> (Arc<AppState>, crossbeam_channel::Receiver<AudioCommand>) {
        let (audio_command_tx, audio_command_rx) = unbounded::<AudioCommand>();

        let state = AppState {
            status: Mutex::new(AppStatus::Idle),
            current_recording_id: Mutex::new(None),
            current_recording_token: Mutex::new(None),
            current_recording_owner: Mutex::new(None),
            recording_recovery_state: Mutex::new(RecordingRecoveryState::None),
            current_sequence: Mutex::new(1),
            audio_command_tx,
            config: Mutex::new(build_test_config()),
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
        };

        (Arc::new(state), audio_command_rx)
    }

    fn encode_token(claims: &LocalControlClaims, secret: &str) -> String {
        encode(
            &Header::default(),
            claims,
            &EncodingKey::from_secret(secret.as_bytes()),
        )
        .unwrap()
    }

    fn build_headers(
        host: &str,
        origin: &str,
        authorization: Option<String>,
    ) -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert("host", HeaderValue::from_str(host).unwrap());
        headers.insert("origin", HeaderValue::from_str(origin).unwrap());
        if let Some(value) = authorization {
            headers.insert(header::AUTHORIZATION, HeaderValue::from_str(&value).unwrap());
        }
        headers
    }

    #[test]
    fn guard_rejects_malformed_host() {
        let config = build_test_config();
        let claims = build_claims();
        let token = encode_token(&claims, "test-local-control-secret");
        let headers = build_headers(
            "http://127.0.0.1:12345",
            "https://paired.example.com",
            Some(format!("Bearer {}", token)),
        );

        let rejection = guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_STATUS_READ_ACTION,
        )
        .unwrap_err();

        assert_eq!(rejection.status, StatusCode::FORBIDDEN);
        assert_eq!(rejection.error, "invalid_local_host");
    }

    #[test]
    fn guard_rejects_rebinding_hostnames() {
        let config = build_test_config();
        let claims = build_claims();
        let token = encode_token(&claims, "test-local-control-secret");
        let headers = build_headers(
            "127.0.0.1.nip.io:12345",
            "https://paired.example.com",
            Some(format!("Bearer {}", token)),
        );

        let rejection = guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_STATUS_READ_ACTION,
        )
        .unwrap_err();

        assert_eq!(rejection.status, StatusCode::FORBIDDEN);
        assert_eq!(rejection.error, "invalid_local_host");
    }

    #[test]
    fn guard_requires_local_control_token() {
        let config = build_test_config();
        let headers = build_headers("127.0.0.1:12345", "https://paired.example.com", None);

        let rejection = guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_STATUS_READ_ACTION,
        )
        .unwrap_err();

        assert_eq!(rejection.status, StatusCode::UNAUTHORIZED);
        assert_eq!(rejection.error, "missing_local_control_token");
    }

    #[test]
    fn guard_rejects_expired_local_control_token() {
        let config = build_test_config();
        let mut claims = build_claims();
        let now = now_timestamp();
        claims.iat = now.saturating_sub(120);
        claims.exp = now.saturating_sub(120);
        let token = encode_token(&claims, "test-local-control-secret");
        let headers = build_headers(
            "127.0.0.1:12345",
            "https://paired.example.com",
            Some(format!("Bearer {}", token)),
        );

        let rejection = guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_STATUS_READ_ACTION,
        )
        .unwrap_err();

        assert_eq!(rejection.status, StatusCode::UNAUTHORIZED);
        assert_eq!(rejection.error, "expired_local_control_token");
    }

    #[test]
    fn guard_rejects_wrong_origin_token() {
        let config = build_test_config();
        let mut claims = build_claims();
        claims.origin = "https://wrong.example.com".to_string();
        let token = encode_token(&claims, "test-local-control-secret");
        let headers = build_headers(
            "127.0.0.1:12345",
            "https://paired.example.com",
            Some(format!("Bearer {}", token)),
        );

        let rejection = guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_STATUS_READ_ACTION,
        )
        .unwrap_err();

        assert_eq!(rejection.status, StatusCode::FORBIDDEN);
        assert_eq!(rejection.error, "wrong_local_control_origin");
    }

    #[test]
    fn guard_rejects_stale_pairing_tokens() {
        let config = build_test_config();
        let mut claims = build_claims();
        claims.secret_version = 2;
        let token = encode_token(&claims, "test-local-control-secret");
        let headers = build_headers(
            "127.0.0.1:12345",
            "https://paired.example.com",
            Some(format!("Bearer {}", token)),
        );

        let rejection = guard_steady_state_request(
            &headers,
            &config,
            LOCAL_CONTROL_STATUS_READ_ACTION,
        )
        .unwrap_err();

        assert_eq!(rejection.status, StatusCode::CONFLICT);
        assert_eq!(rejection.error, "local_pairing_conflict");
    }

    #[test]
    fn recording_owner_check_accepts_same_user_across_tabs() {
        let claims = build_claims();
        let owner = build_recording_owner();

        let result = ensure_same_recording_owner(Some(&owner), &claims, "pause");

        assert!(result.is_ok());
    }

    #[test]
    fn recording_owner_check_rejects_different_user() {
        let mut claims = build_claims();
        claims.user_id = 2;
        claims.username = "bob".to_string();
        let owner = build_recording_owner();

        let rejection = ensure_same_recording_owner(Some(&owner), &claims, "stop").unwrap_err();

        assert_eq!(rejection.status, StatusCode::FORBIDDEN);
        assert_eq!(rejection.error, "recording_owner_mismatch");
    }

    #[test]
    fn recording_owner_check_rejects_mismatched_pairing() {
        let claims = build_claims();
        let mut owner = build_recording_owner();
        owner.companion_pairing_id = "pairing-old".to_string();

        let rejection = ensure_same_recording_owner(Some(&owner), &claims, "resume").unwrap_err();

        assert_eq!(rejection.status, StatusCode::CONFLICT);
        assert_eq!(rejection.error, "recording_owner_conflict");
    }

    #[test]
    fn recording_owner_check_fails_closed_when_metadata_is_missing() {
        let claims = build_claims();

        let rejection = ensure_same_recording_owner(None, &claims, "stop").unwrap_err();

        assert_eq!(rejection.status, StatusCode::CONFLICT);
        assert_eq!(rejection.error, "recording_owner_missing");
    }

    #[test]
    fn backend_disconnect_marks_recording_for_reconnect_without_pausing() {
        let (state, audio_command_rx) = build_test_state();
        *state.current_recording_id.lock().unwrap() = Some(42);
        *state.current_recording_token.lock().unwrap() = Some("upload-token".to_string());
        *state.status.lock().unwrap() = AppStatus::Recording;
        *state.recording_start_time.lock().unwrap() = Some(SystemTime::now());

        let changed = mark_recording_waiting_for_reconnect(&state).unwrap();

        assert!(changed);
        assert_eq!(*state.status.lock().unwrap(), AppStatus::Recording);
        assert_eq!(
            state.recording_recovery_state(),
            RecordingRecoveryState::WaitingForReconnect
        );
        assert!(audio_command_rx.is_empty());
    }

    #[test]
    fn offline_stop_enters_uploading_and_marks_upload_queue() {
        let (state, audio_command_rx) = build_test_state();
        *state.current_recording_id.lock().unwrap() = Some(77);
        *state.current_recording_token.lock().unwrap() = Some("upload-token".to_string());
        *state.status.lock().unwrap() = AppStatus::Recording;
        state.set_recording_recovery_state(RecordingRecoveryState::WaitingForReconnect);

        let update = stop_recording_locally(&state, true).unwrap();

        assert_eq!(*state.status.lock().unwrap(), AppStatus::Uploading);
        assert_eq!(
            state.recording_recovery_state(),
            RecordingRecoveryState::StopRequested
        );
        assert!(matches!(audio_command_rx.recv().unwrap(), AudioCommand::Stop));
        assert_eq!(update.recording_id, 77);
        assert_eq!(update.status, "UPLOADING");
    }

    #[test]
    fn reconnect_clears_recovery_marker_without_stopping_recording() {
        let (state, _audio_command_rx) = build_test_state();
        *state.current_recording_id.lock().unwrap() = Some(88);
        *state.status.lock().unwrap() = AppStatus::Recording;
        state.set_recording_recovery_state(RecordingRecoveryState::WaitingForReconnect);

        let previous_state = restore_recording_after_reconnect(&state);

        assert_eq!(previous_state, RecordingRecoveryState::WaitingForReconnect);
        assert_eq!(*state.status.lock().unwrap(), AppStatus::Recording);
        assert_eq!(
            state.recording_recovery_state(),
            RecordingRecoveryState::None
        );
    }
}
