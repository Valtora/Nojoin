"""
Nojoin Version Management and Auto-Update System
Checks GitHub releases for newer versions and manages updates
"""

import os
import sys
import json
import logging
import requests
import requests.exceptions
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Tuple
from packaging import version

from .config_manager import config_manager, get_nojoin_dir

logger = logging.getLogger(__name__)

class VersionManager:
    """Manages application versioning and updates"""
    
    # Update these for your repository
    GITHUB_REPO = "Valtora/Nojoin"  
    GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    UPDATE_CHECK_INTERVAL_HOURS = 24
    
    def __init__(self):
        self.current_version = self._get_current_version()
        self.last_check_file = Path(get_nojoin_dir()) / "last_update_check.json"
        
    def _get_current_version(self) -> str:
        """Get current application version"""
        try:
            # Try to read from version.json (created during build)
            app_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent.parent.parent
            version_file = app_dir / "version.json"
            
            if version_file.exists():
                with open(version_file, 'r') as f:
                    data = json.load(f)
                    return data.get('version', '0.4.0')
            
            # Fallback to hardcoded version
            return "0.4.0"
            
        except Exception as e:
            logger.warning(f"Could not determine current version: {e}")
            return "0.4.0"
    
    def should_check_for_updates(self) -> bool:
        """Check if enough time has passed since last update check"""
        if not config_manager.get("auto_update_check", True):
            return False
            
        try:
            if not self.last_check_file.exists():
                return True
                
            with open(self.last_check_file, 'r') as f:
                data = json.load(f)
                last_check = datetime.fromisoformat(data['last_check'])
                return datetime.now() - last_check > timedelta(hours=self.UPDATE_CHECK_INTERVAL_HOURS)
                
        except Exception as e:
            logger.warning(f"Error checking update schedule: {e}")
            return True
    
    def _save_last_check_time(self):
        """Save the current time as last update check"""
        try:
            data = {
                'last_check': datetime.now().isoformat(),
                'current_version': self.current_version
            }
            with open(self.last_check_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"Could not save last check time: {e}")
    
    def check_for_updates(self, timeout: int = 10) -> Optional[Dict]:
        """
        Check GitHub for newer version
        
        Returns:
            Dict with update info if available, None if no update or error
            {
                'version': '1.1.0',
                'download_url': 'https://...',
                'release_notes': 'What\'s new...',
                'published_at': '2025-01-01T00:00:00Z'
            }
        """
        self._save_last_check_time()
        
        try:
            logger.info("Checking for updates...")
            
            headers = {'User-Agent': f'Nojoin/{self.current_version}'}
            response = requests.get(self.GITHUB_API_URL, headers=headers, timeout=timeout)
            
            # Handle specific HTTP status codes
            if response.status_code == 404:
                logger.info("No releases found in repository")
                raise requests.exceptions.RequestException("Repository has no releases published yet")
            
            response.raise_for_status()
            
            release_data = response.json()
            latest_version = release_data['tag_name'].lstrip('v')  # Remove 'v' prefix if present
            
            # Compare versions
            if version.parse(latest_version) > version.parse(self.current_version):
                # Find installer download URL
                download_url = None
                for asset in release_data.get('assets', []):
                    if asset['name'].endswith('-setup.exe'):
                        download_url = asset['browser_download_url']
                        break
                
                if download_url:
                    update_info = {
                        'version': latest_version,
                        'download_url': download_url,
                        'release_notes': release_data.get('body', 'No release notes available'),
                        'published_at': release_data.get('published_at'),
                        'download_size': next((a['size'] for a in release_data.get('assets', []) 
                                             if a['name'].endswith('-setup.exe')), 0)
                    }
                    
                    logger.info(f"Update available: {self.current_version} -> {latest_version}")
                    return update_info
                else:
                    logger.warning("Update found but no installer download available")
            else:
                logger.info(f"No updates available (current: {self.current_version}, latest: {latest_version})")
                
        except requests.exceptions.Timeout:
            logger.warning("Timeout while checking for updates")
            raise requests.exceptions.RequestException("Connection timeout - please check your internet connection")
        except requests.exceptions.ConnectionError:
            logger.warning("Connection error while checking for updates")
            raise requests.exceptions.RequestException("Unable to connect to update server - please check your internet connection")
        except requests.exceptions.RequestException as e:
            if "404" in str(e) or "no releases" in str(e).lower():
                logger.info("No releases published yet")
                raise requests.exceptions.RequestException("No releases available - this is a development version")
            logger.warning(f"Network error checking for updates: {e}")
            raise requests.exceptions.RequestException("Update server is unreachable")
        except Exception as e:
            logger.error(f"Error checking for updates: {e}", exc_info=True)
            raise requests.exceptions.RequestException(f"Update check failed: {str(e)}")
            
        return None
    
    def download_update(self, update_info: Dict, progress_callback=None) -> Optional[Path]:
        """
        Download update installer
        
        Args:
            update_info: Update info from check_for_updates()
            progress_callback: Function to call with progress (percent)
            
        Returns:
            Path to downloaded installer or None if failed
        """
        try:
            download_url = update_info['download_url']
            logger.info(f"Downloading update from: {download_url}")
            
            # Create temp file for download
            temp_dir = Path(tempfile.gettempdir()) / "nojoin_update"
            temp_dir.mkdir(exist_ok=True)
            
            installer_name = f"nojoin-{update_info['version']}-setup.exe"
            installer_path = temp_dir / installer_name
            
            # Download with progress
            response = requests.get(download_url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(installer_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if progress_callback and total_size > 0:
                            percent = int((downloaded / total_size) * 100)
                            progress_callback(percent)
            
            logger.info(f"Update downloaded to: {installer_path}")
            return installer_path
            
        except Exception as e:
            logger.error(f"Error downloading update: {e}", exc_info=True)
            return None
    
    def install_update(self, installer_path: Path) -> bool:
        """
        Launch installer and exit current application
        
        Args:
            installer_path: Path to downloaded installer
            
        Returns:
            True if installer launched successfully
        """
        try:
            logger.info(f"Launching installer: {installer_path}")
            
            # Launch installer with silent update flag
            subprocess.Popen([
                str(installer_path),
                '/SILENT',  # Silent install
                '/SUPPRESSMSGBOXES',  # Suppress message boxes
                '/CLOSEAPPLICATIONS',  # Close running applications
                '/RESTARTAPPLICATIONS'  # Restart after install
            ], creationflags=subprocess.DETACHED_PROCESS)
            
            logger.info("Installer launched, exiting application...")
            return True
            
        except Exception as e:
            logger.error(f"Error launching installer: {e}", exc_info=True)
            return False

class UpdateChecker:
    """Background update checker"""
    
    def __init__(self, update_callback=None):
        self.version_manager = VersionManager()
        self.update_callback = update_callback  # Called when update is available
        self._check_thread = None
        
    def check_for_updates_async(self):
        """Check for updates in background thread"""
        if self._check_thread and self._check_thread.is_alive():
            return  # Already checking
            
        self._check_thread = threading.Thread(target=self._background_check, daemon=True)
        self._check_thread.start()
        
    def _background_check(self):
        """Background update check"""
        try:
            if not self.version_manager.should_check_for_updates():
                return
                
            update_info = self.version_manager.check_for_updates()
            if update_info and self.update_callback:
                self.update_callback(update_info)
                
        except requests.exceptions.RequestException as e:
            # Don't trigger callback for expected errors (like no releases)
            if "no releases" in str(e).lower() or "development version" in str(e).lower():
                logger.info("Background update check: No releases available yet")
            else:
                logger.warning(f"Background update check failed: {e}")
        except Exception as e:
            logger.error(f"Error in background update check: {e}", exc_info=True)

def get_version_info() -> Dict:
    """Get comprehensive version information"""
    manager = VersionManager()
    
    return {
        'version': manager.current_version,
        'python_version': sys.version,
        'platform': sys.platform,
        'frozen': getattr(sys, 'frozen', False),
        'executable': sys.executable
    } 