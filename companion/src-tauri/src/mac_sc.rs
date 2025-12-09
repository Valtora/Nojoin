use screencapturekit::{
    sc_content_filter::{SCContentFilter, SCContentFilterInitParams},
    sc_shareable_content::SCShareableContent,
    sc_stream::{SCStream, SCStreamConfiguration, SCStreamOutputType},
    sc_stream_output::SCStreamOutput,
    sc_sys::CMSampleBuffer,
};
use std::sync::mpsc::Sender;
use log::{error, info};

// Import sys crates for audio extraction
#[cfg(target_os = "macos")]
use core_media_sys::{
    CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer, CMSampleBufferRef,
    kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment, CMBlockBufferRef,
};
#[cfg(target_os = "macos")]
use core_audio_sys::{AudioBufferList, AudioBuffer};
#[cfg(target_os = "macos")]
use core_foundation_sys::base::CFRelease;

struct AudioRecorder {
    tx: Sender<Vec<f32>>,
    channels: u16,
}

impl SCStreamOutput for AudioRecorder {
    fn stream_output(&self, sample: CMSampleBuffer, _next_sample_time: u64, output_type: SCStreamOutputType) {
        if let SCStreamOutputType::Audio = output_type {
            #[cfg(target_os = "macos")]
            unsafe {
                // 1. Define a buffer list capable of holding the audio data
                // core-audio-sys defines mBuffers as [AudioBuffer; 1] which is fine for interleaved
                let mut buffer_list = AudioBufferList {
                    mNumberBuffers: 1,
                    mBuffers: [AudioBuffer {
                        mNumberChannels: self.channels as u32,
                        mDataByteSize: 0,
                        mData: std::ptr::null_mut(),
                    }],
                };

                // 2. Retrieve the buffer list from the sample buffer
                // We cast the sample (sc_sys type) to the core_media_sys type
                // Note: sc_sys::CMSampleBuffer is likely a type alias or wrapper around the ref.
                // We assume here it can be cast to CMSampleBufferRef.
                let sample_ref = sample as CMSampleBufferRef;
                
                let mut block_buffer: CMBlockBufferRef = std::ptr::null_mut();
                
                let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
                    sample_ref,
                    std::ptr::null_mut(),
                    &mut buffer_list as *mut _ as *mut _,
                    std::mem::size_of::<AudioBufferList>(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
                    &mut block_buffer
                );

                if status == 0 {
                    // 3. Extract data
                    let buffer = buffer_list.mBuffers[0];
                    if !buffer.mData.is_null() && buffer.mDataByteSize > 0 {
                        // Assuming f32 (Float32) format as requested/configured in SCStreamConfiguration
                        // SCK typically delivers 32-bit float PCM
                        let count = (buffer.mDataByteSize as usize) / std::mem::size_of::<f32>();
                        let src_ptr = buffer.mData as *const f32;
                        let samples = std::slice::from_raw_parts(src_ptr, count);
                        
                        // 4. Send
                        if let Err(e) = self.tx.send(samples.to_vec()) {
                            error!("Failed to send audio samples: {}", e);
                        }
                    }
                    
                    // Release the block buffer if it was retained
                    if !block_buffer.is_null() {
                        CFRelease(block_buffer as _);
                    }
                } else {
                    error!("Failed to get audio buffer list: {}", status);
                }
            }
        }
    }
}

pub async fn start_capture(tx: Sender<Vec<f32>>, sample_rate: u32, channels: u16) -> Result<SCStream, String> {
    let content = SCShareableContent::current().await.map_err(|e| e.to_string())?;
    
    // Find the main display to capture system audio
    // Usually the first display is fine for system audio context
    let display = content.displays.first().ok_or("No display found")?;

    let filter = SCContentFilter::new(SCContentFilterInitParams::Display(display.clone()));

    let config = SCStreamConfiguration {
        captures_audio: true,
        captures_video: false,
        sample_rate,
        channel_count: channels as u32,
        excludes_current_process_audio: true, // Don't record ourselves (feedback loop)
        ..Default::default()
    };

    let recorder = AudioRecorder { tx, channels };
    
    let mut stream = SCStream::new(filter, config, recorder);
    stream.start_capture().await.map_err(|e| e.to_string())?;
    
    Ok(stream)
}
