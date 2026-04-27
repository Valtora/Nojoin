#![cfg_attr(not(any(windows, test)), allow(dead_code))]

use crate::config::{BackendConnection, Config};
use crate::secret_store::{self, BackendSecretBundle};
use reqwest::Method;
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};

const COMPANION_NETWORK_TIMEOUT_SECS: u64 = 10;

#[derive(Serialize)]
struct CompanionCredentialExchangeRequest {
    pairing_session_id: String,
    companion_credential_secret: String,
}

#[derive(Deserialize)]
struct CompanionAccessTokenResponse {
    access_token: String,
}

#[derive(Deserialize, Default)]
struct PairingManagementResponse {
    revoked_count: Option<u64>,
    cancelled_count: Option<u64>,
}

fn backend_pairing_id(backend: &BackendConnection) -> Result<String, String> {
    backend
        .backend_pairing_id
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(|value| value.to_string())
        .ok_or_else(|| "Backend pairing id is missing from the paired backend state.".to_string())
}

pub async fn exchange_access_token_for_config(config: &Config) -> Result<String, String> {
    let backend = config.backend_connection().ok_or_else(|| {
        "No paired backend is configured for companion access token exchange.".to_string()
    })?;
    exchange_access_token_for_backend(&backend).await
}

pub async fn exchange_access_token_for_backend(
    backend: &BackendConnection,
) -> Result<String, String> {
    let bundle = secret_store::load_backend_secret_bundle_for_backend(backend)?;
    exchange_access_token_for_backend_with_bundle(backend, &bundle).await
}

pub async fn exchange_access_token_for_backend_with_bundle(
    backend: &BackendConnection,
    bundle: &BackendSecretBundle,
) -> Result<String, String> {
    let pairing_session_id = backend_pairing_id(backend)?;
    exchange_access_token_for_target(
        &backend.api_protocol,
        &backend.api_host,
        backend.api_port,
        &pairing_session_id,
        &bundle.companion_credential_secret,
        backend.tls_fingerprint.clone(),
    )
    .await
}

pub async fn exchange_access_token_for_target(
    protocol: &str,
    host: &str,
    port: u16,
    pairing_session_id: &str,
    companion_credential_secret: &str,
    tls_fingerprint: Option<String>,
) -> Result<String, String> {
    let backend_target = format!("{}://{}:{}", protocol, host, port);
    let url = format!("{}/api/v1/login/companion-token/exchange", backend_target);
    let started_at = Instant::now();
    let client = crate::tls::create_client(tls_fingerprint).map_err(|error| error.to_string())?;

    let response = match tokio::time::timeout(
        Duration::from_secs(COMPANION_NETWORK_TIMEOUT_SECS),
        client
            .post(url.clone())
            .json(&CompanionCredentialExchangeRequest {
                pairing_session_id: pairing_session_id.to_string(),
                companion_credential_secret: companion_credential_secret.to_string(),
            })
            .send(),
    )
    .await
    {
        Ok(Ok(response)) => response,
        Ok(Err(error)) => {
            return Err(format!(
                "Companion credential exchange against {} failed after {} ms: {}",
                backend_target,
                started_at.elapsed().as_millis(),
                error
            ));
        }
        Err(_) => {
            return Err(format!(
                "Companion credential exchange against {} timed out after {} ms.",
                backend_target,
                started_at.elapsed().as_millis()
            ));
        }
    };

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.map_err(|error| error.to_string())?;
        return Err(format!(
            "Companion credential exchange against {} failed in {} ms with status {} and body '{}'",
            backend_target,
            started_at.elapsed().as_millis(),
            status,
            body
        ));
    }

    let payload = response
        .json::<CompanionAccessTokenResponse>()
        .await
        .map_err(|error| format!("Failed to decode companion access token response: {}", error))?;
    Ok(payload.access_token)
}

