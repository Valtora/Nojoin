use reqwest::multipart;
use std::path::Path;
use anyhow::Result;
use tokio::fs::File;
use tokio::io::AsyncReadExt;
use crate::config::Config;

pub async fn upload_segment(recording_id: i32, sequence: i32, file_path: &Path, config: &Config) -> Result<()> {
    let client = reqwest::Client::new();
    
    // Read file manually to avoid issues with Form::file
    let mut file = File::open(file_path).await?;
    let mut contents = Vec::new();
    file.read_to_end(&mut contents).await?;
    
    let part = multipart::Part::bytes(contents).file_name("segment.wav");
    let form = multipart::Form::new().part("file", part);
        
    let url = format!("{}/recordings/{}/segment?sequence={}", config.api_url, recording_id, sequence);
    
    let res = client.post(&url)
        .header("Authorization", format!("Bearer {}", config.api_token))
        .multipart(form)
        .send()
        .await?;
        
    if !res.status().is_success() {
        return Err(anyhow::anyhow!("Upload failed: {}", res.status()));
    }
    
    Ok(())
}

pub async fn finalize_recording(recording_id: i32, config: &Config) -> Result<()> {
    let client = reqwest::Client::new();
    let url = format!("{}/recordings/{}/finalize", config.api_url, recording_id);
    
    let res = client.post(&url)
        .header("Authorization", format!("Bearer {}", config.api_token))
        .send()
        .await?;
        
    if !res.status().is_success() {
        return Err(anyhow::anyhow!("Finalize failed: {}", res.status()));
    }
    
    Ok(())
}
