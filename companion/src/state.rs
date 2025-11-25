use std::sync::Mutex;
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
}


#[derive(Debug, Clone)]
pub enum AudioCommand {
    Start(i64), // recording_id
    Pause,
    Resume,
    Stop,
}
