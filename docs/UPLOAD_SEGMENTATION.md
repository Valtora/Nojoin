# Audio Upload Segmentation

## Overview

The Nojoin Companion app automatically segments long audio recordings into smaller chunks during upload to prevent failures caused by proxy size limits (such as Cloudflare's 100 MB limit on the free tier).

## Problem

When recording meetings from outside your local network (e.g., through a Cloudflare tunnel or reverse proxy), uploads may fail with a "413 Payload Too Large" error if the recording exceeds the proxy's size limit. For example:
- A 36-minute recording at 48kHz mono results in ~201 MB
- Cloudflare free tier has a 100 MB upload limit
- This causes upload failures and loss of recordings

## Solution

The companion app now implements **automatic time-based segmentation** that splits recordings into manageable chunks:

### Configuration
- **Segment Duration**: 5 minutes per segment
- **Segment Size**: ~27 MB per segment (well under all known proxy limits)
- **Audio Format**: Mono, 48kHz, 16-bit PCM

### How It Works

1. **Recording starts** with segment #1
2. **Every 5 minutes**, the current segment is:
   - Finalized and saved
   - Uploaded to the backend
   - Deleted after successful upload
3. **New segment starts** automatically with incremented sequence number
4. **Recording continues** seamlessly with no audio gaps
5. **When stopped**, the final segment (regardless of duration) is uploaded

### Example Scenarios

#### Short Meeting (4 minutes)
- Creates 1 segment (~22 MB)
- No change from previous behavior

#### Medium Meeting (12 minutes)
- Creates 3 segments:
  - Segment 1: 5 minutes (~27 MB)
  - Segment 2: 5 minutes (~27 MB)
  - Segment 3: 2 minutes (~11 MB)

#### Long Meeting (36 minutes - original failure case)
- Creates 8 segments:
  - Segments 1-7: 5 minutes each (~27 MB each)
  - Segment 8: 1 minute (~5.5 MB)
- **Total size unchanged** but split into uploadable chunks
- **All segments upload successfully**

## Technical Details

### Size Calculations
```
Audio Format: Mono (1 channel), 48000 Hz, 16-bit
- Bytes per sample: 2
- Samples per second: 48,000
- Bytes per second: 96,000
- Bytes per 5 minutes: 28,800,000 (~27.47 MB)
```

### Code Location
The segmentation logic is implemented in `companion/src-tauri/src/audio.rs` in the `start_segment()` function.

### Backend Compatibility
The backend already supports multi-segment uploads through the `/recordings/{id}/segment` endpoint. The segmentation feature uses existing infrastructure:
- Each segment is uploaded individually
- Segments are numbered sequentially
- Backend concatenates segments on finalization
- No backend changes required

## Monitoring

When segmentation occurs, you'll see log messages like:
```
[INFO nojoin_companion::audio] Segment 1 reached maximum duration, starting new segment
[INFO nojoin_companion::audio] Segment finished: "temp_2025120415070269_1.wav"
[INFO nojoin_companion::uploader] Segment 1 uploaded successfully
```

## Benefits

1. **Reliability**: Recordings upload successfully regardless of length
2. **Progress**: Each segment uploads immediately, providing incremental progress
3. **Recovery**: If upload fails mid-recording, earlier segments are already saved
4. **Proxy-Agnostic**: Works with any reverse proxy, CDN, or upload size limit
5. **Transparent**: No user action required, works automatically

## Troubleshooting

### What if a segment upload fails?
- The companion app retries uploads up to 60 times with exponential backoff
- The recording continues even if one segment fails to upload
- Failed segments remain in the temp directory for manual recovery
- Check logs for specific error messages

### Can I change the segment duration?
Yes, modify `MAX_SEGMENT_DURATION_SECS` in `companion/src-tauri/src/audio.rs`:
```rust
const MAX_SEGMENT_DURATION_SECS: u64 = 5 * 60; // 5 minutes
```

**Note**: Shorter segments = smaller files but more overhead. We recommend keeping it at 5 minutes unless you have specific requirements.

### What about audio quality?
Segmentation does not affect audio quality:
- Same sample rate, bit depth, and channel configuration
- No re-encoding or compression
- Segments are seamlessly concatenated by the backend
- Final processed audio is identical to single-file upload

## References

- Original issue: User unable to upload 36-minute recording from outside LAN
- Error: `413 Payload Too Large`
- Root cause: Cloudflare proxy 100 MB upload limit
- Fix: Automatic 5-minute segmentation (~27 MB per segment)
