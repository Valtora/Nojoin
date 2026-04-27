use crate::config::Config;
use crate::local_https_identity::LocalHttpsRepairReason;
use crossbeam_channel::Sender;
use log::error;
use rand::Rng;
use serde::{Deserialize, Serialize};
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Mutex, MutexGuard, PoisonError};
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

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum LocalHttpsStatus {
    Ready,
    Repairing,
    NeedsRepair,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalHttpsHealth {
    pub status: LocalHttpsStatus,
    pub detail_message: String,
    pub repair_reason: Option<LocalHttpsRepairReason>,
    pub current_user_trust_installed: Option<bool>,
    pub listener_running: bool,
}

impl Default for LocalHttpsHealth {
    fn default() -> Self {
        Self::repairing("Companion is reconciling its secure local connection.")
    }
}

impl LocalHttpsHealth {
    pub fn ready(listener_running: bool) -> Self {
        Self {
            status: LocalHttpsStatus::Ready,
            detail_message: "Secure local browser connections are ready.".to_string(),
            repair_reason: None,
            current_user_trust_installed: Some(true),
            listener_running,
        }
    }

    pub fn repairing(message: impl Into<String>) -> Self {
        Self {
            status: LocalHttpsStatus::Repairing,
            detail_message: message.into(),
            repair_reason: None,
            current_user_trust_installed: None,
            listener_running: false,
        }
    }

    pub fn needs_repair(
        message: impl Into<String>,
        repair_reason: Option<LocalHttpsRepairReason>,
        current_user_trust_installed: Option<bool>,
        listener_running: bool,
    ) -> Self {
        Self {
            status: LocalHttpsStatus::NeedsRepair,
            detail_message: message.into(),
            repair_reason,
            current_user_trust_installed,
            listener_running,
        }
    }
}

pub fn pairing_block_message(status: &AppStatus) -> Option<&'static str> {
    match status {
        AppStatus::Idle | AppStatus::BackendOffline => None,
        AppStatus::Recording | AppStatus::Paused => {
            Some("Pairing is unavailable while a recording is active.")
        }
        AppStatus::Uploading => Some("Pairing is unavailable while uploads are still finishing."),
        AppStatus::Error(_) => {
            Some("Pairing is unavailable until the Companion returns to an idle state.")
        }
    }
}

#[derive(Debug, Clone)]
pub struct PairingSession {
    pub canonical_code: String,
    pub display_code: String,
    pub expires_at: SystemTime,
    pub failed_attempts: u8,
    pub completion_in_progress: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PairingValidationError {
    NotActive,
    Expired,
    Invalid,
    LockedOut,
    InProgress,
}

pub fn canonicalize_pairing_code(input: &str) -> String {
    input
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric())
        .map(|ch| ch.to_ascii_uppercase())
        .collect()
}

pub fn pairing_code_log_label(input: &str) -> String {
    let canonical = canonicalize_pairing_code(input);
    if canonical.is_empty() {
        return "<empty>".to_string();
    }

    if canonical.len() <= 4 {
        return canonical;
    }

    format!(
        "{}...{}",
        &canonical[..2],
        &canonical[canonical.len() - 2..]
    )
}

