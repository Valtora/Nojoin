import huggingface_hub
import logging
import torchaudio

logger = logging.getLogger(__name__)

# --- HuggingFace Patch ---
_original_hf_hub_download = huggingface_hub.hf_hub_download

def _patched_hf_hub_download(*args, **kwargs):
    # If 'use_auth_token' is present, rename it to 'token'
    if 'use_auth_token' in kwargs:
        token = kwargs.pop('use_auth_token')
        # Only add 'token' if it's not already there to avoid conflicts
        if 'token' not in kwargs:
            kwargs['token'] = token
            
    return _original_hf_hub_download(*args, **kwargs)

# --- Pyannote Inference Patch ---
# Pyannote's Inference class (used for embeddings) might still be using use_auth_token internally
# or passing it to other functions that reject it. We need to patch Inference.__init__
try:
    from pyannote.audio import Inference
    _original_inference_init = Inference.__init__

    def _patched_inference_init(self, model, window="sliding", duration=None, step=None, batch_size=32, device=None, use_auth_token=None, token=None, **kwargs):
        # Normalize token argument
        actual_token = token or use_auth_token
        
        # Call original with 'use_auth_token' if it expects it, or 'token' if it expects that.
        # Since we don't know exactly what the installed version expects, we try to be smart.
        # But based on the error "unexpected keyword argument 'token'", it seems the installed 
        # version of Inference.__init__ DOES NOT accept 'token' but DOES accept 'use_auth_token' 
        # (or neither, but likely the former if it's an older version, or the latter if newer).
        
        # Wait, the error was: TypeError: Inference.__init__() got an unexpected keyword argument 'token'
        # This means we passed 'token' (from my previous fix) but it didn't like it.
        # So it probably WANTS 'use_auth_token'.
        
        # Let's try to pass 'use_auth_token' if 'token' was provided.
        if token and not use_auth_token:
            use_auth_token = token
            
        # We will try calling the original with use_auth_token.
        # If the original signature doesn't have it, it might be in **kwargs.
        
        return _original_inference_init(self, model, window=window, duration=duration, step=step, batch_size=batch_size, device=device, use_auth_token=use_auth_token, **kwargs)

except ImportError:
    _patched_inference_init = None
    logger.warning("Could not import pyannote.audio.Inference for patching")


# --- Torchaudio Patch ---
def _patched_list_audio_backends():
    """
    Mock implementation of list_audio_backends for compatibility with older libraries (like speechbrain).
    Returns a list of available backends (ffmpeg, soundfile, etc.)
    """
    return ['ffmpeg', 'soundfile']

def _patched_torchaudio_load(uri, frame_offset=0, num_frames=-1, normalize=True, channels_first=True, *args, **kwargs):
    """
    Replacement for torchaudio.load that forces usage of soundfile directly,
    bypassing the broken torchcodec backend in PyTorch 2.9.1+.
    """
    import soundfile as sf
    import torch
    
    # Map arguments to soundfile
    start = frame_offset
    stop = None if num_frames == -1 else start + num_frames
    
    # soundfile.read returns (data, samplerate)
    # data is (frames, channels) for multi-channel, or (frames,) for mono
    data, sr = sf.read(uri, start=start, stop=stop, always_2d=False)
    
    # Convert to tensor
    tensor = torch.from_numpy(data).float()
    
    # Ensure (channels, frames) format which torchaudio expects
    if tensor.ndim == 1:
        tensor = tensor.unsqueeze(0) # (1, frames)
    else:
        tensor = tensor.t() # (channels, frames)
        
    return tensor, sr

