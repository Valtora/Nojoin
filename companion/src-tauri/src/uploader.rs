use crate::config::Config;
use anyhow::Result;
use log::{error, info, warn};
use reqwest::multipart;
use reqwest::StatusCode;
use serde::Deserialize;
use std::path::Path;
use tokio::fs::File;
use tokio::io::AsyncReadExt;

#[derive(Deserialize)]
struct RecordingUploadTokenResponse {
    upload_token: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UploadAttemptOutcome {
    Completed { refreshed_token: Option<String> },
    Aborted,
}

enum UploadTokenRefreshOutcome {
    Refreshed(String),
    Aborted,
}

fn body_indicates_terminal_upload_state(body: &str) -> bool {
    let normalized = body.to_ascii_lowercase();
    [
        "recording is no longer accepting companion uploads",
        "recording is no longer accepting companion status updates",
        "recording is not in uploading state",
        "only in-flight companion uploads can be discarded",
    ]
    .iter()
    .any(|marker| normalized.contains(marker))
}

fn response_indicates_terminal_upload_state(status: StatusCode, body: &str) -> bool {
    matches!(status, StatusCode::NOT_FOUND | StatusCode::GONE)
        || matches!(status, StatusCode::BAD_REQUEST | StatusCode::CONFLICT)
            && body_indicates_terminal_upload_state(body)
}

async fn refresh_recording_upload_token(
    recording_id: &str,
    config: &Config,
) -> Result<UploadTokenRefreshOutcome> {
    let client = crate::tls::create_client(config.tls_fingerprint())?;
    let access_token = crate::companion_auth::exchange_access_token_for_config(config)
        .await
        .map_err(|error| anyhow::anyhow!(error))?;
    let url = format!(
        "{}/recordings/{}/upload-token",
        config.get_api_url(),
        recording_id
    );

    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", access_token))
        .send()
        .await?;

    if response.status().is_success() {
        let payload = response.json::<RecordingUploadTokenResponse>().await?;
        return Ok(UploadTokenRefreshOutcome::Refreshed(payload.upload_token));
    }

    let status = response.status();
    let body = response.text().await.unwrap_or_default();
    if response_indicates_terminal_upload_state(status, &body) {
        return Ok(UploadTokenRefreshOutcome::Aborted);
    }

    if !status.is_success() {
        return Err(anyhow::anyhow!(
            "Failed to refresh recording upload token: {}",
            status
        ));
    }

    unreachable!("successful responses are returned above")
}

pub async fn upload_segment(
    recording_id: &str,
    sequence: i32,
    file_path: &Path,
    config: &Config,
    api_token: &str,
) -> Result<UploadAttemptOutcome> {
    // Allow invalid certs for self-signed SSL (development)
    let client = crate::tls::create_client(config.tls_fingerprint())?;
    let mut upload_token = api_token.to_string();
    let mut refreshed_token = None;

    // Read file manually to avoid issues with Form::file
    let mut file = File::open(file_path).await?;
    let mut contents = Vec::new();
    file.read_to_end(&mut contents).await?;

    let url = format!(
        "{}/recordings/{}/segment?sequence={}",
        config.get_api_url(),
        recording_id,
        sequence
    );

    let mut attempts = 0;
    const MAX_ATTEMPTS: u32 = 60; // Retry for ~5 minutes

    loop {
        attempts += 1;

        // Re-create the form for each attempt because the stream is consumed
        // Re-reads file or clones bytes.
        let part = multipart::Part::bytes(contents.clone()).file_name("segment.wav");
        let form = multipart::Form::new().part("file", part);

        let res = client
            .post(&url)
            .header("Authorization", format!("Bearer {}", upload_token))
            .multipart(form)
            .send()
            .await;

        match res {
            Ok(response) => {
                if response.status().is_success() {
                    info!(
                        "Segment {} uploaded successfully for recording {}",
                        sequence, recording_id
                    );
                    return Ok(UploadAttemptOutcome::Completed { refreshed_token });
                }

                let status = response.status();
                let body = response.text().await.unwrap_or_default();

                if response_indicates_terminal_upload_state(status, &body) {
                    info!(
                        "Stopping upload for recording {} segment {} because Nojoin closed the upload session ({}).",
                        recording_id,
                        sequence,
                        status,
                    );
                    return Ok(UploadAttemptOutcome::Aborted);
                }

                if status == StatusCode::UNAUTHORIZED && refreshed_token.is_none() {
                    match refresh_recording_upload_token(recording_id, config).await {
                        Ok(UploadTokenRefreshOutcome::Refreshed(new_token)) => {
                            info!(
                                "Refreshed upload token for recording {} after a 401 while uploading segment {}.",
                                recording_id,
                                sequence,
                            );
                            upload_token = new_token.clone();
                            refreshed_token = Some(new_token);
                            continue;
                        }
                        Ok(UploadTokenRefreshOutcome::Aborted) => {
                            info!(
                                "Stopping upload for recording {} segment {} because the upload token refresh endpoint reported a closed upload session.",
                                recording_id,
                                sequence,
                            );
                            return Ok(UploadAttemptOutcome::Aborted);
                        }
                        Err(error) => {
                            warn!(
                                "Upload token refresh failed for recording {} after a 401 on segment {}: {}",
                                recording_id,
                                sequence,
                                error,
                            );
                        }
                    }
                } else {
                    warn!(
                        "Upload failed (attempt {}/{}): {}",
                        attempts,
                        MAX_ATTEMPTS,
                        status
                    );
                }
            }
            Err(e) => {
                warn!(
                    "Upload error (attempt {}/{}): {}",
                    attempts, MAX_ATTEMPTS, e
                );
            }
        }

        if attempts >= MAX_ATTEMPTS {
            error!("Upload failed after {} attempts", MAX_ATTEMPTS);
            return Err(anyhow::anyhow!(
                "Upload failed after {} attempts",
                MAX_ATTEMPTS
            ));
        }

        let wait_time = std::cmp::min(2u64.pow(attempts), 5);
        tokio::time::sleep(tokio::time::Duration::from_secs(wait_time)).await;
    }
}

pub async fn finalize_recording(
    recording_id: &str,
    config: &Config,
    api_token: &str,
) -> Result<UploadAttemptOutcome> {
    let client = crate::tls::create_client(config.tls_fingerprint())?;
    let mut upload_token = api_token.to_string();
    let mut refreshed_token = None;
    let url = format!(
        "{}/recordings/{}/finalize",
        config.get_api_url(),
        recording_id
    );

    let mut attempts = 0;
    const MAX_ATTEMPTS: u32 = 60;

    loop {
        attempts += 1;
        let res = client
            .post(&url)
            .header("Authorization", format!("Bearer {}", upload_token))
            .send()
            .await;

        match res {
            Ok(response) => {
                if response.status().is_success() {
                    info!("Recording {} finalized successfully", recording_id);
                    return Ok(UploadAttemptOutcome::Completed { refreshed_token });
                }

                let status = response.status();
                let body = response.text().await.unwrap_or_default();

                if response_indicates_terminal_upload_state(status, &body) {
                    info!(
                        "Stopping finalize for recording {} because Nojoin closed the upload session ({}).",
                        recording_id,
                        status,
                    );
                    return Ok(UploadAttemptOutcome::Aborted);
                }

                if status == StatusCode::UNAUTHORIZED && refreshed_token.is_none() {
                    match refresh_recording_upload_token(recording_id, config).await {
                        Ok(UploadTokenRefreshOutcome::Refreshed(new_token)) => {
                            info!(
                                "Refreshed upload token for recording {} after a 401 during finalize.",
                                recording_id,
                            );
                            upload_token = new_token.clone();
                            refreshed_token = Some(new_token);
                            continue;
                        }
                        Ok(UploadTokenRefreshOutcome::Aborted) => {
                            info!(
                                "Stopping finalize for recording {} because the upload token refresh endpoint reported a closed upload session.",
                                recording_id,
                            );
                            return Ok(UploadAttemptOutcome::Aborted);
                        }
                        Err(error) => {
                            warn!(
                                "Upload token refresh failed for recording {} after a 401 during finalize: {}",
                                recording_id,
                                error,
                            );
                        }
                    }
                } else {
                    warn!(
                        "Finalize failed (attempt {}/{}): {}",
                        attempts,
                        MAX_ATTEMPTS,
                        status
                    );
                }
            }
            Err(e) => {
                warn!(
                    "Finalize error (attempt {}/{}): {}",
                    attempts, MAX_ATTEMPTS, e
                );
            }
        }

        if attempts >= MAX_ATTEMPTS {
            error!("Finalize failed after {} attempts", MAX_ATTEMPTS);
            return Err(anyhow::anyhow!(
                "Finalize failed after {} attempts",
                MAX_ATTEMPTS
            ));
        }

        let wait_time = std::cmp::min(2u64.pow(attempts), 5);
        tokio::time::sleep(tokio::time::Duration::from_secs(wait_time)).await;
    }
}

pub async fn update_client_status(
    recording_id: &str,
    status: &str,
    config: &Config,
    api_token: &str,
) -> Result<Option<String>> {
    let client = crate::tls::create_client(config.tls_fingerprint())?;
    let mut upload_token = api_token.to_string();
    let url = format!(
        "{}/recordings/{}/client_status?status={}",
        config.get_api_url(),
        recording_id,
        status
    );

    let res = client
        .put(&url)
        .header("Authorization", format!("Bearer {}", upload_token))
        .send()
        .await?;

    if res.status() == StatusCode::UNAUTHORIZED {
        let refresh_outcome = refresh_recording_upload_token(recording_id, config).await?;
        let UploadTokenRefreshOutcome::Refreshed(new_token) = refresh_outcome else {
            info!(
                "Skipping client status update for recording {} because Nojoin closed the upload session.",
                recording_id,
            );
            return Ok(None);
        };
        upload_token = new_token.clone();
        let retry = client
            .put(&url)
            .header("Authorization", format!("Bearer {}", upload_token))
            .send()
            .await?;
        let retry_status = retry.status();
        let retry_body = retry.text().await.unwrap_or_default();
        if response_indicates_terminal_upload_state(retry_status, &retry_body) {
            info!(
                "Skipping client status update for recording {} because Nojoin closed the upload session after token refresh.",
                recording_id,
            );
            return Ok(None);
        }
        if !retry_status.is_success() {
            return Err(anyhow::anyhow!(
                "Failed to update status: {}",
                retry_status
            ));
        }
        return Ok(Some(new_token));
    }

    let status = res.status();
    let body = res.text().await.unwrap_or_default();
    if response_indicates_terminal_upload_state(status, &body) {
        info!(
            "Skipping client status update for recording {} because Nojoin closed the upload session.",
            recording_id,
        );
        return Ok(None);
    }

    if !status.is_success() {
        return Err(anyhow::anyhow!("Failed to update status: {}", status));
    }

    Ok(None)
}

pub async fn update_status_with_progress(
    recording_id: &str,
    status: &str,
    progress: i32,
    config: &Config,
    api_token: &str,
) -> Result<Option<String>> {
    let client = crate::tls::create_client(config.tls_fingerprint())?;
    let mut upload_token = api_token.to_string();
    let url = format!(
        "{}/recordings/{}/client_status?status={}&upload_progress={}",
        config.get_api_url(),
        recording_id,
        status,
        progress
    );

    let res = client
        .put(&url)
        .header("Authorization", format!("Bearer {}", upload_token))
        .send()
        .await?;

    if res.status() == StatusCode::UNAUTHORIZED {
        let refresh_outcome = refresh_recording_upload_token(recording_id, config).await?;
        let UploadTokenRefreshOutcome::Refreshed(new_token) = refresh_outcome else {
            info!(
                "Skipping upload progress update for recording {} because Nojoin closed the upload session.",
                recording_id,
            );
            return Ok(None);
        };
        upload_token = new_token.clone();
        let retry = client
            .put(&url)
            .header("Authorization", format!("Bearer {}", upload_token))
            .send()
            .await?;
        let retry_status = retry.status();
        let retry_body = retry.text().await.unwrap_or_default();
        if response_indicates_terminal_upload_state(retry_status, &retry_body) {
            info!(
                "Skipping upload progress update for recording {} because Nojoin closed the upload session after token refresh.",
                recording_id,
            );
            return Ok(None);
        }
        if !retry_status.is_success() {
            return Err(anyhow::anyhow!(
                "Failed to update status: {}",
                retry_status
            ));
        }
        return Ok(Some(new_token));
    }

    let status = res.status();
    let body = res.text().await.unwrap_or_default();
    if response_indicates_terminal_upload_state(status, &body) {
        info!(
            "Skipping upload progress update for recording {} because Nojoin closed the upload session.",
            recording_id,
        );
        return Ok(None);
    }

    if !status.is_success() {
        return Err(anyhow::anyhow!("Failed to update status: {}", status));
    }

    Ok(None)
}

pub async fn discard_recording(
    recording_id: &str,
    config: &Config,
    api_token: &str,
) -> Result<Option<String>> {
    let client = crate::tls::create_client(config.tls_fingerprint())?;
    let mut upload_token = api_token.to_string();
    let url = format!(
        "{}/recordings/{}/discard",
        config.get_api_url(),
        recording_id
    );

    let res = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", upload_token))
        .send()
        .await?;

    if res.status() == StatusCode::UNAUTHORIZED {
        let refresh_outcome = refresh_recording_upload_token(recording_id, config).await?;
        let UploadTokenRefreshOutcome::Refreshed(new_token) = refresh_outcome else {
            info!(
                "Discard for recording {} became unnecessary because Nojoin had already closed the upload session.",
                recording_id,
            );
            return Ok(None);
        };
        upload_token = new_token.clone();
        let retry = client
            .post(&url)
            .header("Authorization", format!("Bearer {}", upload_token))
            .send()
            .await?;
        let retry_status = retry.status();
        let retry_body = retry.text().await.unwrap_or_default();
        if response_indicates_terminal_upload_state(retry_status, &retry_body) {
            info!(
                "Discard for recording {} became unnecessary because Nojoin had already closed the upload session after token refresh.",
                recording_id,
            );
            return Ok(None);
        }
        if !retry_status.is_success() {
            return Err(anyhow::anyhow!(
                "Failed to delete recording: {}",
                retry_status
            ));
        }
        info!(
            "Recording {} deleted successfully (too short)",
            recording_id
        );
        return Ok(Some(new_token));
    }

    let status = res.status();
    let body = res.text().await.unwrap_or_default();
    if response_indicates_terminal_upload_state(status, &body) {
        info!(
            "Discard for recording {} became unnecessary because Nojoin had already closed the upload session.",
            recording_id,
        );
        return Ok(None);
    }

    if !status.is_success() {
        return Err(anyhow::anyhow!(
            "Failed to delete recording: {}",
            status
        ));
    }

    info!(
        "Recording {} deleted successfully (too short)",
        recording_id
    );
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::{body_indicates_terminal_upload_state, response_indicates_terminal_upload_state};
    use reqwest::StatusCode;

    #[test]
    fn terminal_upload_markers_cover_closed_companion_sessions() {
        assert!(body_indicates_terminal_upload_state(
            r#"{"detail":"Recording is no longer accepting companion uploads"}"#,
        ));
        assert!(body_indicates_terminal_upload_state(
            r#"{"detail":"Recording is no longer accepting companion status updates"}"#,
        ));
        assert!(body_indicates_terminal_upload_state(
            r#"{"detail":"Recording is not in uploading state"}"#,
        ));
    }

    #[test]
    fn conflict_requires_terminal_upload_marker() {
        assert!(response_indicates_terminal_upload_state(
            StatusCode::CONFLICT,
            r#"{"detail":"Recording is no longer accepting companion uploads"}"#,
        ));
        assert!(!response_indicates_terminal_upload_state(
            StatusCode::CONFLICT,
            r#"{"detail":"Recording upload is still in progress; finalize after all segment uploads complete."}"#,
        ));
    }

    #[test]
    fn not_found_is_treated_as_terminal_upload_abort() {
        assert!(response_indicates_terminal_upload_state(
            StatusCode::NOT_FOUND,
            "",
        ));
    }
}
