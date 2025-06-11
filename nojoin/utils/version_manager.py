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
GITHUB_COMMITS_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/commits/main"
GITHUB_MAIN_ZIP_URL = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"

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
        """Get the current version of Nojoin based on Git commit."""
        current_commit = self._get_current_commit_sha()
        if current_commit:
            return f"main-{current_commit[:8]}"
        else:
            return "main-unknown"
    
    def check_for_updates(self, timeout: int = 10) -> Tuple[bool, Optional[Dict]]:
        """
        Check for available updates from GitHub main branch.
        
        Args:
            timeout: Request timeout in seconds
            
        Returns:
            Tuple of (has_update, release_info)
        """
        try:
            logger.info("Checking for updates from main branch...")
            response = requests.get(GITHUB_COMMITS_API_URL, timeout=timeout)
            response.raise_for_status()
            
            commit_data = response.json()
            latest_commit_sha = commit_data.get("sha", "")
            latest_commit_date = commit_data.get("commit", {}).get("committer", {}).get("date", "")
            commit_message = commit_data.get("commit", {}).get("message", "")
            author_name = commit_data.get("commit", {}).get("author", {}).get("name", "")
            
            if not latest_commit_sha:
                logger.warning("Could not parse latest commit from GitHub")
                return False, None
            
            # Check if we have a newer commit than what we currently have
            current_commit = self._get_current_commit_sha()
            has_update = current_commit != latest_commit_sha
            
            if has_update:
                # Format the date for display
                formatted_date = latest_commit_date
                if latest_commit_date:
                    try:
                        parsed_date = datetime.fromisoformat(latest_commit_date.replace('Z', '+00:00'))
                        formatted_date = parsed_date.strftime('%B %d, %Y at %H:%M UTC')
                    except ValueError:
                        pass
                
                logger.info(f"Update available: {latest_commit_sha[:8]} (current: {current_commit[:8] if current_commit else 'unknown'})")
                return True, {
                    'version': f"main-{latest_commit_sha[:8]}",
                    'name': f'Latest from main branch ({latest_commit_sha[:8]})',
                    'body': f"Latest commit by {author_name}:\n{commit_message}",
                    'published_at': latest_commit_date,
                    'commit_sha': latest_commit_sha,
                    'commit_date_formatted': formatted_date,
                    'download_url': GITHUB_MAIN_ZIP_URL,
                    'size': None  # Size unknown for direct ZIP download
                }
            else:
                logger.info("No updates available - already on latest commit")
                return False, None
                
        except requests.RequestException as e:
            logger.error(f"Network error checking for updates: {e}")
            return False, None
        except Exception as e:
            logger.error(f"Error checking for updates: {e}")
            return False, None
    
    def _get_current_commit_sha(self) -> Optional[str]:
        """Get the current commit SHA if available."""
        try:
            # Try to get from git if available
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
            pass
        
        # Try to get from stored file
        commit_file = os.path.join(self.project_root, '.current_commit')
        if os.path.exists(commit_file):
            try:
                with open(commit_file, 'r') as f:
                    return f.read().strip()
            except Exception:
                pass
        
        return None
    
    def _store_current_commit_sha(self, commit_sha: str):
        """Store the current commit SHA for future reference."""
        try:
            commit_file = os.path.join(self.project_root, '.current_commit')
            with open(commit_file, 'w') as f:
                f.write(commit_sha)
        except Exception as e:
            logger.warning(f"Could not store current commit SHA: {e}")
    
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
        
        # Check if user chose to skip this commit
        skip_version = prefs.get("skip_version")
        commit_sha = release_info.get("commit_sha", "")
        if skip_version == commit_sha or skip_version == release_info.get("version"):
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
        """Mark a version/commit to be skipped."""
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
GITHUB_REPO = "{GITHUB_REPO}"

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
            try:
                backup_db_path = os.path.join(tempfile.gettempdir(), "nojoin_data_backup.db")
                shutil.copy2(db_path, backup_db_path)
                user_data['database'] = backup_db_path
                logger.info("Database backed up")
            except Exception as e:
                logger.warning(f"Failed to backup database: {{e}}")
        
        # Backup config
        config_path = os.path.join(PROJECT_ROOT, "nojoin", "config.json")
        if os.path.exists(config_path):
            try:
                backup_config_path = os.path.join(tempfile.gettempdir(), "config_backup.json")
                shutil.copy2(config_path, backup_config_path)
                user_data['config'] = backup_config_path
                logger.info("Config backed up")
            except Exception as e:
                logger.warning(f"Failed to backup config: {{e}}")
        
        # Backup recordings directory
        recordings_dir = os.path.join(PROJECT_ROOT, "recordings")
        if os.path.exists(recordings_dir):
            try:
                backup_recordings_dir = os.path.join(tempfile.gettempdir(), "recordings_backup")
                if os.path.exists(backup_recordings_dir):
                    shutil.rmtree(backup_recordings_dir)
                shutil.copytree(recordings_dir, backup_recordings_dir)
                user_data['recordings'] = backup_recordings_dir
                logger.info("Recordings backed up")
            except Exception as e:
                logger.warning(f"Failed to backup recordings: {{e}}")
        
        logger.info(f"User data backup completed. Items backed up: {{list(user_data.keys())}}")
        return user_data
        
    except Exception as e:
        logger.error(f"Failed to backup user data: {{e}}")
        return user_data  # Return empty dict instead of None

