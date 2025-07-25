import os
import sys
import logging

if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(0, 2)
    except Exception as e:
        logging.warning(f"Failed to initialize COM in STA mode: {e}")

# Ensure the 'nojoin' directory is in the Python path
# This allows importing modules like 'nojoin.ui.main_window'
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

# Initialize path management system early
from nojoin.utils.path_manager import path_manager

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

# Set up logging as early as possible
try:
    from nojoin.utils import logging_config
    logging_config.setup_logging()
except ImportError:
    logging.basicConfig(level=logging.INFO)
    logging.warning("Could not import logging_config, using basicConfig at INFO level.")

# Import the main window class (assuming it's defined correctly)
try:
    from nojoin.ui.main_window import MainWindow
except ImportError as e:
    logging.error(f"Error importing MainWindow: {e}")
    logging.error("Please ensure 'nojoin/ui/main_window.py' exists and PySide6 is installed.")
    sys.exit(1)

# Import and initialize database
try:
    from nojoin.db import database as db_ops
    db_ops.init_db()
except ImportError as e:
    logging.critical(f"Could not import database module: {e}")
    sys.exit(1)
except Exception as e:
    logging.critical(f"Failed to initialize database: {e}")
    sys.exit(1)

# Ensure config is loaded from new location
from nojoin.utils.config_manager import config_manager

# Import SplashScreen
from nojoin.ui.splash import SplashScreen



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

    # Set application icon using PathManager
    icon_path = path_manager.assets_directory / "NojoinLogo.png"
    
    if icon_path.exists():
        app_icon = QIcon(str(icon_path))
        app.setWindowIcon(app_icon)
    else:
        logging.warning(f"Icon file not found at {icon_path}")

    # Show Splash Screen as early as possible using PathManager
    splash_image_path = path_manager.assets_directory / "Banner_Image1.png"
    
    splash = None
    if splash_image_path.exists():
        from nojoin.ui.splash import SplashScreen
        splash = SplashScreen(str(splash_image_path))
        splash.show_splash()
    else:
        logging.warning(f"Splash image not found at {splash_image_path}")

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