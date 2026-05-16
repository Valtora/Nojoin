use crate::config::Config;
use crate::notifications;
use crate::state::{recover_mutex_guard, AppState, AppStatus, AudioCommand};
use crate::uploader;
use anyhow;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::Device;
use crossbeam_channel::Receiver;
use hound;
use log::{info, warn};
use std::path::Path;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::thread;
use tauri::AppHandle;
use tokio::sync::mpsc;

async fn wait_for_backend_reconnect(state: &Arc<AppState>, reason: &str) {
    let mut logged_wait = false;

    while !state.is_backend_connected.load(Ordering::SeqCst) {
        if !logged_wait {
            info!(
                "Backend unavailable; waiting to {} once Nojoin reconnects.",
                reason
            );
            logged_wait = true;
        }

        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

#[derive(Debug, Default)]
struct SegmentThreadOutcome {
    failed_upload_segments: Vec<i32>,
}

impl SegmentThreadOutcome {
    fn upload_failure_reason(&self) -> Option<String> {
        if self.failed_upload_segments.is_empty() {
            None
        } else {
            Some(format!(
                "segments {} failed to upload after repeated retries",
                self.failed_upload_segments
                    .iter()
                    .map(|segment| segment.to_string())
                    .collect::<Vec<_>>()
                    .join(", ")
            ))
        }
    }
}

#[derive(Debug)]
enum SegmentUploadOutcome {
    Uploaded,
    Failed(i32),
}

fn prune_empty_parent_directories(start_dir: &Path) {
    let mut current = Some(start_dir);

    while let Some(dir) = current {
        let is_empty = match std::fs::read_dir(dir) {
            Ok(mut entries) => entries.next().is_none(),
            Err(error) => {
                warn!("Failed to inspect temp directory {:?}: {}", dir, error);
                return;
            }
        };

        if !is_empty {
            return;
        }

        match std::fs::remove_dir(dir) {
            Ok(()) => current = dir.parent(),
            Err(error) => {
                warn!("Failed to remove empty temp directory {:?}: {}", dir, error);
                return;
            }
        }
    }
}

fn cleanup_segment_file(path: &Path, reason: &str) {
    match std::fs::remove_file(path) {
        Ok(()) => info!("Removed temp segment file {:?} after {}.", path, reason),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => {
            warn!(
                "Failed to remove temp segment file {:?} after {}: {}",
                path,
                reason,
                error
            );
            return;
        }
    }

    if let Some(parent) = path.parent() {
        prune_empty_parent_directories(parent);
    }
}

fn reset_recording_state(state: &Arc<AppState>) {
    *recover_mutex_guard(state.status.lock(), "status") = AppStatus::Idle;
    *recover_mutex_guard(state.current_recording_id.lock(), "current_recording_id") = None;
    *recover_mutex_guard(state.current_recording_token.lock(), "current_recording_token") = None;
    *recover_mutex_guard(state.current_recording_owner.lock(), "current_recording_owner") = None;
    state.clear_recording_recovery_state();
    *recover_mutex_guard(state.current_sequence.lock(), "current_sequence") = 1;
    *recover_mutex_guard(state.accumulated_duration.lock(), "accumulated_duration") = std::time::Duration::new(0, 0);
    *recover_mutex_guard(state.recording_start_time.lock(), "recording_start_time") = None;
}

fn join_recording_thread(
    recording_handle: &mut Option<std::thread::JoinHandle<anyhow::Result<SegmentThreadOutcome>>>,
) -> anyhow::Result<Option<SegmentThreadOutcome>> {
    let Some(handle) = recording_handle.take() else {
        return Ok(None);
    };

    match handle.join() {
        Ok(result) => result.map(Some),
        Err(_) => Err(anyhow::anyhow!(
            "Recording thread panicked while closing the current segment"
        )),
    }
}

fn handle_terminal_recording_failure(state: &Arc<AppState>, app_handle: &AppHandle, reason: &str) {
    warn!("Recording cannot be finalized safely: {}", reason);

    let recording_id = recover_mutex_guard(state.current_recording_id.lock(), "current_recording_id").clone();
    let recording_token = recover_mutex_guard(state.current_recording_token.lock(), "current_recording_token").clone();
    let config = recover_mutex_guard(state.config.lock(), "config").clone();

    if let (Some(recording_id), Some(recording_token)) = (recording_id, recording_token) {
        thread::spawn(move || {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async move {
                if let Err(error) = uploader::discard_recording(&recording_id, &config, &recording_token).await {
                    warn!(
                        "Best-effort discard failed for recording {} after terminal upload failure: {}",
                        recording_id,
                        error
                    );
                }
            });
        });
    } else {
        warn!("Missing recording identity while handling terminal recording failure.");
    }

    notifications::show_notification(
        app_handle,
        "Recording Upload Failed",
        "A recording segment could not be uploaded after repeated retries. The in-flight recording was discarded.",
    );
    reset_recording_state(state);
}

fn find_input_device(host: &cpal::Host, config: &Config) -> Option<Device> {
    if let Some(name) = config.input_device_name() {
        if let Ok(devices) = host.input_devices() {
            for device in devices {
                if let Ok(device_name) = device.name() {
                    if device_name == name {
                        info!("Using configured input device: {}", device_name);
                        return Some(device);
                    }
                }
            }
        }
        warn!(
            "Configured input device '{}' not found, using default",
            name
        );
    }
    host.default_input_device()
}

fn find_output_device(host: &cpal::Host, config: &Config) -> Option<Device> {
    if let Some(name) = config.output_device_name() {
        if let Ok(devices) = host.output_devices() {
            for device in devices {
                if let Ok(device_name) = device.name() {
                    if device_name == name {
                        info!("Using configured output device: {}", device_name);
                        return Some(device);
                    }
                }
            }
        }
        warn!(
            "Configured output device '{}' not found, using default",
            name
        );
    }
    host.default_output_device()
}

pub fn run_audio_loop(
    state: Arc<AppState>,
    command_rx: Receiver<AudioCommand>,
    app_handle: AppHandle,
) {
    let host = cpal::default_host();

    // Log available devices on startup
    let input_device = host
        .default_input_device()
        .map(|d| d.name().unwrap_or("Unknown".to_string()));
    let output_device = host
        .default_output_device()
        .map(|d| d.name().unwrap_or("Unknown".to_string()));

    info!("Audio System Initialized:");
    info!("  Default Input:  {:?}", input_device);
    info!("  Default Output: {:?}", output_device);

    if let Ok(devices) = host.input_devices() {
        info!("  Available Input Devices:");
        for d in devices {
            info!("    - {}", d.name().unwrap_or_default());
        }
    }

    // Shared flag to stop the stream thread
    let is_recording = Arc::new(AtomicBool::new(false));

    // Tracks recording thread handle to await uploads.
    let mut recording_handle: Option<
        std::thread::JoinHandle<anyhow::Result<SegmentThreadOutcome>>,
    > = None;

    // Re-acquires default device in thread.

    loop {
        let command = command_rx.recv().unwrap();

        match command {
            AudioCommand::Start(id) => {
                recording_handle = Some(start_segment(id, 1, state.clone(), is_recording.clone()));
            }
            AudioCommand::Resume => {
                let id = recover_mutex_guard(state.current_recording_id.lock(), "current_recording_id").clone();
                let seq = *recover_mutex_guard(state.current_sequence.lock(), "current_sequence");
                if let Some(rec_id) = id {
                    recording_handle = Some(start_segment(
                        rec_id,
                        seq,
                        state.clone(),
                        is_recording.clone(),
                    ));
                }
            }
            AudioCommand::Pause => {
                is_recording.store(false, Ordering::SeqCst);
                // Wait for the current segment to finish uploading
                match join_recording_thread(&mut recording_handle) {
                    Ok(Some(outcome)) => {
                        if let Some(reason) = outcome.upload_failure_reason() {
                            handle_terminal_recording_failure(&state, &app_handle, &reason);
                        }
                    }
                    Ok(None) => {}
                    Err(error) => {
                        handle_terminal_recording_failure(&state, &app_handle, &error.to_string());
                    }
                }
            }
            AudioCommand::Stop => {
                is_recording.store(false, Ordering::SeqCst);
                // Wait for the current segment to finish uploading
                match join_recording_thread(&mut recording_handle) {
                    Ok(Some(outcome)) => {
                        if let Some(reason) = outcome.upload_failure_reason() {
                            handle_terminal_recording_failure(&state, &app_handle, &reason);
                            continue;
                        }
                    }
                    Ok(None) => {}
                    Err(error) => {
                        handle_terminal_recording_failure(&state, &app_handle, &error.to_string());
                        continue;
                    }
                }

                // Trigger finalize
                let id = recover_mutex_guard(state.current_recording_id.lock(), "current_recording_id").clone();
                let config = recover_mutex_guard(state.config.lock(), "config").clone();

                // Calculate duration
                let duration = {
                    let acc = *recover_mutex_guard(state.accumulated_duration.lock(), "accumulated_duration");
                    let start = *recover_mutex_guard(state.recording_start_time.lock(), "recording_start_time");
                    if let Some(s) = start {
                        if let Ok(elapsed) = s.elapsed() {
                            acc + elapsed
                        } else {
                            acc
                        }
                    } else {
                        acc
                    }
                };

                if let Some(rec_id) = id {
                    let state_finalize = state.clone();
                    let app_handle_finalize = app_handle.clone();
                    // Creates new runtime for async task within sync thread.
                    thread::spawn(move || {
                        let rt = tokio::runtime::Runtime::new().unwrap();
                        rt.block_on(async move {
                            // Check minimum length
                            let min_minutes = config.min_meeting_length().unwrap_or(0);
                            let duration_secs = duration.as_secs();
                            let recording_token = recover_mutex_guard(state_finalize.current_recording_token.lock(), "current_recording_token").clone();

                            let Some(recording_token) = recording_token else {
                                eprintln!("Missing recording upload token for recording {}", rec_id);
                                reset_recording_state(&state_finalize);
                                return;
                            };

                            if !state_finalize.is_backend_connected.load(Ordering::SeqCst) {
                                wait_for_backend_reconnect(&state_finalize, "finalize the saved recording").await;
                            }

                            if min_minutes > 0 && duration_secs < (min_minutes as u64 * 60) {
                                info!("Recording too short ({}s < {}m). Discarding.", duration_secs, min_minutes);
                                match uploader::discard_recording(&rec_id, &config, &recording_token).await {
                                    Ok(refreshed_token) => {
                                        if let Some(new_token) = refreshed_token {
                                            *recover_mutex_guard(state_finalize.current_recording_token.lock(), "current_recording_token") = Some(new_token.clone());
                                        }
                                        info!("Deleted short recording.");
                                        notifications::show_notification(
                                            &app_handle_finalize,
                                            "Recording Discarded",
                                            &format!("Meeting was shorter than {} minutes, discarding meeting.", min_minutes)
                                        );
                                    },
                                    Err(e) => {
                                        eprintln!("Failed to delete short recording: {}", e);
                                        // Attempts finalization if delete fails.
                                        match uploader::finalize_recording(&rec_id, &config, &recording_token).await {
                                            Ok(refreshed_token) => {
                                                if let Some(new_token) = refreshed_token {
                                                    *recover_mutex_guard(state_finalize.current_recording_token.lock(), "current_recording_token") = Some(new_token.clone());
                                                }
                                                println!("Recording finalized (after delete failed)")
                                            }
                                            Err(e) => eprintln!("Failed to finalize: {}", e),
                                        }
                                    },
                                }
                            } else {
                                // Sleep unnecessary; upload verified complete.
                                match uploader::finalize_recording(&rec_id, &config, &recording_token).await {
                                    Ok(refreshed_token) => {
                                        if let Some(new_token) = refreshed_token {
                                            *recover_mutex_guard(state_finalize.current_recording_token.lock(), "current_recording_token") = Some(new_token.clone());
                                        }
                                        println!("Recording finalized")
                                    }
                                    Err(e) => eprintln!("Failed to finalize: {}", e),
                                }
                            }

                            reset_recording_state(&state_finalize);
                        });
                    });
                } else {
                    reset_recording_state(&state);
                }
            }
        }
    }
}

fn start_segment(
    recording_id: String,
    sequence: i32,
    state: Arc<AppState>,
    is_recording: Arc<AtomicBool>,
) -> std::thread::JoinHandle<anyhow::Result<SegmentThreadOutcome>> {
    is_recording.store(true, Ordering::SeqCst);
    let config = recover_mutex_guard(state.config.lock(), "config").clone();

    thread::spawn(move || {
        let run = || -> anyhow::Result<SegmentThreadOutcome> {
            const MAX_SEGMENT_DURATION_SECS: u64 = 2;
            let temp_dir = std::env::temp_dir().join("Nojoin").join("recordings");
            if let Err(e) = std::fs::create_dir_all(&temp_dir) {
                log::error!("Failed to create temp directory {:?}: {}", temp_dir, e);
                return Err(anyhow::anyhow!("Failed to create temp directory: {}", e));
            }
            info!("Using temp directory: {:?}", temp_dir);

            let host = cpal::default_host();

            // Channels for data transfer
            let (mic_tx, mic_rx) = crossbeam_channel::unbounded::<Vec<f32>>();
            let (sys_tx, sys_rx) = crossbeam_channel::unbounded::<Vec<f32>>();

            // Helper to calculate RMS level (0.0 to 1.0)
            fn calculate_rms(data: &[f32]) -> f32 {
                if data.is_empty() {
                    return 0.0;
                }
                let sum_squares: f32 = data.iter().map(|s| s * s).sum();
                (sum_squares / data.len() as f32).sqrt()
            }

            // 1. Setup Microphone (Input)
            // Attempts to find real device; falls back to virtual generator.
            let (mic_stream, mic_sample_rate) = {
                let device_opt = find_input_device(&host, &config);

                match device_opt {
                    Some(mic_device) => {
                        info!(
                            "Selected Input Device: {}",
                            mic_device.name().unwrap_or_else(|_| "Unknown".to_string())
                        );

                        let config_result: anyhow::Result<cpal::SupportedStreamConfig> = mic_device
                            .default_input_config()
                            .map_err(|e| {
                                anyhow::anyhow!("Failed to get default input config: {}", e)
                            })
                            .or_else(|e| {
                                warn!("{}. Trying to find first supported config...", e);
                                let config = mic_device
                                    .supported_input_configs()
                                    .map_err(|e| {
                                        anyhow::anyhow!("Failed to get supported configs: {}", e)
                                    })?
                                    .next()
                                    .ok_or_else(|| {
                                        anyhow::anyhow!("No supported input configs found")
                                    })?
                                    .with_max_sample_rate();
                                Ok(config)
                            });

                        match config_result {
                            Ok(mic_config) => {
                                let mic_channels = mic_config.channels();
                                let mic_sample_rate = mic_config.sample_rate().0;
                                info!("Mic Configured: {}ch, {}Hz", mic_channels, mic_sample_rate);

                                let err_fn = |err| log::error!("Mic Stream error: {}", err);
                                let tx = mic_tx.clone();
                                let state_mic = state.clone();

                                // Helper to convert interleaved to mono
                                let to_mono_mic = move |data: &[f32], channels: u16| -> Vec<f32> {
                                    if channels == 1 {
                                        return data.to_vec();
                                    }
                                    let mut mono =
                                        Vec::with_capacity(data.len() / channels as usize);
                                    for chunk in data.chunks(channels as usize) {
                                        let sum: f32 = chunk.iter().sum();
                                        mono.push(sum / channels as f32);
                                    }
                                    mono
                                };

                                let stream = mic_device
                                    .build_input_stream(
                                        &mic_config.into(),
                                        move |data: &[f32], _: &_| {
                                            let mono = to_mono_mic(data, mic_channels);

                                            // Update input level
                                            let rms = calculate_rms(&mono);
                                            state_mic.record_input_level(rms);

                                            let _ = tx.send(mono);
                                        },
                                        err_fn,
                                        None,
                                    )
                                    .map_err(|e| {
                                        anyhow::anyhow!("Failed to build mic stream: {}", e)
                                    })?;

                                (Some(stream), mic_sample_rate)
                            }
                            Err(e) => {
                                warn!("Failed to configure microphone: {}. Falling back to Virtual Silence Microphone.", e);
                                (None, 48000)
                            }
                        }
                    }
                    None => {
                        warn!("No input device found. Falling back to Virtual Silence Microphone.");
                        (None, 48000)
                    }
                }
            };

            // If using virtual mic, spawn the generator
            if mic_stream.is_none() {
                let tx = mic_tx.clone();
                let is_rec = is_recording.clone();
                let sample_rate = mic_sample_rate;

                thread::spawn(move || {
                    info!("Starting Virtual Silence Generator at {}Hz", sample_rate);
                    let chunk_duration_ms = 100;
                    let samples_per_chunk =
                        (sample_rate as f32 * (chunk_duration_ms as f32 / 1000.0)) as usize;

                    while is_rec.load(Ordering::SeqCst) {
                        let start = std::time::Instant::now();
                        let _ = tx.send(vec![0.0; samples_per_chunk]);

                        let elapsed = start.elapsed();
                        let wait = std::time::Duration::from_millis(chunk_duration_ms as u64);
                        if wait > elapsed {
                            thread::sleep(wait - elapsed);
                        }
                    }
                });
            }

            // 2. Setup System Audio (Loopback) - use configured or default
            // Uses output device for loopback on Windows WASAPI.
            let sys_device = find_output_device(&host, &config)
                .ok_or_else(|| anyhow::anyhow!("No output device available"))?;
            let sys_config = sys_device
                .default_output_config()
                .map_err(|e| anyhow::anyhow!("Failed to get sys config: {}", e))?;
            let sys_channels = sys_config.channels();

            info!(
                "Sys: {} ({}ch, {}Hz)",
                sys_device.name().unwrap_or_default(),
                sys_channels,
                sys_config.sample_rate().0
            );

            // Target format: Mono, 16-bit, Mic Sample Rate (Master Clock)
            let spec = hound::WavSpec {
                channels: 1,
                sample_rate: mic_sample_rate,
                bits_per_sample: 16,
                sample_format: hound::SampleFormat::Int,
            };

            // Check for sample rate mismatch between Input (Mic) and Output (System Loopback)
            let sys_rate = sys_config.sample_rate().0;
            if mic_sample_rate.abs_diff(sys_rate) > 1000 {
                warn!(
                   "Sample rate mismatch! Mic: {}Hz, System: {}Hz. Audio drift or artifacts may occur.",
                   mic_sample_rate,
                   sys_rate
                );
            }

            let err_fn = |err: cpal::StreamError| log::error!("Stream error: {}", err);

            // Helper to convert interleaved to mono (redefined for sys stream scope)
            let to_mono = |data: &[f32], channels: u16| -> Vec<f32> {
                if channels == 1 {
                    return data.to_vec();
                }
                let mut mono = Vec::with_capacity(data.len() / channels as usize);
                for chunk in data.chunks(channels as usize) {
                    let sum: f32 = chunk.iter().sum();
                    mono.push(sum / channels as f32);
                }
                mono
            };

            // 3. Build Sys Stream
            let _sys_stream = {
                let is_recording_sys = is_recording.clone();
                let state_sys = state.clone();

                let sys_device = find_output_device(&host, &config)
                    .ok_or_else(|| anyhow::anyhow!("No output device available"))?;
                let sys_config = sys_device
                    .default_output_config()
                    .map_err(|e| anyhow::anyhow!("Failed to get sys config: {}", e))?;
                let sys_channels = sys_config.channels();

                info!(
                    "Sys: {} ({}ch, {}Hz)",
                    sys_device.name().unwrap_or_default(),
                    sys_channels,
                    sys_config.sample_rate().0
                );

                let sys_stream = sys_device
                    .build_input_stream(
                        &sys_config.into(),
                        move |data: &[f32], _: &_| {
                            let mono = to_mono(data, sys_channels);
                            // Update output level (always, for monitoring)
                            let rms = calculate_rms(&mono);
                            state_sys.record_output_level(rms);

                            if is_recording_sys.load(Ordering::SeqCst) {
                                let _ = sys_tx.send(mono);
                            }
                        },
                        err_fn,
                        None,
                    )
                    .map_err(|e| anyhow::anyhow!("Failed to build sys stream: {}", e))?;

                sys_stream
                    .play()
                    .map_err(|e| anyhow::anyhow!("Failed to play sys stream: {}", e))?;
                sys_stream
            };

            if let Some(stream) = mic_stream {
                stream
                    .play()
                    .map_err(|e| anyhow::anyhow!("Failed to play mic stream: {}", e))?;

                // 5. Mixing Loop with automatic segmentation
                return run_mixing_loop(
                    recording_id,
                    sequence,
                    spec,
                    mic_rx,
                    sys_rx,
                    is_recording,
                    state.clone(),
                    MAX_SEGMENT_DURATION_SECS,
                    temp_dir,
                );
            } else {
                // Virtual mic mode
                return run_mixing_loop(
                    recording_id,
                    sequence,
                    spec,
                    mic_rx,
                    sys_rx,
                    is_recording,
                    state.clone(),
                    MAX_SEGMENT_DURATION_SECS,
                    temp_dir,
                );
            }
        };

        match run() {
            Ok(outcome) => Ok(outcome),
            Err(e) => {
                log::error!("Recording thread error: {}", e);
                let mut status = recover_mutex_guard(state.status.lock(), "status");
                *status = AppStatus::Error(e.to_string());
                Err(e)
            }
        }
    })
}

// Helper function for the mixing loop to avoid code duplication
fn run_mixing_loop(
    recording_id: String,
    mut current_sequence: i32,
    spec: hound::WavSpec,
    mic_rx: crossbeam_channel::Receiver<Vec<f32>>,
    sys_rx: crossbeam_channel::Receiver<Vec<f32>>,
    is_recording: Arc<AtomicBool>,
    state: Arc<AppState>,
    max_duration: u64,
    temp_dir: std::path::PathBuf,
) -> anyhow::Result<SegmentThreadOutcome> {
    let mut sys_buffer: Vec<f32> = Vec::new();
    let rt = tokio::runtime::Runtime::new().unwrap();
    let mut upload_handles = Vec::new();
    let (upload_outcome_tx, mut upload_outcome_rx) = mpsc::unbounded_channel();

    while is_recording.load(Ordering::SeqCst) {
        let filename = format!("temp_{}_{}.wav", recording_id, current_sequence);
        let path = temp_dir.join(&filename);

        let mut writer = hound::WavWriter::create(&path, spec)
            .map_err(|e| anyhow::anyhow!("Failed to create wav writer: {}", e))?;

        let segment_start = std::time::Instant::now();

        // Record for up to MAX_SEGMENT_DURATION_SECS or until stopped
        while is_recording.load(Ordering::SeqCst) {
            // Flush the live segment once the ~2 second interval elapses.
            if segment_start.elapsed().as_secs() >= max_duration {
                info!(
                    "Segment {} reached live flush interval, starting new segment",
                    current_sequence
                );
                break;
            }

            // Block on Mic data (Master)
            if let Ok(mic_data) = mic_rx.recv_timeout(std::time::Duration::from_millis(500)) {
                // Collect available System data
                while let Ok(sys_chunk) = sys_rx.try_recv() {
                    sys_buffer.extend(sys_chunk);
                }

                // Mix
                for (_i, mic_sample) in mic_data.iter().enumerate() {
                    let mut mixed = *mic_sample;

                    // Simple mixing: Add system audio if available
                    // Note: This is a naive mix. Real mixing needs resampling if rates differ.
                    // Sample rate mismatch warning is logged at startup if significant.
                    if !sys_buffer.is_empty() {
                        let sys_sample = sys_buffer.remove(0);
                        mixed += sys_sample;
                    }

                    // Hard clip to avoid wrapping
                    if mixed > 1.0 {
                        mixed = 1.0;
                    } else if mixed < -1.0 {
                        mixed = -1.0;
                    }

                    // Convert f32 (-1.0 to 1.0) to i16
                    let sample_i16 = (mixed * i16::MAX as f32) as i16;
                    writer.write_sample(sample_i16).unwrap();
                }
            }
        }

        writer
            .finalize()
            .map_err(|e| anyhow::anyhow!("Failed to finalize wav writer: {}", e))?;
        info!("Segment {} recorded: {:?}", current_sequence, path);

        // Upload segment in background
        let state_upload = state.clone();
        let path_clone = path.clone();
        let seq = current_sequence;
        let config = recover_mutex_guard(state.config.lock(), "config").clone();
        let recording_token = recover_mutex_guard(state.current_recording_token.lock(), "current_recording_token").clone();
        let tx = upload_outcome_tx.clone();
        let recording_id_for_upload = recording_id.clone();

        let handle = rt.spawn(async move {
            let Some(recording_token) = recording_token else {
                log::error!("Missing recording upload token for segment {}", seq);
                cleanup_segment_file(&path_clone, "missing upload token");
                tx.send(SegmentUploadOutcome::Failed(seq)).ok();
                return;
            };

            if !state_upload.is_backend_connected.load(Ordering::SeqCst) {
                wait_for_backend_reconnect(
                    &state_upload,
                    &format!("upload queued segment {}", seq),
                )
                .await;
            }

            match uploader::upload_segment(
                &recording_id_for_upload,
                seq,
                &path_clone,
                &config,
                &recording_token,
            )
            .await
            {
                Ok(refreshed_token) => {
                    if let Some(new_token) = refreshed_token {
                        *recover_mutex_guard(state_upload.current_recording_token.lock(), "current_recording_token") =
                            Some(new_token.clone());
                    }
                    info!("Segment {} uploaded successfully", seq);
                    cleanup_segment_file(&path_clone, "successful upload");
                    tx.send(SegmentUploadOutcome::Uploaded).ok();
                }
                Err(e) => {
                    log::error!("Failed to upload segment {}: {}", seq, e);
                    cleanup_segment_file(&path_clone, "terminal upload failure");
                    tx.send(SegmentUploadOutcome::Failed(seq)).ok();
                }
            }
        });
        upload_handles.push(handle);

        current_sequence += 1;
        *recover_mutex_guard(state.current_sequence.lock(), "current_sequence") = current_sequence;
    }

    // Wait for all uploads to complete
    info!("Waiting for pending uploads to complete...");
    drop(upload_outcome_tx);

    let total_segments = current_sequence.saturating_sub(1);
    let config = recover_mutex_guard(state.config.lock(), "config").clone();
    let recording_token = recover_mutex_guard(state.current_recording_token.lock(), "current_recording_token").clone();

    // Check if we should report "UPLOADING" status
    // We only want to do this if we are STOPPING/UPLOADING, not if we are PAUSED.
    let should_report_uploading = {
        let status = recover_mutex_guard(state.status.lock(), "status");
        matches!(*status, AppStatus::Uploading)
    };

    let thread_outcome = rt.block_on(async {
        let Some(recording_token) = recording_token else {
            log::error!(
                "Missing recording upload token while reporting upload progress for {}",
                recording_id
            );
            return SegmentThreadOutcome::default();
        };
        let mut recording_token = recording_token;

        if should_report_uploading && !state.is_backend_connected.load(Ordering::SeqCst) {
            wait_for_backend_reconnect(&state, "resume queued upload progress").await;
        }

        // Set initial status
        if should_report_uploading {
            uploader::update_status_with_progress(
                &recording_id,
                "UPLOADING",
                0,
                &config,
                &recording_token,
            )
            .await
            .map(|refreshed_token| {
                if let Some(new_token) = refreshed_token {
                    *recover_mutex_guard(state.current_recording_token.lock(), "current_recording_token") = Some(new_token.clone());
                    recording_token = new_token;
                }
            })
            .ok();
        }

        let mut completed_count = 0;
        let mut failed_segments = Vec::new();
        while let Some(outcome) = upload_outcome_rx.recv().await {
            match outcome {
                SegmentUploadOutcome::Uploaded => {
                    completed_count += 1;
                    let progress = if total_segments > 0 {
                        ((completed_count as f32 / total_segments as f32) * 20.0) as i32
                    } else {
                        20
                    };

                    if should_report_uploading {
                        uploader::update_status_with_progress(
                            &recording_id,
                            "UPLOADING",
                            progress,
                            &config,
                            &recording_token,
                        )
                        .await
                        .map(|refreshed_token| {
                            if let Some(new_token) = refreshed_token {
                                *recover_mutex_guard(state.current_recording_token.lock(), "current_recording_token") =
                                    Some(new_token.clone());
                                recording_token = new_token;
                            }
                        })
                        .ok();
                    }
                }
                SegmentUploadOutcome::Failed(sequence) => {
                    failed_segments.push(sequence);
                }
            }
        }

        for handle in upload_handles {
            if let Err(e) = handle.await {
                log::error!("Upload task join error: {}", e);
            }
        }

        SegmentThreadOutcome {
            failed_upload_segments: failed_segments,
        }
    });
    info!("All uploads completed.");

    Ok(thread_outcome)
}

#[cfg(test)]
mod tests {
    use super::{cleanup_segment_file, SegmentThreadOutcome};
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn unique_test_dir() -> std::path::PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!("nojoin-audio-test-{}", unique))
    }

    #[test]
    fn cleanup_segment_file_removes_file_and_empty_parent_directories() {
        let recordings_dir = unique_test_dir().join("Nojoin").join("recordings");
        fs::create_dir_all(&recordings_dir).unwrap();
        let segment_path = recordings_dir.join("temp_1_1.wav");
        fs::write(&segment_path, b"segment-bytes").unwrap();

        cleanup_segment_file(&segment_path, "test cleanup");

        assert!(!segment_path.exists());
        assert!(!recordings_dir.exists());
        assert!(!recordings_dir.parent().unwrap().exists());
    }

    #[test]
    fn segment_thread_outcome_reports_failed_upload_segments() {
        let outcome = SegmentThreadOutcome {
            failed_upload_segments: vec![2, 4, 7],
        };

        assert_eq!(
            outcome.upload_failure_reason().as_deref(),
            Some("segments 2, 4, 7 failed to upload after repeated retries")
        );
    }
}
