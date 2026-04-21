use crate::config::Config;
use crossbeam_channel::Sender;
use rand::Rng;
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Mutex;
use std::time::{Duration, SystemTime};
use tauri::menu::{CheckMenuItem, MenuItem};
use tauri::tray::TrayIcon;
use tauri::Wry;

const PAIRING_CODE_ALPHABET: &[u8] = b"ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const PAIRING_CODE_LENGTH: usize = 8;
pub const PAIRING_WINDOW_LIFETIME_SECS: u64 = 300;
pub const MAX_PAIRING_ATTEMPTS: u8 = 5;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum AppStatus {
    Idle,
    Recording,
    Paused,
    Uploading,
    BackendOffline,
    Error(String),
}

#[derive(Debug, Clone)]
pub struct PairingSession {
    pub canonical_code: String,
    pub display_code: String,
    pub expires_at: SystemTime,
    pub failed_attempts: u8,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PairingValidationError {
    NotActive,
    Expired,
    Invalid,
    LockedOut,
}

pub fn canonicalize_pairing_code(input: &str) -> String {
    input
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric())
        .map(|ch| ch.to_ascii_uppercase())
        .collect()
}

fn format_pairing_code(canonical_code: &str) -> String {
    let (left, right) = canonical_code.split_at(4);
    format!("{}-{}", left, right)
}

impl PairingSession {
    pub fn new() -> Self {
        let mut rng = rand::thread_rng();
        let canonical_code: String = (0..PAIRING_CODE_LENGTH)
            .map(|_| {
                let index = rng.gen_range(0..PAIRING_CODE_ALPHABET.len());
                PAIRING_CODE_ALPHABET[index] as char
            })
            .collect();

        Self {
            display_code: format_pairing_code(&canonical_code),
            canonical_code,
            expires_at: SystemTime::now() + Duration::from_secs(PAIRING_WINDOW_LIFETIME_SECS),
            failed_attempts: 0,
        }
    }

    pub fn is_expired(&self) -> bool {
        SystemTime::now() >= self.expires_at
    }

    pub fn remaining_seconds(&self) -> u64 {
        self.expires_at
            .duration_since(SystemTime::now())
            .map(|duration| duration.as_secs())
            .unwrap_or(0)
    }
}

pub struct AppState {
    pub status: Mutex<AppStatus>,
    pub current_recording_id: Mutex<Option<i64>>,
    pub current_recording_token: Mutex<Option<String>>,
    pub current_sequence: Mutex<i32>,
    pub audio_command_tx: Sender<AudioCommand>,
    pub config: Mutex<Config>,
    pub recording_start_time: Mutex<Option<std::time::SystemTime>>,
    pub accumulated_duration: Mutex<std::time::Duration>,
    // Audio levels (0-100 scaled, stored as u32 for atomic access)
    pub input_level: AtomicU32,
    pub output_level: AtomicU32,
    pub live_input_level: AtomicU32,
    pub live_output_level: AtomicU32,
    pub is_backend_connected: AtomicBool,
    // Update status
    pub update_available: AtomicBool,
    pub latest_version: Mutex<Option<String>>,
    pub latest_update_url: Mutex<Option<String>>,

    // Tray Menu Items
    pub tray_status_item: Mutex<Option<MenuItem<Wry>>>,
    pub tray_run_on_startup_item: Mutex<Option<CheckMenuItem<Wry>>>,
    pub tray_open_web_item: Mutex<Option<MenuItem<Wry>>>,
    pub tray_icon: Mutex<Option<TrayIcon<Wry>>>,

    // Manual pairing state
    pub pairing_session: Mutex<Option<PairingSession>>,
}

impl AppState {
    pub fn record_input_level(&self, level: f32) {
        // Convert 0.0-1.0 to 0-100
        let scaled = (level.clamp(0.0, 1.0) * 100.0) as u32;
        self.input_level.fetch_max(scaled, Ordering::Relaxed);
        self.live_input_level.store(scaled, Ordering::Relaxed);
    }

    pub fn record_output_level(&self, level: f32) {
        let scaled = (level.clamp(0.0, 1.0) * 100.0) as u32;
        self.output_level.fetch_max(scaled, Ordering::Relaxed);
        self.live_output_level.store(scaled, Ordering::Relaxed);
    }

    pub fn take_input_level(&self) -> u32 {
        self.input_level.swap(0, Ordering::Relaxed)
    }

    pub fn take_output_level(&self) -> u32 {
        self.output_level.swap(0, Ordering::Relaxed)
    }

    pub fn peek_live_input_level(&self) -> u32 {
        self.live_input_level.load(Ordering::Relaxed)
    }

    pub fn peek_live_output_level(&self) -> u32 {
        self.live_output_level.load(Ordering::Relaxed)
    }

    /// Check if the companion has a valid API token configured
    pub fn is_authenticated(&self) -> bool {
        let config = self.config.lock().unwrap();
        config.is_authenticated()
    }

    pub fn begin_pairing_session(&self) -> PairingSession {
        let session = PairingSession::new();
        let mut pairing_session = self.pairing_session.lock().unwrap();
        *pairing_session = Some(session.clone());
        session
    }

    pub fn clear_pairing_session(&self) {
        let mut pairing_session = self.pairing_session.lock().unwrap();
        *pairing_session = None;
    }

    pub fn current_pairing_session(&self) -> Option<PairingSession> {
        let mut pairing_session = self.pairing_session.lock().unwrap();

        if pairing_session
            .as_ref()
            .map(|session| session.is_expired())
            .unwrap_or(false)
        {
            *pairing_session = None;
        }

        pairing_session.clone()
    }

    pub fn is_pairing_active(&self) -> bool {
        self.current_pairing_session().is_some()
    }

    pub fn validate_pairing_code(
        &self,
        submitted_code: &str,
    ) -> Result<(), PairingValidationError> {
        let mut pairing_session = self.pairing_session.lock().unwrap();
        let session = match pairing_session.as_mut() {
            Some(session) => session,
            None => return Err(PairingValidationError::NotActive),
        };

        if session.is_expired() {
            *pairing_session = None;
            return Err(PairingValidationError::Expired);
        }

        if session.canonical_code == canonicalize_pairing_code(submitted_code) {
            return Ok(());
        }

        session.failed_attempts += 1;
        if session.failed_attempts >= MAX_PAIRING_ATTEMPTS {
            *pairing_session = None;
            return Err(PairingValidationError::LockedOut);
        }

        Err(PairingValidationError::Invalid)
    }
}

#[derive(Debug, Clone)]
pub enum AudioCommand {
    Start(i64), // recording_id
    Pause,
    Resume,
    Stop,
}
