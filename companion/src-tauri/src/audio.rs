use crate::config::Config;
use crate::state::{AppState, AppStatus, AudioCommand};
use crate::uploader;
use crate::notifications;
use anyhow;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::Device;
use crossbeam_channel::Receiver;
use hound;
use log::{info, warn};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::thread;
use tauri::AppHandle;

// Removed mod mac_sc; declaration from here as it should be in main.rs/lib.rs

fn find_input_device(host: &cpal::Host, config: &Config) -> Option<Device> {
    if let Some(ref name) = config.input_device_name {
        if let Ok(devices) = host.input_devices() {
            for device in devices {
                if let Ok(device_name) = device.name() {
                    if &device_name == name {
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
    if let Some(ref name) = config.output_device_name {
        if let Ok(devices) = host.output_devices() {
            for device in devices {
                if let Ok(device_name) = device.name() {
                    if &device_name == name {
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

pub fn run_audio_loop(state: Arc<AppState>, command_rx: Receiver<AudioCommand>, app_handle: AppHandle) {
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

    // Track the recording thread handle to ensure we wait for uploads
    let mut recording_handle: Option<std::thread::JoinHandle<()>> = None;

    // We need to keep the device alive or clone it.
    // Since we can't easily clone Device in a generic way without knowing the backend,
    // we will just re-acquire it inside the thread or move it if possible.
    // But we need it for multiple segments.
    // Let's assume we can just use the host to get it by name or index if needed,
    // but for now let's try to just pass a reference? No, thread needs 'static.
    // We will re-acquire the default device in the thread for simplicity.

    loop {
        let command = command_rx.recv().unwrap();

        match command {
            AudioCommand::Start(id) => {
                recording_handle = Some(start_segment(id, 1, state.clone(), is_recording.clone()));
            }
            AudioCommand::Resume => {
                let id = *state.current_recording_id.lock().unwrap();
                let seq = *state.current_sequence.lock().unwrap();
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
                if let Some(handle) = recording_handle.take() {
                    let _ = handle.join();
                }
            }
            AudioCommand::Stop => {
                is_recording.store(false, Ordering::SeqCst);
                // Wait for the current segment to finish uploading
                if let Some(handle) = recording_handle.take() {
                    let _ = handle.join();
                }

                // Trigger finalize
                let id = *state.current_recording_id.lock().unwrap();
                let config = state.config.lock().unwrap().clone();

                // Calculate duration
                let duration = {
                    let acc = *state.accumulated_duration.lock().unwrap();
                    let start = *state.recording_start_time.lock().unwrap();
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
                    // Create a new runtime for the async task since we are in a sync thread
                    thread::spawn(move || {
                        let rt = tokio::runtime::Runtime::new().unwrap();
                        rt.block_on(async move {
                            // Check minimum length
                            let min_minutes = config.min_meeting_length.unwrap_or(0);
                            let duration_secs = duration.as_secs();
                            
                            if min_minutes > 0 && duration_secs < (min_minutes as u64 * 60) {
                                info!("Recording too short ({}s < {}m). Discarding.", duration_secs, min_minutes);
                                match uploader::delete_recording(rec_id, &config).await {
                                    Ok(_) => {
                                        info!("Deleted short recording.");
                                        notifications::show_notification(
                                            &app_handle_finalize,
                                            "Recording Discarded",
                                            &format!("Meeting was shorter than {} minutes, discarding meeting.", min_minutes)
                                        );
                                    },
                                    Err(e) => {
                                        eprintln!("Failed to delete short recording: {}", e);
                                        // Fallback to finalize if delete fails? No, better to leave it or try finalize.
                                        // If delete fails, maybe we should finalize so user can delete it manually.
                                        match uploader::finalize_recording(rec_id, &config).await {
                                            Ok(_) => println!("Recording finalized (after delete failed)"),
                                            Err(e) => eprintln!("Failed to finalize: {}", e),
                                        }
                                    },
                                }
                            } else {
                                // No sleep needed anymore, we know upload is done
                                match uploader::finalize_recording(rec_id, &config).await {
                                    Ok(_) => println!("Recording finalized"),
                                    Err(e) => eprintln!("Failed to finalize: {}", e),
                                }
                            }

                            // Cleanup State
                            {
                                let mut status = state_finalize.status.lock().unwrap();
                                *status = AppStatus::Idle;

                                let mut id = state_finalize.current_recording_id.lock().unwrap();
                                *id = None;

                                let mut seq = state_finalize.current_sequence.lock().unwrap();
                                *seq = 1;

                                // Reset duration
                                *state_finalize.accumulated_duration.lock().unwrap() = std::time::Duration::new(0, 0);
                                *state_finalize.recording_start_time.lock().unwrap() = None;
                            }
                        });
                    });
                } else {
                    // If no ID, just reset status
                    let mut status = state.status.lock().unwrap();
                    *status = AppStatus::Idle;
                }
            }
        }
    }
}

fn start_segment(
    recording_id: i64,
    sequence: i32,
    state: Arc<AppState>,
    is_recording: Arc<AtomicBool>,
) -> std::thread::JoinHandle<()> {
    is_recording.store(true, Ordering::SeqCst);
    let config = state.config.lock().unwrap().clone();

    thread::spawn(move || {
        let run = || -> anyhow::Result<()> {
            const MAX_SEGMENT_DURATION_SECS: u64 = 5 * 60;
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
            // We attempt to find a real device. If none found or config fails, we fallback to a virtual silence generator.
            let (mic_stream, mic_sample_rate) = {
                let device_opt = find_input_device(&host, &config);
                
                match device_opt {
                    Some(mic_device) => {
                        info!("Selected Input Device: {}", mic_device.name().unwrap_or_else(|_| "Unknown".to_string()));
                        
                        let config_result: anyhow::Result<cpal::SupportedStreamConfig> = mic_device.default_input_config()
                            .map_err(|e| anyhow::anyhow!("Failed to get default input config: {}", e))
                            .or_else(|e| {
                                warn!("{}. Trying to find first supported config...", e);
                                let config = mic_device.supported_input_configs()
                                    .map_err(|e| anyhow::anyhow!("Failed to get supported configs: {}", e))?
                                    .next()
                                    .ok_or_else(|| anyhow::anyhow!("No supported input configs found"))?
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
                                    let mut mono = Vec::with_capacity(data.len() / channels as usize);
                                    for chunk in data.chunks(channels as usize) {
                                        let sum: f32 = chunk.iter().sum();
                                        mono.push(sum / channels as f32);
                                    }
                                    mono
                                };

                                let stream = mic_device.build_input_stream(
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
                                ).map_err(|e| anyhow::anyhow!("Failed to build mic stream: {}", e))?;

                                (Some(stream), mic_sample_rate)
                            },
                            Err(e) => {
                                warn!("Failed to configure microphone: {}. Falling back to Virtual Silence Microphone.", e);
                                (None, 48000)
                            }
                        }
                    },
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
                    let samples_per_chunk = (sample_rate as f32 * (chunk_duration_ms as f32 / 1000.0)) as usize;
                    
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
            // On Windows WASAPI, we use the output device for loopback
            #[cfg(not(target_os = "macos"))]
            let sys_device = find_output_device(&host, &config)
                .ok_or_else(|| anyhow::anyhow!("No output device available"))?;
            #[cfg(not(target_os = "macos"))]
            let sys_config = sys_device
                .default_output_config()
                .map_err(|e| anyhow::anyhow!("Failed to get sys config: {}", e))?;
            #[cfg(not(target_os = "macos"))]
            let sys_channels = sys_config.channels();
            
            #[cfg(not(target_os = "macos"))]
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
            #[cfg(not(target_os = "macos"))]
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
                
                sys_stream.play().map_err(|e| anyhow::anyhow!("Failed to play sys stream: {}", e))?;
                sys_stream
            };

            #[cfg(target_os = "macos")]
            let sys_stream = {
                // Note: SCK usually captures at 48kHz. If mic is different, we might have drift.
                // For now, we pass the mic sample rate to SCK config and hope it respects it.
                let target_sample_rate = mic_sample_rate;
                
                // Clone the sender for the capture callback
                let tx = sys_tx.clone();
                
                info!("Starting ScreenCaptureKit for System Audio at {}Hz", target_sample_rate);
                
                // Start capture using the synchronous API (screencapturekit 1.3)
                let stream = crate::mac_sc::start_capture(tx, target_sample_rate, 2)
                    .map_err(|e| anyhow::anyhow!("Failed to start SCK: {}", e))?;
                
                stream
            };

            if let Some(stream) = mic_stream {
                stream
                    .play()
                    .map_err(|e| anyhow::anyhow!("Failed to play mic stream: {}", e))?;
                
                // 5. Mixing Loop with automatic segmentation
                run_mixing_loop(
                    recording_id,
                    sequence,
                    spec,
                    mic_rx,
                    sys_rx,
                    is_recording,
                    state.clone(),
                    MAX_SEGMENT_DURATION_SECS,
                    temp_dir,
                )?;
            } else {
                // Virtual mic mode
                run_mixing_loop(
                    recording_id,
                    sequence,
                    spec,
                    mic_rx,
                    sys_rx,
                    is_recording,
                    state.clone(),
                    MAX_SEGMENT_DURATION_SECS,
                    temp_dir,
                )?;
            }

            Ok(())
        };

        if let Err(e) = run() {
            log::error!("Recording thread error: {}", e);
            // Update status to Error
            let mut status = state.status.lock().unwrap();
            *status = AppStatus::Error(e.to_string());
        }
    })
}

// Helper function for the mixing loop to avoid code duplication
fn run_mixing_loop(
    recording_id: i64,
    mut current_sequence: i32,
    spec: hound::WavSpec,
    mic_rx: crossbeam_channel::Receiver<Vec<f32>>,
    sys_rx: crossbeam_channel::Receiver<Vec<f32>>,
    is_recording: Arc<AtomicBool>,
    state: Arc<AppState>,
    max_duration: u64,
    temp_dir: std::path::PathBuf,
) -> anyhow::Result<()> {
    let mut sys_buffer: Vec<f32> = Vec::new();
    let rt = tokio::runtime::Runtime::new().unwrap();

    while is_recording.load(Ordering::SeqCst) {
        let filename = format!("temp_{}_{}.wav", recording_id, current_sequence);
        let path = temp_dir.join(&filename);

        let mut writer = hound::WavWriter::create(&path, spec)
            .map_err(|e| anyhow::anyhow!("Failed to create wav writer: {}", e))?;

        let segment_start = std::time::Instant::now();

        // Record for up to MAX_SEGMENT_DURATION_SECS or until stopped
        while is_recording.load(Ordering::SeqCst) {
            // Check if we've exceeded the maximum segment duration
            if segment_start.elapsed().as_secs() >= max_duration {
                info!(
                    "Segment {} reached maximum duration, starting new segment",
                    current_sequence
                );
                break;
            }

            // Block on Mic data (Master)
            if let Ok(mic_data) = mic_rx.recv_timeout(std::time::Duration::from_millis(500))
            {
                // Collect available System data
                while let Ok(sys_chunk) = sys_rx.try_recv() {
                    sys_buffer.extend(sys_chunk);
                }

                // Mix
                for (_i, mic_sample) in mic_data.iter().enumerate() {
                    let mut mixed = *mic_sample;

                    // Simple mixing: Add system audio if available
                    // Note: This is a naive mix. Real mixing needs resampling if rates differ.
                    // We assume rates are close enough or identical for now.
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

        writer.finalize().map_err(|e| anyhow::anyhow!("Failed to finalize wav writer: {}", e))?;
        info!("Segment {} recorded: {:?}", current_sequence, path);

        // Upload segment in background
        let _state_upload = state.clone();
        let path_clone = path.clone();
        let seq = current_sequence;
        let config = state.config.lock().unwrap().clone();

        rt.spawn(async move {
            match uploader::upload_segment(recording_id, seq, &path_clone, &config).await {
                Ok(_) => info!("Segment {} uploaded successfully", seq),
                Err(e) => log::error!("Failed to upload segment {}: {}", seq, e),
            }
        });

        current_sequence += 1;
        *state.current_sequence.lock().unwrap() = current_sequence;
    }
    Ok(())
}

