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
import re
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, Callable
from packaging import version
from pathlib import Path

from .config_manager import config_manager, get_project_root
from .path_manager import path_manager

logger = logging.getLogger(__name__)

# GitHub repository information
GITHUB_REPO = "Valtora/Nojoin"
GITHUB_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"

class UpdatePreference:
    """Enumeration of update reminder preferences."""
    NEVER = "never"
    NEXT_RUN = "next_run"
    ONE_WEEK = "one_week"
    ONE_MONTH = "one_month"

class VersionManager:
    """Manages version checking, update preferences, and update process."""
    
    def __init__(self):
        self.project_root = Path(get_project_root())
        # Also have access to both deployment modes
        self.path_manager = path_manager
        self._current_version = None
        
    def get_current_version(self) -> str:
        """Get the current version of Nojoin by extracting from latest commit."""
        if self._current_version is not None:
            return self._current_version
            
        try:
            # Try to get version from git if we're in a git repository
            git_version = self._get_version_from_git()
            if git_version:
                self._current_version = git_version
                logger.debug(f"Version from git: {git_version}")
                return git_version
        except Exception as e:
            logger.debug(f"Could not read version from git: {e}")
        
        # Final fallback if git fails
        fallback_version = "Unknown"  # Default version
        logger.warning(f"Could not determine version from git, using fallback: {fallback_version}")
        self._current_version = fallback_version
        return fallback_version
    
    def _get_version_from_git(self) -> Optional[str]:
        """
        Extract version using multi-tier approach:
        1. Git tags (most reliable)
        2. Enhanced commit message parsing (fallback)
        """
        try:
            # Tier 1: Try to get version from git tags
            tag_version = self._get_version_from_git_tags()
            if tag_version:
                logger.debug(f"Version from git tag: {tag_version}")
                return tag_version
            
            # Tier 2: Fallback to enhanced commit message parsing
            commit_version = self._get_version_from_commit_messages()
            if commit_version:
                logger.debug(f"Version from commit message: {commit_version}")
                return commit_version
            
            logger.debug("No version found in git tags or commit messages")
            return None
                
        except Exception as e:
            logger.debug(f"Unexpected error getting git version: {e}")
            return None
    
    def _get_version_from_git_tags(self) -> Optional[str]:
        """Get version from git tags - most reliable method."""
        try:
            # Try to get exact tag for current commit
            result = subprocess.run([
                'git', 'describe', '--tags', '--exact-match', 'HEAD'
            ], capture_output=True, text=True, cwd=self.project_root, timeout=5)
            
            if result.returncode == 0:
                tag_name = result.stdout.strip()
                # Remove 'v' prefix if present
                version = tag_name.lstrip('v')
                logger.debug(f"Found exact tag for HEAD: {tag_name} -> {version}")
                return version
            
            # If no exact tag, get the latest tag with distance info for context
            result = subprocess.run([
                'git', 'describe', '--tags', '--abbrev=0'
            ], capture_output=True, text=True, cwd=self.project_root, timeout=5)
            
            if result.returncode == 0:
                latest_tag = result.stdout.strip()
                # Check if we're ahead of the latest tag
                result_distance = subprocess.run([
                    'git', 'rev-list', '--count', f'{latest_tag}..HEAD'
                ], capture_output=True, text=True, cwd=self.project_root, timeout=5)
                
                if result_distance.returncode == 0:
                    distance = int(result_distance.stdout.strip())
                    base_version = latest_tag.lstrip('v')
                    
                    if distance == 0:
                        # We're exactly on the tagged commit
                        logger.debug(f"On tagged commit: {latest_tag} -> {base_version}")
                        return base_version
                    else:
                        # We're ahead of the tag - this is development version
                        # Get short commit hash for unique identification
                        result_hash = subprocess.run([
                            'git', 'rev-parse', '--short', 'HEAD'
                        ], capture_output=True, text=True, cwd=self.project_root, timeout=5)
                        
                        if result_hash.returncode == 0:
                            short_hash = result_hash.stdout.strip()
                            dev_version = f"{base_version}-dev.{distance}+{short_hash}"
                            logger.debug(f"Development version: {dev_version} ({distance} commits ahead of {latest_tag})")
                            return dev_version
            
            return None
                
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError) as e:
            logger.debug(f"Git tag command error: {e}")
            return None
        except Exception as e:
            logger.debug(f"Unexpected error getting version from git tags: {e}")
            return None
    
    def _get_version_from_commit_messages(self) -> Optional[str]:
        """Fallback: Extract version from recent commit messages with enhanced regex."""
        try:
            # Get last 10 commit messages to scan for version info
            result = subprocess.run([
                'git', 'log', '-10', '--pretty=format:%s'
            ], capture_output=True, text=True, cwd=self.project_root, timeout=5)
            
            if result.returncode == 0:
                commit_messages = result.stdout.strip().split('\n')
                
                # Enhanced regex to support 4+ digit versioning
                # Matches: v0.7.0.1, 0.7.0.1, v1.2.3.4.5, 1.2.3-beta, etc.
                version_pattern = r'v?(\d+(?:\.\d+)*(?:-[a-zA-Z0-9.-]+)?)'
                
                for i, commit_message in enumerate(commit_messages):
                    logger.debug(f"Scanning commit {i+1}: {commit_message}")
                    match = re.search(version_pattern, commit_message)
                    
                    if match:
                        extracted_version = match.group(1)
                        # Validate it looks like a version (has at least one dot)
                        if '.' in extracted_version:
                            logger.debug(f"Found version in commit {i+1}: {extracted_version}")
                            return extracted_version
                
                logger.debug("No version pattern found in recent commit messages")
                return None
            else:
                logger.debug("Git log command failed")
                return None
                
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError) as e:
            logger.debug(f"Git commit message command error: {e}")
            return None
        except Exception as e:
            logger.debug(f"Unexpected error getting version from commit messages: {e}")
            return None
    
    def check_for_updates(self, timeout: int = 10, use_releases: bool = True) -> Tuple[bool, Optional[Dict]]:
        """
        Check for available updates from GitHub releases or main branch.
        
        Args:
            timeout: Request timeout in seconds
            use_releases: If True, check releases API; if False, check main branch and releases (hybrid)
            
        Returns:
            Tuple of (has_update, release_info)
        """
        current_version = self.get_current_version()
        logger.debug(f"Current version: {current_version}")
        
        if use_releases:
            # Stable channel: Only check releases
            logger.info("Checking for updates from GitHub releases...")
            return self._check_releases_only(timeout, current_version)
        else:
            # Development channel: Check both main branch AND releases, show whichever is newer
            logger.info("Checking for updates from main branch and releases (hybrid mode)...")
            return self._check_hybrid_updates(timeout, current_version)
    
    def _check_releases_only(self, timeout: int, current_version: str) -> Tuple[bool, Optional[Dict]]:
        """Check only GitHub releases for updates."""
        try:
            response = requests.get(GITHUB_RELEASES_URL, timeout=timeout)
            response.raise_for_status()
            
            releases = response.json()
            if not releases:
                logger.warning("No releases found on GitHub")
                return False, None
            
            # Find the latest release (including pre-releases) by version comparison
            latest_release = None
            latest_version = None
            
            for release in releases:
                release_version = release.get("tag_name", "").lstrip('v')
                if not release_version:
                    continue
                    
                logger.debug(f"Found release: {release_version}")
                
                # Skip drafts
                if release.get("draft", False):
                    logger.debug(f"Skipping draft release: {release_version}")
                    continue
                
                try:
                    # Compare versions using semantic versioning
                    if latest_version is None or version.parse(release_version) > version.parse(latest_version):
                        latest_version = release_version
                        latest_release = release
                        logger.debug(f"New latest version candidate: {release_version}")
                except Exception as e:
                    logger.warning(f"Error parsing version {release_version}: {e}")
                    continue
            
            if not latest_release or not latest_version:
                logger.warning("Could not find any valid releases")
                return False, None
            
            logger.info(f"Latest release found: {latest_version}")
            
            # Compare with current version
            return self._compare_and_build_update_info(current_version, latest_version, latest_release, is_release=True)
                
        except requests.RequestException as e:
            logger.error(f"Network error checking for updates: {e}")
            return False, None
        except Exception as e:
            logger.error(f"Error checking for updates: {e}")
            return False, None
    
    def _check_hybrid_updates(self, timeout: int, current_version: str) -> Tuple[bool, Optional[Dict]]:
        """Check both releases and main branch, return whichever is newer."""
        try:
            # Check releases first
            logger.info("Checking releases for hybrid mode...")
            has_release_update, release_info = self._check_releases_only(timeout, current_version)
            logger.info(f"Release check result: has_update={has_release_update}, version={release_info['version'] if release_info else 'None'}")
            
            # Check main branch
            logger.info("Checking main branch for hybrid mode...")
            has_branch_update, branch_info = self._check_main_branch_update(timeout)
            logger.info(f"Branch check result: has_update={has_branch_update}, version={branch_info['version'] if branch_info else 'None'}")
            
            # Determine which update to show
            if not has_release_update and not has_branch_update:
                logger.info("No updates available from releases or main branch")
                return False, None
            
            if has_release_update and not has_branch_update:
                logger.info(f"Update available from releases only: {release_info['version']}")
                return True, release_info
            
            if has_branch_update and not has_release_update:
                logger.info(f"Update available from main branch only: {branch_info['version']}")
                return True, branch_info
            
            # Both have updates - compare versions to show newer one
            if has_release_update and has_branch_update:
                release_version = release_info['version']
                branch_version = branch_info['version']
                
                try:
                    # If branch version is just a commit SHA, prefer the release
                    if branch_version.startswith('main-'):
                        logger.info(f"Both updates available, preferring release: {release_version} over main branch")
                        return True, release_info
                    
                    # Compare semantic versions
                    if version.parse(release_version) >= version.parse(branch_version):
                        logger.info(f"Release version {release_version} is newer than or equal to branch version {branch_version}")
                        return True, release_info
                    else:
                        logger.info(f"Branch version {branch_version} is newer than release version {release_version}")
                        return True, branch_info
                        
                except Exception as e:
                    logger.warning(f"Error comparing versions, preferring release: {e}")
                    return True, release_info
            
            return False, None
            
        except Exception as e:
            logger.error(f"Error in hybrid update check: {e}")
            return False, None
    
    def _compare_and_build_update_info(self, current_version: str, latest_version: str, latest_release: Dict, is_release: bool = True) -> Tuple[bool, Optional[Dict]]:
        """Compare versions and build update info if update is available."""
        try:
            if version.parse(latest_version) > version.parse(current_version):
                # Use zipball download URL for source archive
                download_url = f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{latest_release.get('tag_name', latest_version)}.zip"
                
                logger.info(f"Update available: {latest_version} (current: {current_version})")
                release_type = "pre-release" if latest_release.get("prerelease", False) else "release"
                logger.info(f"Release type: {release_type}")
                
                return True, {
                    'version': latest_version,
                    'name': latest_release.get('name', f'Version {latest_version}'),
                    'body': latest_release.get('body', ''),
                    'published_at': latest_release.get('published_at'),
                    'download_url': download_url,
                    'tag_name': latest_release.get('tag_name', latest_version),
                    'prerelease': latest_release.get('prerelease', False)
                }
            else:
                logger.info("Current version is up to date")
                return False, None
                
        except Exception as e:
            logger.warning(f"Error comparing versions: {e}")
            # Fallback to string comparison if semantic versioning fails
            if latest_version != current_version:
                logger.info("Using fallback version comparison")
                download_url = f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{latest_release.get('tag_name', latest_version)}.zip"
                return True, {
                    'version': latest_version,
                    'name': latest_release.get('name', f'Version {latest_version}'),
                    'body': latest_release.get('body', ''),
                    'published_at': latest_release.get('published_at'),
                    'download_url': download_url,
                    'tag_name': latest_release.get('tag_name', latest_version),
                    'prerelease': latest_release.get('prerelease', False)
                }
            else:
                return False, None
    
    def get_update_preferences(self) -> Dict:
        """Get current update preferences from config."""
        return config_manager.get("update_preferences", {
            "check_on_startup": True,
            "last_check": None,
            "last_reminded": None,
            "reminder_preference": UpdatePreference.ONE_WEEK,
            "skip_version": None,
            "update_channel": "stable"  # "stable" for releases, "development" for main branch
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
                # For development channel, check more frequently (every 6 hours)
                # For stable channel, check once per day
                update_channel = prefs.get("update_channel", "stable")
                check_interval = timedelta(hours=6) if update_channel == "development" else timedelta(days=1)
                if datetime.now() - last_check_date < check_interval:
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
        Download the update source archive.
        
        Args:
            release_info: Release information dictionary
            progress_callback: Optional callback for progress updates
            
        Returns:
            Path to downloaded archive or None if failed
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
            archive_name = f"Nojoin-{release_info['version']}-source.zip"
            temp_path = os.path.join(temp_dir, archive_name)
            
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
            
            logger.info(f"Source archive downloaded to: {temp_path}")
            return temp_path
            
        except Exception as e:
            logger.error(f"Error downloading source archive: {e}")
            if progress_callback:
                progress_callback(-1, f"Download failed: {str(e)}")
            return None
    
    def create_update_script(self, archive_path: str, release_info: Dict) -> Optional[str]:
        """
        Create an update script that will be executed from temp directory.
        
        Args:
            archive_path: Path to the downloaded source archive
            release_info: Release information
            
        Returns:
            Path to the created update script or None if failed
        """
        try:
            # Get project root and important paths using path_manager
            project_root = self.project_root
            venv_path = project_root / ".venv"
            config_path = self.path_manager.config_path
            db_path = self.path_manager.database_path
            user_data_dir = self.path_manager.user_data_directory
            deployment_mode = self.path_manager.deployment_mode
            
            # Create update script content
            script_content = f'''#!/usr/bin/env python3
"""
Nojoin Update Script
Automatically generated by VersionManager
"""

import os
import sys
import shutil
import zipfile
import subprocess
import time
import logging
from pathlib import Path

# Configure logging
log_file = os.path.join(os.path.dirname(__file__), "nojoin_update.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def main():
    """Main update process."""
    logger.info("Starting Nojoin update process...")
    
    # Paths
    archive_path = r"{archive_path}"
    project_root = Path(r"{project_root}")
    venv_path = project_root / ".venv"
    config_path = Path(r"{config_path}")
    db_path = Path(r"{db_path}")
    user_data_dir = Path(r"{user_data_dir}")
    
    # Wait for main app to close
    logger.info("Waiting for main application to close...")
    time.sleep(3)
    
    try:
        # Extract archive to temp location
        logger.info("Extracting source archive...")
        temp_extract_dir = Path(os.path.dirname(__file__)) / "nojoin_extract"
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)
        temp_extract_dir.mkdir()
        
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            zip_ref.extractall(temp_extract_dir)
        
        # Find the extracted folder (usually has format repo-tag)
        extracted_folders = [d for d in temp_extract_dir.iterdir() if d.is_dir()]
        if not extracted_folders:
            raise Exception("No folders found in extracted archive")
        
        source_dir = extracted_folders[0]  # Take the first (should be only) folder
        logger.info(f"Source directory: {{source_dir}}")
        
        # Backup important files
        logger.info("Backing up important files...")
        backup_dir = Path(os.path.dirname(__file__)) / "nojoin_backup"
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        backup_dir.mkdir()
        
        # Backup config and database if they exist
        if config_path.exists():
            shutil.copy2(config_path, backup_dir / "config.json")
            logger.info("Config backed up")
        
        if db_path.exists():
            shutil.copy2(db_path, backup_dir / "nojoin_data.db")
            logger.info("Database backed up")
        
        # Note: .venv backup not needed since it's in .gitignore and won't be in source archive
        # and it's preserved by being in preserve_items list
        
        # Update project files (excluding .venv)
        logger.info("Updating project files...")
        
        # List of items to preserve during update - depends on deployment mode
        # In development mode: preserve .venv, .git, and user data is in nojoin/ subdir
        # In production mode: preserve .venv, .git, user data is separate in Documents
        preserve_items = {{".venv", ".git"}}
        
        # Add deployment-specific preservations
        deployment_mode = "{deployment_mode}"
        if deployment_mode == "development":
            # In development mode, user data directory might be in project root
            user_data_relative = user_data_dir.relative_to(project_root) if user_data_dir.is_relative_to(project_root) else None
            if user_data_relative and str(user_data_relative) != ".":
                preserve_items.add(str(user_data_relative).split("/")[0])  # Preserve top-level directory
        
        # Remove old files except preserved ones
        for item in project_root.iterdir():
            if item.name not in preserve_items:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                logger.info(f"Removed: {{item.name}}")
        
        # Copy new files from source
        for item in source_dir.iterdir():
            if item.name not in preserve_items:
                dest_path = project_root / item.name
                if item.is_dir():
                    shutil.copytree(item, dest_path)
                else:
                    shutil.copy2(item, dest_path)
                logger.info(f"Updated: {{item.name}}")
        
        # Restore backed up files
        logger.info("Restoring important files...")
        # Ensure user data directory exists
        user_data_dir.mkdir(parents=True, exist_ok=True)
        
        if (backup_dir / "config.json").exists():
            shutil.copy2(backup_dir / "config.json", config_path)
        
        if (backup_dir / "nojoin_data.db").exists():
            shutil.copy2(backup_dir / "nojoin_data.db", db_path)
        
        # Update dependencies
        logger.info("Updating dependencies...")
        if venv_path.exists():
            # Use the preserved virtual environment
            if sys.platform == "win32":
                pip_path = venv_path / "Scripts" / "pip.exe"
                python_path = venv_path / "Scripts" / "python.exe"
            else:
                pip_path = venv_path / "bin" / "pip"
                python_path = venv_path / "bin" / "python"
            
            requirements_path = project_root / "requirements.txt"
            if requirements_path.exists() and pip_path.exists():
                logger.info("Running pip install...")
                result = subprocess.run([
                    str(pip_path), 
                    "install", 
                    "-r", 
                    str(requirements_path)
                ], capture_output=True, text=True, timeout=300)
                
                if result.returncode == 0:
                    logger.info("Dependencies updated successfully")
                else:
                    logger.warning(f"pip install had issues: {{result.stderr}}")
            else:
                logger.warning("Could not find requirements.txt or pip executable")
        else:
            logger.warning("No virtual environment found - skipping dependency update")
        
        # Version is now tracked via git commit messages, no config update needed
        logger.info("Version tracking is handled via git commits")
        
        # Clean up
        logger.info("Cleaning up temporary files...")
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        if os.path.exists(archive_path):
            os.remove(archive_path)
        
        logger.info("Update completed successfully!")
        
        # Restart application
        logger.info("Restarting Nojoin...")
        
        # Define python_path for restart (may not be defined if dependency update was skipped)
        if venv_path.exists():
            if sys.platform == "win32":
                python_path = venv_path / "Scripts" / "python.exe"
            else:
                python_path = venv_path / "bin" / "python"
        else:
            python_path = None
        
        if sys.platform == "win32":
            if (project_root / "Nojoin.py").exists():
                if python_path and python_path.exists():
                    subprocess.Popen([str(python_path), str(project_root / "Nojoin.py")])
                else:
                    subprocess.Popen([sys.executable, str(project_root / "Nojoin.py")])
            else:
                logger.error("Could not find Nojoin.py to restart")
        else:
            # For non-Windows systems
            if (project_root / "Nojoin.py").exists():
                if python_path and python_path.exists():
                    subprocess.Popen([str(python_path), str(project_root / "Nojoin.py")])
                else:
                    subprocess.Popen([sys.executable, str(project_root / "Nojoin.py")])
        
        return True
        
    except Exception as e:
        logger.error(f"Update failed: {{e}}")
        # Try to restore from backup if available
        try:
            if backup_dir.exists():
                logger.info("Attempting to restore from backup...")
                # Ensure user data directory exists for restoration
                user_data_dir.mkdir(parents=True, exist_ok=True)
                
                if (backup_dir / "config.json").exists():
                    shutil.copy2(backup_dir / "config.json", config_path)
                if (backup_dir / "nojoin_data.db").exists():
                    shutil.copy2(backup_dir / "nojoin_data.db", db_path)
                logger.info("Backup restored")
        except Exception as restore_error:
            logger.error(f"Failed to restore backup: {{restore_error}}")
        
        return False

if __name__ == "__main__":
    success = main()
    if success:
        logger.info("Update script completed successfully")
    else:
        logger.error("Update script failed")
        input("Press Enter to continue...")  # Keep window open on error
'''
            
            # Write script to temp directory
            temp_dir = tempfile.gettempdir()
            script_path = os.path.join(temp_dir, "nojoin_update.py")
            
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(script_content)
            
            logger.info(f"Update script created at: {script_path}")
            return script_path
            
        except Exception as e:
            logger.error(f"Failed to create update script: {e}")
            return None
    
    def _find_system_python(self) -> str:
        """
        Find system Python executable (not from virtual environment).
        
        Returns:
            Path to system Python executable
        """
        try:
            # Try to find system Python by checking common locations
            system_python_candidates = []
            
            if sys.platform == "win32":
                # Windows locations
                system_python_candidates = [
                    "python.exe",  # If Python is in PATH
                    "py.exe",      # Python Launcher
                    r"C:\Python39\python.exe",
                    r"C:\Python310\python.exe", 
                    r"C:\Python311\python.exe",
                    r"C:\Python312\python.exe",
                    r"C:\Program Files\Python39\python.exe",
                    r"C:\Program Files\Python310\python.exe",
                    r"C:\Program Files\Python311\python.exe",
                    r"C:\Program Files\Python312\python.exe",
                ]
            else:
                # Unix-like systems
                system_python_candidates = [
                    "python3",     # Most common
                    "python",      # Fallback
                    "/usr/bin/python3",
                    "/usr/bin/python",
                    "/usr/local/bin/python3",
                    "/usr/local/bin/python",
                ]
            
            # Test each candidate
            for candidate in system_python_candidates:
                try:
                    # Test if this Python works and is not in a venv
                    result = subprocess.run([
                        candidate, "-c", 
                        "import sys; print('OK' if not hasattr(sys, 'real_prefix') and not (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix) else 'VENV')"
                    ], capture_output=True, text=True, timeout=5)
                    
                    if result.returncode == 0 and result.stdout.strip() == "OK":
                        logger.info(f"Found system Python: {candidate}")
                        return candidate
                        
                except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
                    continue
            
            # If we can't find system Python, fall back to current executable
            # but log a warning
            logger.warning("Could not find system Python, using current executable (may cause issues)")
            return sys.executable
            
        except Exception as e:
            logger.warning(f"Error finding system Python: {e}, using current executable")
            return sys.executable
    
    def _check_main_branch_update(self, timeout: int = 10) -> Tuple[bool, Optional[Dict]]:
        """
        Check for updates from the main branch.
        
        Args:
            timeout: Request timeout in seconds
            
        Returns:
            Tuple of (has_update, release_info)
        """
        try:
            # Get the latest commit from main branch
            commits_url = f"https://api.github.com/repos/{GITHUB_REPO}/commits/main"
            response = requests.get(commits_url, timeout=timeout)
            response.raise_for_status()
            
            commit_data = response.json()
            latest_commit_sha = commit_data['sha'][:7]  # Short SHA
            commit_date = commit_data['commit']['committer']['date']
            commit_message = commit_data['commit']['message'].split('\n')[0]  # First line
            
            current_version = self.get_current_version()
            logger.debug(f"Main branch check: current={current_version}, latest_commit={latest_commit_sha}")
            logger.debug(f"Latest commit message: {commit_message}")
            
            # Extract version from commit message if available
            version_pattern = r'v?(\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?)'
            match = re.search(version_pattern, commit_message)
            
            if match:
                extracted_version = match.group(1)
                logger.debug(f"Extracted version from commit message: {extracted_version}")
                # Compare with current version
                try:
                    if version.parse(extracted_version) <= version.parse(current_version):
                        logger.info(f"Main branch version {extracted_version} is not newer than current {current_version}")
                        return False, None  # No update needed
                except:
                    logger.warning(f"Could not compare versions, assuming update available")
                    pass  # Fall through to assume update available
                    
                version_display = extracted_version
            else:
                # No version in commit message, use commit SHA
                logger.debug(f"No version found in commit message, using commit SHA")
                version_display = f"main-{latest_commit_sha}"
                
                # For SHA-based versions, only show update if commit is newer than current
                # This prevents showing updates when on a tagged version
                try:
                    if not current_version.startswith('main-') and 'dev' not in current_version:
                        logger.info(f"Current version {current_version} is a release, not showing main branch commit as update")
                        return False, None
                except:
                    pass
            
            download_url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"
            
            return True, {
                'version': version_display,
                'name': f'Latest Development Version ({version_display})',
                'body': f'Latest commit: {commit_message}\n\nNote: This is the development version from the main branch.',
                'published_at': commit_date,
                'download_url': download_url,
                'tag_name': 'main',
                'prerelease': True,  # Mark main branch as pre-release
                'commit_sha': latest_commit_sha,
                'commit_message': commit_message
            }
            
        except requests.RequestException as e:
            logger.error(f"Network error checking main branch: {e}")
            return False, None
        except Exception as e:
            logger.error(f"Error checking main branch: {e}")
            return False, None
    
    def execute_update(self, archive_path: str, release_info: Dict) -> bool:
        """
        Execute the update process using a separate update script.
        
        Args:
            archive_path: Path to the downloaded source archive
            release_info: Release information
            
        Returns:
            True if update script started successfully, False otherwise
        """
        try:
            logger.info("Preparing update script...")
            
            # Create the update script
            script_path = self.create_update_script(archive_path, release_info)
            if not script_path:
                logger.error("Failed to create update script")
                return False
            
            # Verify archive exists
            if not os.path.exists(archive_path):
                logger.error(f"Source archive not found: {archive_path}")
                return False
            
            logger.info("Starting update script...")
            
            # Find system Python to avoid using venv Python for the update
            system_python = self._find_system_python()
            
            # Start the update script in a separate process using system Python
            if sys.platform == "win32":
                subprocess.Popen([
                    system_python, 
                    script_path
                ], 
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True)
            else:
                # For non-Windows systems
                subprocess.Popen([
                    system_python, 
                    script_path
                ], 
                start_new_session=True)
            
            logger.info("Update script started successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start update script: {e}")
            return False
    
    def mark_successful_update(self, version: str):
        """Mark that an update was successful."""
        try:
            # Clear any skipped version since we successfully updated
            prefs = self.get_update_preferences()
            prefs["skip_version"] = None
            prefs["last_check"] = datetime.now().isoformat()
            self.set_update_preferences(prefs)
            
            # Reset cached version so it gets re-read from git
            self._current_version = None
            
            logger.info(f"Successfully updated to version {version}")
        except Exception as e:
            logger.warning(f"Could not mark successful update: {e}")


# Global instance
version_manager = VersionManager() 