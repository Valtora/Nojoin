"""
UI Scale Manager for dynamic resolution-aware scaling.

This module provides a singleton UIScaleManager that detects screen resolution
and applies appropriate scaling factors to maintain usability across different
display sizes while preserving the application's visual design.
"""

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QScreen
from typing import Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class UIScaleManager(QObject):
    """
    Singleton class for managing UI scaling based on screen resolution.
    
    Implements responsive design principles with predefined scaling tiers:
    - Compact: For screens < 1400px width (e.g., 1366x768)
    - Standard: For screens 1400-1800px width (e.g., 1440x900, 1600x900)
    - Comfortable: For screens >= 1800px width (e.g., 1920x1080+)
    """
    
    scale_changed = Signal(float)  # Emitted when scale factor changes
    
    _instance: Optional['UIScaleManager'] = None
    
    # Scaling tier definitions
    SCALE_TIERS = {
        'compact': {
            'name': 'Compact',
            'min_width': 0,
            'max_width': 1399,
            'base_scale': 0.75,
            'spacing_scale': 0.8,
            'font_scale': 0.9,
            'description': 'Optimized for small screens (< 1400px)'
        },
        'standard': {
            'name': 'Standard', 
            'min_width': 1400,
            'max_width': 1799,
            'base_scale': 0.9,
            'spacing_scale': 0.95,
            'font_scale': 1.0,
            'description': 'Balanced for medium screens (1400-1800px)'
        },
        'comfortable': {
            'name': 'Comfortable',
            'min_width': 1800,
            'max_width': float('inf'),
            'base_scale': 1.0,
            'spacing_scale': 1.0,
            'font_scale': 1.0,
            'description': 'Full size for large screens (≥ 1800px)'
        }
    }
    
    def __new__(cls) -> 'UIScaleManager':
        """Ensure singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the UI scale manager."""
        if self._initialized:
            return
            
        super().__init__()
        self._initialized = True
        
        # Current scaling state
        self._current_tier = 'comfortable'
        self._user_scale_override: Optional[float] = None
        self._screen_info: Dict = {}
        
        # Cache for scaled values
        self._scaled_cache: Dict[Tuple[str, float], float] = {}
        
        # Initialize with current screen
        self._detect_screen_properties()
        self._determine_optimal_tier()
        
        # Save current tier to config for settings display
        self._save_current_tier_to_config()
        
        logger.info(f"UIScaleManager initialized with tier: {self._current_tier}")
    
    def _detect_screen_properties(self) -> None:
        """Detect current screen resolution and DPI properties."""
        app = QApplication.instance()
        if not app:
            logger.warning("No QApplication instance found, using default screen properties")
            self._screen_info = {
                'width': 1920,
                'height': 1080,
                'dpi': 96,
                'device_pixel_ratio': 1.0
            }
            return
        
        primary_screen = app.primaryScreen()
        if not primary_screen:
            logger.warning("No primary screen found, using default properties")
            self._screen_info = {
                'width': 1920,
                'height': 1080,
                'dpi': 96,
                'device_pixel_ratio': 1.0
            }
            return
        
        geometry = primary_screen.geometry()
        self._screen_info = {
            'width': geometry.width(),
            'height': geometry.height(),
            'dpi': primary_screen.logicalDotsPerInch(),
            'device_pixel_ratio': primary_screen.devicePixelRatio()
        }
        
        logger.info(f"Screen detected: {self._screen_info['width']}x{self._screen_info['height']} "
                   f"@ {self._screen_info['dpi']} DPI")
    
    def _determine_optimal_tier(self) -> None:
        """Determine the optimal scaling tier based on screen width."""
        screen_width = self._screen_info['width']
        
        for tier_id, tier_config in self.SCALE_TIERS.items():
            if tier_config['min_width'] <= screen_width <= tier_config['max_width']:
                self._current_tier = tier_id
                break
        
        logger.info(f"Selected scaling tier: {self._current_tier} "
                   f"({self.SCALE_TIERS[self._current_tier]['name']})")
    
    def _save_current_tier_to_config(self):
        """Save the current tier to config for settings display."""
        try:
            from nojoin.utils.config_manager import config_manager
            ui_scale_config = config_manager.get("ui_scale", {})
            ui_scale_config["tier"] = self._current_tier
            config_manager.set("ui_scale", ui_scale_config)
        except Exception as e:
            logger.warning(f"Failed to save tier to config: {e}")
    
    def _update_global_theme(self):
        """Update the global application theme with current font scaling."""
        try:
            from PySide6.QtWidgets import QApplication
            from nojoin.utils.config_manager import config_manager
            from nojoin.utils.theme_utils import get_theme_qss
            
            app = QApplication.instance()
            if app:
                theme = config_manager.get("theme", "dark")
                font_scale_factor = self.get_font_scale_factor()
                scaled_qss = get_theme_qss(theme, font_scale_factor)
                app.setStyleSheet(scaled_qss)
                logger.info(f"Updated global theme with font scale factor: {font_scale_factor}")
        except Exception as e:
            logger.warning(f"Failed to update global theme: {e}")
    
    def get_current_tier(self) -> str:
        """Get the current scaling tier identifier."""
        return self._current_tier
    
    def get_tier_info(self, tier_id: Optional[str] = None) -> Dict:
        """Get information about a scaling tier."""
        tier_id = tier_id or self._current_tier
        return self.SCALE_TIERS.get(tier_id, self.SCALE_TIERS['comfortable'])
    
    def get_available_tiers(self) -> Dict[str, Dict]:
        """Get all available scaling tiers for settings UI."""
        return self.SCALE_TIERS.copy()
    
    def set_user_override(self, scale_factor: Optional[float]) -> None:
        """Set a user-defined scale override (None to use automatic)."""
        old_scale = self.get_base_scale_factor()
        self._user_scale_override = scale_factor
        self._scaled_cache.clear()  # Clear cache when scale changes
        
        new_scale = self.get_base_scale_factor()
        if abs(old_scale - new_scale) > 0.01:  # Only emit if meaningful change
            self._update_global_theme()  # Update global theme with new scale
            self.scale_changed.emit(new_scale)
            logger.info(f"User scale override set to: {scale_factor}")
    
    def get_base_scale_factor(self) -> float:
        """Get the current base scale factor."""
        if self._user_scale_override is not None:
            return self._user_scale_override
        
        tier_config = self.SCALE_TIERS[self._current_tier]
        return tier_config['base_scale']
    
    def get_spacing_scale_factor(self) -> float:
        """Get the spacing-specific scale factor."""
        if self._user_scale_override is not None:
            return self._user_scale_override
        
        tier_config = self.SCALE_TIERS[self._current_tier]
        return tier_config['spacing_scale']
    
    def get_font_scale_factor(self) -> float:
        """Get the font-specific scale factor."""
        if self._user_scale_override is not None:
            return self._user_scale_override
        
        tier_config = self.SCALE_TIERS[self._current_tier]
        return tier_config['font_scale']
    
    def scale_value(self, original_value: float, scale_type: str = 'base') -> int:
        """
        Scale a value based on current scaling settings.
        
        Args:
            original_value: The original value to scale
            scale_type: Type of scaling ('base', 'spacing', 'font')
            
        Returns:
            Scaled integer value
        """
        cache_key = (scale_type, original_value)
        if cache_key in self._scaled_cache:
            return self._scaled_cache[cache_key]
        
        if scale_type == 'spacing':
            scale_factor = self.get_spacing_scale_factor()
        elif scale_type == 'font':
            scale_factor = self.get_font_scale_factor()
        else:
            scale_factor = self.get_base_scale_factor()
        
        scaled_value = int(original_value * scale_factor)
        # Ensure minimum values for usability
        if scale_type in ('base', 'spacing') and scaled_value < 1:
            scaled_value = 1
        elif scale_type == 'font' and scaled_value < 8:
            scaled_value = 8
        
        self._scaled_cache[cache_key] = scaled_value
        return scaled_value
    
    def get_scaled_minimum_sizes(self) -> Dict[str, Tuple[int, int]]:
        """Get scaled minimum sizes for major UI components."""
        base_scale = self.get_base_scale_factor()
        
        # Apply more aggressive scaling for compact mode
        if self.is_compact_mode():
            return {
                'main_window': (
                    self.scale_value(1200, 'base'),  # Reduced from 1700
                    self.scale_value(700, 'base')   # Reduced from 800
                ),
                'left_panel': (self.scale_value(250, 'base'), 0),     # Reduced from 330
                'center_panel': (self.scale_value(350, 'base'), 0),   # Reduced from 500
                'right_panel': (self.scale_value(280, 'base'), 0),    # Reduced from 360
                'settings_dialog': (self.scale_value(380, 'base'), 0), # Reduced from 420
                'participants_dialog': (self.scale_value(550, 'base'), 0), # Reduced from 650
                'global_speakers_dialog': (self.scale_value(400, 'base'), 0), # Reduced from 450
                'find_replace_dialog': (self.scale_value(450, 'base'), self.scale_value(350, 'base')), # Reduced
                'transcript_dialog': (self.scale_value(600, 'base'), self.scale_value(450, 'base')) # Reduced
            }
        else:
            return {
                'main_window': (
                    self.scale_value(1700, 'base'),
                    self.scale_value(800, 'base')
                ),
                'left_panel': (self.scale_value(330, 'base'), 0),
                'center_panel': (self.scale_value(500, 'base'), 0),
                'right_panel': (self.scale_value(360, 'base'), 0),
                'settings_dialog': (self.scale_value(420, 'base'), 0),
                'participants_dialog': (self.scale_value(650, 'base'), 0),
                'global_speakers_dialog': (self.scale_value(450, 'base'), 0),
                'find_replace_dialog': (self.scale_value(500, 'base'), self.scale_value(400, 'base')),
                'transcript_dialog': (self.scale_value(700, 'base'), self.scale_value(500, 'base'))
            }
    
    def get_scaled_base_spacing(self, original_spacing: int = 8) -> int:
        """Get scaled base spacing value."""
        return self.scale_value(original_spacing, 'spacing')
    
    def is_compact_mode(self) -> bool:
        """Check if we're in compact mode (small screen)."""
        return self._current_tier == 'compact'
    
    def is_comfortable_mode(self) -> bool:
        """Check if we're in comfortable mode (large screen)."""
        return self._current_tier == 'comfortable'
    
    def get_screen_info(self) -> Dict:
        """Get current screen information."""
        return self._screen_info.copy()
    
    def refresh_screen_detection(self) -> None:
        """Re-detect screen properties and update scaling."""
        old_tier = self._current_tier
        old_scale = self.get_base_scale_factor()
        
        self._detect_screen_properties()
        self._determine_optimal_tier()
        self._scaled_cache.clear()
        
        new_scale = self.get_base_scale_factor()
        if old_tier != self._current_tier or abs(old_scale - new_scale) > 0.01:
            self._save_current_tier_to_config()  # Save new tier
            self._update_global_theme()  # Update global theme with new scale
            self.scale_changed.emit(new_scale)
            logger.info(f"Screen properties refreshed, tier changed: {old_tier} -> {self._current_tier}")


# Global instance accessor
_ui_scale_manager: Optional[UIScaleManager] = None

def get_ui_scale_manager() -> UIScaleManager:
    """Get the global UI scale manager instance."""
    global _ui_scale_manager
    if _ui_scale_manager is None:
        _ui_scale_manager = UIScaleManager()
    return _ui_scale_manager 