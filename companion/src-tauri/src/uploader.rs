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

async fn refresh_recording_upload_token(recording_id: i64, config: &Config) -> Result<String> {
    let client = crate::tls::create_client(config.tls_fingerprint())?;
    let url = format!(
        "{}/recordings/{}/upload-token",
        config.get_api_url(),
        recording_id
    );

    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", config.api_token()))
        .send()
        .await?;

    if !response.status().is_success() {
        return Err(anyhow::anyhow!(
            "Failed to refresh recording upload token: {}",
            response.status()
        ));
    }

    let payload = response.json::<RecordingUploadTokenResponse>().await?;
    Ok(payload.upload_token)
}

pub async fn upload_segment(
    recording_id: i64,
    sequence: i32,
    file_path: &Path,
    config: &Config,
    api_token: &str,
) -> Result<Option<String>> {
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
                    return Ok(refreshed_token);
                } else if response.status() == StatusCode::UNAUTHORIZED && refreshed_token.is_none() {
                    match refresh_recording_upload_token(recording_id, config).await {
                        Ok(new_token) => {
                            info!(
                                "Refreshed upload token for recording {} after a 401 while uploading segment {}.",
                                recording_id,
                                sequence,
                            );
                            upload_token = new_token.clone();
                            refreshed_token = Some(new_token);
                            continue;
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
                        response.status()
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
    recording_id: i64,
    config: &Config,
    api_token: &str,
) -> Result<Option<String>> {
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
                    return Ok(refreshed_token);
                } else if response.status() == StatusCode::UNAUTHORIZED && refreshed_token.is_none() {
                    match refresh_recording_upload_token(recording_id, config).await {
                        Ok(new_token) => {
                            info!(
                                "Refreshed upload token for recording {} after a 401 during finalize.",
                                recording_id,
                            );
                            upload_token = new_token.clone();
                            refreshed_token = Some(new_token);
                            continue;
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
                        response.status()
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
    recording_id: i64,
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
        let new_token = refresh_recording_upload_token(recording_id, config).await?;
        upload_token = new_token.clone();
        let retry = client
            .put(&url)
            .header("Authorization", format!("Bearer {}", upload_token))
            .send()
            .await?;
        if !retry.status().is_success() {
            return Err(anyhow::anyhow!("Failed to update status: {}", retry.status()));
        }
        return Ok(Some(new_token));
    }

    if !res.status().is_success() {
        return Err(anyhow::anyhow!("Failed to update status: {}", res.status()));
    }

    Ok(None)
}

pub async fn update_status_with_progress(
    recording_id: i64,
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
        let new_token = refresh_recording_upload_token(recording_id, config).await?;
        upload_token = new_token.clone();
        let retry = client
            .put(&url)
            .header("Authorization", format!("Bearer {}", upload_token))
            .send()
            .await?;
        if !retry.status().is_success() {
            return Err(anyhow::anyhow!("Failed to update status: {}", retry.status()));
        }
        return Ok(Some(new_token));
    }

    if !res.status().is_success() {
        return Err(anyhow::anyhow!("Failed to update status: {}", res.status()));
    }

    Ok(None)
}

pub async fn discard_recording(
    recording_id: i64,
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
        let new_token = refresh_recording_upload_token(recording_id, config).await?;
        upload_token = new_token.clone();
        let retry = client
            .post(&url)
            .header("Authorization", format!("Bearer {}", upload_token))
            .send()
            .await?;
        if !retry.status().is_success() {
            return Err(anyhow::anyhow!(
                "Failed to delete recording: {}",
                retry.status()
            ));
        }
        info!(
            "Recording {} deleted successfully (too short)",
            recording_id
        );
        return Ok(Some(new_token));
    }

    if !res.status().is_success() {
        return Err(anyhow::anyhow!(
            "Failed to delete recording: {}",
            res.status()
        ));
    }

    info!(
        "Recording {} deleted successfully (too short)",
        recording_id
    );
    Ok(None)
}
