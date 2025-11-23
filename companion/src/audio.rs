use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};
use crossbeam_channel::Receiver;
use crate::state::{AppState, AudioCommand};
use crate::uploader;
use std::thread;
use hound;

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
                start_segment(id, 1, state.clone(), is_recording.clone());
            }
            AudioCommand::Resume => {
                let id = *state.current_recording_id.lock().unwrap();
                let seq = *state.current_sequence.lock().unwrap();
                if let Some(rec_id) = id {
                    start_segment(rec_id, seq, state.clone(), is_recording.clone());
                }
            }
            AudioCommand::Pause => {
                is_recording.store(false, Ordering::SeqCst);
            }
            AudioCommand::Stop => {
                is_recording.store(false, Ordering::SeqCst);
                // Trigger finalize
                let id = *state.current_recording_id.lock().unwrap();
                if let Some(rec_id) = id {
                    // Create a new runtime for the async task since we are in a sync thread
                    thread::spawn(move || {
                        let rt = tokio::runtime::Runtime::new().unwrap();
                        rt.block_on(async move {
                            // Wait a bit for the upload to finish
                            tokio::time::sleep(tokio::time::Duration::from_secs(2)).await; 
                            match uploader::finalize_recording(rec_id).await {
                                Ok(_) => println!("Recording finalized"),
                                Err(e) => eprintln!("Failed to finalize: {}", e),
                            }
                        });
                    });
                }
            }
        }
    }
}

fn start_segment(
    recording_id: i32,
    sequence: i32,
    _state: Arc<AppState>,
    is_recording: Arc<AtomicBool>
) {
    is_recording.store(true, Ordering::SeqCst);
    
    thread::spawn(move || {
        let host = cpal::default_host();
        
        // 1. Setup Microphone (Input)
        let mic_device = host.default_input_device().expect("No input device available");
        let mic_config = mic_device.default_input_config().expect("Failed to get mic config");
        let mic_channels = mic_config.channels();
        
        // 2. Setup System Audio (Loopback)
        // On Windows WASAPI, we use the default output device for loopback
        let sys_device = host.default_output_device().expect("No output device available");
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

        // 3. Build Mic Stream
        let is_recording_mic = is_recording.clone();
        let mic_stream = mic_device.build_input_stream(
            &mic_config.into(),
            move |data: &[f32], _: &_| {
                if is_recording_mic.load(Ordering::SeqCst) {
                    let mono = to_mono(data, mic_channels);
                    mic_tx.send(mono).unwrap();
                }
            },
            err_fn,
            None
        ).unwrap();

        // 4. Build System Stream
        let is_recording_sys = is_recording.clone();
        let sys_stream = sys_device.build_input_stream(
            &sys_config.into(),
            move |data: &[f32], _: &_| {
                if is_recording_sys.load(Ordering::SeqCst) {
                    let mono = to_mono(data, sys_channels);
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
            match uploader::upload_segment(recording_id, sequence, &path).await {
                Ok(_) => println!("Segment uploaded successfully"),
                Err(e) => eprintln!("Failed to upload segment: {}", e),
            }
        });
        
        // Cleanup
        let _ = std::fs::remove_file(path);
    });
}
