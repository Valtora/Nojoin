//! macOS ScreenCaptureKit audio capture implementation
//!
//! This module uses Apple's ScreenCaptureKit framework to capture system audio output.
//! Requires Screen Recording permission on macOS.

use crossbeam_channel::Sender;
use log::error;
use screencapturekit::prelude::*;

/// Audio recorder that implements SCStreamOutputTrait to receive audio samples
struct AudioRecorder {
    tx: Sender<Vec<f32>>,
    channels: u16,
}

impl SCStreamOutputTrait for AudioRecorder {
    fn did_output_sample_buffer(&self, sample: CMSampleBuffer, of_type: SCStreamOutputType) {
        if let SCStreamOutputType::Audio = of_type {
            // Extract audio data from the CMSampleBuffer
            if let Some(audio_data) = extract_audio_samples(&sample, self.channels) {
                if let Err(e) = self.tx.send(audio_data) {
                    error!("Failed to send audio samples: {}", e);
                }
            }
        }
    }
}

/// Extract f32 audio samples from a CMSampleBuffer
fn extract_audio_samples(sample: &CMSampleBuffer, channels: u16) -> Option<Vec<f32>> {
    // Get the audio buffer list from the sample buffer
    let audio_buffer_list = sample.audio_buffer_list()?;

    // Collect all samples from all buffers
    let mut all_samples: Vec<f32> = Vec::new();

    for buffer in &audio_buffer_list {
        let data = buffer.data();
        if data.is_empty() {
            continue;
        }

        // ScreenCaptureKit delivers audio in Float32 format
        // Convert raw bytes to f32 samples
        let sample_count = data.len() / std::mem::size_of::<f32>();
        if sample_count == 0 {
            continue;
        }

        // Safety: We're reading f32 values from the audio buffer which is Float32 PCM
        let float_ptr = data.as_ptr() as *const f32;
        for i in 0..sample_count {
            unsafe {
                all_samples.push(*float_ptr.add(i));
            }
        }
    }

    if all_samples.is_empty() {
        return None;
    }

    // If stereo, convert to mono by averaging channels
    if channels == 2 && all_samples.len() >= 2 {
        let mono_samples: Vec<f32> = all_samples
            .chunks(2)
            .map(|chunk| {
                if chunk.len() == 2 {
                    (chunk[0] + chunk[1]) / 2.0
                } else {
                    chunk[0]
                }
            })
            .collect();
        return Some(mono_samples);
    }

    Some(all_samples)
}

/// Wrapper to hold the stream and keep it alive
pub struct AudioCaptureStream {
    _stream: SCStream,
}

/// Start capturing system audio using ScreenCaptureKit
///
/// # Arguments
/// * `tx` - Channel sender for audio samples (Vec<f32>)
/// * `sample_rate` - Desired sample rate (e.g., 48000)
/// * `channels` - Number of channels (typically 2 for stereo capture)
///
/// # Returns
/// * `Ok(AudioCaptureStream)` - The capture stream wrapper (must be kept alive)
/// * `Err(String)` - Error message if capture setup fails
pub fn start_capture(
    tx: Sender<Vec<f32>>,
    sample_rate: u32,
    channels: u16,
) -> Result<AudioCaptureStream, String> {
    // Get available content (displays, windows, apps)
    let content = SCShareableContent::get()
        .map_err(|e| format!("Failed to get shareable content: {:?}", e))?;

    // Get the first display for the content filter
    let displays = content.displays();
    let display = displays.first().ok_or("No display found")?;

    // Create a content filter for the display (required even for audio-only capture)
    let filter = SCContentFilter::builder()
        .display(display)
        .exclude_windows(&[])
        .build();

    // Configure the stream for audio capture
    let config = SCStreamConfiguration::new()
        .with_width(2) // Minimal video settings (required but not used)
        .with_height(2)
        .with_captures_audio(true)
        .with_sample_rate(sample_rate as i32)
        .with_channel_count(channels as i32)
        .with_excludes_current_process_audio(true); // Don't capture our own audio

    // Create the audio recorder handler
    let recorder = AudioRecorder { tx, channels };

    // Create and configure the stream
    let mut stream = SCStream::new(&filter, &config);

    // Add the output handler for audio
    stream.add_output_handler(recorder, SCStreamOutputType::Audio);

    // Start capturing
    stream
        .start_capture()
        .map_err(|e| format!("Failed to start capture: {:?}", e))?;

    Ok(AudioCaptureStream { _stream: stream })
}

/// Check permissions by attempting to fetch shareable content.
/// This will trigger the system permission prompt if not already granted.
pub fn check_permissions() -> bool {
    match SCShareableContent::get() {
        Ok(_) => true,
        Err(e) => {
            log::warn!("Permission check failed (this might trigger prompt): {:?}", e);
            false
        }
    }
}