pub fn pairing_code_fingerprint(input: &str) -> String {
    let canonical = canonicalize_pairing_code(input);
    if canonical.is_empty() {
        return "empty".to_string();
    }

    let mut hasher = DefaultHasher::new();
    canonical.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
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
            completion_in_progress: false,
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActiveRecordingOwner {
    pub user_id: i64,
    pub username: String,
    pub companion_pairing_id: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RecordingRecoveryState {
    None,
    WaitingForReconnect,
    StopRequested,
}

pub struct AppState {
    pub status: Mutex<AppStatus>,
    pub current_recording_id: Mutex<Option<i64>>,
    pub current_recording_token: Mutex<Option<String>>,
    pub current_recording_owner: Mutex<Option<ActiveRecordingOwner>>,
    pub recording_recovery_state: Mutex<RecordingRecoveryState>,
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
    pub local_https_health: Mutex<LocalHttpsHealth>,

    // Tray Menu Items
    pub tray_status_item: Mutex<Option<MenuItem<Wry>>>,
    pub tray_run_on_startup_item: Mutex<Option<CheckMenuItem<Wry>>>,
    pub tray_icon: Mutex<Option<TrayIcon<Wry>>>,

    // Manual pairing state
    pub pairing_session: Mutex<Option<PairingSession>>,
}

fn recover_mutex_guard<'a, T>(
    lock_result: Result<MutexGuard<'a, T>, PoisonError<MutexGuard<'a, T>>>,
    label: &str,
) -> MutexGuard<'a, T> {
    match lock_result {
        Ok(guard) => guard,
        Err(poisoned) => {
            error!("Recovering from poisoned {} mutex.", label);
            poisoned.into_inner()
        }
    }
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
        let config = recover_mutex_guard(self.config.lock(), "config");
        config.is_authenticated()
    }

    pub fn begin_pairing_session(&self) -> PairingSession {
        let session = PairingSession::new();
        let mut pairing_session = recover_mutex_guard(self.pairing_session.lock(), "pairing_session");
        *pairing_session = Some(session.clone());
        session
    }

    pub fn clear_pairing_session(&self) {
        let mut pairing_session = recover_mutex_guard(self.pairing_session.lock(), "pairing_session");
        *pairing_session = None;
    }

    pub fn current_pairing_session(&self) -> Option<PairingSession> {
        let mut pairing_session = recover_mutex_guard(self.pairing_session.lock(), "pairing_session");

        if pairing_session
            .as_ref()
            .map(|session| session.is_expired())
            .unwrap_or(false)
        {
            *pairing_session = None;
        }

        pairing_session.clone()
    }

    pub fn pairing_session_snapshot(&self) -> Option<PairingSession> {
        recover_mutex_guard(self.pairing_session.lock(), "pairing_session").clone()
    }

    pub fn is_pairing_active(&self) -> bool {
        self.current_pairing_session().is_some()
    }

    pub fn begin_pairing_completion(
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

        if session.completion_in_progress {
            return Err(PairingValidationError::InProgress);
        }

        if session.canonical_code == canonicalize_pairing_code(submitted_code) {
            session.completion_in_progress = true;
            return Ok(());
        }

        session.failed_attempts += 1;
        if session.failed_attempts >= MAX_PAIRING_ATTEMPTS {
            *pairing_session = None;
            return Err(PairingValidationError::LockedOut);
        }

        Err(PairingValidationError::Invalid)
    }

    pub fn release_pairing_completion(&self) {
        let mut pairing_session = self.pairing_session.lock().unwrap();
        let Some(session) = pairing_session.as_mut() else {
            return;
        };

        if session.is_expired() {
            *pairing_session = None;
            return;
        }

        session.completion_in_progress = false;
    }

    pub fn complete_pairing_session(&self) {
        self.clear_pairing_session();
    }

    pub fn set_current_recording_owner(&self, owner: ActiveRecordingOwner) {
        let mut current_owner = self.current_recording_owner.lock().unwrap();
        *current_owner = Some(owner);
    }

    pub fn current_recording_owner(&self) -> Option<ActiveRecordingOwner> {
        self.current_recording_owner.lock().unwrap().clone()
    }

    pub fn clear_current_recording_owner(&self) {
        let mut current_owner = self.current_recording_owner.lock().unwrap();
        *current_owner = None;
    }

    pub fn recording_recovery_state(&self) -> RecordingRecoveryState {
        *self.recording_recovery_state.lock().unwrap()
    }

    pub fn set_recording_recovery_state(&self, recovery_state: RecordingRecoveryState) {
        let mut current_state = self.recording_recovery_state.lock().unwrap();
        *current_state = recovery_state;
    }

    pub fn clear_recording_recovery_state(&self) {
        self.set_recording_recovery_state(RecordingRecoveryState::None);
    }

    pub fn local_https_health(&self) -> LocalHttpsHealth {
        recover_mutex_guard(self.local_https_health.lock(), "local_https_health").clone()
    }

    pub fn local_https_status(&self) -> LocalHttpsStatus {
        recover_mutex_guard(self.local_https_health.lock(), "local_https_health").status
    }

    pub fn set_local_https_health(&self, health: LocalHttpsHealth) {
        let mut current_health = recover_mutex_guard(self.local_https_health.lock(), "local_https_health");
        *current_health = health;
    }
}

#[derive(Debug, Clone)]
pub enum AudioCommand {
    Start(i64), // recording_id
    Pause,
    Resume,
    Stop,
}

#[cfg(test)]
mod tests {
    use super::{
        canonicalize_pairing_code, pairing_block_message, pairing_code_fingerprint,
        pairing_code_log_label, AppState, AppStatus, AudioCommand, LocalHttpsHealth,
        PairingSession, PairingValidationError, RecordingRecoveryState, MAX_PAIRING_ATTEMPTS,
    };
    use crate::config::Config;
    use crossbeam_channel::unbounded;
    use std::sync::atomic::{AtomicBool, AtomicU32};
    use std::sync::Mutex;
    use std::time::{Duration, SystemTime};

