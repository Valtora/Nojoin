"""
Version Management System for Nojoin

This module provides comprehensive version checking, update management, and user preference handling.
It integrates with GitHub releases to check for updates and manages the update process.
"""

import os
import sys
import json
import logging
import requests
import subprocess
import tempfile
import shutil
import zipfile
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, Callable
from packaging import version
from pathlib import Path

from .config_manager import config_manager, get_project_root
from .backup_restore import BackupRestoreManager

logger = logging.getLogger(__name__)

# GitHub repository information
GITHUB_REPO = "Valtora/Nojoin"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
CURRENT_VERSION = "0.5.2"  # This should be updated with each release

class UpdatePreference:
    """Enumeration of update reminder preferences."""
    NEVER = "never"
    NEXT_RUN = "next_run"
    ONE_WEEK = "one_week"
    ONE_MONTH = "one_month"

class VersionManager:
    """Manages version checking, update preferences, and update process."""
    
    def __init__(self):
        self.project_root = get_project_root()
        self.backup_manager = BackupRestoreManager()
        
    def get_current_version(self) -> str:
        """Get the current version of Nojoin."""
        try:
            # Try to get version from our version module
            from .. import __version__
            return __version__.get_version()
        except Exception:
            # Fallback to hardcoded version
            return CURRENT_VERSION
    
    def check_for_updates(self, timeout: int = 10) -> Tuple[bool, Optional[Dict]]:
        """
        Check for available updates from GitHub releases.
        
        Args:
            timeout: Request timeout in seconds
            
        Returns:
            Tuple of (has_update, release_info)
        """
        try:
            logger.info("Checking for updates...")
            response = requests.get(GITHUB_API_URL, timeout=timeout)
            response.raise_for_status()
            
            release_data = response.json()
            latest_version = release_data.get("tag_name", "").lstrip("v")
            
            if not latest_version:
                logger.warning("Could not parse latest version from GitHub")
                return False, None
            
            current_ver = version.parse(self.get_current_version())
            latest_ver = version.parse(latest_version)
            
            has_update = latest_ver > current_ver
            
            if has_update:
                logger.info(f"Update available: {latest_version} (current: {self.get_current_version()})")
                return True, {
                    'version': latest_version,
                    'name': release_data.get('name', f'Version {latest_version}'),
                    'body': release_data.get('body', ''),
                    'published_at': release_data.get('published_at'),
                    'download_url': self._get_download_url(release_data),
                    'size': self._get_download_size(release_data)
                }
            else:
                logger.info("No updates available")
                return False, None
                
        except requests.RequestException as e:
            logger.error(f"Network error checking for updates: {e}")
            return False, None
        except Exception as e:
            logger.error(f"Error checking for updates: {e}")
            return False, None
    
    def _get_download_url(self, release_data: Dict) -> Optional[str]:
        """Extract download URL from release data."""
        assets = release_data.get('assets', [])
        
        # Look for zip file containing source code
        for asset in assets:
            if asset.get('name', '').endswith('.zip') and 'source' not in asset.get('name', '').lower():
                return asset.get('browser_download_url')
        
        # Fallback to source code zip
        return release_data.get('zipball_url')
    
    def _get_download_size(self, release_data: Dict) -> Optional[int]:
        """Extract download size from release data."""
        assets = release_data.get('assets', [])
        
        for asset in assets:
            if asset.get('name', '').endswith('.zip'):
                return asset.get('size')
        
        return None
    
    def get_update_preferences(self) -> Dict:
        """Get current update preferences from config."""
        return config_manager.get("update_preferences", {
            "check_on_startup": True,
            "last_check": None,
            "last_reminded": None,
            "reminder_preference": UpdatePreference.ONE_WEEK,
            "skip_version": None
        })
    
    def set_update_preferences(self, preferences: Dict):
        """Save update preferences to config."""
        current_prefs = self.get_update_preferences()
        current_prefs.update(preferences)
        config_manager.set("update_preferences", current_prefs)
    
    def should_check_for_updates(self) -> bool:
        """Determine if we should check for updates based on user preferences."""
        prefs = self.get_update_preferences()
        
        if not prefs.get("check_on_startup", True):
            return False
        
        # Check if we've checked recently
        last_check = prefs.get("last_check")
        if last_check:
            try:
                last_check_date = datetime.fromisoformat(last_check)
                # Only check once per day
                if datetime.now() - last_check_date < timedelta(days=1):
                    return False
            except ValueError:
                pass  # Invalid date format, proceed with check
        
        return True
    
    def should_remind_about_update(self, release_info: Dict) -> bool:
        """Determine if we should remind the user about an available update."""
        prefs = self.get_update_preferences()
        reminder_pref = prefs.get("reminder_preference", UpdatePreference.ONE_WEEK)
        
        # Check if user chose to skip this version
        skip_version = prefs.get("skip_version")
        if skip_version == release_info.get("version"):
            return False
        
        # Never remind
        if reminder_pref == UpdatePreference.NEVER:
            return False
        
        # Always remind on next run
        if reminder_pref == UpdatePreference.NEXT_RUN:
            return True
        
        # Check time-based reminders
        last_reminded = prefs.get("last_reminded")
        if not last_reminded:
            return True
        
        try:
            last_reminded_date = datetime.fromisoformat(last_reminded)
            now = datetime.now()
            
            if reminder_pref == UpdatePreference.ONE_WEEK:
                return now - last_reminded_date >= timedelta(weeks=1)
            elif reminder_pref == UpdatePreference.ONE_MONTH:
                return now - last_reminded_date >= timedelta(days=30)
        except ValueError:
            return True  # Invalid date format, show reminder
        
        return False
    
    def mark_update_check_performed(self):
        """Mark that an update check was performed."""
        prefs = self.get_update_preferences()
        prefs["last_check"] = datetime.now().isoformat()
        self.set_update_preferences(prefs)
    
    def mark_update_reminder_shown(self):
        """Mark that an update reminder was shown to the user."""
        prefs = self.get_update_preferences()
        prefs["last_reminded"] = datetime.now().isoformat()
        self.set_update_preferences(prefs)
    
    def set_reminder_preference(self, preference: str):
        """Set the user's reminder preference."""
        prefs = self.get_update_preferences()
        prefs["reminder_preference"] = preference
        # Reset last reminded time when preference changes
        prefs["last_reminded"] = None
        self.set_update_preferences(prefs)
    
    def skip_version(self, version_string: str):
        """Mark a version to be skipped."""
        prefs = self.get_update_preferences()
        prefs["skip_version"] = version_string
        self.set_update_preferences(prefs)
    
    def download_update(self, release_info: Dict, progress_callback: Optional[Callable[[int, str], None]] = None) -> Optional[str]:
        """
        Download the update file.
        
        Args:
            release_info: Release information dictionary
            progress_callback: Optional callback for progress updates
            
        Returns:
            Path to downloaded file or None if failed
        """
        download_url = release_info.get("download_url")
        if not download_url:
            logger.error("No download URL available")
            return None
        
        try:
            if progress_callback:
                progress_callback(0, "Starting download...")
            
            # Create temporary file
            temp_dir = tempfile.gettempdir()
            filename = f"nojoin_update_{release_info['version']}.zip"
            temp_path = os.path.join(temp_dir, filename)
            
            # Download with progress tracking
            response = requests.get(download_url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if progress_callback and total_size > 0:
                            progress = int((downloaded / total_size) * 90)  # Reserve 10% for extraction
                            progress_callback(progress, f"Downloaded {downloaded // 1024} KB")
            
            if progress_callback:
                progress_callback(100, "Download complete")
            
            logger.info(f"Update downloaded to: {temp_path}")
            return temp_path
            
        except Exception as e:
            logger.error(f"Error downloading update: {e}")
            if progress_callback:
                progress_callback(-1, f"Download failed: {str(e)}")
            return None
    
    def create_backup_before_update(self, progress_callback: Optional[Callable[[int, str], None]] = None) -> Optional[str]:
        """Create a backup before updating."""
        try:
            temp_dir = tempfile.gettempdir()
            backup_filename = f"nojoin_backup_before_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            backup_path = os.path.join(temp_dir, backup_filename)
            
            success = self.backup_manager.create_backup(backup_path, progress_callback)
            
            if success:
                logger.info(f"Pre-update backup created: {backup_path}")
                return backup_path
            else:
                logger.error("Failed to create pre-update backup")
                return None
                
        except Exception as e:
            logger.error(f"Error creating pre-update backup: {e}")
            return None
    
    def prepare_update_script(self, update_archive_path: str, backup_path: Optional[str] = None) -> str:
        """
        Prepare the standalone update script.
        
        Args:
            update_archive_path: Path to the downloaded update archive
            backup_path: Optional path to backup file
            
        Returns:
            Path to the update script
        """
        temp_dir = tempfile.gettempdir()
        script_path = os.path.join(temp_dir, "nojoin_updater.py")
        
        # Create the updater script
        updater_code = self._generate_updater_script(update_archive_path, backup_path)
        
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(updater_code)
        
        logger.info(f"Update script prepared: {script_path}")
        return script_path
    
    def _generate_updater_script(self, update_archive_path: str, backup_path: Optional[str] = None) -> str:
        """Generate the standalone updater script code."""
        return f'''#!/usr/bin/env python3
"""
Nojoin Standalone Updater Script

This script handles the update process for Nojoin by:
1. Closing the main application
2. Backing up the current installation (if not already done)
3. Extracting the new version
4. Restoring user data
5. Restarting the application
"""

import os
import sys
import time
import shutil
import zipfile
import tempfile
import subprocess
import logging
from pathlib import Path

# Configuration
UPDATE_ARCHIVE = r"{update_archive_path}"
BACKUP_PATH = r"{backup_path or ''}"
PROJECT_ROOT = r"{self.project_root}"
EXECUTABLE_NAME = "Nojoin.py"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(tempfile.gettempdir(), 'nojoin_updater.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def wait_for_process_end():
    """Wait for the main Nojoin process to end."""
    try:
        import psutil
        
        logger.info("Waiting for Nojoin process to end...")
        max_wait = 30  # seconds
        waited = 0
        
        while waited < max_wait:
            nojoin_running = False
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline', [])
                    if any('Nojoin.py' in str(arg) for arg in cmdline):
                        nojoin_running = True
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            if not nojoin_running:
                logger.info("Nojoin process has ended")
                break
            
            time.sleep(1)
            waited += 1
    except ImportError:
        # psutil not available, just wait a bit
        logger.info("psutil not available, waiting 5 seconds...")
        time.sleep(5)
    
    # Additional safety wait
    time.sleep(2)

def backup_current_installation():
    """Create a backup of the current installation if not already done."""
    if BACKUP_PATH and os.path.exists(BACKUP_PATH):
        logger.info(f"Using existing backup: {{BACKUP_PATH}}")
        return BACKUP_PATH
    
    logger.info("Creating backup of current installation...")
    backup_name = f"nojoin_backup_{{int(time.time())}}.zip"
    backup_path = os.path.join(tempfile.gettempdir(), backup_name)
    
    try:
        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(PROJECT_ROOT):
                # Skip temporary files and caches
                dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
                
                for file in files:
                    if file.endswith(('.pyc', '.pyo', '.log')):
                        continue
                        
                    file_path = os.path.join(root, file)
                    arc_path = os.path.relpath(file_path, PROJECT_ROOT)
                    zipf.write(file_path, arc_path)
        
        logger.info(f"Backup created: {{backup_path}}")
        return backup_path
        
    except Exception as e:
        logger.error(f"Failed to create backup: {{e}}")
        return None

def backup_user_data():
    """Backup user data before overwriting."""
    user_data = {{}}
    
    try:
        # Backup database
        db_path = os.path.join(PROJECT_ROOT, "nojoin", "nojoin_data.db")
        if os.path.exists(db_path):
            backup_db_path = os.path.join(tempfile.gettempdir(), "nojoin_data_backup.db")
            shutil.copy2(db_path, backup_db_path)
            user_data['database'] = backup_db_path
        
        # Backup config
        config_path = os.path.join(PROJECT_ROOT, "nojoin", "config.json")
        if os.path.exists(config_path):
            backup_config_path = os.path.join(tempfile.gettempdir(), "config_backup.json")
            shutil.copy2(config_path, backup_config_path)
            user_data['config'] = backup_config_path
        
        # Backup recordings directory
        recordings_dir = os.path.join(PROJECT_ROOT, "recordings")
        if os.path.exists(recordings_dir):
            backup_recordings_dir = os.path.join(tempfile.gettempdir(), "recordings_backup")
            if os.path.exists(backup_recordings_dir):
                shutil.rmtree(backup_recordings_dir)
            shutil.copytree(recordings_dir, backup_recordings_dir)
            user_data['recordings'] = backup_recordings_dir
        
        logger.info("User data backed up")
        return user_data
        
    except Exception as e:
        logger.error(f"Failed to backup user data: {{e}}")
        return user_data

def restore_user_data(user_data_backup):
    """Restore user data after update."""
    try:
        # Restore database
        if 'database' in user_data_backup:
            dest_db_path = os.path.join(PROJECT_ROOT, "nojoin", "nojoin_data.db")
            os.makedirs(os.path.dirname(dest_db_path), exist_ok=True)
            shutil.copy2(user_data_backup['database'], dest_db_path)
            os.remove(user_data_backup['database'])
        
        # Restore config
        if 'config' in user_data_backup:
            dest_config_path = os.path.join(PROJECT_ROOT, "nojoin", "config.json")
            os.makedirs(os.path.dirname(dest_config_path), exist_ok=True)
            shutil.copy2(user_data_backup['config'], dest_config_path)
            os.remove(user_data_backup['config'])
        
        # Restore recordings
        if 'recordings' in user_data_backup:
            dest_recordings_dir = os.path.join(PROJECT_ROOT, "recordings")
            if os.path.exists(dest_recordings_dir):
                shutil.rmtree(dest_recordings_dir)
            shutil.copytree(user_data_backup['recordings'], dest_recordings_dir)
            shutil.rmtree(user_data_backup['recordings'])
        
        logger.info("User data restored")
        
    except Exception as e:
        logger.error(f"Failed to restore user data: {{e}}")

def cleanup_old_installation():
    """Remove old installation files (preserving user data temporarily)."""
    try:
        preserve_items = {{'recordings', 'nojoin'}}  # These are backed up separately
        
        for item in os.listdir(PROJECT_ROOT):
            item_path = os.path.join(PROJECT_ROOT, item)
            
            if item in preserve_items:
                continue
            
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            elif os.path.isfile(item_path):
                os.remove(item_path)
        
        logger.info("Old installation cleaned up")
        
    except Exception as e:
        logger.error(f"Failed to cleanup old installation: {{e}}")

def extract_update():
    """Extract the update archive."""
    logger.info("Extracting update...")
    
    try:
        # Create temporary extraction directory
        extract_dir = os.path.join(tempfile.gettempdir(), "nojoin_update_extract")
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        os.makedirs(extract_dir)
        
        # Extract update
        with zipfile.ZipFile(UPDATE_ARCHIVE, 'r') as zipf:
            zipf.extractall(extract_dir)
        
        # Find the extracted directory (might be nested)
        extracted_items = os.listdir(extract_dir)
        if len(extracted_items) == 1 and os.path.isdir(os.path.join(extract_dir, extracted_items[0])):
            source_dir = os.path.join(extract_dir, extracted_items[0])
        else:
            source_dir = extract_dir
        
        # Backup user data before overwriting
        user_data_backup = backup_user_data()
        
        # Remove old installation (except user data)
        cleanup_old_installation()
        
        # Copy new files
        logger.info("Installing new version...")
        for item in os.listdir(source_dir):
            source_path = os.path.join(source_dir, item)
            dest_path = os.path.join(PROJECT_ROOT, item)
            
            if os.path.isdir(source_path):
                if os.path.exists(dest_path):
                    shutil.rmtree(dest_path)
                shutil.copytree(source_path, dest_path)
            else:
                shutil.copy2(source_path, dest_path)
        
        # Restore user data
        restore_user_data(user_data_backup)
        
        # Cleanup
        shutil.rmtree(extract_dir)
        
        logger.info("Update extracted successfully")
        return True
        
    except Exception as e:
        logger.error(f"Failed to extract update: {{e}}")
        return False

def restart_application():
    """Restart the Nojoin application."""
    logger.info("Restarting Nojoin...")
    
    try:
        executable_path = os.path.join(PROJECT_ROOT, EXECUTABLE_NAME)
        
        if sys.platform == "win32":
            # On Windows, use the virtual environment if available
            venv_python = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
            if os.path.exists(venv_python):
                subprocess.Popen([venv_python, executable_path], cwd=PROJECT_ROOT)
            else:
                subprocess.Popen([sys.executable, executable_path], cwd=PROJECT_ROOT)
        else:
            # On Unix systems
            venv_python = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")
            if os.path.exists(venv_python):
                subprocess.Popen([venv_python, executable_path], cwd=PROJECT_ROOT)
            else:
                subprocess.Popen([sys.executable, executable_path], cwd=PROJECT_ROOT)
        
        logger.info("Nojoin restarted successfully")
        
    except Exception as e:
        logger.error(f"Failed to restart Nojoin: {{e}}")
        input("Press Enter to exit updater...")

def main():
    """Main updater function."""
    logger.info("Nojoin updater started")
    
    try:
        # Check if update archive exists
        if not os.path.exists(UPDATE_ARCHIVE):
            logger.error(f"Update archive not found: {{UPDATE_ARCHIVE}}")
            return False
        
        # Wait for main process to end
        wait_for_process_end()
        
        # Create backup if needed
        backup_path = backup_current_installation()
        if not backup_path:
            logger.warning("Could not create backup, continuing anyway...")
        
        # Extract and install update
        if not extract_update():
            logger.error("Update failed")
            return False
        
        # Restart application
        restart_application()
        
        # Cleanup
        if os.path.exists(UPDATE_ARCHIVE):
            os.remove(UPDATE_ARCHIVE)
        
        logger.info("Update completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Update failed with error: {{e}}")
        return False

if __name__ == "__main__":
    success = main()
    if not success:
        input("Update failed. Press Enter to exit...")
    sys.exit(0 if success else 1)
'''
    
    def execute_update(self, script_path: str) -> bool:
        """Execute the update script as a separate process."""
        try:
            logger.info("Starting update process...")
            
            # Start the updater script as a separate process
            if os.name == 'nt':  # Windows
                subprocess.Popen([
                    sys.executable, script_path
                ], creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:  # Unix
                subprocess.Popen([
                    sys.executable, script_path
                ], start_new_session=True)
            
            logger.info("Update process started")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start update process: {e}")
            return False


# Global instance
version_manager = VersionManager() 