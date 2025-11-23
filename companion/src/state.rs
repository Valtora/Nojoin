use std::sync::Mutex;
use serde::{Serialize, Deserialize};
use crossbeam_channel::Sender;
use crate::config::Config;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum AppStatus {
    Idle,
    Recording,
    Paused,
    Error(String),
}

pub struct AppState {
    pub status: Mutex<AppStatus>,
    pub current_recording_id: Mutex<Option<i32>>,
    pub current_sequence: Mutex<i32>,
    pub audio_command_tx: Sender<AudioCommand>,
    pub config: Mutex<Config>,
}


#[derive(Debug, Clone)]
pub enum AudioCommand {
    Start(i32), // recording_id
    Pause,
    Resume,
    Stop,
}
