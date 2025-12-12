import pytest
from backend.utils.config_manager import config_manager, WHISPER_MODEL_SIZES, APP_THEMES

def test_validate_config_value_valid():
    assert config_manager.validate_config_value("whisper_model_size", "tiny") is True
    assert config_manager.validate_config_value("theme", "dark") is True
    assert config_manager.validate_config_value("llm_provider", "gemini") is True

def test_validate_config_value_invalid():
    with pytest.raises(ValueError):
        config_manager.validate_config_value("whisper_model_size", "invalid_size")
    
    with pytest.raises(ValueError):
        config_manager.validate_config_value("theme", "invalid_theme")
        
    with pytest.raises(ValueError):
        config_manager.validate_config_value("llm_provider", "invalid_provider")
