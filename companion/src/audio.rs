use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::Device;
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};
use crossbeam_channel::Receiver;
use crate::state::{AppState, AudioCommand, AppStatus};
use crate::uploader;
use crate::config::Config;
use std::thread;
use hound;

fn find_input_device(host: &cpal::Host, config: &Config) -> Option<Device> {
    if let Some(ref name) = config.input_device_name {
        if let Ok(devices) = host.input_devices() {
            for device in devices {
                if let Ok(device_name) = device.name() {
                    if &device_name == name {
                        println!("Using configured input device: {}", device_name);
                        return Some(device);
                    }
                }
            }
        }
        println!("Warning: Configured input device '{}' not found, using default", name);
    }
    host.default_input_device()
}

fn find_output_device(host: &cpal::Host, config: &Config) -> Option<Device> {
    if let Some(ref name) = config.output_device_name {
        if let Ok(devices) = host.output_devices() {
            for device in devices {
                if let Ok(device_name) = device.name() {
                    if &device_name == name {
                        println!("Using configured output device: {}", device_name);
                        return Some(device);
                    }
                }
            }
        }
        println!("Warning: Configured output device '{}' not found, using default", name);
    }
    host.default_output_device()
}

pub fn run_audio_loop(state: Arc<AppState>, command_rx: Receiver<AudioCommand>) {
    let host = cpal::default_host();
    
    // Log available devices on startup
    let input_device = host.default_input_device().map(|d| d.name().unwrap_or("Unknown".to_string()));
    let output_device = host.default_output_device().map(|d| d.name().unwrap_or("Unknown".to_string()));
    
    println!("Audio System Initialized:");
    println!("  Default Input:  {:?}", input_device);
    println!("  Default Output: {:?}", output_device);

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
                    recording_handle = Some(start_segment(rec_id, seq, state.clone(), is_recording.clone()));
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
    is_recording: Arc<AtomicBool>
) -> std::thread::JoinHandle<()> {
    is_recording.store(true, Ordering::SeqCst);
    let config = state.config.lock().unwrap().clone();
    
    thread::spawn(move || {
        let host = cpal::default_host();
        
        // 1. Setup Microphone (Input) - use configured or default
        let mic_device = find_input_device(&host, &config).expect("No input device available");
        let mic_config = mic_device.default_input_config().expect("Failed to get mic config");
        let mic_channels = mic_config.channels();
        
        // 2. Setup System Audio (Loopback) - use configured or default
        // On Windows WASAPI, we use the output device for loopback
        let sys_device = find_output_device(&host, &config).expect("No output device available");
        let sys_config = sys_device.default_output_config().expect("Failed to get sys config");
        let sys_channels = sys_config.channels();

        println!("Mic: {} ({}ch, {}Hz)", mic_device.name().unwrap_or_default(), mic_channels, mic_config.sample_rate().0);
        println!("Sys: {} ({}ch, {}Hz)", sys_device.name().unwrap_or_default(), sys_channels, sys_config.sample_rate().0);

        // Target format: Mono, 16-bit, Mic Sample Rate (Master Clock)
        let spec = hound::WavSpec {
            channels: 1,
            sample_rate: mic_config.sample_rate().0,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };

        let filename = format!("temp_{}_{}.wav", recording_id, sequence);
        let path = std::env::current_dir().unwrap().join(&filename);
        
        let mut writer = hound::WavWriter::create(&path, spec).unwrap();
        
        // Channels for data transfer
        let (mic_tx, mic_rx) = crossbeam_channel::unbounded::<Vec<f32>>();
        let (sys_tx, sys_rx) = crossbeam_channel::unbounded::<Vec<f32>>();
        
        let err_fn = |err| eprintln!("Stream error: {}", err);
        
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
        let mic_stream = mic_device.build_input_stream(
            &mic_config.into(),
            move |data: &[f32], _: &_| {
                let mono = to_mono(data, mic_channels);
                // Update input level (always, for monitoring)
                let rms = calculate_rms(&mono);
                state_mic.record_input_level(rms);
                
                if is_recording_mic.load(Ordering::SeqCst) {
                    mic_tx.send(mono).unwrap();
                }
            },
            err_fn,
            None
        ).unwrap();

        // 4. Build System Stream
        let is_recording_sys = is_recording.clone();
        let state_sys = state.clone();
        let sys_stream = sys_device.build_input_stream(
            &sys_config.into(),
            move |data: &[f32], _: &_| {
                let mono = to_mono(data, sys_channels);
                // Update output level (always, for monitoring)
                let rms = calculate_rms(&mono);
                state_sys.record_output_level(rms);
                
                if is_recording_sys.load(Ordering::SeqCst) {
                    sys_tx.send(mono).unwrap();
                }
            },
            err_fn,
            None
        ).unwrap();
        
        mic_stream.play().unwrap();
        sys_stream.play().unwrap();
        
        // 5. Mixing Loop
        // We use Mic as the master clock.
        let mut sys_buffer: Vec<f32> = Vec::new();
        
        while is_recording.load(Ordering::SeqCst) {
            // Block on Mic data (Master)
            if let Ok(mic_data) = mic_rx.recv_timeout(std::time::Duration::from_millis(500)) {
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
                    writer.write_sample((mixed * amplitude) as i16).unwrap();
                }
                
                // Remove used system samples
                if sys_buffer.len() >= mic_data.len() {
                    sys_buffer.drain(0..mic_data.len());
                } else {
                    sys_buffer.clear(); // Drained all, some mic samples were unmixed (silence)
                }
            }
        }
        
        // Flush remaining Mic data? 
        // Usually we stop immediately on pause/stop.
        
        writer.finalize().unwrap();
        drop(mic_stream);
        drop(sys_stream);
        
        println!("Segment finished: {:?}", path);
        
        // Upload
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            match uploader::upload_segment(recording_id, sequence, &path, &config).await {
                Ok(_) => {
                    println!("Segment uploaded successfully");
                    // Only delete file if upload was successful
                    if let Err(e) = std::fs::remove_file(&path) {
                        eprintln!("Failed to delete temp file {:?}: {}", path, e);
                    }
                },
                Err(e) => eprintln!("Failed to upload segment: {}. File preserved at {:?}", e, path),
            }
        });
    })
}
