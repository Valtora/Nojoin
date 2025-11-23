"""
Path management system for Nojoin application.

This module provides centralized path resolution for different deployment modes:
- Development mode: Data stored in project directory
- Production mode: App in %APPDATA%, user data in Documents

The system automatically detects the deployment mode and provides appropriate
paths for configuration, database, logs, and user data.
"""

import os
import sys
import platform
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class PathManager:
    """
    Centralized path management for Nojoin application.
    
    Handles environment detection and provides appropriate directory paths
    for different deployment modes (development vs production).
    """
    
    _instance: Optional['PathManager'] = None
    
    def __new__(cls):
        """Singleton pattern to ensure consistent path resolution."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        self._deployment_mode = None
        self._app_directory = None
        self._executable_directory = None  # New: Directory where executable/assets are located
        self._user_data_directory = None
        
        # Initialize paths
        self._detect_deployment_mode()
        self._initialize_directories()
        
        # Minimal logging during initialization (full logging happens later in main app)
        logger.debug(f"PathManager initialized - Mode: {self._deployment_mode}")
        logger.debug(f"Executable directory: {self._executable_directory}")
        logger.debug(f"App directory: {self._app_directory}")
        logger.debug(f"User data directory: {self._user_data_directory}")
    
    def _detect_deployment_mode(self) -> str:
        """
        Detect if running in development or production mode.
        
        Returns:
            str: 'development' or 'production'
        """
        # Check if we're in a frozen executable (PyInstaller/cx_Freeze)
        if getattr(sys, 'frozen', False):
            self._deployment_mode = 'production'
            return 'production'
        
        # Check if we're in a typical development structure
        # Look for indicators like requirements.txt, README.md, etc.
        script_dir = Path(__file__).parent.parent.parent
        dev_indicators = ['requirements.txt', 'README.md', '.git', 'Nojoin.py']
        
        if any((script_dir / indicator).exists() for indicator in dev_indicators):
            self._deployment_mode = 'development'
            return 'development'
        
        # Default to production for safety
        self._deployment_mode = 'production'
        return 'production'
    
    def _initialize_directories(self):
        """Initialize application and user data directories based on deployment mode."""
        if self._deployment_mode == 'development':
            # Development mode: everything in project directory
            project_root = Path(__file__).parent.parent.parent
            self._executable_directory = project_root  # Assets are in project root
            self._app_directory = project_root  # Keep for backward compatibility
            self._user_data_directory = project_root / 'data'
        else:
            # Production mode: executable has assets, user data in Documents
            self._executable_directory = self._get_executable_directory()  # Where assets are bundled
            self._app_directory = self._get_app_directory()  # User data location (backward compatibility)
            self._user_data_directory = self._get_user_data_directory()
    
    def _get_executable_directory(self) -> Path:
        """Get the directory where the executable (and bundled assets) are located."""
        if getattr(sys, 'frozen', False):
            # Running as bundled executable
            if hasattr(sys, '_MEIPASS'):
                # PyInstaller temporary directory
                return Path(sys._MEIPASS)
            else:
                # Other bundlers or direct executable
                return Path(sys.executable).parent
        else:
            # Running as script - return script directory
            return Path(__file__).parent.parent.parent
    
    def _get_app_directory(self) -> Path:
        """Get platform-appropriate application directory."""
        system = platform.system()
        
        if system == "Windows":
            app_data = os.environ.get('APPDATA')
            if not app_data:
                app_data = os.path.expanduser('~\\AppData\\Roaming')
            return Path(app_data) / 'Nojoin'
        
        elif system == "Darwin":  # macOS
            return Path.home() / 'Library' / 'Application Support' / 'Nojoin'
        
        else:  # Linux and other Unix-like
            xdg_data = os.environ.get('XDG_DATA_HOME')
            if xdg_data:
                return Path(xdg_data) / 'Nojoin'
            return Path.home() / '.local' / 'share' / 'Nojoin'
    
    def _get_user_data_directory(self) -> Path:
        """Get platform-appropriate user data directory (Documents folder)."""
        system = platform.system()
        
        if system == "Windows":
            # Try to get Documents folder from registry or environment
            documents = os.environ.get('USERPROFILE')
            if documents:
                return Path(documents) / 'Documents' / 'Nojoin'
            return Path.home() / 'Documents' / 'Nojoin'
        
        else:  # macOS, Linux, and other Unix-like
            return Path.home() / 'Documents' / 'Nojoin'
    
    def ensure_directories_exist(self):
        """Create necessary directories if they don't exist."""
        try:
            self._user_data_directory.mkdir(parents=True, exist_ok=True)
            
            # Create subdirectories
            (self._user_data_directory / 'logs').mkdir(exist_ok=True)
            (self._user_data_directory / 'recordings').mkdir(exist_ok=True)
            
            logger.info(f"Ensured directories exist: {self._user_data_directory}")
            
        except OSError as e:
            logger.error(f"Failed to create directories: {e}")
            raise
    
    # Public API
    @property
    def deployment_mode(self) -> str:
        """Get the current deployment mode."""
        return self._deployment_mode
    
    @property
    def is_development_mode(self) -> bool:
        """Check if running in development mode."""
        return self._deployment_mode == 'development'
    
    @property
    def is_production_mode(self) -> bool:
        """Check if running in production mode."""
        return self._deployment_mode == 'production'
    
    @property
    def app_directory(self) -> Path:
        """Get the application directory path."""
        return self._app_directory
    
    @property
    def executable_directory(self) -> Path:
        """Get the executable directory path (where bundled assets are located)."""
        return self._executable_directory
    
    @property
    def assets_directory(self) -> Path:
        """Get the assets directory path (where icons, images, etc. are bundled)."""
        return self._executable_directory / "assets"
    
    @property
    def user_data_directory(self) -> Path:
        """Get the user data directory path."""
        return self._user_data_directory
    
    @property
    def config_path(self) -> Path:
        """Get the configuration file path."""
        return self._user_data_directory / 'config.json'
    
    @property
    def database_path(self) -> Path:
        """Get the database file path."""
        return self._user_data_directory / 'nojoin_data.db'
    
    @property
    def log_path(self) -> Path:
        """Get the log file path."""
        return self._user_data_directory / 'logs' / 'nojoin.log'
    
    @property
    def recordings_directory(self) -> Path:
        """Get the default recordings directory path."""
        return self._user_data_directory / 'recordings'
    
    def get_recordings_directory_from_config(self, config_recordings_dir: str) -> Path:
        """
        Resolve recordings directory from configuration.
        
        Args:
            config_recordings_dir: Directory from configuration (can be relative or absolute)
            
        Returns:
            Path: Absolute path to recordings directory
        """
        if os.path.isabs(config_recordings_dir):
            # Absolute path - use as-is
            return Path(config_recordings_dir)
        else:
            # Relative path - resolve relative to user data directory
            return self._user_data_directory / config_recordings_dir
    
    def to_user_data_relative_path(self, absolute_path: str) -> str:
        """
        Convert absolute path to path relative to user data directory.
        
        Args:
            absolute_path: Absolute file path
            
        Returns:
            str: Path relative to user data directory, or absolute path if not within user data dir
        """
        try:
            abs_path = Path(absolute_path).resolve()
            return str(abs_path.relative_to(self._user_data_directory))
        except ValueError:
            # Path is not relative to user data directory
            return str(absolute_path)
    
    def from_user_data_relative_path(self, relative_path: str) -> Path:
        """
        Convert user-data-relative path to absolute path.
        
        Args:
            relative_path: Path relative to user data directory
            
        Returns:
            Path: Absolute path
        """
        if os.path.isabs(relative_path):
            return Path(relative_path)
        return self._user_data_directory / relative_path
    
    def migrate_from_project_directory(self) -> bool:
        """
        Migrate data from old project-based structure to new structure.
        
        This handles the transition from development to production deployment.
        
        Returns:
            bool: True if migration was needed and successful, False if no migration needed
        """
        if self._deployment_mode == 'development':
            # No migration needed in development mode
            return False
        
        # Look for old files in project structure
        old_project_root = Path(__file__).parent.parent.parent
        old_nojoin_dir = old_project_root / 'nojoin'
        
        migration_needed = False
        migrations_performed = []
        
        # Check for old files and migrate them
        old_files = {
            'config.json': old_nojoin_dir / 'config.json',
            'nojoin_data.db': old_nojoin_dir / 'nojoin_data.db',
            'nojoin.log': old_nojoin_dir / 'nojoin.log'
        }
        
        try:
            self.ensure_directories_exist()
            
            for file_type, old_path in old_files.items():
                if old_path.exists():
                    migration_needed = True
                    
                    if file_type == 'config.json':
                        new_path = self.config_path
                    elif file_type == 'nojoin_data.db':
                        new_path = self.database_path
                    elif file_type == 'nojoin.log':
                        new_path = self.log_path
                    
                    # Only migrate if new file doesn't already exist
                    if not new_path.exists():
                        try:
                            # Copy instead of move to preserve original during transition
                            import shutil
                            shutil.copy2(str(old_path), str(new_path))
                            migrations_performed.append(f"{file_type} -> {new_path}")
                            logger.info(f"Migrated {file_type}: {old_path} -> {new_path}")
                        except Exception as e:
                            logger.error(f"Failed to migrate {file_type}: {e}")
                            return False
            
            if migration_needed and migrations_performed:
                logger.info(f"Migration completed successfully. Migrated: {', '.join(migrations_performed)}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            return False


# Global instance
path_manager = PathManager() 