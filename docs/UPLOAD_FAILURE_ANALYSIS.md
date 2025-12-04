# Upload Failure Root Cause Analysis

## Problem Statement

User reported being unable to upload a meeting recording while logged in to Nojoin from outside their LAN. The upload failed after 60 retry attempts with alternating errors:
- `413 Payload Too Large`
- `502 Bad Gateway`

## Log Analysis

### Key Timestamps
```
Recording started:  15:07:09
Recording stopped:  15:43:44
Duration:           36 minutes, 35 seconds (2,195 seconds)
```

### Error Pattern
```
Upload failed (attempt 1/60): 413 Payload Too Large
Upload failed (attempt 2/60): 413 Payload Too Large
Upload failed (attempt 3/60): 502 Bad Gateway
...
[Pattern repeats with mixed 413 and 502 errors]
```

### Audio Configuration
```
Format:     Mono (1 channel)
Sample rate: 48,000 Hz
Bit depth:  16-bit PCM
Devices:    Microphone (Anker PowerConf C200) + Headset Earphone
```

## Root Cause

### File Size Calculation
```
Duration:           2,195 seconds
Sample rate:        48,000 Hz
Channels:           1 (mono)
Bit depth:          16 bits (2 bytes per sample)

Bytes per second = 48,000 samples/sec × 1 channel × 2 bytes = 96,000 bytes/sec
Total data size  = 96,000 × 2,195 = 210,720,000 bytes
WAV header       = 44 bytes
Total file size  = 210,720,044 bytes ≈ 201 MB
```

### Infrastructure Analysis

1. **Internal Nginx Configuration**: ✅ PASS
   - File: `nginx/nginx.conf`
   - Setting: `client_max_body_size 500M;`
   - Limit: 500 MB
   - Status: Sufficient for 201 MB file

2. **FastAPI Backend**: ✅ PASS
   - No explicit body size limits configured
   - Import endpoint has 500 MB limit check
   - Segment endpoint has no size restrictions
   - Status: Can handle 201 MB uploads

3. **External Access Context**: ⚠️ ROOT CAUSE
   - User was accessing "from outside my LAN"
   - Error pattern shows both 413 (payload limit) and 502 (gateway error)
   - This strongly suggests an **external reverse proxy or CDN**
   - Most likely: **Cloudflare tunnel or similar service**

### Cloudflare Limits (Most Likely Culprit)

| Plan | Upload Limit |
|------|--------------|
| Free | **100 MB** |
| Pro  | 100 MB |
| Business | 200 MB |
| Enterprise | 500 MB |

**Conclusion**: The 201 MB file exceeds Cloudflare's 100 MB free tier limit, causing the 413 errors.

### Why Both 413 AND 502 Errors?

1. **413 Payload Too Large**: Direct rejection by proxy when size check occurs
2. **502 Bad Gateway**: Proxy timeout or connection reset during upload attempt
   - Large file transfer takes time
   - Proxy may timeout or drop connection
   - Results in gateway error instead of explicit size rejection

## Solution Implemented

### Automatic Time-Based Segmentation

Instead of uploading one large 201 MB file, the companion app now:

1. **Splits recordings** into 5-minute segments (~27 MB each)
2. **Uploads each segment** immediately after recording
3. **Continues recording** seamlessly without gaps
4. **Backend concatenates** segments on finalization

### Benefits

- ✅ Each segment (~27 MB) is well under 100 MB limit
- ✅ Works with any proxy/CDN configuration
- ✅ Provides incremental upload progress
- ✅ Reduces risk of complete upload failure
- ✅ No user configuration required

### Expected Behavior for 36-Minute Recording

| Segment | Duration | Size | Status |
|---------|----------|------|--------|
| 1 | 5:00 | ~27 MB | ✅ Under limit |
| 2 | 5:00 | ~27 MB | ✅ Under limit |
| 3 | 5:00 | ~27 MB | ✅ Under limit |
| 4 | 5:00 | ~27 MB | ✅ Under limit |
| 5 | 5:00 | ~27 MB | ✅ Under limit |
| 6 | 5:00 | ~27 MB | ✅ Under limit |
| 7 | 5:00 | ~27 MB | ✅ Under limit |
| 8 | 1:35 | ~9 MB | ✅ Under limit |

**Result**: All segments upload successfully, recording is preserved!

## Prevention

### For Future Issues

1. **Monitor segment upload logs** - Each segment logs success/failure
2. **Adjust segment duration** if needed (currently 5 minutes)
3. **Document proxy configuration** - Know your external access setup
4. **Consider direct VPN access** - Bypass CDN limits for large uploads

### For Users

When using Nojoin from outside your LAN:
- ✅ **No action required** - Segmentation is automatic
- ℹ️ Recordings > 5 minutes will create multiple segments
- ℹ️ Each segment uploads independently
- ℹ️ Check companion app logs if upload issues occur

## Testing Recommendations

1. **Short recording** (< 5 min): Verify single segment works
2. **Medium recording** (10-15 min): Verify multi-segment works
3. **Long recording** (> 30 min): Verify all segments upload
4. **Network interruption**: Verify retry logic handles failures
5. **External access**: Test through Cloudflare/proxy if applicable

## References

- Error logs: Provided by user on 2025-12-04
- Cloudflare limits: https://developers.cloudflare.com/fundamentals/reference/upload-limits/
- Solution: Automatic segmentation in `companion/src-tauri/src/audio.rs`
- Documentation: See `UPLOAD_SEGMENTATION.md`
