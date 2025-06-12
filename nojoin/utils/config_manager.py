# nojoin/utils/config_manager.py

import json
import logging
import os
import torch
import soundcard as sc

logger = logging.getLogger(__name__)

CONFIG_FILENAME = 'config.json'
# Place config in the project root alongside the DB for simplicity
CONFIG_PATH = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')), CONFIG_FILENAME)

def _get_default_models():
    """Get default models from LLM_Services to avoid circular imports."""
    try:
        from nojoin.processing.LLM_Services import get_default_model_for_provider
        return {
            "gemini_model": get_default_model_for_provider("gemini"),
            "openai_model": get_default_model_for_provider("openai"),
            "anthropic_model": get_default_model_for_provider("anthropic"),
        }
    except ImportError:
        # Fallback in case of import issues
        return {
            "gemini_model": "gemini-2.5-pro-preview-06-05",
            "openai_model": "gpt-4.1-mini-2025-04-14",
            "anthropic_model": "claude-sonnet-4-20250514",
        }

# Get default models
_default_models = _get_default_models()

DEFAULT_CONFIG = {
    "whisper_model_size": "turbo", # Default model size (e.g., tiny, base, small, medium, large)
    "processing_device": "cuda" if torch.cuda.is_available() else "cpu", # Default to GPU if available
    "recordings_directory": "recordings",
    # Add other settings as needed, e.g., default input/output devices
    "default_input_device_index": None, # None means system default
    "default_output_device_index": None, # None means system default
    "theme": "dark", # Default theme (dark, light)
    "auto_transcribe_on_recording_finish": False, # Automatically transcribe new recordings when finished
    "llm_provider": "gemini",  # LLM provider selection
    "gemini_api_key": None,     # Google Gemini API key
    "openai_api_key": None,     # OpenAI API key
    "anthropic_api_key": None,  # Anthropic API key
    "gemini_model": _default_models["gemini_model"],     # Default Gemini model
    "openai_model": _default_models["openai_model"],     # Default OpenAI model
    "anthropic_model": _default_models["anthropic_model"], # Default Anthropic model
    "notes_font_size": "Medium",  # Font size for meeting notes display
    "advanced": {
        "log_verbosity": "INFO"
    },
    "min_meeting_length_seconds": 1, # Always at least 1 second
    "ui_scale": {
        "mode": "auto",  # "auto", "manual"
        "scale_factor": 1.0,  # Manual scale factor override (when mode is "manual")
        "tier": None  # Auto-detected tier (for display purposes)
    }
}

WHISPER_MODEL_SIZES = ["turbo", "tiny", "base", "small", "medium", "large"]
APP_THEMES = ["dark", "light"] # Available UI themes

def get_available_whisper_model_sizes():
    """Returns a list of supported Whisper model sizes."""
    return WHISPER_MODEL_SIZES.copy()

def get_available_themes():
    """Returns a list of available UI themes."""
    return APP_THEMES.copy()

def get_available_processing_devices():
    """Returns a list of available processing devices (e.g., ["cpu", "cuda"] if CUDA is available)."""
    devices = ["cpu"]
    if torch.cuda.is_available():
        devices.append("cuda")
    return devices

def get_available_input_devices():
    """Returns a list of (index, name) for available input devices (excluding loopback)."""
    devices = []
    try:
        all_mics = sc.all_microphones(include_loopback=True)
        for i, mic in enumerate(all_mics):
            if not mic.isloopback:
                devices.append((i, mic.name))
    except Exception as e:
        logger.error(f"Error listing input devices: {e}", exc_info=True)
    return devices

def get_available_output_devices():
    """Returns a list of (index, name) for available output loopback devices."""
    devices = []
    try:
        all_mics = sc.all_microphones(include_loopback=True)
        for i, mic in enumerate(all_mics):
            if mic.isloopback:
                # Clean up name for display
                name = mic.name
                if '(' in name:
                    name = name.split('(')[0].strip()
                elif 'Loopback' in name:
                    name = name.replace('Loopback', '').replace('(','').replace(')','').strip()
                devices.append((i, f"{name} (Loopback)"))
    except Exception as e:
        logger.error(f"Error listing output devices: {e}", exc_info=True)
    return devices

def get_available_notes_font_sizes():
    """
    Returns available font size options for meeting notes.
    
    Returns:
        list: List of available font size options
    """
    return ["Small", "Medium", "Large"]

def get_available_ui_scale_modes():
    """Returns available UI scale modes."""
    return ["auto", "manual"]

def get_notes_font_size_pixels(size_setting):
    """
    Maps font size setting to actual pixel size for meeting notes.
    
    Args:
        size_setting (str): Font size setting ("Small", "Medium", "Large")
        
    Returns:
        int: Font size in pixels
    """
    font_size_map = {
        "Small": 9,
        "Medium": 12,
        "Large": 15
    }
    return font_size_map.get(size_setting, 12)  # Default to Medium (12px)