def restore_user_data(user_data_backup):
    """Restore user data after update."""
    if not user_data_backup:
        logger.info("No user data to restore")
        return
        
    try:
        # Restore database
        if 'database' in user_data_backup:
            try:
                dest_db_path = os.path.join(PROJECT_ROOT, "nojoin", "nojoin_data.db")
                os.makedirs(os.path.dirname(dest_db_path), exist_ok=True)
                shutil.copy2(user_data_backup['database'], dest_db_path)
                os.remove(user_data_backup['database'])
                logger.info("Database restored")
            except Exception as e:
                logger.warning(f"Failed to restore database: {{e}}")
        
        # Restore config
        if 'config' in user_data_backup:
            try:
                dest_config_path = os.path.join(PROJECT_ROOT, "nojoin", "config.json")
                os.makedirs(os.path.dirname(dest_config_path), exist_ok=True)
                shutil.copy2(user_data_backup['config'], dest_config_path)
                os.remove(user_data_backup['config'])
                logger.info("Config restored")
            except Exception as e:
                logger.warning(f"Failed to restore config: {{e}}")
        
        # Restore recordings
        if 'recordings' in user_data_backup:
            try:
                dest_recordings_dir = os.path.join(PROJECT_ROOT, "recordings")
                if os.path.exists(dest_recordings_dir):
                    shutil.rmtree(dest_recordings_dir)
                shutil.copytree(user_data_backup['recordings'], dest_recordings_dir)
                shutil.rmtree(user_data_backup['recordings'])
                logger.info("Recordings restored")
            except Exception as e:
                logger.warning(f"Failed to restore recordings: {{e}}")
        
        logger.info("User data restoration completed")
        
    except Exception as e:
        logger.error(f"Failed to restore user data: {{e}}")

def cleanup_old_installation():
    """Remove old installation files (preserving user data temporarily)."""
    try:
        if not os.path.exists(PROJECT_ROOT):
            logger.error(f"Project root does not exist: {{PROJECT_ROOT}}")
            return False
        
        preserve_items = {{'recordings', 'nojoin'}}  # These are backed up separately
        
        try:
            items_to_process = os.listdir(PROJECT_ROOT)
        except (OSError, PermissionError) as e:
            logger.error(f"Cannot access project directory: {{e}}")
            return False
        
        if items_to_process is None:
            logger.error("Failed to list project directory contents")
            return False
        
        for item in items_to_process:
            if item in preserve_items:
                continue
                
            item_path = os.path.join(PROJECT_ROOT, item)
            
            try:
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                elif os.path.isfile(item_path):
                    os.remove(item_path)
            except (OSError, PermissionError) as e:
                logger.warning(f"Could not remove {{item}}: {{e}}")
        
        logger.info("Old installation cleaned up")
        return True
        
    except Exception as e:
        logger.error(f"Failed to cleanup old installation: {{e}}")
        return False