async fn send_pairing_management_request_for_target(
    method: Method,
    protocol: &str,
    host: &str,
    port: u16,
    pairing_session_id: &str,
    companion_credential_secret: &str,
    tls_fingerprint: Option<String>,
    path: &str,
) -> Result<u64, String> {
    let access_token = exchange_access_token_for_target(
        protocol,
        host,
        port,
        pairing_session_id,
        companion_credential_secret,
        tls_fingerprint.clone(),
    )
    .await?;
    let backend_target = format!("{}://{}:{}", protocol, host, port);
    let url = format!("{}{}", format!("{}/api/v1", backend_target), path);
    let started_at = Instant::now();
    let client = crate::tls::create_client(tls_fingerprint).map_err(|error| error.to_string())?;

    let response = match tokio::time::timeout(
        Duration::from_secs(COMPANION_NETWORK_TIMEOUT_SECS),
        client
            .request(method.clone(), url)
            .header("Authorization", format!("Bearer {}", access_token))
            .send(),
    )
    .await
    {
        Ok(Ok(response)) => response,
        Ok(Err(error)) => {
            return Err(format!(
                "Pairing management request {} {}{} failed after {} ms: {}",
                method.as_str(),
                backend_target,
                path,
                started_at.elapsed().as_millis(),
                error
            ));
        }
        Err(_) => {
            return Err(format!(
                "Pairing management request {} {}{} timed out after {} ms.",
                method.as_str(),
                backend_target,
                path,
                started_at.elapsed().as_millis()
            ));
        }
    };

    let status = response.status();
    let body = response.text().await.map_err(|error| error.to_string())?;
    if !status.is_success() {
        return Err(format!(
            "Pairing management request {} {}{} failed in {} ms with status {} and body '{}'",
            method.as_str(),
            backend_target,
            path,
            started_at.elapsed().as_millis(),
            status,
            body
        ));
    }

    let parsed: PairingManagementResponse = serde_json::from_str(&body).unwrap_or_default();
    Ok(parsed
        .revoked_count
        .or(parsed.cancelled_count)
        .unwrap_or_default())
}

async fn send_pairing_management_request_for_backend_with_bundle(
    method: Method,
    backend: &BackendConnection,
    bundle: &BackendSecretBundle,
    path: &str,
) -> Result<u64, String> {
    let pairing_session_id = backend_pairing_id(backend)?;
    send_pairing_management_request_for_target(
        method,
        &backend.api_protocol,
        &backend.api_host,
        backend.api_port,
        &pairing_session_id,
        &bundle.companion_credential_secret,
        backend.tls_fingerprint.clone(),
        path,
    )
    .await
}

async fn send_pairing_management_request_for_backend(
    method: Method,
    backend: &BackendConnection,
    path: &str,
) -> Result<u64, String> {
    let bundle = secret_store::load_backend_secret_bundle_for_backend(backend)?;
    send_pairing_management_request_for_backend_with_bundle(method, backend, &bundle, path).await
}

pub async fn cancel_pending_pairing_for_target(
    protocol: &str,
    host: &str,
    port: u16,
    pairing_session_id: &str,
    companion_credential_secret: &str,
    tls_fingerprint: Option<String>,
) -> Result<u64, String> {
    send_pairing_management_request_for_target(
        Method::DELETE,
        protocol,
        host,
        port,
        pairing_session_id,
        companion_credential_secret,
        tls_fingerprint,
        "/login/companion-pairing/pending",
    )
    .await
}

pub async fn cancel_pending_pairing_for_backend(
    backend: &BackendConnection,
) -> Result<u64, String> {
    send_pairing_management_request_for_backend(
        Method::DELETE,
        backend,
        "/login/companion-pairing/pending",
    )
    .await
}

pub async fn revoke_backend_pairings_with_bundle(
    backend: &BackendConnection,
    bundle: &BackendSecretBundle,
) -> Result<u64, String> {
    send_pairing_management_request_for_backend_with_bundle(
        Method::DELETE,
        backend,
        bundle,
        "/login/companion-pairing",
    )
    .await
}

pub async fn signal_explicit_backend_disconnect_with_bundle(
    backend: &BackendConnection,
    bundle: &BackendSecretBundle,
) -> Result<u64, String> {
    send_pairing_management_request_for_backend_with_bundle(
        Method::POST,
        backend,
        bundle,
        "/login/companion-pairing/disconnect",
    )
    .await
}
