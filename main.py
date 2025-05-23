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
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    print("[Nojoin] WARNING: Could not import logging_config, using basicConfig at INFO level.")

# Ensure config is loaded from new location
from nojoin.utils.config_manager import config_manager

# Import SplashScreen
from nojoin.ui.splash import SplashScreen

# TODO: Add configuration loading here later

def main():
    """Application entry point."""
    app = QApplication(sys.argv)

    # Set Fusion style globally for QSS compliance
    app.setStyle("Fusion")
    # Set global QSS from theme
    from nojoin.utils.theme_utils import get_theme_qss
    theme = config_manager.get("theme", "dark")
    app.setStyleSheet(get_theme_qss(theme))

    # Show Splash Screen as early as possible
    splash_image_path = os.path.join(script_dir, "assets", "Banner_Image1.png")
    splash = None
    if os.path.exists(splash_image_path):
        from nojoin.ui.splash import SplashScreen
        splash = SplashScreen(splash_image_path)
        splash.show_splash()
    else:
        print(f"Warning: Splash image not found at {splash_image_path}")

    # Now import and initialize the rest of the app
    # Set Application Icon
    icon_path = os.path.join(script_dir, "assets", "NojoinLogo.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    else:
        print(f"Warning: Icon file not found at {icon_path}")

    window = MainWindow()
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))

    if splash:
        splash.finish_splash(window)

    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main() 