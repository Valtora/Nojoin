use screencapturekit::{
    sc_content_filter::SCContentFilter,
    sc_shareable_content::SCShareableContent,
    sc_stream::{SCStream, SCStreamConfiguration},
    sc_output_handler::{SCStreamOutput, SCStreamOutputType},
};
// Assuming CMSampleBuffer is available via sc_stream or sc_output_handler or top level.
// If sc_sys is missing, we try to find it elsewhere.
// We will use CMSampleBufferRef from core_media_sys if the trait uses raw pointers,
// but likely it uses a wrapper. We'll try to import it from sc_stream.
// If that fails, the user might need to check where CMSampleBuffer is.
// For now, we'll assume it's in sc_stream or we use the raw ref if the trait allows.
// Let's try importing it from sc_stream as a best guess for v0.2.8.
use screencapturekit::sc_stream::CMSampleBuffer; 

use std::sync::mpsc::Sender;
use log::{error, info};

// Import sys crates for audio extraction
#[cfg(target_os = "macos")]
use core_media_sys::{
    CMSampleBufferRef,
    kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment, CMBlockBufferRef,
};
#[cfg(target_os = "macos")]
use coreaudio_sys::{AudioBufferList, AudioBuffer};
#[cfg(target_os = "macos")]
use core_foundation_sys::base::CFRelease;

// Manually define the function missing from core-media-sys 0.1.2
#[cfg(target_os = "macos")]
#[link(name = "CoreMedia", kind = "framework")]
extern "C" {
    pub fn CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
        sbuf: CMSampleBufferRef,
        bufferListSizeNeededOut: *mut usize,
        bufferListOut: *mut AudioBufferList,
        bufferListSize: usize,
        bbufStructAllocator: *mut std::ffi::c_void,
        bbufMemoryAllocator: *mut std::ffi::c_void,
        flags: u32,
        blockBufferOut: *mut CMBlockBufferRef,
    ) -> i32;
}

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
                let mut buffer_list = AudioBufferList {
                    mNumberBuffers: 1,
                    mBuffers: [AudioBuffer {
                        mNumberChannels: self.channels as u32,
                        mDataByteSize: 0,
                        mData: std::ptr::null_mut(),
                    }],
                };

                // 2. Retrieve the buffer list from the sample buffer
                // We assume sample can be cast to CMSampleBufferRef or has a method.
                // If CMSampleBuffer is a wrapper, we might need .as_raw() or similar.
                // We'll try casting as a fallback, but if it's a struct, this might fail.
                // However, without docs, we assume it wraps the ref.
                // Let's try `sample.as_raw()` if it exists, otherwise `sample as CMSampleBufferRef`.
                // Since we can't check, we'll use `sample as CMSampleBufferRef` and hope it's a type alias.
                // If it's a struct, the user will see an error "non-primitive cast".
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
                        let count = (buffer.mDataByteSize as usize) / std::mem::size_of::<f32>();
                        let src_ptr = buffer.mData as *const f32;
                        let samples = std::slice::from_raw_parts(src_ptr, count);
                        
                        if let Err(e) = self.tx.send(samples.to_vec()) {
                            error!("Failed to send audio samples: {}", e);
                        }
                    }
                    
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
    
    let display = content.displays.first().ok_or("No display found")?;

    // SCContentFilter::new(display)
    let filter = SCContentFilter::new(display.clone());

    // SCStreamConfiguration with setters
    let mut config = SCStreamConfiguration::default();
    config.set_captures_audio(true);
    config.set_captures_video(false);
    config.set_sample_rate(sample_rate);
    config.set_channel_count(channels as u32);
    config.set_excludes_current_process_audio(true);

    let recorder = AudioRecorder { tx, channels };
    
    let mut stream = SCStream::new(filter, config, recorder);
    stream.start_capture().await.map_err(|e| e.to_string())?;
    
    Ok(stream)
}
