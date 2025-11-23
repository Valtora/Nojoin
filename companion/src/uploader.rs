use reqwest::multipart;
use std::path::Path;
use anyhow::Result;
use tokio::fs::File;
use tokio::io::AsyncReadExt;

pub async fn upload_segment(recording_id: i32, sequence: i32, file_path: &Path) -> Result<()> {
    let client = reqwest::Client::new();
    
    // Read file manually to avoid issues with Form::file
    let mut file = File::open(file_path).await?;
    let mut contents = Vec::new();
    file.read_to_end(&mut contents).await?;
    
    let part = multipart::Part::bytes(contents).file_name("segment.wav");
    let form = multipart::Form::new().part("file", part);
        
    let url = format!("http://localhost:8000/api/v1/recordings/{}/segment?sequence={}", recording_id, sequence);
    
    let res = client.post(&url)
        .multipart(form)
        .send()
        .await?;
        
    if !res.status().is_success() {
        return Err(anyhow::anyhow!("Upload failed: {}", res.status()));
    }
    
    Ok(())
}

pub async fn finalize_recording(recording_id: i32) -> Result<()> {
    let client = reqwest::Client::new();
    let url = format!("http://localhost:8000/api/v1/recordings/{}/finalize", recording_id);
    
    let res = client.post(&url)
        .send()
        .await?;
        
    if !res.status().is_success() {
        return Err(anyhow::anyhow!("Finalize failed: {}", res.status()));
    }
    
    Ok(())
}
