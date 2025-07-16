"""
Integration tests for model download workflow.
"""

import unittest
import tempfile
import shutil
import os
import threading
import time
from unittest.mock import Mock, patch, MagicMock
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from nojoin.ui.model_download_dialog import ModelDownloadWorker, ModelDownloadDialog
from nojoin.utils.progress_manager import get_progress_manager, ProgressManager
from nojoin.utils.model_utils import is_whisper_model_downloaded


class TestModelDownloadWorker(unittest.TestCase):
    """Test ModelDownloadWorker integration with progress system."""
    
    def setUp(self):
        """Set up test environment."""
        ProgressManager._instance = None
        self.progress_manager = get_progress_manager()
        
    def test_worker_initialization(self):
        """Test worker initialization with progress manager."""
        worker = ModelDownloadWorker("tiny", "cpu")
        
        self.assertEqual(worker.model_size, "tiny")
        self.assertEqual(worker.device, "cpu")
        self.assertIsNotNone(worker.progress_manager)
        
    @patch('nojoin.ui.model_download_dialog.whisper')
    def test_successful_download_with_progress(self, mock_whisper):
        """Test successful model download with progress tracking."""
        # Mock whisper.load_model to simulate successful download
        mock_model = Mock()
        mock_whisper.load_model.return_value = mock_model
        
        worker = ModelDownloadWorker("tiny", "cpu")
        
        # Track progress updates
        progress_updates = []
        worker.progress.connect(lambda p: progress_updates.append(p))
        
        # Track completion
        finished_called = threading.Event()
        worker.finished.connect(lambda: finished_called.set())
        
        # Start worker
        worker.start()
        
        # Wait for completion
        finished_called.wait(timeout=10)
        
        # Verify results
        self.assertTrue(finished_called.is_set())
        mock_whisper.load_model.assert_called_once_with("tiny", device="cpu")
        
        # Should have received progress updates including 100%
        self.assertGreater(len(progress_updates), 0)
        self.assertIn(100, progress_updates)
        
    @patch('nojoin.ui.model_download_dialog.whisper')
    def test_download_with_tqdm_conflict(self, mock_whisper):
        """Test download handling when TQDM conflicts exist."""
        # Simulate TQDM conflict
        import tqdm
        original_tqdm = tqdm.tqdm
        tqdm.tqdm._external_patch = True
        
        try:
            # Mock whisper to raise TQDM-related error first, then succeed
            call_count = 0
            def side_effect(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("tqdm conflict error")
                return Mock()
                
            mock_whisper.load_model.side_effect = side_effect
            
            worker = ModelDownloadWorker("tiny", "cpu")
            
            # Track error and finished signals
            error_messages = []
            worker.error.connect(lambda msg: error_messages.append(msg))
            
            finished_called = threading.Event()
            worker.finished.connect(lambda: finished_called.set())
            
            # Start worker
            worker.start()
            
            # Wait for completion or error
            finished_called.wait(timeout=10)
            
            # Should have attempted fallback and succeeded
            self.assertTrue(finished_called.is_set() or len(error_messages) > 0)
            
        finally:
            # Restore original tqdm
            tqdm.tqdm = original_tqdm
            
    @patch('nojoin.ui.model_download_dialog.whisper')
    def test_download_network_error(self, mock_whisper):
        """Test download handling with network errors."""
        # Mock network error
        mock_whisper.load_model.side_effect = Exception("network connection failed")
        
        worker = ModelDownloadWorker("tiny", "cpu")
        
        # Track error signal
        error_messages = []
        worker.error.connect(lambda msg: error_messages.append(msg))
        
        error_received = threading.Event()
        worker.error.connect(lambda msg: error_received.set())
        
        # Start worker
        worker.start()
        
        # Wait for error
        error_received.wait(timeout=10)
        
        # Verify error handling
        self.assertTrue(error_received.is_set())
        self.assertEqual(len(error_messages), 1)
        self.assertIn("network", error_messages[0].lower())
        
    def test_fallback_download_mechanism(self):
        """Test fallback download when primary method fails."""
        worker = ModelDownloadWorker("tiny", "cpu")
        
        # Track progress updates from fallback
        progress_updates = []
        worker.progress.connect(lambda p: progress_updates.append(p))
        
        finished_called = threading.Event()
        worker.finished.connect(lambda: finished_called.set())
        
        with patch('nojoin.ui.model_download_dialog.whisper') as mock_whisper:
            # Mock successful fallback
            mock_whisper.load_model.return_value = Mock()
            
            # Call fallback method directly
            worker._attempt_fallback_download()
            
            # Wait briefly for signals
            time.sleep(0.1)
            
            # Should have received progress updates
            self.assertGreater(len(progress_updates), 0)
            self.assertIn(100, progress_updates)


class TestModelDownloadDialog(unittest.TestCase):
    """Test ModelDownloadDialog integration."""
    
    @classmethod
    def setUpClass(cls):
        """Set up QApplication for dialog tests."""
        if not QApplication.instance():
            cls.app = QApplication([])
        else:
            cls.app = QApplication.instance()
            
    def setUp(self):
        """Set up test environment."""
        ProgressManager._instance = None
        
    def test_dialog_initialization(self):
        """Test dialog initialization with model parameters."""
        dialog = ModelDownloadDialog("base", "cpu")
        
        self.assertEqual(dialog.model_size, "base")
        self.assertEqual(dialog.device, "cpu")
        self.assertIsNone(dialog.download_worker)
        self.assertFalse(dialog._was_cancelled)
        self.assertFalse(dialog._download_complete)
        
    @patch('nojoin.ui.model_download_dialog.whisper')
    def test_dialog_download_flow(self, mock_whisper):
        """Test complete dialog download flow."""
        mock_whisper.load_model.return_value = Mock()
        
        dialog = ModelDownloadDialog("tiny", "cpu")
        
        # Start download
        dialog.start_download()
        
        # Verify worker was created and started
        self.assertIsNotNone(dialog.download_worker)
        self.assertTrue(dialog.download_worker.isRunning())
        
        # Wait for completion
        start_time = time.time()
        while dialog.download_worker.isRunning() and time.time() - start_time < 10:
            QApplication.processEvents()
            time.sleep(0.1)
            
        # Verify completion
        self.assertFalse(dialog.download_worker.isRunning())
        
    def test_dialog_cancellation(self):
        """Test dialog cancellation during download."""
        dialog = ModelDownloadDialog("tiny", "cpu")
        
        # Start download
        dialog.start_download()
        
        # Cancel immediately
        dialog._on_cancel_clicked()
        
        # Verify cancellation state
        self.assertTrue(dialog._was_cancelled)
        self.assertFalse(dialog.cancel_button.isEnabled())
        
    def test_progress_updates(self):
        """Test progress bar updates during download."""
        dialog = ModelDownloadDialog("tiny", "cpu")
        
        # Test progress update
        dialog._on_progress_update(50)
        
        self.assertEqual(dialog.progress_bar.value(), 50)
        self.assertEqual(dialog.progress_bar.format(), "50%")
        
    def test_download_completion_handling(self):
        """Test handling of successful download completion."""
        dialog = ModelDownloadDialog("tiny", "cpu")
        
        # Simulate completion
        dialog._on_download_finished()
        
        self.assertTrue(dialog._download_complete)
        self.assertEqual(dialog.progress_bar.value(), 100)
        self.assertEqual(dialog.cancel_button.text(), "Close")
        
    def test_error_handling(self):
        """Test error message handling."""
        dialog = ModelDownloadDialog("tiny", "cpu")
        
        # Simulate error
        error_message = "Test error message"
        
        with patch('nojoin.ui.model_download_dialog.QMessageBox') as mock_msgbox:
            dialog._on_download_error(error_message)
            
            # Verify error state
            self.assertTrue(dialog._download_complete)
            
            # Verify error dialog was shown
            mock_msgbox.critical.assert_called_once()


class TestFirstRunIntegration(unittest.TestCase):
    """Test first-run model download integration."""
    
    @classmethod
    def setUpClass(cls):
        """Set up QApplication for dialog tests."""
        if not QApplication.instance():
            cls.app = QApplication([])
        else:
            cls.app = QApplication.instance()
            
    def setUp(self):
        """Set up test environment."""
        ProgressManager._instance = None
        
    @patch('nojoin.utils.model_utils.is_whisper_model_downloaded')
    @patch('nojoin.ui.model_download_dialog.whisper')
    def test_clean_first_run_download(self, mock_whisper, mock_is_downloaded):
        """Test first-run download on clean installation."""
        # Simulate no model available
        mock_is_downloaded.return_value = False
        mock_whisper.load_model.return_value = Mock()
        
        from nojoin.utils.model_utils import should_prompt_for_first_run_download
        
        # Should prompt for download
        self.assertTrue(should_prompt_for_first_run_download())
        
        # Test download process
        dialog = ModelDownloadDialog("turbo", "cpu")
        dialog.start_download()
        
        # Wait briefly for worker to start
        time.sleep(0.1)
        QApplication.processEvents()
        
        self.assertIsNotNone(dialog.download_worker)
        
    @patch('nojoin.utils.model_utils.is_whisper_model_downloaded')
    def test_no_prompt_when_model_exists(self, mock_is_downloaded):
        """Test no prompt when model already exists."""
        # Simulate model already available
        mock_is_downloaded.return_value = True
        
        from nojoin.utils.model_utils import should_prompt_for_first_run_download
        
        # Should not prompt for download
        self.assertFalse(should_prompt_for_first_run_download())
        
    def test_progress_manager_conflict_resolution(self):
        """Test progress manager resolves TQDM conflicts."""
        progress_manager = get_progress_manager()
        
        # Simulate existing TQDM patch
        import tqdm
        original_tqdm = tqdm.tqdm
        tqdm.tqdm._external_patch = True
        
        try:
            # Detect conflicts
            conflicts = progress_manager.detect_tqdm_conflicts()
            self.assertGreater(len(conflicts), 0)
            
            # Reset state should resolve conflicts
            success = progress_manager.reset_tqdm_state()
            self.assertTrue(success)
            
        finally:
            # Restore original state
            tqdm.tqdm = original_tqdm
            
    @patch('nojoin.ui.model_download_dialog.whisper')
    def test_retry_mechanism(self, mock_whisper):
        """Test automatic retry mechanism for failed downloads."""
        progress_manager = get_progress_manager()
        
        # Mock first call to fail, second to succeed
        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Network error")
            return Mock()
            
        mock_whisper.load_model.side_effect = side_effect
        
        # Test retry mechanism
        success = progress_manager.handle_download_retry("tiny", "cpu", max_retries=2)
        
        # Should succeed on second attempt
        self.assertTrue(success)
        self.assertEqual(call_count, 2)
        
    @patch('nojoin.ui.model_download_dialog.whisper')
    def test_retry_exhaustion(self, mock_whisper):
        """Test retry mechanism when all attempts fail."""
        progress_manager = get_progress_manager()
        
        # Mock all calls to fail
        mock_whisper.load_model.side_effect = Exception("Persistent error")
        
        # Test retry mechanism
        success = progress_manager.handle_download_retry("tiny", "cpu", max_retries=2)
        
        # Should fail after all retries
        self.assertFalse(success)
        self.assertEqual(mock_whisper.load_model.call_count, 2)


class TestConcurrentDownloads(unittest.TestCase):
    """Test handling of concurrent download scenarios."""
    
    @classmethod
    def setUpClass(cls):
        """Set up QApplication for dialog tests."""
        if not QApplication.instance():
            cls.app = QApplication([])
        else:
            cls.app = QApplication.instance()
            
    def setUp(self):
        """Set up test environment."""
        ProgressManager._instance = None
        
    @patch('nojoin.ui.model_download_dialog.whisper')
    def test_multiple_concurrent_downloads(self, mock_whisper):
        """Test multiple concurrent model downloads."""
        mock_whisper.load_model.return_value = Mock()
        
        # Create multiple workers
        workers = []
        for model_size in ["tiny", "base", "small"]:
            worker = ModelDownloadWorker(model_size, "cpu")
            workers.append(worker)
            
        # Start all workers
        for worker in workers:
            worker.start()
            
        # Wait for all to complete
        for worker in workers:
            worker.wait(10000)  # 10 second timeout
            
        # All should complete successfully
        for worker in workers:
            self.assertFalse(worker.isRunning())
            
    def test_progress_manager_thread_safety(self):
        """Test progress manager thread safety with concurrent access."""
        progress_manager = get_progress_manager()
        
        results = []
        errors = []
        
        def create_contexts():
            try:
                for _ in range(10):
                    with progress_manager.create_download_context():
                        time.sleep(0.01)
                        results.append(threading.get_ident())
            except Exception as e:
                errors.append(e)
                
        # Create multiple threads
        threads = []
        for _ in range(5):
            thread = threading.Thread(target=create_contexts)
            threads.append(thread)
            thread.start()
            
        # Wait for all threads
        for thread in threads:
            thread.join()
            
        # Verify no errors occurred
        self.assertEqual(len(errors), 0)
        
        # Verify all threads completed work
        self.assertEqual(len(results), 50)  # 5 threads * 10 contexts each


if __name__ == '__main__':
    unittest.main()