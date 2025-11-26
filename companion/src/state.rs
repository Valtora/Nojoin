use std::sync::Mutex;
use std::sync::atomic::{AtomicU32, Ordering};
use serde::{Serialize, Deserialize};
use crossbeam_channel::Sender;
use crate::config::Config;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum AppStatus {
    Idle,
    Recording,
    Paused,
    Uploading,
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
}

impl AppState {
    pub fn set_input_level(&self, level: f32) {
        // Convert 0.0-1.0 to 0-100
        let scaled = (level.clamp(0.0, 1.0) * 100.0) as u32;
        self.input_level.store(scaled, Ordering::Relaxed);
    }
    
    pub fn set_output_level(&self, level: f32) {
        let scaled = (level.clamp(0.0, 1.0) * 100.0) as u32;
        self.output_level.store(scaled, Ordering::Relaxed);
    }
    
    pub fn get_input_level(&self) -> u32 {
        self.input_level.load(Ordering::Relaxed)
    }
    
    pub fn get_output_level(&self) -> u32 {
        self.output_level.load(Ordering::Relaxed)
    }
}


#[derive(Debug, Clone)]
pub enum AudioCommand {
    Start(i64), // recording_id
    Pause,
    Resume,
    Stop,
}
