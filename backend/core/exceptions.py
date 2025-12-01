class AudioProcessingError(Exception):
    """Base class for audio processing exceptions."""
    pass

class AudioFormatError(AudioProcessingError):
    """Raised when the audio file format is invalid or unsupported."""
    pass

class VADNoSpeechError(AudioProcessingError):
    """Raised when no speech is detected in the audio file."""
    pass