class ConfigManager:
    def __init__(self, config_path=None):
        if config_path is None:
            old_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config.json'))
            new_path = CONFIG_PATH
            self.migrate_file_if_needed(old_path, new_path)
            config_path = new_path
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self):
        """Loads configuration from file, applying defaults for missing keys."""
        config = DEFAULT_CONFIG.copy()
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    # Remove deprecated keys if present
                    loaded_config.pop("save_raw_transcript", None)
                    loaded_config.pop("save_diarized_transcript", None)
                    loaded_config.pop("transcripts_directory", None)  # Remove deprecated transcripts directory
                    # Update default config with loaded values, preserving defaults for missing keys
                    config.update(loaded_config) 
                    logger.info(f"Configuration loaded from {self.config_path}")
            else:
                logger.info(f"Configuration file not found at {self.config_path}. Using default settings.")
                # Save the default config if the file doesn't exist
                self._save_config(config) 

            # Ensure necessary directories exist
            self._ensure_dirs_exist(config)

            # --- Ensure min_meeting_length_seconds is always an int >= 1 ---
            min_length = config.get("min_meeting_length_seconds", 1)
            if not isinstance(min_length, int) or min_length < 1:
                min_length = 1
            config["min_meeting_length_seconds"] = min_length

        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {self.config_path}: {e}. Using default settings.", exc_info=True)
            config = DEFAULT_CONFIG.copy() # Reset to defaults on error
            self._ensure_dirs_exist(config)
        except Exception as e:
            logger.error(f"Error loading configuration: {e}. Using default settings.", exc_info=True)
            config = DEFAULT_CONFIG.copy() # Reset to defaults on error
            self._ensure_dirs_exist(config)
        return config

    def _save_config(self, config_data):
        """Saves the current configuration state to the file."""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4)
            logger.info(f"Configuration saved to {self.config_path}")
        except IOError as e:
            logger.error(f"Error saving configuration to {self.config_path}: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"An unexpected error occurred while saving configuration: {e}", exc_info=True)

    def _ensure_dirs_exist(self, config):
        """Creates directories specified in the config if they don't exist."""
        recordings_dir = config.get("recordings_directory")
        for d in [recordings_dir]:
            if d and not os.path.exists(d):
                try:
                    os.makedirs(d, exist_ok=True)
                    logger.info(f"Created directory: {d}")
                except OSError as e:
                    logger.error(f"Failed to create directory {d}: {e}", exc_info=True)
                    # Potentially fallback to a default or raise an error

    def get(self, key, default=None):
        """Gets a configuration value."""
        return self.config.get(key, default)

    def set(self, key, value):
        """Sets a configuration value and saves the config."""
        self.config[key] = value
        self._save_config(self.config)
        # Re-ensure directories if a path was changed
        if key == "recordings_directory":
            self._ensure_dirs_exist(self.config)

    def get_all(self):
        """Returns the entire configuration dictionary."""
        return self.config.copy()

    def migrate_file_if_needed(self, old_path, new_path):
        if os.path.exists(old_path) and not os.path.exists(new_path):
            try:
                os.rename(old_path, new_path)
            except Exception:
                import shutil
                shutil.move(old_path, new_path)

# Global instance (Singleton pattern)
# This makes it easy to access the config from anywhere in the application
# after it's initialized once at startup.
config_manager = ConfigManager()

# Example Usage:
# from nojoin.utils.config_manager import config_manager
# model = config_manager.get("whisper_model_size")
# config_manager.set("whisper_model_size", "small")

# --- Path Utilities ---
def get_project_root():
    """Returns the absolute path to the project root directory."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

def get_recordings_dir():
    """Returns the absolute path to the recordings directory from config."""
    root = get_project_root()
    rel_dir = config_manager.get("recordings_directory", "recordings")
    return os.path.abspath(os.path.join(root, rel_dir))

# Note: get_transcripts_dir() function removed - transcripts are now stored in database

def to_project_relative_path(abs_path):
    """Converts an absolute path to a path relative to the project root."""
    root = get_project_root()
    abs_path = os.path.abspath(abs_path)
    try:
        rel_path = os.path.relpath(abs_path, root)
        return rel_path
    except ValueError:
        # If abs_path is on a different drive (Windows), return as-is
        return abs_path

def from_project_relative_path(rel_path):
    """Converts a project-root-relative path to an absolute path."""
    root = get_project_root()
    return os.path.abspath(os.path.join(root, rel_path))

# --- Path Utilities (Extended) ---
def get_nojoin_dir():
    """Returns the absolute path to the 'nojoin' package directory."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

def get_config_path():
    """Returns the absolute path to the config.json file in the nojoin directory."""
    return os.path.join(get_nojoin_dir(), 'config.json')

def get_log_path():
    """Returns the absolute path to the nojoin.log file in the nojoin directory."""
    return os.path.join(get_nojoin_dir(), 'nojoin.log')

def get_db_path():
    """Returns the absolute path to the nojoin_data.db file in the nojoin directory."""
    return os.path.join(get_nojoin_dir(), 'nojoin_data.db')

# --- Migration Logic for Old Files ---
def migrate_file_if_needed(old_path, new_path):
    if os.path.exists(old_path) and not os.path.exists(new_path):
        try:
            os.rename(old_path, new_path)
        except Exception:
            import shutil
            shutil.move(old_path, new_path)

def get_default_model_for_provider(provider: str) -> str:
    """
    Get the default model for a specific LLM provider.
    This imports from LLM_Services to maintain single source of truth.
    """
    try:
        from nojoin.processing.LLM_Services import get_default_model_for_provider as _get_default
        return _get_default(provider)
    except ImportError:
        # Fallback values
        defaults = {
            "gemini": "gemini-2.5-pro-preview-06-05",
            "openai": "gpt-4.1-mini-2025-04-14", 
            "anthropic": "claude-sonnet-4-20250514"
        }
        return defaults.get(provider, "")

def is_llm_available():
    provider = config_manager.get("llm_provider", "gemini")
    api_key = config_manager.get(f"{provider}_api_key")
    model = config_manager.get(f"{provider}_model")
    return bool(api_key and model) 