def extract_update():
    """Extract the update archive."""
    logger.info("Extracting update...")
    
    try:
        # Validate update archive exists
        if not os.path.exists(UPDATE_ARCHIVE):
            logger.error(f"Update archive not found: {{UPDATE_ARCHIVE}}")
            return False
        
        # Create temporary extraction directory
        extract_dir = os.path.join(tempfile.gettempdir(), "nojoin_update_extract")
        if os.path.exists(extract_dir):
            try:
                shutil.rmtree(extract_dir)
            except Exception as e:
                logger.warning(f"Could not remove existing extract directory: {{e}}")
        
        try:
            os.makedirs(extract_dir, exist_ok=True)
        except Exception as e:
            logger.error(f"Could not create extraction directory: {{e}}")
            return False
        
        # Extract update
        try:
            logger.info("Extracting archive...")
            with zipfile.ZipFile(UPDATE_ARCHIVE, 'r') as zipf:
                zipf.extractall(extract_dir)
        except Exception as e:
            logger.error(f"Failed to extract archive: {{e}}")
            return False
        
        # Find the extracted directory (might be nested)
        try:
            extracted_items = os.listdir(extract_dir)
        except (OSError, PermissionError) as e:
            logger.error(f"Cannot access extracted files: {{e}}")
            return False
        
        if not extracted_items:
            logger.error("No files found in extracted archive")
            return False
        
        if len(extracted_items) == 1 and os.path.isdir(os.path.join(extract_dir, extracted_items[0])):
            source_dir = os.path.join(extract_dir, extracted_items[0])
            logger.info(f"Found nested directory: {{extracted_items[0]}}")
        else:
            source_dir = extract_dir
            logger.info("Using extraction directory as source")
        
        # Validate source directory
        if not os.path.exists(source_dir):
            logger.error(f"Source directory not found: {{source_dir}}")
            return False
        
        # Backup user data before overwriting
        logger.info("Backing up user data...")
        user_data_backup = backup_user_data()
        # backup_user_data() always returns a dict, never None
        
        # Remove old installation (except user data)
        logger.info("Cleaning up old installation...")
        if not cleanup_old_installation():
            logger.error("Failed to cleanup old installation")
            return False
        
        # Copy new files
        logger.info("Installing new version...")
        try:
            source_items = os.listdir(source_dir)
        except (OSError, PermissionError) as e:
            logger.error(f"Cannot access source directory: {{e}}")
            return False
        
        if not source_items:
            logger.error("No files found in source directory")
            return False
        
        for item in source_items:
            source_path = os.path.join(source_dir, item)
            dest_path = os.path.join(PROJECT_ROOT, item)
            
            try:
                if os.path.isdir(source_path):
                    if os.path.exists(dest_path):
                        shutil.rmtree(dest_path)
                    shutil.copytree(source_path, dest_path)
                else:
                    # Ensure destination directory exists
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    shutil.copy2(source_path, dest_path)
                    
                logger.debug(f"Copied {{item}}")
            except Exception as e:
                logger.error(f"Failed to copy {{item}}: {{e}}")
                return False
        
        # Restore user data
        logger.info("Restoring user data...")
        restore_user_data(user_data_backup)
        
        # Cleanup extraction directory
        try:
            shutil.rmtree(extract_dir)
            logger.info("Cleaned up extraction directory")
        except Exception as e:
            logger.warning(f"Could not cleanup extraction directory: {{e}}")
        
        logger.info("Update extracted successfully")
        return True
        
    except Exception as e:
        logger.error(f"Failed to extract update: {{e}}")
        import traceback
        logger.error(f"Traceback: {{traceback.format_exc()}}")
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
        # Validate environment
        if not UPDATE_ARCHIVE:
            logger.error("Update archive path not specified")
            return False
            
        if not PROJECT_ROOT:
            logger.error("Project root not specified")
            return False
        
        # Check if update archive exists
        if not os.path.exists(UPDATE_ARCHIVE):
            logger.error(f"Update archive not found: {{UPDATE_ARCHIVE}}")
            return False
        
        # Validate project root exists
        if not os.path.exists(PROJECT_ROOT):
            logger.error(f"Project root not found: {{PROJECT_ROOT}}")
            return False
        
        logger.info(f"Update archive: {{UPDATE_ARCHIVE}}")
        logger.info(f"Project root: {{PROJECT_ROOT}}")
        
        # Wait for main process to end
        try:
            wait_for_process_end()
        except Exception as e:
            logger.warning(f"Error waiting for process to end: {{e}}")
        
        # Create backup if needed
        logger.info("Creating backup...")
        backup_path = backup_current_installation()
        if not backup_path:
            logger.warning("Could not create backup, continuing anyway...")
        
        # Extract and install update
        logger.info("Starting update extraction...")
        if not extract_update():
            logger.error("Update extraction failed")
            return False
        
        # Restart application
        logger.info("Restarting application...")
        try:
            restart_application()
        except Exception as e:
            logger.error(f"Failed to restart application: {{e}}")
            # Don't return False here as the update itself succeeded
        
        # Cleanup update archive
        logger.info("Cleaning up...")
        try:
            if os.path.exists(UPDATE_ARCHIVE):
                os.remove(UPDATE_ARCHIVE)
                logger.info("Update archive removed")
        except Exception as e:
            logger.warning(f"Could not remove update archive: {{e}}")
        
        # Store the updated commit SHA (extract from archive name or metadata)
        try:
            logger.info("Updating commit tracking...")
            # Try to get the latest commit SHA from GitHub after update
            import requests
            response = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/commits/main", timeout=10)
            if response.status_code == 200:
                commit_data = response.json()
                latest_commit_sha = commit_data.get("sha", "")
                if latest_commit_sha:
                    commit_file = os.path.join(PROJECT_ROOT, '.current_commit')
                    with open(commit_file, 'w') as f:
                        f.write(latest_commit_sha)
                    logger.info(f"Stored current commit SHA: {{latest_commit_sha[:8]}}")
                else:
                    logger.warning("Could not get commit SHA from response")
            else:
                logger.warning(f"GitHub API request failed: {{response.status_code}}")
        except Exception as e:
            logger.warning(f"Could not store commit SHA: {{e}}")
        
        logger.info("Update completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Update failed with error: {{e}}")
        import traceback
        logger.error(f"Traceback: {{traceback.format_exc()}}")
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
    
    def mark_successful_update(self, commit_sha: str):
        """Mark that an update was successful and store the new commit SHA."""
        try:
            self._store_current_commit_sha(commit_sha)
            # Clear any skipped version since we successfully updated
            prefs = self.get_update_preferences()
            prefs["skip_version"] = None
            prefs["last_check"] = datetime.now().isoformat()
            self.set_update_preferences(prefs)
            logger.info(f"Successfully updated to commit {commit_sha[:8]}")
        except Exception as e:
            logger.warning(f"Could not mark successful update: {e}")


# Global instance
version_manager = VersionManager() 