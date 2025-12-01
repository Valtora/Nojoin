use std::sync::Mutex;
use std::sync::atomic::{AtomicU32, AtomicBool, Ordering};
use serde::{Serialize, Deserialize};
use crossbeam_channel::Sender;
use crate::config::Config;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum AppStatus {
    Idle,
    Recording,
    Paused,
    Uploading,
    BackendOffline,
    Error(String),
}

pub struct AppState {
    pub status: Mutex<AppStatus>,
    pub current_recording_id: Mutex<Option<i64>>,
    pub current_sequence: Mutex<i32>,
    pub audio_command_tx: Sender<AudioCommand>,
    pub config: Mutex<Config>,
    pub recording_start_time: Mutex<Option<std::time::SystemTime>>,
    pub accumulated_duration: Mutex<std::time::Duration>,
    // Audio levels (0-100 scaled, stored as u32 for atomic access)
    pub input_level: AtomicU32,
    pub output_level: AtomicU32,
    // Dynamic Web URL fetched from backend
    pub web_url: Mutex<Option<String>>,
    pub is_backend_connected: AtomicBool,
}

impl AppState {
    pub fn record_input_level(&self, level: f32) {
        // Convert 0.0-1.0 to 0-100
        let scaled = (level.clamp(0.0, 1.0) * 100.0) as u32;
        self.input_level.fetch_max(scaled, Ordering::Relaxed);
    }
    
    pub fn record_output_level(&self, level: f32) {
        let scaled = (level.clamp(0.0, 1.0) * 100.0) as u32;
        self.output_level.fetch_max(scaled, Ordering::Relaxed);
    }
    
    pub fn take_input_level(&self) -> u32 {
        self.input_level.swap(0, Ordering::Relaxed)
    }
    
    pub fn take_output_level(&self) -> u32 {
        self.output_level.swap(0, Ordering::Relaxed)
    }

    /// Check if the companion has a valid API token configured
    pub fn is_authenticated(&self) -> bool {
        let config = self.config.lock().unwrap();
        !config.api_token.is_empty()
    }
}


#[derive(Debug, Clone)]
pub enum AudioCommand {
    Start(i64), // recording_id
    Pause,
    Resume,
    Stop,
}
