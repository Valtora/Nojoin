"""
Unit tests for the progress management system.
"""

import unittest
import threading
import time
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from nojoin.utils.progress_manager import (
    ProgressManager, ProgressContext, ContextType, ProgressEvent,
    FallbackProgressReporter, get_progress_manager
)


class TestProgressEvent(unittest.TestCase):
    """Test ProgressEvent creation and properties."""
    
    def test_progress_event_creation(self):
        """Test creating a progress event with calculated percentage."""
        event = ProgressEvent.create(50, 100, "test")
        
        self.assertEqual(event.current, 50)
        self.assertEqual(event.total, 100)
        self.assertEqual(event.percent, 50.0)
        self.assertEqual(event.context, "test")
        self.assertIsInstance(event.timestamp, datetime)
        self.assertIsInstance(event.thread_id, str)
        
    def test_progress_event_zero_total(self):
        """Test progress event with zero total."""
        event = ProgressEvent.create(10, 0, "test")
        
        self.assertEqual(event.percent, 0.0)
        
    def test_progress_event_over_100_percent(self):
        """Test progress event capped at 100%."""
        event = ProgressEvent.create(150, 100, "test")
        
        self.assertEqual(event.percent, 100.0)


class TestProgressManager(unittest.TestCase):
    """Test ProgressManager singleton behavior and functionality."""
    
    def setUp(self):
        """Reset singleton for each test."""
        ProgressManager._instance = None
        
    def test_singleton_behavior(self):
        """Test that ProgressManager is a singleton."""
        manager1 = ProgressManager()
        manager2 = ProgressManager()
        
        self.assertIs(manager1, manager2)
        
    def test_get_progress_manager(self):
        """Test global progress manager getter."""
        manager1 = get_progress_manager()
        manager2 = get_progress_manager()
        
        self.assertIs(manager1, manager2)
        self.assertIsInstance(manager1, ProgressManager)
        
    def test_context_creation(self):
        """Test creating different types of progress contexts."""
        manager = ProgressManager()
        
        download_ctx = manager.create_download_context()
        transcription_ctx = manager.create_transcription_context()
        diarization_ctx = manager.create_diarization_context()
        
        self.assertIsInstance(download_ctx, ProgressContext)
        self.assertEqual(download_ctx.context_type, ContextType.MODEL_DOWNLOAD)
        
        self.assertIsInstance(transcription_ctx, ProgressContext)
        self.assertEqual(transcription_ctx.context_type, ContextType.TRANSCRIPTION)
        
        self.assertIsInstance(diarization_ctx, ProgressContext)
        self.assertEqual(diarization_ctx.context_type, ContextType.DIARIZATION)
        
    def test_context_registration(self):
        """Test context registration and unregistration."""
        manager = ProgressManager()
        
        # Initially no active contexts
        self.assertEqual(len(manager.get_active_contexts()), 0)
        
        # Create and enter context
        with manager.create_download_context() as ctx:
            active_contexts = manager.get_active_contexts()
            self.assertEqual(len(active_contexts), 1)
            self.assertIn(ContextType.MODEL_DOWNLOAD.value, active_contexts)
            
        # Context should be unregistered after exit
        self.assertEqual(len(manager.get_active_contexts()), 0)
        
    def test_multiple_contexts(self):
        """Test multiple concurrent contexts."""
        manager = ProgressManager()
        
        with manager.create_download_context():
            with manager.create_transcription_context():
                active_contexts = manager.get_active_contexts()
                self.assertEqual(len(active_contexts), 2)
                self.assertIn(ContextType.MODEL_DOWNLOAD.value, active_contexts)
                self.assertIn(ContextType.TRANSCRIPTION.value, active_contexts)
                
    @patch('nojoin.utils.progress_manager.tqdm')
    def test_tqdm_conflict_detection(self, mock_tqdm):
        """Test TQDM conflict detection."""
        manager = ProgressManager()
        
        # Mock TQDM with external patch
        mock_tqdm.tqdm.__module__ = 'external_module'
        mock_tqdm.tqdm.__name__ = 'CustomTqdm'
        
        conflicts = manager.detect_tqdm_conflicts()
        self.assertIn("external_tqdm_patch", conflicts)
        
    def test_reset_tqdm_state(self):
        """Test TQDM state reset functionality."""
        manager = ProgressManager()
        
        # Add some active contexts
        with manager.create_download_context():
            self.assertEqual(len(manager.get_active_contexts()), 1)
            
            # Reset state
            success = manager.reset_tqdm_state()
            self.assertTrue(success)
            
        # Contexts should be cleared
        self.assertEqual(len(manager.get_active_contexts()), 0)


