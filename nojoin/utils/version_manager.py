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
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, Callable
from packaging import version
from pathlib import Path

from .config_manager import config_manager, get_project_root
from .path_manager import path_manager

logger = logging.getLogger(__name__)

# GitHub repository information
GITHUB_REPO = "Valtora/Nojoin"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

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
        # Also have access to both deployment modes
        self.path_manager = path_manager
        
    def get_current_version(self) -> str:
        """Get the current version of Nojoin from registry or fallback methods."""
        # Try registry first (set by Inno Setup)
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valtora\Nojoin")
            version_str = winreg.QueryValueEx(key, "Version")[0]
            winreg.CloseKey(key)
            logger.debug(f"Version from registry: {version_str}")
            return version_str
        except Exception as e:
            logger.debug(f"Could not read version from registry: {e}")
        
        # Try to read from a version file if it exists
        try:
            version_file = os.path.join(self.project_root, 'VERSION')
            if os.path.exists(version_file):
                with open(version_file, 'r') as f:
                    version_str = f.read().strip()
                    logger.debug(f"Version from file: {version_str}")
                    return version_str
        except Exception as e:
            logger.debug(f"Could not read version from file: {e}")
        
        # Fallback to hardcoded version (should match current Inno Setup version)
        fallback_version = "0.6.2"
        logger.debug(f"Using fallback version: {fallback_version}")
        return fallback_version
    
    def check_for_updates(self, timeout: int = 10) -> Tuple[bool, Optional[Dict]]:
        """
        Check for available updates from GitHub releases.
        
        Args:
            timeout: Request timeout in seconds
            
        Returns:
            Tuple of (has_update, release_info)
        """
        try:
            logger.info("Checking for updates from GitHub releases...")
            response = requests.get(GITHUB_API_URL, timeout=timeout)
            response.raise_for_status()
            
            release_data = response.json()
            latest_version = release_data.get("tag_name", "").lstrip('v')
            current_version = self.get_current_version()
            
            if not latest_version:
                logger.warning("Could not parse latest version from GitHub")
                return False, None
            
            # Compare versions using semantic versioning
            try:
                if version.parse(latest_version) > version.parse(current_version):
                    # Find Windows installer asset
                    installer_asset = None
                    for asset in release_data.get("assets", []):
                        asset_name = asset.get("name", "").lower()
                        if asset_name.endswith(".exe") and ("setup" in asset_name or "install" in asset_name):
                            installer_asset = asset
                            break
                    
                    if installer_asset:
                        logger.info(f"Update available: {latest_version} (current: {current_version})")
                        return True, {
                            'version': latest_version,
                            'name': release_data.get('name', f'Version {latest_version}'),
                            'body': release_data.get('body', ''),
                            'published_at': release_data.get('published_at'),
                            'download_url': installer_asset['browser_download_url'],
                            'size': installer_asset.get('size'),
                            'installer_name': installer_asset.get('name')
                        }
                    else:
                        logger.warning("No Windows installer found in release assets")
                        return False, None
                else:
                    logger.info("Current version is up to date")
                    return False, None
                    
            except Exception as e:
                logger.warning(f"Error comparing versions: {e}")
                # Fallback to string comparison if semantic versioning fails
                if latest_version != current_version:
                    logger.info("Using fallback version comparison")
                    return True, {
                        'version': latest_version,
                        'name': release_data.get('name', f'Version {latest_version}'),
                        'body': release_data.get('body', ''),
                        'published_at': release_data.get('published_at'),
                        'download_url': release_data.get('html_url', ''),  # Fallback to release page
                        'size': None
                    }
                else:
                    return False, None
                
        except requests.RequestException as e:
            logger.error(f"Network error checking for updates: {e}")
            return False, None
        except Exception as e:
            logger.error(f"Error checking for updates: {e}")
            return False, None
    
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
        current_version = release_info.get("version", "")
        if skip_version == current_version:
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
        """Mark that an update reminder was shown."""
        prefs = self.get_update_preferences()
        prefs["last_reminded"] = datetime.now().isoformat()
        self.set_update_preferences(prefs)
    
    def set_reminder_preference(self, preference: str):
        """Set the reminder preference."""
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
        Download the update installer.
        
        Args:
            release_info: Release information dictionary
            progress_callback: Optional callback for progress updates
            
        Returns:
            Path to downloaded installer or None if failed
        """
        download_url = release_info.get("download_url")
        if not download_url:
            logger.error("No download URL available")
            return None
        
        try:
            if progress_callback:
                progress_callback(0, "Starting download...")
            
            # Create temporary file with proper extension
            temp_dir = tempfile.gettempdir()
            installer_name = release_info.get('installer_name', f"Nojoin-Setup-{release_info['version']}.exe")
            temp_path = os.path.join(temp_dir, installer_name)
            
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
                            progress = int((downloaded / total_size) * 100)
                            progress_callback(progress, f"Downloaded {downloaded // 1024} KB of {total_size // 1024} KB")
            
            if progress_callback:
                progress_callback(100, "Download complete")
            
            logger.info(f"Installer downloaded to: {temp_path}")
            return temp_path
            
        except Exception as e:
            logger.error(f"Error downloading installer: {e}")
            if progress_callback:
                progress_callback(-1, f"Download failed: {str(e)}")
            return None
    
    def execute_update(self, installer_path: str) -> bool:
        """
        Execute the Inno Setup installer.
        
        Args:
            installer_path: Path to the downloaded installer
            
        Returns:
            True if installer started successfully, False otherwise
        """
        try:
            logger.info("Starting Inno Setup installer...")
            
            # Verify installer exists
            if not os.path.exists(installer_path):
                logger.error(f"Installer not found: {installer_path}")
                return False
            
            # Run installer with silent upgrade flags
            # /SILENT = Silent install (no user interaction)
            # /NOCANCEL = Don't allow cancellation during install
            # /NORESTART = Don't restart system automatically
            # /RESTARTAPPLICATIONS = Try to restart the application after install
            subprocess.Popen([
                installer_path, 
                '/SILENT',
                '/NOCANCEL',
                '/NORESTART',
                '/RESTARTAPPLICATIONS'
            ])
            
            logger.info("Installer started successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start installer: {e}")
            return False
    
    def mark_successful_update(self, version: str):
        """Mark that an update was successful and store the new version."""
        try:
            # Update registry with new version (if on Windows)
            try:
                import winreg
                key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valtora\Nojoin")
                winreg.SetValueEx(key, "Version", 0, winreg.REG_SZ, version)
                winreg.CloseKey(key)
                logger.info(f"Updated registry with version: {version}")
            except Exception as e:
                logger.debug(f"Could not update registry: {e}")
            
            # Write version to file as backup
            try:
                version_file = os.path.join(self.project_root, 'VERSION')
                with open(version_file, 'w') as f:
                    f.write(version)
                logger.info(f"Updated version file: {version}")
            except Exception as e:
                logger.debug(f"Could not update version file: {e}")
            
            # Clear any skipped version since we successfully updated
            prefs = self.get_update_preferences()
            prefs["skip_version"] = None
            prefs["last_check"] = datetime.now().isoformat()
            self.set_update_preferences(prefs)
            logger.info(f"Successfully updated to version {version}")
        except Exception as e:
            logger.warning(f"Could not mark successful update: {e}")


# Global instance
version_manager = VersionManager() 