use crate::config::Config;
use crate::state::{AppState, AppStatus, AudioCommand};
use crate::uploader;
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

pub fn run_audio_loop(state: Arc<AppState>, command_rx: Receiver<AudioCommand>) {
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

                if let Some(rec_id) = id {
                    let state_finalize = state.clone();
                    // Create a new runtime for the async task since we are in a sync thread
                    thread::spawn(move || {
                        let rt = tokio::runtime::Runtime::new().unwrap();
                        rt.block_on(async move {
                            // No sleep needed anymore, we know upload is done
                            match uploader::finalize_recording(rec_id, &config).await {
                                Ok(_) => println!("Recording finalized"),
                                Err(e) => eprintln!("Failed to finalize: {}", e),
                            }

                            // Cleanup State
                            {
                                let mut status = state_finalize.status.lock().unwrap();
                                *status = AppStatus::Idle;

                                let mut id = state_finalize.current_recording_id.lock().unwrap();
                                *id = None;

                                let mut seq = state_finalize.current_sequence.lock().unwrap();
                                *seq = 1;
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
            let host = cpal::default_host();

            // 1. Setup Microphone (Input) - use configured or default
            let mic_device = find_input_device(&host, &config)
                .ok_or_else(|| anyhow::anyhow!("No input device available"))?;
            let mic_config = mic_device
                .default_input_config()
                .map_err(|e| anyhow::anyhow!("Failed to get mic config: {}", e))?;
            let mic_channels = mic_config.channels();

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

            info!(
                "Mic: {} ({}ch, {}Hz)",
                mic_device.name().unwrap_or_default(),
                mic_channels,
                mic_config.sample_rate().0
            );
            
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
                sample_rate: mic_config.sample_rate().0,
                bits_per_sample: 16,
                sample_format: hound::SampleFormat::Int,
            };

            // Channels for data transfer
            let (mic_tx, mic_rx) = crossbeam_channel::unbounded::<Vec<f32>>();
            let (sys_tx, sys_rx) = crossbeam_channel::unbounded::<Vec<f32>>();

            let err_fn = |err| log::error!("Stream error: {}", err);

            // Helper to convert interleaved to mono
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

            // Helper to calculate RMS level (0.0 to 1.0)
            fn calculate_rms(data: &[f32]) -> f32 {
                if data.is_empty() {
                    return 0.0;
                }
                let sum_squares: f32 = data.iter().map(|s| s * s).sum();
                (sum_squares / data.len() as f32).sqrt()
            }

            // 3. Build Mic Stream
            let is_recording_mic = is_recording.clone();
            let state_mic = state.clone();
            let mic_stream = mic_device
                .build_input_stream(
                    &mic_config.into(),
                    move |data: &[f32], _: &_| {
                        let mono = to_mono(data, mic_channels);
                        // Update input level (always, for monitoring)
                        let rms = calculate_rms(&mono);
                        state_mic.record_input_level(rms);

                        if is_recording_mic.load(Ordering::SeqCst) {
                            let _ = mic_tx.send(mono);
                        }
                    },
                    err_fn,
                    None,
                )
                .map_err(|e| anyhow::anyhow!("Failed to build mic stream: {}", e))?;

            // 4. Build System Stream
            let is_recording_sys = is_recording.clone();
            let state_sys = state.clone();
            
            #[cfg(not(target_os = "macos"))]
            let sys_stream = {
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
                // Create a runtime for SCK async start
                let rt = tokio::runtime::Runtime::new().unwrap();
                
                // We need to bridge the SCK callback to our sys_tx
                // The SCK implementation in mac_sc.rs handles the callback and sending to sys_tx
                // We just need to start it.
                
                // Note: SCK usually captures at 48kHz. If mic is different, we might have drift.
                // For now, we pass the mic sample rate to SCK config and hope it respects it or we accept the drift/resampling need.
                let target_sample_rate = mic_config.sample_rate().0;
                
                // We need to wrap the sender to handle the data format
                // Our sys_tx expects Vec<f32> (mono)
                // The mac_sc implementation should handle mono conversion if needed or we do it here.
                // Let's assume mac_sc sends Vec<f32> mono.
                
                // We need to clone the sender for the async block
                let tx = sys_tx.clone();
                
                info!("Starting ScreenCaptureKit for System Audio at {}Hz", target_sample_rate);
                
                let stream = rt.block_on(async {
                    crate::mac_sc::start_capture(tx, target_sample_rate, 2).await
                }).map_err(|e| anyhow::anyhow!("Failed to start SCK: {}", e))?;
                
                stream
            };

            mic_stream
                .play()
                .map_err(|e| anyhow::anyhow!("Failed to play mic stream: {}", e))?;
            
            // 5. Mixing Loop with automatic segmentation
            // Maximum segment duration: 5 minutes to keep files under ~27 MB
            const MAX_SEGMENT_DURATION_SECS: u64 = 5 * 60;

            let mut current_sequence = sequence;
            // Note: sys_buffer is intentionally preserved across segments to avoid losing
            // system audio data that hasn't been mixed yet (samples arrive asynchronously)
            let mut sys_buffer: Vec<f32> = Vec::new();

            // Create a single tokio runtime for all segment uploads (reused for efficiency)
            let rt = tokio::runtime::Runtime::new().unwrap();

            // Setup temp directory for segments
            let temp_dir = std::env::temp_dir().join("Nojoin").join("recordings");
            if let Err(e) = std::fs::create_dir_all(&temp_dir) {
                log::error!("Failed to create temp directory {:?}: {}", temp_dir, e);
                return Err(anyhow::anyhow!("Failed to create temp directory: {}", e));
            }
            info!("Using temp directory: {:?}", temp_dir);

            while is_recording.load(Ordering::SeqCst) {
                let filename = format!("temp_{}_{}.wav", recording_id, current_sequence);
                let path = temp_dir.join(&filename);

                let mut writer = hound::WavWriter::create(&path, spec)
                    .map_err(|e| anyhow::anyhow!("Failed to create wav writer: {}", e))?;

                let segment_start = std::time::Instant::now();

                // Record for up to MAX_SEGMENT_DURATION_SECS or until stopped
                while is_recording.load(Ordering::SeqCst) {
                    // Check if we've exceeded the maximum segment duration
                    if segment_start.elapsed().as_secs() >= MAX_SEGMENT_DURATION_SECS {
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
                        for (i, mic_sample) in mic_data.iter().enumerate() {
                            let mut mixed = *mic_sample;

                            // If we have system audio, add it
                            if i < sys_buffer.len() {
                                mixed += sys_buffer[i];
                            }

                            // Hard clip to prevent wrapping
                            mixed = mixed.clamp(-1.0, 1.0);

                            let amplitude = i16::MAX as f32;
                            writer
                                .write_sample((mixed * amplitude) as i16)
                                .map_err(|e| anyhow::anyhow!("Failed to write sample: {}", e))?;
                        }

                        // Remove used system samples
                        if sys_buffer.len() >= mic_data.len() {
                            sys_buffer.drain(0..mic_data.len());
                        } else {
                            sys_buffer.clear(); // Drained all, some mic samples were unmixed (silence)
                        }
                    }
                }

                // Finalize current segment
                writer
                    .finalize()
                    .map_err(|e| anyhow::anyhow!("Failed to finalize writer: {}", e))?;

                info!("Segment finished: {:?}", path);

                // Upload segment (reuse runtime for efficiency)
                let upload_result = rt.block_on(async {
                    uploader::upload_segment(recording_id, current_sequence, &path, &config).await
                });

                match upload_result {
                    Ok(_) => {
                        info!("Segment {} uploaded successfully", current_sequence);
                        // Only delete file if upload was successful
                        if let Err(e) = std::fs::remove_file(&path) {
                            log::error!("Failed to delete temp file {:?}: {}", path, e);
                        }
                    }
                    Err(e) => {
                        log::error!("Failed to upload segment {}: {}", current_sequence, e);
                        // Continue recording even if upload fails - user can retry later
                    }
                }

                // Increment sequence and update state before starting next segment
                // This ensures Resume commands always get the correct next sequence number
                current_sequence += 1;
                {
                    let mut seq = state.current_sequence.lock().unwrap();
                    *seq = current_sequence;
                }
            }

            drop(mic_stream);
            drop(sys_stream);

            Ok(())
        };

        if let Err(e) = run() {
            log::error!("Audio thread error: {}", e);
            // Ensure we stop recording state if we crash
            is_recording.store(false, Ordering::SeqCst);
        }
    })
}
