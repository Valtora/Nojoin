use reqwest::multipart;
use std::path::Path;
use anyhow::Result;
use tokio::fs::File;
use tokio::io::AsyncReadExt;
use crate::config::Config;

pub async fn upload_segment(recording_id: i64, sequence: i32, file_path: &Path, config: &Config) -> Result<()> {
    let client = reqwest::Client::new();
    
    // Read file manually to avoid issues with Form::file
    let mut file = File::open(file_path).await?;
    let mut contents = Vec::new();
    file.read_to_end(&mut contents).await?;
            
    let url = format!("{}/recordings/{}/segment?sequence={}", config.api_url, recording_id, sequence);
    
    let mut attempts = 0;
    const MAX_ATTEMPTS: u32 = 5;
    
    loop {
        attempts += 1;
        
        // Re-create the form for each attempt because the stream is consumed
        // We need to read the file again or clone the bytes
        let part = multipart::Part::bytes(contents.clone()).file_name("segment.wav");
        let form = multipart::Form::new().part("file", part);

        let res = client.post(&url)
            .header("Authorization", format!("Bearer {}", config.api_token))
            .multipart(form)
            .send()
            .await;

        match res {
            Ok(response) => {
                if response.status().is_success() {
                    return Ok(());
                } else {
                    eprintln!("Upload failed (attempt {}/{}): {}", attempts, MAX_ATTEMPTS, response.status());
                }
            },
            Err(e) => {
                eprintln!("Upload error (attempt {}/{}): {}", attempts, MAX_ATTEMPTS, e);
            }
        }

        if attempts >= MAX_ATTEMPTS {
            return Err(anyhow::anyhow!("Upload failed after {} attempts", MAX_ATTEMPTS));
        }

        let wait_time = 2u64.pow(attempts);
        tokio::time::sleep(tokio::time::Duration::from_secs(wait_time)).await;
    }
}

pub async fn finalize_recording(recording_id: i64, config: &Config) -> Result<()> {
    let client = reqwest::Client::new();
    let url = format!("{}/recordings/{}/finalize", config.api_url, recording_id);
    
    let mut attempts = 0;
    const MAX_ATTEMPTS: u32 = 5;

    loop {
        attempts += 1;
        let res = client.post(&url)
            .header("Authorization", format!("Bearer {}", config.api_token))
            .send()
            .await;
            
        match res {
            Ok(response) => {
                if response.status().is_success() {
                    return Ok(());
                } else {
                    eprintln!("Finalize failed (attempt {}/{}): {}", attempts, MAX_ATTEMPTS, response.status());
                }
            },
            Err(e) => {
                eprintln!("Finalize error (attempt {}/{}): {}", attempts, MAX_ATTEMPTS, e);
            }
        }

        if attempts >= MAX_ATTEMPTS {
            return Err(anyhow::anyhow!("Finalize failed after {} attempts", MAX_ATTEMPTS));
        }

        let wait_time = 2u64.pow(attempts);
        tokio::time::sleep(tokio::time::Duration::from_secs(wait_time)).await;
    }
}
