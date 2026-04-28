#![cfg_attr(not(any(windows, test)), allow(dead_code, unused_imports))]

use crate::companion_auth;
use crate::config::{BackendConnection, Config, MachineLocalUpdate};
use crate::local_https_identity::LocalHttpsServerIdentity;
use crate::notifications;
use crate::secret_store::{self, BackendSecretBundle};
use crate::state::{
    pairing_block_message, pairing_code_fingerprint, pairing_code_log_label, ActiveRecordingOwner,
    AppState, AppStatus, AudioCommand, LocalHttpsStatus, PairingSession, PairingValidationError,
    RecordingRecoveryState,
};
use crate::uploader;
use axum::debug_handler;
use axum::{
    extract::State,
    http::{header, uri::Authority, HeaderMap, HeaderName, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use cpal::traits::{DeviceTrait, HostTrait};
use hyper_util::rt::{TokioExecutor, TokioIo};
use hyper_util::server::conn::auto::Builder as HyperConnectionBuilder;
use hyper_util::service::TowerToHyperService;
use jsonwebtoken::{decode, errors::ErrorKind, Algorithm, DecodingKey, Validation};
use log::{error, info, warn};
use reqwest::Url;
use rustls::ServerConfig;
use std::collections::HashSet;
use std::str::FromStr;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tauri::Manager;
use tokio_rustls::TlsAcceptor;
use tower_http::cors::CorsLayer;

#[derive(Clone)]
pub struct ServerContext {
    pub state: Arc<AppState>,
    pub app_handle: tauri::AppHandle,
}

pub async fn start_server(
    state: Arc<AppState>,
    app_handle: tauri::AppHandle,
    server_identity: LocalHttpsServerIdentity,
    shutdown_rx: tokio::sync::watch::Receiver<bool>,
) -> Result<(), String> {
    let local_port = {
        let config = state.config.lock().unwrap();
        config.local_port()
    };

    let context = ServerContext {
        state: state.clone(),
        app_handle,
    };
    let app = build_local_api_router(context);

    let bind_addr = format!("127.0.0.1:{}", local_port);
    let listener = tokio::net::TcpListener::bind(&bind_addr)
        .await
        .map_err(|error| {
            format!(
                "Failed to bind the local HTTPS listener on {}: {}",
                bind_addr, error
            )
        })?;

    serve_https_router(listener, app, server_identity, shutdown_rx).await
}

fn build_local_api_router(context: ServerContext) -> Router {
    Router::new()
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
        .layer(build_cors_layer(context.state.clone()))
        .with_state(context)
}

fn build_cors_layer(state: Arc<AppState>) -> CorsLayer {
    CorsLayer::new()
        .allow_origin(tower_http::cors::AllowOrigin::predicate(
            move |origin: &axum::http::HeaderValue, request_parts: &axum::http::request::Parts| {
                if let Ok(origin_str) = origin.to_str() {
                    let config = state.config.lock().unwrap();
                    if canonicalize_loopback_host(
                        request_parts.headers.get("host"),
                        config.local_port(),
                    )
                    .is_none()
                    {
                        return false;
                    }

                    if request_parts.uri.path() == "/pair/complete" {
                        return state.is_pairing_active()
                            && canonicalize_origin_value(origin_str).is_some();
                    }

                    return is_allowed_origin_value(origin_str, &config);
                }

                false
            },
        ))
        .allow_methods(tower_http::cors::Any)
        .allow_headers([
            header::AUTHORIZATION,
            header::CONTENT_TYPE,
            HeaderName::from_static(FRONTEND_RUNTIME_HEADER),
            HeaderName::from_static(FRONTEND_PAIR_REQUEST_HEADER),
            HeaderName::from_static(FRONTEND_SOURCE_HEADER),
        ])
        .allow_private_network(true)
}

fn build_tls_server_config(
    server_identity: LocalHttpsServerIdentity,
) -> Result<Arc<ServerConfig>, String> {
    let mut config = ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(server_identity.certificate_chain, server_identity.private_key)
        .map_err(|error| {
            format!(
                "Failed to build the local HTTPS server configuration from the persisted identity: {}",
                error
            )
        })?;
    config.alpn_protocols = vec![b"http/1.1".to_vec()];
    Ok(Arc::new(config))
}

async fn serve_https_router(
    listener: tokio::net::TcpListener,
    app: Router,
    server_identity: LocalHttpsServerIdentity,
    mut shutdown_rx: tokio::sync::watch::Receiver<bool>,
) -> Result<(), String> {
    let bound_addr = listener.local_addr().map_err(|error| {
        format!(
            "Failed to read the bound local HTTPS listener address: {}",
            error
        )
    })?;
    let tls_acceptor = TlsAcceptor::from(build_tls_server_config(server_identity)?);
    let connection_builder = HyperConnectionBuilder::new(TokioExecutor::new());

    info!("Server running on https://{}", bound_addr);

    loop {
        let accept_result = tokio::select! {
            changed = shutdown_rx.changed() => {
                match changed {
                    Ok(()) if *shutdown_rx.borrow() => {
                        info!("Stopping local HTTPS listener on request.");
                        break;
                    }
                    Ok(()) => continue,
                    Err(_) => {
                        info!("Stopping local HTTPS listener because its shutdown channel closed.");
                        break;
                    }
                }
            }
            accepted = listener.accept() => accepted,
        };

        let (tcp_stream, remote_addr) = match accept_result {
            Ok(connection) => connection,
            Err(error) => {
                warn!("Failed to accept a local HTTPS connection: {}", error);
                continue;
            }
        };

        let tls_acceptor = tls_acceptor.clone();
        let connection_builder = connection_builder.clone();
        let app = app.clone();

        tokio::spawn(async move {
            if let Err(error) = tcp_stream.set_nodelay(true) {
                warn!(
                    "Failed to set TCP_NODELAY on local HTTPS connection {}: {}",
                    remote_addr, error
                );
            }

            let tls_stream = match tls_acceptor.accept(tcp_stream).await {
                Ok(stream) => stream,
                Err(error) => {
                    warn!(
                        "Rejected local HTTPS connection from {} during TLS handshake: {}",
                        remote_addr, error
                    );
                    return;
                }
            };

            let io = TokioIo::new(tls_stream);
            let service = TowerToHyperService::new(app);

            if let Err(error) = connection_builder
                .serve_connection_with_upgrades(io, service)
                .await
            {
                warn!(
                    "Local HTTPS connection error for {}: {}",
                    remote_addr, error
                );
            }
        });
    }

    Ok(())
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
    #[serde(rename = "localHttpsStatus")]
    local_https_status: LocalHttpsStatus,
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
const FRONTEND_RUNTIME_HEADER: &str = "x-nojoin-frontend-runtime";
const FRONTEND_PAIR_REQUEST_HEADER: &str = "x-nojoin-frontend-pair-request";
const FRONTEND_SOURCE_HEADER: &str = "x-nojoin-frontend-source";

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
            response
                .headers_mut()
                .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Bearer"));
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

fn ensure_loopback_request(
    headers: &HeaderMap,
    config: &Config,
) -> Result<String, LocalGuardRejection> {
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
    let local_control_secret = secret_store::load_backend_secret_bundle_for_backend(&backend)
        .map_err(|_| {
            LocalGuardRejection::conflict(
                "local_pairing_conflict",
                "Companion pairing state is missing or expired. Pair again from Nojoin.",
            )
        })?
        .local_control_secret;
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
    if !claims
        .actions
        .iter()
        .any(|action| action == required_action)
    {
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
    recording_id: String,
    status: &'static str,
    config: Config,
    token: Option<String>,
    state: Arc<AppState>,
}

fn active_recording_id(state: &Arc<AppState>) -> Result<String, String> {
    state
        .current_recording_id
        .lock()
        .unwrap()
        .clone()
        .ok_or_else(|| "No active recording is currently running.".to_string())
}

fn build_recording_status_update(
    state: &Arc<AppState>,
    recording_id: String,
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
                &update.recording_id,
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
                        update.status, update.recording_id, error
                    );
                }
            }
        } else {
            error!(
                "Missing recording upload token while reporting {} for recording {}",
                update.status, update.recording_id
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

    Ok(build_recording_status_update(
        state,
        recording_id,
        "RECORDING",
    ))
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

    Ok(build_recording_status_update(
        state,
        recording_id,
        "UPLOADING",
    ))
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
    build_status_response(&headers, &context.state).map(Json)
}

fn build_status_response(
    headers: &HeaderMap,
    state: &Arc<AppState>,
) -> LocalApiResult<StatusResponse> {
    {
        let config = state.config.lock().unwrap();
        let _guard =
            guard_steady_state_request(headers, &config, LOCAL_CONTROL_STATUS_READ_ACTION)?;
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

    Ok(StatusResponse {
        status,
        duration_seconds: duration.as_secs(),
        version: env!("CARGO_PKG_VERSION"),
        authenticated,
        api_host,
        update_available,
        latest_version,
        local_https_status: state.local_https_status(),
    })
}

#[derive(serde::Deserialize)]
struct PairingCompleteRequest {
    pairing_code: String,
    companion_credential_secret: String,
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

fn compact_log_value(value: &str, max_len: usize) -> String {
    let collapsed = value.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.len() <= max_len {
        collapsed
    } else {
        format!("{}...", &collapsed[..max_len])
    }
}

fn short_pairing_id(pairing_id: Option<&str>) -> String {
    let Some(pairing_id) = pairing_id.map(str::trim).filter(|value| !value.is_empty()) else {
        return "<missing>".to_string();
    };

    if pairing_id.len() <= 12 {
        pairing_id.to_string()
    } else {
        format!("{}...", &pairing_id[..12])
    }
}

fn read_optional_log_header(headers: &HeaderMap, name: &str, max_len: usize) -> String {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(|value| compact_log_value(value, max_len))
        .unwrap_or_else(|| "<missing>".to_string())
}

fn pairing_backend_target(payload: &PairingCompleteRequest) -> String {
    let protocol = payload
        .api_protocol
        .as_deref()
        .unwrap_or("<missing>")
        .trim();
    let host = payload.api_host.as_deref().unwrap_or("<missing>").trim();
    let port = payload
        .api_port
        .map(|value| value.to_string())
        .unwrap_or_else(|| "<missing>".to_string());

    format!("{}://{}:{}", protocol, host, port)
}

fn short_fingerprint_label(fingerprint: &str) -> String {
    let normalized = fingerprint.trim();

    if normalized.len() <= 23 {
        normalized.to_string()
    } else {
        format!(
            "{}...{}",
            &normalized[..11],
            &normalized[normalized.len() - 11..]
        )
    }
}

fn summarize_pairing_request(
    payload: &PairingCompleteRequest,
    origin_header: Option<&str>,
    host_header: Option<&str>,
) -> String {
    format!(
        "origin_header={} host_header={} backend_target={} pairing_code={} code_hash={} payload_tls_fingerprint_present={} backend_pairing_id={} local_secret_present={} local_secret_version={}",
        origin_header
            .map(|value| compact_log_value(value, 120))
            .unwrap_or_else(|| "<missing>".to_string()),
        host_header
            .map(|value| compact_log_value(value, 120))
            .unwrap_or_else(|| "<missing>".to_string()),
        pairing_backend_target(payload),
        pairing_code_log_label(&payload.pairing_code),
        pairing_code_fingerprint(&payload.pairing_code),
        payload
            .tls_fingerprint
            .as_deref()
            .map(|value| !value.trim().is_empty())
            .unwrap_or(false),
        short_pairing_id(payload.backend_pairing_id.as_deref()),
        payload
            .local_control_secret
            .as_deref()
            .map(|value| !value.trim().is_empty())
            .unwrap_or(false),
        payload
            .local_control_secret_version
            .map(|value| value.to_string())
            .unwrap_or_else(|| "<missing>".to_string()),
    )
}

fn summarize_pairing_session(session: Option<&PairingSession>) -> String {
    match session {
        Some(session) => format!(
            "active_pairing=code:{} code_hash:{} expires_in={}s failed_attempts={} completion_in_progress={} expired={}",
            pairing_code_log_label(&session.canonical_code),
            pairing_code_fingerprint(&session.canonical_code),
            session.remaining_seconds(),
            session.failed_attempts,
            session.completion_in_progress,
            session.is_expired(),
        ),
        None => "active_pairing=<none>".to_string(),
    }
}

fn pairing_backend_details(payload: &PairingCompleteRequest) -> Result<(&str, &str, u16), String> {
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

    Ok((protocol, host, port))
}

async fn capture_pairing_tls_fingerprint(
    payload: &PairingCompleteRequest,
) -> Result<String, String> {
    let (protocol, host, port) = pairing_backend_details(payload)?;
    if !protocol.eq_ignore_ascii_case("https") {
        return Err("Pairing requires an HTTPS backend target.".to_string());
    }

    let fingerprint = crate::tls::capture_tls_fingerprint(host, port).await?;
    info!(
        "Captured pairing TLS fingerprint for {}://{}:{} (peer_cert={})",
        protocol,
        host,
        port,
        short_fingerprint_label(&fingerprint)
    );

    Ok(fingerprint)
}

async fn exchange_companion_access_token_for_pairing(
    payload: &PairingCompleteRequest,
    tls_fingerprint: &str,
) -> Result<String, String> {
    let (protocol, host, port) = pairing_backend_details(payload)?;
    let pairing_session_id = payload
        .backend_pairing_id
        .as_deref()
        .ok_or_else(|| "Missing backend pairing id".to_string())?;
    let companion_credential_secret = payload.companion_credential_secret.trim();
    if companion_credential_secret.is_empty() {
        return Err("Missing companion credential secret".to_string());
    }

    let backend_target = format!("{}://{}:{}", protocol, host, port);
    let started_at = Instant::now();
    info!(
        "Exchanging companion credential against {} (backend_pairing_id={})",
        backend_target,
        short_pairing_id(payload.backend_pairing_id.as_deref())
    );

    let access_token = companion_auth::exchange_access_token_for_target(
        protocol,
        host,
        port,
        pairing_session_id,
        companion_credential_secret,
        Some(tls_fingerprint.to_string()),
    )
    .await?;

    info!(
        "Companion credential exchange succeeded for {} in {} ms (backend_pairing_id={})",
        backend_target,
        started_at.elapsed().as_millis(),
        short_pairing_id(payload.backend_pairing_id.as_deref())
    );
    Ok(access_token)
}

async fn cancel_pending_pairing_from_request(
    payload: &PairingCompleteRequest,
    tls_fingerprint: Option<String>,
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
    let pairing_session_id = payload
        .backend_pairing_id
        .as_deref()
        .ok_or_else(|| "Missing backend pairing id".to_string())?;
    let companion_credential_secret = payload.companion_credential_secret.trim();
    if companion_credential_secret.is_empty() {
        return Err("Missing companion credential secret".to_string());
    }

    companion_auth::cancel_pending_pairing_for_target(
        protocol,
        host,
        port,
        pairing_session_id,
        companion_credential_secret,
        tls_fingerprint.or_else(|| payload.tls_fingerprint.clone()),
    )
    .await
}

fn is_same_backend_target(previous: &BackendConnection, replacement: &BackendConnection) -> bool {
    previous
        .api_protocol
        .eq_ignore_ascii_case(&replacement.api_protocol)
        && previous
            .api_host
            .eq_ignore_ascii_case(&replacement.api_host)
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
    let host_header = headers.get("host").and_then(|value| value.to_str().ok());
    let origin_header = headers.get("origin").and_then(|value| value.to_str().ok());
    let frontend_runtime_header = read_optional_log_header(&headers, FRONTEND_RUNTIME_HEADER, 160);
    let frontend_request_header =
        read_optional_log_header(&headers, FRONTEND_PAIR_REQUEST_HEADER, 160);
    let frontend_source_header = read_optional_log_header(&headers, FRONTEND_SOURCE_HEADER, 80);
    let referer_header = read_optional_log_header(&headers, "referer", 160);
    let sec_fetch_mode_header = read_optional_log_header(&headers, "sec-fetch-mode", 40);
    let sec_fetch_site_header = read_optional_log_header(&headers, "sec-fetch-site", 40);
    let request_summary = format!(
        "{} frontend_runtime={} frontend_request={} frontend_source={} referer={} sec_fetch_mode={} sec_fetch_site={}",
        summarize_pairing_request(&payload, origin_header, host_header),
        frontend_runtime_header,
        frontend_request_header,
        frontend_source_header,
        referer_header,
        sec_fetch_mode_header,
        sec_fetch_site_header
    );
    let session_summary = summarize_pairing_session(state.pairing_session_snapshot().as_ref());
    info!(
        "Received pairing completion request: {}; {}",
        request_summary, session_summary
    );

    {
        let config = state.config.lock().unwrap();
        if ensure_loopback_request(&headers, &config).is_err() {
            warn!(
                "Rejected pairing completion because request was not loopback-safe: {}",
                request_summary
            );
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
            warn!(
                "Rejected pairing completion because Origin header was invalid: {}",
                request_summary
            );
            return (
                StatusCode::FORBIDDEN,
                Json(PairingCompleteResponse {
                    success: false,
                    message: "Pairing requests must include a valid Origin header.".to_string(),
                }),
            );
        }
    };

    if payload.companion_credential_secret.trim().is_empty()
        || payload.api_host.as_deref().unwrap_or("").trim().is_empty()
        || payload
            .api_protocol
            .as_deref()
            .unwrap_or("")
            .trim()
            .is_empty()
        || payload.api_port.is_none()
        || payload.pairing_code.trim().is_empty()
        || payload
            .local_control_secret
            .as_deref()
            .unwrap_or("")
            .trim()
            .is_empty()
        || payload
            .backend_pairing_id
            .as_deref()
            .unwrap_or("")
            .trim()
            .is_empty()
        || payload.local_control_secret_version.unwrap_or_default() == 0
    {
        warn!(
            "Rejected pairing completion because required fields were missing: {}",
            request_summary
        );
        return (
            StatusCode::BAD_REQUEST,
            Json(PairingCompleteResponse {
                success: false,
                message: "Pairing code, companion credential, local control secret, protocol, host, port, and pairing metadata are required."
                    .to_string(),
            }),
        );
    }

    {
        let status = state.status.lock().unwrap().clone();
        if let Some(message) = pairing_block_message(&status) {
            warn!(
                "Rejected pairing completion because companion state blocked pairing: status={:?}; {}",
                status, request_summary
            );
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
        Ok(()) => {
            info!(
                "Accepted pairing completion request and locked active pairing session: {}; {}",
                request_summary,
                summarize_pairing_session(state.pairing_session_snapshot().as_ref())
            );
        }
        Err(PairingValidationError::NotActive) => {
            warn!(
                "Rejected pairing completion because no active pairing session was present: {}; {}",
                request_summary,
                summarize_pairing_session(state.pairing_session_snapshot().as_ref())
            );
            if let Err(err) = cancel_pending_pairing_from_request(&payload, None).await {
                error!(
                    "Failed to cancel pending pairing after inactive request: {}",
                    err
                );
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
            warn!(
                "Rejected pairing completion because the active pairing session expired: {}; {}",
                request_summary,
                summarize_pairing_session(state.pairing_session_snapshot().as_ref())
            );
            if let Some(window) = context.app_handle.get_webview_window("pairing") {
                let _ = window.close();
            }
            if let Err(err) = cancel_pending_pairing_from_request(&payload, None).await {
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
            warn!(
                "Rejected pairing completion because the submitted pairing code did not match the active session: {}; {}",
                request_summary,
                summarize_pairing_session(state.pairing_session_snapshot().as_ref())
            );
            if let Err(err) = cancel_pending_pairing_from_request(&payload, None).await {
                error!(
                    "Failed to cancel pending pairing after invalid code: {}",
                    err
                );
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
            warn!(
                "Rejected pairing completion because the pairing attempt budget was exhausted: {}; {}",
                request_summary,
                summarize_pairing_session(state.pairing_session_snapshot().as_ref())
            );
            if let Some(window) = context.app_handle.get_webview_window("pairing") {
                let _ = window.close();
            }
            if let Err(err) = cancel_pending_pairing_from_request(&payload, None).await {
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
            warn!(
                "Rejected pairing completion because another completion is already in progress: {}; {}",
                request_summary,
                summarize_pairing_session(state.pairing_session_snapshot().as_ref())
            );
            return (
                StatusCode::CONFLICT,
                Json(PairingCompleteResponse {
                    success: false,
                    message: "A pairing completion is already in progress.".to_string(),
                }),
            );
        }
    }

    let captured_tls_fingerprint = match capture_pairing_tls_fingerprint(&payload).await {
        Ok(fingerprint) => fingerprint,
        Err(err) => {
            error!(
                "TLS capture failed during pairing completion: {}; {}",
                err, request_summary
            );
            state.release_pairing_completion();
            if let Err(cancel_err) = cancel_pending_pairing_from_request(&payload, None).await {
                error!(
                    "Failed to cancel pending pairing after TLS capture failure: {}",
                    cancel_err
                );
            }
            return (
                StatusCode::BAD_GATEWAY,
                Json(PairingCompleteResponse {
                    success: false,
                    message: "Unable to capture the backend TLS certificate. Verify the Nojoin URL and certificate, then start pairing again from Nojoin.".to_string(),
                }),
            );
        }
    };

    if let Err(err) =
        exchange_companion_access_token_for_pairing(&payload, &captured_tls_fingerprint).await
    {
        error!(
            "Companion credential exchange failed during pairing completion: {}; {}",
            err, request_summary
        );
        state.release_pairing_completion();
        if let Err(cancel_err) =
            cancel_pending_pairing_from_request(&payload, Some(captured_tls_fingerprint.clone()))
                .await
        {
            error!(
                "Failed to cancel pending pairing after bootstrap validation failure: {}",
                cancel_err
            );
        }
        return (
            StatusCode::UNAUTHORIZED,
            Json(PairingCompleteResponse {
                success: false,
                message: "The backend pairing credential is invalid or expired. Start pairing again from Nojoin.".to_string(),
            }),
        );
    }

    let backend = BackendConnection {
        api_protocol: payload.api_protocol.clone().unwrap_or_default(),
        api_host: payload.api_host.clone().unwrap_or_default(),
        api_port: payload.api_port.unwrap_or_default(),
        tls_fingerprint: Some(captured_tls_fingerprint.clone()),
        paired_web_origin: Some(origin.clone()),
        backend_pairing_id: payload.backend_pairing_id.clone(),
        local_control_secret_version: payload.local_control_secret_version,
    };
    let new_secret_bundle = BackendSecretBundle {
        companion_credential_secret: payload.companion_credential_secret.trim().to_string(),
        local_control_secret: payload
            .local_control_secret
            .as_deref()
            .unwrap_or_default()
            .trim()
            .to_string(),
    };

    let previous_backend = {
        let config = state.config.lock().unwrap();
        config.backend_connection()
    };
    let previous_secret_bundle = previous_backend
        .as_ref()
        .and_then(|existing| secret_store::load_backend_secret_bundle_for_backend(existing).ok());
    let had_existing_backend = previous_backend.is_some();
    let should_revoke_previous_backend = previous_backend
        .as_ref()
        .map(|existing| !is_same_backend_target(existing, &backend))
        .unwrap_or(false);

    if let Err(error) =
        secret_store::save_backend_secret_bundle_for_backend(&backend, &new_secret_bundle)
    {
        error!(
            "Failed to save the local companion secret bundle after successful credential exchange: {}; {}",
            error, request_summary
        );
        state.release_pairing_completion();
        if let Err(cancel_err) =
            cancel_pending_pairing_from_request(&payload, Some(captured_tls_fingerprint.clone()))
                .await
        {
            error!(
                "Failed to cancel pending pairing after local secret save failure: {}",
                cancel_err
            );
        }
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(PairingCompleteResponse {
                success: false,
                message: format!("Failed to save pairing secrets: {}", error),
            }),
        );
    }

    let save_result = {
        let mut config = state.config.lock().unwrap();
        config.replace_backend_and_save(backend.clone())
    };

    if let Err(e) = save_result {
        error!(
            "Failed to save pairing config after successful bootstrap validation: {}; {}",
            e, request_summary
        );
        if let Err(delete_error) = secret_store::delete_backend_secret_bundle_for_backend(&backend)
        {
            error!(
                "Failed to delete newly saved companion secret bundle after config save failure: {}",
                delete_error
            );
        }
        state.release_pairing_completion();
        if let Err(cancel_err) =
            cancel_pending_pairing_from_request(&payload, Some(captured_tls_fingerprint.clone()))
                .await
        {
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

    info!(
        "Persisted paired backend configuration: origin={} backend_target={} had_existing_backend={} revoke_previous_backend={}",
        origin,
        pairing_backend_target(&payload),
        had_existing_backend,
        should_revoke_previous_backend
    );

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
        if let Some(previous_backend) = previous_backend.as_ref() {
            let previous_backend_target = format!(
                "{}://{}:{}",
                previous_backend.api_protocol, previous_backend.api_host, previous_backend.api_port
            );
            match previous_secret_bundle {
                Some(bundle) => {
                    if let Err(revoke_err) = companion_auth::revoke_backend_pairings_with_bundle(
                        &previous_backend,
                        &bundle,
                    )
                    .await
                    {
                        error!(
                            "Failed to revoke pairing state on the previous backend after successful re-pair: {}",
                            revoke_err
                        );
                    } else {
                        info!(
                            "Revoked pairing state on previous backend after successful re-pair: {}",
                            previous_backend_target
                        );
                    }
                }
                None => {
                    error!(
                        "Failed to load the previous backend companion secret bundle for remote cleanup after successful re-pair: {}",
                        previous_backend_target
                    );
                }
            }
        }
    }

    if let Some(previous_backend) = previous_backend.as_ref() {
        if previous_backend.backend_pairing_id != backend.backend_pairing_id {
            if let Err(delete_error) =
                secret_store::delete_backend_secret_bundle_for_backend(previous_backend)
            {
                error!(
                    "Failed to delete the previous backend companion secret bundle after successful pairing: {}",
                    delete_error
                );
            }
        }
    }

    info!(
        "Companion pairing completed successfully for origin {}",
        origin
    );
    let (notification_title, notification_body, success_message) = if should_revoke_previous_backend
    {
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
    notifications::show_notification(&context.app_handle, notification_title, notification_body);
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
        let _guard =
            guard_steady_state_request(&headers, &config, LOCAL_CONTROL_WAVEFORM_READ_ACTION)?;
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
        let _guard =
            guard_steady_state_request(&headers, &config, LOCAL_CONTROL_WAVEFORM_READ_ACTION)?;
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
}

#[derive(serde::Serialize)]
struct StartResponse {
    id: String,
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
        guard_steady_state_request(&headers, &config, LOCAL_CONTROL_RECORDING_START_ACTION)?
    };

    // Check status (and drop lock immediately)
    {
        let status = state.status.lock().unwrap();
        if *status != AppStatus::Idle && *status != AppStatus::BackendOffline {
            return Ok((
                StatusCode::CONFLICT,
                Json(StartResponse {
                    id: String::new(),
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
    let client = crate::tls::create_client(fingerprint).map_err(|err| {
        LocalGuardRejection::internal(
            "local_tls_client_error",
            format!("Failed to prepare the paired backend TLS client: {}", err),
        )
    })?;

    let config_snapshot = { state.config.lock().unwrap().clone() };
    let token = companion_auth::exchange_access_token_for_config(&config_snapshot)
        .await
        .map_err(|error| {
            LocalGuardRejection::internal(
                "backend_access_token_error",
                format!("Failed to authenticate with the paired backend: {}", error),
            )
        })?;
    let api_url = config_snapshot.get_api_url();

    let res = client
        .post(format!("{}/recordings/init", api_url))
        .header("Authorization", format!("Bearer {}", token))
        .query(&[("name", &payload.name)])
        .send()
        .await;

    match res {
        Ok(response) => {
            if let Ok(json) = response.json::<serde_json::Value>().await {
                if let Some(id) = json.get("id").and_then(|v| v.as_str()) {
                    let id = id.to_string();
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
                                id: String::new(),
                                message: "Recording upload token missing".to_string(),
                            }),
                        ));
                    }

                    // Start Audio Thread
                    *state.current_recording_id.lock().unwrap() = Some(id.clone());
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
                        .send(AudioCommand::Start(id.clone()))
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
            id: String::new(),
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
        guard_steady_state_request(&headers, &config, LOCAL_CONTROL_RECORDING_STOP_ACTION)?
    };

    if state.current_recording_id.lock().unwrap().is_none() {
        return Err(LocalGuardRejection::conflict(
            "recording_not_active",
            "No active recording is currently running.",
        ));
    }
    let owner = state.current_recording_owner();
    ensure_same_recording_owner(owner.as_ref(), &guard.claims, "stop")?;

    let status_update = stop_recording_locally(state, false)
        .map_err(|message| LocalGuardRejection::conflict("recording_not_active", message))?;
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
        guard_steady_state_request(&headers, &config, LOCAL_CONTROL_RECORDING_PAUSE_ACTION)?
    };

    if state.current_recording_id.lock().unwrap().is_none() {
        return Err(LocalGuardRejection::conflict(
            "recording_not_active",
            "No active recording is currently running.",
        ));
    }
    let owner = state.current_recording_owner();
    ensure_same_recording_owner(owner.as_ref(), &guard.claims, "pause")?;

    let status_update = pause_recording_locally(state)
        .map_err(|message| LocalGuardRejection::conflict("recording_not_active", message))?;
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
        guard_steady_state_request(&headers, &config, LOCAL_CONTROL_RECORDING_RESUME_ACTION)?
    };

    if state.current_recording_id.lock().unwrap().is_none() {
        return Err(LocalGuardRejection::conflict(
            "recording_not_active",
            "No active recording is currently running.",
        ));
    }
    let owner = state.current_recording_owner();
    ensure_same_recording_owner(owner.as_ref(), &guard.claims, "resume")?;

    let status_update = resume_recording_locally(state)
        .map_err(|message| LocalGuardRejection::conflict("recording_not_active", message))?;
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
        let _guard =
            guard_steady_state_request(&headers, &config, LOCAL_CONTROL_SETTINGS_READ_ACTION)?;

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
        let _guard =
            guard_steady_state_request(&headers, &config, LOCAL_CONTROL_DEVICES_READ_ACTION)?;
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

    let _guard =
        guard_steady_state_request(&headers, &config, LOCAL_CONTROL_SETTINGS_WRITE_ACTION)?;

    let mut updated = config.clone();
    let mut should_save = false;

    if payload.api_port.is_some() {
        let mut backend = updated.backend_or_default();
        if let Some(port) = payload.api_port {
            backend.api_port = port;
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
        let _guard =
            guard_steady_state_request(&headers, &config, LOCAL_CONTROL_UPDATE_TRIGGER_ACTION)?;
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
    use crate::local_https_identity;
    use crate::state::LocalHttpsHealth;
    use axum::http::HeaderMap;
    use axum::response::IntoResponse;
    use axum::{extract::State, response::Json};
    use crossbeam_channel::unbounded;
    use jsonwebtoken::{encode, EncodingKey, Header};
    use rand::random;
    use reqwest::{Certificate, Method};
    use std::collections::BTreeSet;
    use std::fs;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
    use std::sync::Mutex as StdMutex;
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, SystemTime, UNIX_EPOCH};
    use time::OffsetDateTime;

    #[derive(Default)]
    struct FakeTrustStore {
        trusted_fingerprints: StdMutex<BTreeSet<String>>,
    }

    #[derive(serde::Deserialize)]
    struct StatusResponseBody {
        status: AppStatus,
        authenticated: bool,
        api_host: String,
        #[serde(rename = "localHttpsStatus")]
        local_https_status: LocalHttpsStatus,
    }

    impl local_https_identity::LocalCaTrustStore for FakeTrustStore {
        fn is_ca_trusted(&self, ca_certificate_der: &[u8]) -> Result<bool, String> {
            Ok(self
                .trusted_fingerprints
                .lock()
                .unwrap()
                .contains(&fingerprint_for_test(ca_certificate_der)))
        }

        fn install_ca(&self, ca_certificate_der: &[u8]) -> Result<(), String> {
            self.trusted_fingerprints
                .lock()
                .unwrap()
                .insert(fingerprint_for_test(ca_certificate_der));
            Ok(())
        }

        fn install_crl(&self, _crl_der: &[u8]) -> Result<(), String> {
            Ok(())
        }

        fn remove_ca(&self, ca_certificate_der: &[u8]) -> Result<bool, String> {
            Ok(self
                .trusted_fingerprints
                .lock()
                .unwrap()
                .remove(&fingerprint_for_test(ca_certificate_der)))
        }

        fn remove_crl(&self, _crl_der: &[u8]) -> Result<bool, String> {
            Ok(false)
        }
    }

    struct TestDir {
        path: PathBuf,
    }

    impl TestDir {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "nojoin-server-test-{}-{}",
                std::process::id(),
                random::<u64>()
            ));
            fs::create_dir_all(&path).unwrap();
            Self { path }
        }

        fn paths(&self) -> local_https_identity::LocalHttpsPaths {
            local_https_identity::LocalHttpsPaths::from_app_data_dir(&self.path)
        }
    }

    impl Drop for TestDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    fn next_test_pairing_id() -> String {
        static NEXT_TEST_PAIRING_ID: AtomicU32 = AtomicU32::new(1);
        format!(
            "pairing-{}",
            NEXT_TEST_PAIRING_ID.fetch_add(1, Ordering::Relaxed)
        )
    }

    fn build_test_config() -> Config {
        let pairing_id = next_test_pairing_id();
        let _ = crate::secret_store::save_backend_secret_bundle(
            &pairing_id,
            &crate::secret_store::BackendSecretBundle {
                companion_credential_secret: "pairing-secret".to_string(),
                local_control_secret: "test-local-control-secret".to_string(),
            },
        );

        Config {
            version: 3,
            machine_local: crate::config::MachineLocalSettings {
                local_port: 12345,
                input_device_name: None,
                output_device_name: None,
                last_version: None,
                min_meeting_length: None,
                run_on_startup: None,
                launcher_intro_seen: None,
            },
            backend: Some(BackendConnection {
                api_protocol: "https".to_string(),
                api_host: "localhost".to_string(),
                api_port: 14443,
                tls_fingerprint: Some("AA:BB:CC".to_string()),
                paired_web_origin: Some("https://paired.example.com".to_string()),
                backend_pairing_id: Some(pairing_id),
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

    fn build_claims(pairing_id: &str) -> LocalControlClaims {
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
            companion_pairing_id: pairing_id.to_string(),
            secret_version: 3,
        }
    }

    fn build_recording_owner(pairing_id: &str) -> ActiveRecordingOwner {
        ActiveRecordingOwner {
            user_id: 1,
            username: "alice".to_string(),
            companion_pairing_id: pairing_id.to_string(),
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
            local_https_health: Mutex::new(LocalHttpsHealth::ready(true)),
            tray_status_item: Mutex::new(None),
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

    fn build_headers(host: &str, origin: &str, authorization: Option<String>) -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert("host", HeaderValue::from_str(host).unwrap());
        headers.insert("origin", HeaderValue::from_str(origin).unwrap());
        if let Some(value) = authorization {
            headers.insert(
                header::AUTHORIZATION,
                HeaderValue::from_str(&value).unwrap(),
            );
        }
        headers
    }

    fn fingerprint_for_test(bytes: &[u8]) -> String {
        use sha2::{Digest, Sha256};

        let mut hasher = Sha256::new();
        hasher.update(bytes);
        hasher
            .finalize()
            .iter()
            .map(|byte| format!("{:02X}", byte))
            .collect::<Vec<String>>()
            .join(":")
    }

    fn fixed_https_identity_now() -> OffsetDateTime {
        OffsetDateTime::now_utc()
    }

    fn build_test_https_identity() -> local_https_identity::LocalHttpsReadyIdentity {
        let temp_dir = TestDir::new();
        let paths = temp_dir.paths();
        let trust_store = FakeTrustStore::default();
        let result = local_https_identity::ensure_local_https_identity_with(
            &paths,
            &trust_store,
            fixed_https_identity_now(),
        )
        .unwrap();

        match result.state {
            local_https_identity::LocalHttpsReconcileState::Ready(ready_identity) => ready_identity,
            local_https_identity::LocalHttpsReconcileState::RepairRequired(repair) => {
                panic!(
                    "expected a ready HTTPS identity, got repair-required: {}",
                    repair.message
                )
            }
        }
    }

    async fn test_status_handler(
        headers: HeaderMap,
        State(state): State<Arc<AppState>>,
    ) -> LocalApiResult<Json<StatusResponse>> {
        build_status_response(&headers, &state).map(Json)
    }

    async fn test_pairing_handler() -> impl IntoResponse {
        StatusCode::NO_CONTENT
    }

    fn build_test_transport_router(state: Arc<AppState>) -> Router {
        Router::new()
            .route("/status", get(test_status_handler))
            .route("/pair/complete", post(test_pairing_handler))
            .layer(build_cors_layer(state.clone()))
            .with_state(state)
    }

    async fn start_test_https_server(
        app: Router,
        server_identity: LocalHttpsServerIdentity,
    ) -> (
        std::net::SocketAddr,
        tokio::sync::watch::Sender<bool>,
        tokio::task::JoinHandle<Result<(), String>>,
    ) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);
        let task = tokio::spawn(async move {
            serve_https_router(listener, app, server_identity, shutdown_rx).await
        });
        (addr, shutdown_tx, task)
    }

    fn set_test_local_port(state: &Arc<AppState>, port: u16) {
        state.config.lock().unwrap().machine_local.local_port = port;
    }

    fn build_https_client(ca_certificate_der: &[u8]) -> reqwest::Client {
        reqwest::Client::builder()
            .add_root_certificate(Certificate::from_der(ca_certificate_der).unwrap())
            .build()
            .unwrap()
    }

    #[test]
    fn guard_rejects_malformed_host() {
        let config = build_test_config();
        let claims = build_claims(&config.backend_pairing_id().unwrap());
        let token = encode_token(&claims, "test-local-control-secret");
        let headers = build_headers(
            "http://127.0.0.1:12345",
            "https://paired.example.com",
            Some(format!("Bearer {}", token)),
        );

        let rejection =
            guard_steady_state_request(&headers, &config, LOCAL_CONTROL_STATUS_READ_ACTION)
                .unwrap_err();

        assert_eq!(rejection.status, StatusCode::FORBIDDEN);
        assert_eq!(rejection.error, "invalid_local_host");
    }

    #[test]
    fn guard_rejects_rebinding_hostnames() {
        let config = build_test_config();
        let claims = build_claims(&config.backend_pairing_id().unwrap());
        let token = encode_token(&claims, "test-local-control-secret");
        let headers = build_headers(
            "127.0.0.1.nip.io:12345",
            "https://paired.example.com",
            Some(format!("Bearer {}", token)),
        );

        let rejection =
            guard_steady_state_request(&headers, &config, LOCAL_CONTROL_STATUS_READ_ACTION)
                .unwrap_err();

        assert_eq!(rejection.status, StatusCode::FORBIDDEN);
        assert_eq!(rejection.error, "invalid_local_host");
    }

    #[test]
    fn guard_requires_local_control_token() {
        let config = build_test_config();
        let headers = build_headers("127.0.0.1:12345", "https://paired.example.com", None);

        let rejection =
            guard_steady_state_request(&headers, &config, LOCAL_CONTROL_STATUS_READ_ACTION)
                .unwrap_err();

        assert_eq!(rejection.status, StatusCode::UNAUTHORIZED);
        assert_eq!(rejection.error, "missing_local_control_token");
    }

    #[test]
    fn guard_rejects_expired_local_control_token() {
        let config = build_test_config();
        let mut claims = build_claims(&config.backend_pairing_id().unwrap());
        let now = now_timestamp();
        claims.iat = now.saturating_sub(120);
        claims.exp = now.saturating_sub(120);
        let token = encode_token(&claims, "test-local-control-secret");
        let headers = build_headers(
            "127.0.0.1:12345",
            "https://paired.example.com",
            Some(format!("Bearer {}", token)),
        );

        let rejection =
            guard_steady_state_request(&headers, &config, LOCAL_CONTROL_STATUS_READ_ACTION)
                .unwrap_err();

        assert_eq!(rejection.status, StatusCode::UNAUTHORIZED);
        assert_eq!(rejection.error, "expired_local_control_token");
    }

    #[test]
    fn guard_rejects_wrong_origin_token() {
        let config = build_test_config();
        let mut claims = build_claims(&config.backend_pairing_id().unwrap());
        claims.origin = "https://wrong.example.com".to_string();
        let token = encode_token(&claims, "test-local-control-secret");
        let headers = build_headers(
            "127.0.0.1:12345",
            "https://paired.example.com",
            Some(format!("Bearer {}", token)),
        );

        let rejection =
            guard_steady_state_request(&headers, &config, LOCAL_CONTROL_STATUS_READ_ACTION)
                .unwrap_err();

        assert_eq!(rejection.status, StatusCode::FORBIDDEN);
        assert_eq!(rejection.error, "wrong_local_control_origin");
    }

    #[test]
    fn guard_rejects_stale_pairing_tokens() {
        let config = build_test_config();
        let mut claims = build_claims(&config.backend_pairing_id().unwrap());
        claims.secret_version = 2;
        let token = encode_token(&claims, "test-local-control-secret");
        let headers = build_headers(
            "127.0.0.1:12345",
            "https://paired.example.com",
            Some(format!("Bearer {}", token)),
        );

        let rejection =
            guard_steady_state_request(&headers, &config, LOCAL_CONTROL_STATUS_READ_ACTION)
                .unwrap_err();

        assert_eq!(rejection.status, StatusCode::CONFLICT);
        assert_eq!(rejection.error, "local_pairing_conflict");
    }

    #[test]
    fn recording_owner_check_accepts_same_user_across_tabs() {
        let claims = build_claims("pairing-123");
        let owner = build_recording_owner("pairing-123");

        let result = ensure_same_recording_owner(Some(&owner), &claims, "pause");

        assert!(result.is_ok());
    }

    #[test]
    fn recording_owner_check_rejects_different_user() {
        let mut claims = build_claims("pairing-123");
        claims.user_id = 2;
        claims.username = "bob".to_string();
        let owner = build_recording_owner("pairing-123");

        let rejection = ensure_same_recording_owner(Some(&owner), &claims, "stop").unwrap_err();

        assert_eq!(rejection.status, StatusCode::FORBIDDEN);
        assert_eq!(rejection.error, "recording_owner_mismatch");
    }

    #[test]
    fn recording_owner_check_rejects_mismatched_pairing() {
        let claims = build_claims("pairing-123");
        let mut owner = build_recording_owner("pairing-123");
        owner.companion_pairing_id = "pairing-old".to_string();

        let rejection = ensure_same_recording_owner(Some(&owner), &claims, "resume").unwrap_err();

        assert_eq!(rejection.status, StatusCode::CONFLICT);
        assert_eq!(rejection.error, "recording_owner_conflict");
    }

    #[test]
    fn recording_owner_check_fails_closed_when_metadata_is_missing() {
        let claims = build_claims("pairing-123");

        let rejection = ensure_same_recording_owner(None, &claims, "stop").unwrap_err();

        assert_eq!(rejection.status, StatusCode::CONFLICT);
        assert_eq!(rejection.error, "recording_owner_missing");
    }

    #[test]
    fn backend_disconnect_marks_recording_for_reconnect_without_pausing() {
        let (state, audio_command_rx) = build_test_state();
        *state.current_recording_id.lock().unwrap() = Some("42".to_string());
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
        *state.current_recording_id.lock().unwrap() = Some("77".to_string());
        *state.current_recording_token.lock().unwrap() = Some("upload-token".to_string());
        *state.status.lock().unwrap() = AppStatus::Recording;
        state.set_recording_recovery_state(RecordingRecoveryState::WaitingForReconnect);

        let update = stop_recording_locally(&state, true).unwrap();

        assert_eq!(*state.status.lock().unwrap(), AppStatus::Uploading);
        assert_eq!(
            state.recording_recovery_state(),
            RecordingRecoveryState::StopRequested
        );
        assert!(matches!(
            audio_command_rx.recv().unwrap(),
            AudioCommand::Stop
        ));
        assert_eq!(update.recording_id, "77");
        assert_eq!(update.status, "UPLOADING");
    }

    #[test]
    fn reconnect_clears_recovery_marker_without_stopping_recording() {
        let (state, _audio_command_rx) = build_test_state();
        *state.current_recording_id.lock().unwrap() = Some("88".to_string());
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

    #[test]
    fn status_response_surfaces_local_https_needs_repair() {
        let (state, _audio_command_rx) = build_test_state();
        state.set_local_https_health(LocalHttpsHealth::needs_repair(
            "Companion local HTTPS needs repair.",
            None,
            Some(true),
            true,
        ));
        let token = encode_token(
            &build_claims(&state.config.lock().unwrap().backend_pairing_id().unwrap()),
            "test-local-control-secret",
        );
        let headers = build_headers(
            "127.0.0.1:12345",
            "https://paired.example.com",
            Some(format!("Bearer {}", token)),
        );

        let payload = build_status_response(&headers, &state).unwrap();

        assert_eq!(payload.local_https_status, LocalHttpsStatus::NeedsRepair);
    }

    #[tokio::test]
    async fn https_transport_serves_steady_state_status_route() {
        let (state, _audio_command_rx) = build_test_state();
        let ready_identity = build_test_https_identity();
        let ca_certificate_der = ready_identity.persisted_identity.ca.certificate_der.clone();
        let app = build_test_transport_router(state.clone());
        let (addr, _shutdown_tx, server_task) =
            start_test_https_server(app, ready_identity.server_identity).await;
        set_test_local_port(&state, addr.port());

        let client = build_https_client(&ca_certificate_der);
        let token = encode_token(
            &build_claims(&state.config.lock().unwrap().backend_pairing_id().unwrap()),
            "test-local-control-secret",
        );

        let response = client
            .get(format!("https://{}/status", addr))
            .header("origin", "https://paired.example.com")
            .header("authorization", format!("Bearer {}", token))
            .send()
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        let payload = response.json::<StatusResponseBody>().await.unwrap();
        assert_eq!(payload.status, AppStatus::Idle);
        assert!(payload.authenticated);
        assert_eq!(payload.api_host, "localhost");
        assert_eq!(payload.local_https_status, LocalHttpsStatus::Ready);

        server_task.abort();
    }

    #[tokio::test]
    async fn https_transport_handles_pairing_preflight_and_secure_post() {
        let (state, _audio_command_rx) = build_test_state();
        state.begin_pairing_session();

        let ready_identity = build_test_https_identity();
        let ca_certificate_der = ready_identity.persisted_identity.ca.certificate_der.clone();
        let app = build_test_transport_router(state.clone());
        let (addr, _shutdown_tx, server_task) =
            start_test_https_server(app, ready_identity.server_identity).await;
        set_test_local_port(&state, addr.port());

        let client = build_https_client(&ca_certificate_der);
        let pairing_url = format!("https://{}/pair/complete", addr);

        let preflight = client
            .request(Method::OPTIONS, &pairing_url)
            .header("origin", "https://paired.example.com")
            .header("access-control-request-method", "POST")
            .header(
                "access-control-request-headers",
                "content-type,x-nojoin-frontend-runtime,x-nojoin-frontend-pair-request,x-nojoin-frontend-source",
            )
            .header("access-control-request-private-network", "true")
            .send()
            .await
            .unwrap();

        assert!(preflight.status().is_success());
        assert_eq!(
            preflight
                .headers()
                .get("access-control-allow-origin")
                .unwrap(),
            "https://paired.example.com"
        );
        assert_eq!(
            preflight
                .headers()
                .get("access-control-allow-private-network")
                .unwrap(),
            "true"
        );
        let allowed_headers = preflight
            .headers()
            .get("access-control-allow-headers")
            .unwrap()
            .to_str()
            .unwrap()
            .to_ascii_lowercase();
        for expected_header in [
            "content-type",
            "x-nojoin-frontend-runtime",
            "x-nojoin-frontend-pair-request",
            "x-nojoin-frontend-source",
        ] {
            assert!(
                allowed_headers
                    .split(',')
                    .any(|allowed_header| allowed_header.trim() == expected_header),
                "missing allowed header {expected_header} in {allowed_headers}"
            );
        }

        let response = client
            .post(&pairing_url)
            .header("origin", "https://paired.example.com")
            .body("{}")
            .send()
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::NO_CONTENT);
        assert_eq!(
            response
                .headers()
                .get("access-control-allow-origin")
                .unwrap(),
            "https://paired.example.com"
        );

        server_task.abort();
    }

    #[tokio::test]
    async fn https_transport_has_no_plain_http_fallback() {
        let (state, _audio_command_rx) = build_test_state();
        let ready_identity = build_test_https_identity();
        let app = build_test_transport_router(state);
        let (addr, _shutdown_tx, server_task) =
            start_test_https_server(app, ready_identity.server_identity).await;

        let result = reqwest::Client::new()
            .get(format!("http://{}/status", addr))
            .send()
            .await;

        assert!(result.is_err());

        server_task.abort();
    }
}
