import unittest
from typing import Optional
from sqlmodel import SQLModel, Field
from backend.core.backup_manager import BackupManager
import tempfile
import os
import json
import zipfile
from unittest.mock import patch, MagicMock

class MockModel(SQLModel):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    age: int

class TestBackupCompatibility(unittest.TestCase):
    def test_adapt_record_removes_extra_fields(self):
        # Data with extra field "extra"
        data = {
            "id": 1,
            "name": "Test",
            "age": 30,
            "extra": "should be removed"
        }
        
        adapted = BackupManager._adapt_record(MockModel, data)
        
        self.assertIn("id", adapted)
        self.assertIn("name", adapted)
        self.assertIn("age", adapted)
        self.assertNotIn("extra", adapted)
        self.assertEqual(adapted["name"], "Test")

    def test_adapt_record_keeps_existing_fields(self):
        data = {
            "id": 1,
            "name": "Test"
        }
        # Missing age is fine for adaptation (validation happens at instantiation if required)
        adapted = BackupManager._adapt_record(MockModel, data)
        
        self.assertIn("id", adapted)
        self.assertIn("name", adapted)
        self.assertNotIn("age", adapted)

class TestBackupCreation(unittest.IsolatedAsyncioTestCase):
    async def test_create_backup_adds_info(self):
        with patch('backend.core.backup_manager.MODELS', []), \
             patch('backend.core.backup_manager.PathManager') as MockPathManager, \
             patch('backend.core.backup_manager.ensure_ffmpeg_in_path'):
            
            # Setup mock paths
            temp_dir = tempfile.TemporaryDirectory()
            mock_pm = MockPathManager.return_value
            mock_pm.recordings_directory = MagicMock()
            mock_pm.recordings_directory.exists.return_value = False
            mock_pm.config_path = MagicMock()
            mock_pm.config_path.exists.return_value = False
            
            with patch.object(BackupManager, '_get_app_version', return_value="1.2.3"):
                zip_path = await BackupManager.create_backup()
                
                try:
                    with zipfile.ZipFile(zip_path, 'r') as zipf:
                        self.assertIn("backup_info.json", zipf.namelist())
                        info = json.loads(zipf.read("backup_info.json"))
                        self.assertEqual(info["version"], "1.2.3")
                        self.assertIn("timestamp", info)
                finally:
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
                    temp_dir.cleanup()

if __name__ == '__main__':
    unittest.main()