def _patched_torchaudio_info(uri, backend=None):
    """
    Replacement for torchaudio.info that uses soundfile.
    """
    import soundfile as sf
    import torchaudio
    
    # Ensure AudioMetaData is available (it might be patched above)
    AudioMetaData = getattr(torchaudio, 'AudioMetaData', None)
    if AudioMetaData is None:
         from dataclasses import dataclass
         @dataclass
         class AudioMetaData:
            sample_rate: int
            num_frames: int
            num_channels: int
            bits_per_sample: int
            encoding: str
    
    with sf.SoundFile(uri) as f:
        return AudioMetaData(
            sample_rate=f.samplerate,
            num_frames=f.frames,
            num_channels=f.channels,
            bits_per_sample=16, # Approximation
            encoding="PCM_S" # Approximation
        )

def _patched_torchaudio_save(uri, src, sample_rate, channels_first=True, format=None, encoding=None, bits_per_sample=None, buffer_size=4096, backend=None):
    """
    Replacement for torchaudio.save that forces usage of soundfile directly.
    """
    import soundfile as sf
    import torch

    # src is expected to be (channels, time) if channels_first=True (default)
    # soundfile expects (time, channels)
    
    if channels_first:
        if src.ndim == 2:
            src = src.t() # Transpose to (time, channels)
    
    # Convert to numpy
    data = src.detach().cpu().numpy()
    
    # Handle bits_per_sample mapping to subtype
    subtype = None
    if bits_per_sample == 16:
        subtype = 'PCM_16'
    elif bits_per_sample == 24:
        subtype = 'PCM_24'
    elif bits_per_sample == 32:
        subtype = 'PCM_32'
        
    sf.write(uri, data, sample_rate, subtype=subtype, format=format)

def apply_patch():
    """
    Monkeypatches:
    1. huggingface_hub.hf_hub_download to accept 'use_auth_token'
    2. torchaudio.list_audio_backends to exist (for speechbrain compatibility)
    3. torchaudio.load to use soundfile directly (to bypass torchcodec issues)
    4. torchaudio.save to use soundfile directly (to bypass torchcodec issues)
    """
    # Patch HuggingFace
    if huggingface_hub.hf_hub_download != _patched_hf_hub_download:
        huggingface_hub.hf_hub_download = _patched_hf_hub_download
        logger.info("Patched huggingface_hub.hf_hub_download for compatibility")

    # Patch Pyannote Inference
    if _patched_inference_init:
        from pyannote.audio import Inference
        if Inference.__init__ != _patched_inference_init:
            Inference.__init__ = _patched_inference_init
            logger.info("Patched pyannote.audio.Inference.__init__ for compatibility")

    # Patch Torchaudio list_audio_backends
    if not hasattr(torchaudio, 'list_audio_backends'):
        torchaudio.list_audio_backends = _patched_list_audio_backends
        logger.info("Patched torchaudio.list_audio_backends for compatibility")

    # Patch Torchaudio AudioMetaData (missing in some nightly builds)
    if not hasattr(torchaudio, 'AudioMetaData'):
        from dataclasses import dataclass
        @dataclass
        class AudioMetaData:
            sample_rate: int
            num_frames: int
            num_channels: int
            bits_per_sample: int
            encoding: str
        torchaudio.AudioMetaData = AudioMetaData
        logger.info("Patched torchaudio.AudioMetaData for compatibility")

    # Patch Torchaudio info
    if not hasattr(torchaudio, 'info') or torchaudio.info != _patched_torchaudio_info:
        torchaudio.info = _patched_torchaudio_info
        logger.info("Patched torchaudio.info to force soundfile backend")
        
    # Patch Torchaudio load
    # We force this patch because the default load is broken in this env
    if not hasattr(torchaudio, 'load') or torchaudio.load != _patched_torchaudio_load:
        torchaudio.load = _patched_torchaudio_load
        logger.info("Patched torchaudio.load to force soundfile backend")

    # Patch Torchaudio save
    if not hasattr(torchaudio, 'save') or torchaudio.save != _patched_torchaudio_save:
        torchaudio.save = _patched_torchaudio_save
        logger.info("Patched torchaudio.save to force soundfile backend")

# Apply patch automatically on import
apply_patch()
