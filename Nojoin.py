import os
import sys

if sys.platform == "win32":
    try:
        import ctypes
        # COINIT_APARTMENTTHREADED = 2
        ctypes.windll.ole32.CoInitializeEx(0, 2)
    except Exception as e:
        print(f"Warning: Failed to initialize COM in STA mode: {e}")

# Ensure the 'nojoin' directory is in the Python path
# This allows importing modules like 'nojoin.ui.main_window'
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

# Initialize path management system early
from nojoin.utils.path_manager import path_manager

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

# Import the main window class (assuming it's defined correctly)
try:
    from nojoin.ui.main_window import MainWindow
except ImportError as e:
    print(f"Error importing MainWindow: {e}")
    print("Please ensure 'nojoin/ui/main_window.py' exists and PySide6 is installed.")
    sys.exit(1)

# Import and initialize database
try:
    from nojoin.db import database as db_ops
    db_ops.init_db()
except ImportError as e:
    print(f"FATAL: Could not import database module: {e}")
    sys.exit(1)
except Exception as e:
    print(f"FATAL: Failed to initialize database: {e}")
    # Display error message box?
    sys.exit(1)

# TODO: Add logging setup here later
try:
    from nojoin.utils import logging_config
    logging_config.setup_logging()  # Now uses config for log level and new log path
    
    # Log deployment mode information now that logging is configured
    path_manager.log_deployment_mode()
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    print("[Nojoin] WARNING: Could not import logging_config, using basicConfig at INFO level.")
    
    # Log deployment mode even with basic logging
    path_manager.log_deployment_mode()

# Ensure config is loaded from new location
from nojoin.utils.config_manager import config_manager

# Import SplashScreen
from nojoin.ui.splash import SplashScreen

# TODO: Add configuration loading here later

def Nojoin():
    """Application entry point."""
    app = QApplication(sys.argv)

    # Set Fusion style globally for QSS compliance
    app.setStyle("Fusion")
    
    # Initialize UI Scale Manager early
    from nojoin.utils.ui_scale_manager import get_ui_scale_manager
    ui_scale_manager = get_ui_scale_manager()
    
    # Configure scale manager from saved settings
    ui_scale_config = config_manager.get("ui_scale", {})
    if ui_scale_config.get("mode", "auto") == "manual":
        scale_factor = ui_scale_config.get("scale_factor", 1.0)
        ui_scale_manager.set_user_override(scale_factor)
    
    # Set global QSS from theme with scaling
    from nojoin.utils.theme_utils import get_theme_qss
    theme = config_manager.get("theme", "dark")
    font_scale_factor = ui_scale_manager.get_font_scale_factor()
    app.setStyleSheet(get_theme_qss(theme, font_scale_factor))

    # --- Set Application Icon ---
    # This is the most reliable way to set the taskbar icon.
    # Set application icon using PathManager
    if path_manager.is_development_mode:
        icon_path = path_manager.app_directory / "assets" / "NojoinLogo.png"
    else:
        icon_path = path_manager.app_directory / "assets" / "NojoinLogo.png"
    
    if icon_path.exists():
        app_icon = QIcon(str(icon_path))
        app.setWindowIcon(app_icon)
    else:
        print(f"Warning: Icon file not found at {icon_path}")

    # Show Splash Screen as early as possible using PathManager
    if path_manager.is_development_mode:
        splash_image_path = path_manager.app_directory / "assets" / "Banner_Image1.png"
    else:
        splash_image_path = path_manager.app_directory / "assets" / "Banner_Image1.png"
    
    splash = None
    if splash_image_path.exists():
        from nojoin.ui.splash import SplashScreen
        splash = SplashScreen(str(splash_image_path))
        splash.show_splash()
    else:
        print(f"Warning: Splash image not found at {splash_image_path}")

    # Now import and initialize the rest of the app
    window = MainWindow()
    # The app.setWindowIcon from earlier handles this. If it were still needed,
    # you would pass app_icon to window.setWindowIcon() here.

    if splash:
        splash.finish_splash(window)

    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    Nojoin()