    fn test_state() -> AppState {
        let (audio_command_tx, _audio_command_rx) = unbounded::<AudioCommand>();

        AppState {
            status: Mutex::new(AppStatus::Idle),
            current_recording_id: Mutex::new(None),
            current_recording_token: Mutex::new(None),
            current_recording_owner: Mutex::new(None),
            recording_recovery_state: Mutex::new(RecordingRecoveryState::None),
            current_sequence: Mutex::new(1),
            audio_command_tx,
            config: Mutex::new(Config::default()),
            recording_start_time: Mutex::new(None),
            accumulated_duration: Mutex::new(Duration::new(0, 0)),
            input_level: AtomicU32::new(0),
            output_level: AtomicU32::new(0),
            live_input_level: AtomicU32::new(0),
            live_output_level: AtomicU32::new(0),
            is_backend_connected: AtomicBool::new(false),
            update_available: AtomicBool::new(false),
            latest_version: Mutex::new(None),
            latest_update_url: Mutex::new(None),
            local_https_health: Mutex::new(LocalHttpsHealth::default()),
            tray_status_item: Mutex::new(None),
            tray_run_on_startup_item: Mutex::new(None),
            tray_icon: Mutex::new(None),
            pairing_session: Mutex::new(None),
        }
    }

    #[test]
    fn begin_pairing_completion_blocks_replay_until_released() {
        let state = test_state();
        let session = state.begin_pairing_session();

        assert_eq!(
            canonicalize_pairing_code(&session.display_code),
            session.canonical_code
        );
        assert_eq!(
            state.begin_pairing_completion(&session.display_code),
            Ok(())
        );
        assert_eq!(
            state.begin_pairing_completion(&session.display_code),
            Err(PairingValidationError::InProgress)
        );

        state.release_pairing_completion();

        assert_eq!(
            state.begin_pairing_completion(&session.display_code),
            Ok(())
        );
    }

    #[test]
    fn pairing_code_log_helpers_are_redacted_and_stable() {
        assert_eq!(pairing_code_log_label("ab-cd1234"), "AB...34");
        assert_eq!(
            pairing_code_fingerprint("ab-cd1234"),
            pairing_code_fingerprint("ABCD1234")
        );
    }

    #[test]
    fn complete_pairing_session_clears_replay_state() {
        let state = test_state();
        let session = state.begin_pairing_session();

        assert_eq!(
            state.begin_pairing_completion(&session.display_code),
            Ok(())
        );
        state.complete_pairing_session();

        assert_eq!(
            state.begin_pairing_completion(&session.display_code),
            Err(PairingValidationError::NotActive)
        );
    }

    #[test]
    fn expired_pairing_session_fails_closed_and_clears_state() {
        let state = test_state();
        let expired = PairingSession {
            canonical_code: "ABCDEFGH".to_string(),
            display_code: "ABCD-EFGH".to_string(),
            expires_at: SystemTime::now() - Duration::from_secs(1),
            failed_attempts: 0,
            completion_in_progress: false,
        };
        *state.pairing_session.lock().unwrap() = Some(expired);

        assert_eq!(
            state.begin_pairing_completion("ABCD-EFGH"),
            Err(PairingValidationError::Expired)
        );
        assert!(!state.is_pairing_active());
    }

    #[test]
    fn invalid_pairing_attempts_lock_out_after_budget() {
        let state = test_state();
        state.begin_pairing_session();

        for _ in 0..(MAX_PAIRING_ATTEMPTS - 1) {
            assert_eq!(
                state.begin_pairing_completion("ZZZZ-ZZZZ"),
                Err(PairingValidationError::Invalid)
            );
        }

        assert_eq!(
            state.begin_pairing_completion("ZZZZ-ZZZZ"),
            Err(PairingValidationError::LockedOut)
        );
        assert!(!state.is_pairing_active());
    }

    #[test]
    fn pairing_block_message_matches_busy_states() {
        assert_eq!(pairing_block_message(&AppStatus::Idle), None);
        assert_eq!(pairing_block_message(&AppStatus::BackendOffline), None);
        assert_eq!(
            pairing_block_message(&AppStatus::Recording),
            Some("Pairing is unavailable while a recording is active.")
        );
        assert_eq!(
            pairing_block_message(&AppStatus::Paused),
            Some("Pairing is unavailable while a recording is active.")
        );
        assert_eq!(
            pairing_block_message(&AppStatus::Uploading),
            Some("Pairing is unavailable while uploads are still finishing.")
        );
    }

    #[test]
    fn recording_recovery_state_round_trips() {
        let state = test_state();

        assert_eq!(
            state.recording_recovery_state(),
            RecordingRecoveryState::None
        );

        state.set_recording_recovery_state(RecordingRecoveryState::WaitingForReconnect);
        assert_eq!(
            state.recording_recovery_state(),
            RecordingRecoveryState::WaitingForReconnect
        );

        state.set_recording_recovery_state(RecordingRecoveryState::StopRequested);
        assert_eq!(
            state.recording_recovery_state(),
            RecordingRecoveryState::StopRequested
        );

        state.clear_recording_recovery_state();
        assert_eq!(
            state.recording_recovery_state(),
            RecordingRecoveryState::None
        );
    }
}