class TestProgressContext(unittest.TestCase):
    """Test ProgressContext functionality."""
    
    def setUp(self):
        """Reset singleton for each test."""
        ProgressManager._instance = None
        
    def test_context_manager_protocol(self):
        """Test context manager enter/exit behavior."""
        manager = ProgressManager()
        callback = Mock()
        
        context = ProgressContext(manager, ContextType.MODEL_DOWNLOAD, callback)
        
        # Initially not active
        self.assertFalse(context._active)
        
        with context:
            self.assertTrue(context._active)
            
        # Should be inactive after exit
        self.assertFalse(context._active)
        
    def test_progress_emission(self):
        """Test progress event emission."""
        manager = ProgressManager()
        callback = Mock()
        
        with manager.create_download_context(callback) as context:
            context.emit_progress(50, 100)
            
            # Callback should be called with percentage
            callback.assert_called_with(50)
            
    def test_progress_emission_inactive_context(self):
        """Test that inactive contexts don't emit progress."""
        manager = ProgressManager()
        callback = Mock()
        
        context = manager.create_download_context(callback)
        
        # Emit progress without entering context
        context.emit_progress(50, 100)
        
        # Callback should not be called
        callback.assert_not_called()
        
    def test_callback_error_handling(self):
        """Test error handling in progress callbacks."""
        manager = ProgressManager()
        
        def failing_callback(percent):
            raise Exception("Callback error")
            
        with manager.create_download_context(failing_callback) as context:
            # Should not raise exception
            context.emit_progress(50, 100)


class TestFallbackProgressReporter(unittest.TestCase):
    """Test fallback progress reporting functionality."""
    
    def test_progress_pattern_extraction(self):
        """Test extracting progress from log messages."""
        callback = Mock()
        reporter = FallbackProgressReporter(callback)
        
        # Test percentage pattern
        reporter.report_progress("Download progress: 75%")
        callback.assert_called_with(75)
        
        # Test fraction pattern
        callback.reset_mock()
        reporter.report_progress("Processing 25/50 items")
        callback.assert_called_with(50)
        
        # Test decimal percentage
        callback.reset_mock()
        reporter.report_progress("Progress: 33.5% complete")
        callback.assert_called_with(33)
        
    def test_time_based_estimation(self):
        """Test time-based progress estimation."""
        callback = Mock()
        reporter = FallbackProgressReporter(callback)
        
        # Mock start time to simulate elapsed time
        past_time = datetime.now()
        reporter.start_time = past_time
        
        # Report progress without recognizable pattern
        reporter.report_progress("Some random log message")
        
        # Should have called callback with time-based estimate
        self.assertTrue(callback.called)
        
    def test_completion(self):
        """Test marking progress as complete."""
        callback = Mock()
        reporter = FallbackProgressReporter(callback)
        
        reporter.complete()
        callback.assert_called_with(100)
        
    def test_duplicate_progress_filtering(self):
        """Test that duplicate progress values are filtered."""
        callback = Mock()
        reporter = FallbackProgressReporter(callback)
        
        # Report same progress twice
        reporter.report_progress("Progress: 50%")
        reporter.report_progress("Progress: 50%")
        
        # Callback should only be called once
        self.assertEqual(callback.call_count, 1)


class TestThreadSafety(unittest.TestCase):
    """Test thread safety of progress management system."""
    
    def setUp(self):
        """Reset singleton for each test."""
        ProgressManager._instance = None
        
    def test_concurrent_context_creation(self):
        """Test creating contexts from multiple threads."""
        manager = ProgressManager()
        results = []
        
        def create_context():
            with manager.create_download_context():
                results.append(threading.get_ident())
                time.sleep(0.1)
                
        threads = []
        for _ in range(5):
            thread = threading.Thread(target=create_context)
            threads.append(thread)
            thread.start()
            
        for thread in threads:
            thread.join()
            
        # All threads should have completed successfully
        self.assertEqual(len(results), 5)
        self.assertEqual(len(set(results)), 5)  # All unique thread IDs
        
    def test_concurrent_progress_emission(self):
        """Test concurrent progress emission from multiple threads."""
        manager = ProgressManager()
        callback_calls = []
        
        def thread_callback(percent):
            callback_calls.append((threading.get_ident(), percent))
            
        def emit_progress():
            with manager.create_transcription_context(thread_callback) as context:
                for i in range(0, 101, 10):
                    context.emit_progress(i, 100)
                    time.sleep(0.01)
                    
        threads = []
        for _ in range(3):
            thread = threading.Thread(target=emit_progress)
            threads.append(thread)
            thread.start()
            
        for thread in threads:
            thread.join()
            
        # Should have received callbacks from all threads
        self.assertGreater(len(callback_calls), 0)
        
        # Check that we got callbacks from multiple threads
        thread_ids = set(call[0] for call in callback_calls)
        self.assertGreater(len(thread_ids), 1)


if __name__ == '__main__':
    unittest.main()