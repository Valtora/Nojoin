import logging
import os

logger = logging.getLogger(__name__)

def _patched_list_audio_backends():
    """
    Mock implementation of list_audio_backends for compatibility with older libraries (like speechbrain).
    Returns a list of available backends (ffmpeg, soundfile, etc.)
    """
    return ['ffmpeg', 'soundfile']

def _patched_torchaudio_load(uri, frame_offset=0, num_frames=-1, normalize=True, channels_first=True, format=None, *args, **kwargs):
    """
    Replacement for torchaudio.load that forces usage of soundfile directly,
    bypassing the broken torchcodec backend in PyTorch 2.9.1+.
    """
    import soundfile as sf
    try:
        import torch
    except ImportError:
        # If torch is not available (e.g. in API container), we can't return a tensor.
        # This function shouldn't be called in the API container anyway.
        raise ImportError("torch is required for audio loading but is not installed.")

    # Map arguments to soundfile
    start = frame_offset
    stop = None if num_frames == -1 else start + num_frames
    
    # soundfile.read returns (data, samplerate)
    # data is (frames, channels) for multi-channel, or (frames,) for mono
    data, sr = sf.read(uri, start=start, stop=stop, format=format, always_2d=False)
    
    # Convert to tensor
    tensor = torch.from_numpy(data).float()
    
    # Ensure (channels, frames) format which torchaudio expects
    if tensor.ndim == 1:
        if channels_first:
            tensor = tensor.unsqueeze(0) # (1, frames)
        else:
            tensor = tensor.unsqueeze(1) # (frames, 1)
    else:
        if channels_first:
            tensor = tensor.t() # (channels, frames)
        
    return tensor, sr

def _patched_torchaudio_info(uri, backend=None):
    """
    Replacement for torchaudio.info that uses soundfile.
    """
    import soundfile as sf
    import torchaudio
    
    # Ensure AudioMetaData is available (it might be patched above)
    _AudioMetaData = getattr(torchaudio, 'AudioMetaData', None)
    
    if _AudioMetaData is None:
         from dataclasses import dataclass
         @dataclass
         class AudioMetaData:
            sample_rate: int
            num_frames: int
            num_channels: int
            bits_per_sample: int
            encoding: str
    else:
        AudioMetaData = _AudioMetaData
    
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

def setup_audio_environment():
    """
    Configures the audio environment to ensure compatibility and stability.
    
    Monkeypatches:
    1. torchaudio.list_audio_backends to exist (for speechbrain compatibility)
    2. torchaudio.load to use soundfile directly (to bypass torchcodec issues)
    3. torchaudio.save to use soundfile directly (to bypass torchcodec issues)
    """
    try:
        import torchaudio
    except ImportError:
        logger.info("Torchaudio not found. Skipping audio environment setup (API mode).")
        return

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
