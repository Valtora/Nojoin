"""
Centralized progress management system for Nojoin.

This module provides a unified approach to handling progress reporting across
different components (transcription, model downloads, etc.) while managing
TQDM patching conflicts.
"""

import logging
import threading
import tqdm
from typing import Callable, Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class ContextType(Enum):
    """Types of progress contexts."""
    TRANSCRIPTION = "transcription"
    MODEL_DOWNLOAD = "model_download"
    DIARIZATION = "diarization"
    GENERAL = "general"


@dataclass
class ProgressEvent:
    """Represents a progress update event."""
    current: int
    total: int
    percent: float
    context: str
    timestamp: datetime
    thread_id: str
    
    @classmethod
    def create(cls, current: int, total: int, context: str) -> 'ProgressEvent':
        """Create a progress event with calculated percentage."""
        percent = (current / total * 100) if total > 0 else 0
        return cls(
            current=current,
            total=total,
            percent=min(percent, 100.0),
            context=context,
            timestamp=datetime.now(),
            thread_id=str(threading.get_ident())
        )


class ProgressContext:
    """Context manager for safe progress tracking with automatic cleanup."""
    
    def __init__(self, manager: 'ProgressManager', context_type: ContextType, 
                 progress_callback: Optional[Callable[[int], None]] = None):
        self.manager = manager
        self.context_type = context_type
        self.progress_callback = progress_callback
        self.thread_id = str(threading.get_ident())
        self._active = False
        self._original_tqdm = None
        
    def __enter__(self) -> 'ProgressContext':
        """Enter the progress context and set up TQDM patching."""
        logger.debug(f"Entering progress context: {self.context_type.value} (thread: {self.thread_id})")
        
        self._active = True
        self.manager._register_context(self)
        
        # Apply TQDM patch if needed
        if self.manager._should_patch_tqdm(self.context_type):
            self._apply_tqdm_patch()
            
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the progress context and clean up."""
        logger.debug(f"Exiting progress context: {self.context_type.value} (thread: {self.thread_id})")
        
        self._active = False
        
        # Restore TQDM if we patched it
        if self._original_tqdm is not None:
            self._restore_tqdm_patch()
            
        self.manager._unregister_context(self)
        
    def emit_progress(self, current: int, total: int) -> None:
        """Emit a progress update."""
        if not self._active:
            return
            
        event = ProgressEvent.create(current, total, self.context_type.value)
        logger.debug(f"Progress event: {event.percent:.1f}% ({current}/{total}) - {self.context_type.value}")
        
        # Record event for monitoring
        self.manager._record_progress_event(event)
        
        if self.progress_callback:
            try:
                self.progress_callback(int(event.percent))
            except Exception as e:
                logger.error(f"Error in progress callback: {e}")
                
    def _apply_tqdm_patch(self) -> None:
        """Apply TQDM patch for this context."""
        try:
            # Store original tqdm
            self._original_tqdm = tqdm.tqdm
            
            # Create custom tqdm class for this context
            context = self
            
            class ContextAwareTqdm(tqdm.tqdm):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self._current = self.n
                    
                def update(self, n):
                    super().update(n)
                    self._current += n
                    if context._active and context.progress_callback:
                        context.emit_progress(self._current, self.total or 1)
                        
            # Apply the patch
            tqdm.tqdm = ContextAwareTqdm
            
            # Mark as patched by this system
            tqdm.tqdm._nojoin_patched = True
            tqdm.tqdm._nojoin_context = self.context_type.value
            
            logger.debug(f"Applied TQDM patch for context: {self.context_type.value}")
            
        except Exception as e:
            logger.error(f"Failed to apply TQDM patch: {e}")
            self._original_tqdm = None
            
    def _restore_tqdm_patch(self) -> None:
        """Restore original TQDM."""
        try:
            if self._original_tqdm is not None:
                tqdm.tqdm = self._original_tqdm
                logger.debug(f"Restored original TQDM for context: {self.context_type.value}")
        except Exception as e:
            logger.error(f"Failed to restore TQDM: {e}")


class ProgressManager:
    """Centralized progress management with context-aware TQDM patching."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
        
    def __init__(self):
        if self._initialized:
            return
            
        self._initialized = True
        self._active_contexts: Dict[str, ProgressContext] = {}
        self._context_lock = threading.Lock()
        self._tqdm_patch_count = 0
        self._progress_events: List[ProgressEvent] = []
        self._max_events = 1000  # Keep last 1000 events for monitoring
        
        logger.info("ProgressManager initialized")
        self._log_system_state()
        
    def create_download_context(self, progress_callback: Optional[Callable[[int], None]] = None) -> ProgressContext:
        """Create a progress context for model downloads."""
        return ProgressContext(self, ContextType.MODEL_DOWNLOAD, progress_callback)
        
    def create_transcription_context(self, progress_callback: Optional[Callable[[int], None]] = None) -> ProgressContext:
        """Create a progress context for transcription."""
        return ProgressContext(self, ContextType.TRANSCRIPTION, progress_callback)
        
    def create_diarization_context(self, progress_callback: Optional[Callable[[int], None]] = None) -> ProgressContext:
        """Create a progress context for diarization."""
        return ProgressContext(self, ContextType.DIARIZATION, progress_callback)
        
    def is_tqdm_patched(self) -> bool:
        """Check if TQDM is currently patched by this system."""
        return hasattr(tqdm.tqdm, '_nojoin_patched') and tqdm.tqdm._nojoin_patched
        
    def get_active_contexts(self) -> List[str]:
        """Get list of active context types."""
        with self._context_lock:
            return [ctx.context_type.value for ctx in self._active_contexts.values()]
            
    def detect_tqdm_conflicts(self) -> List[str]:
        """Detect existing TQDM patches and potential conflicts."""
        conflicts = []
        
        # Check if tqdm is patched by something else
        if hasattr(tqdm.tqdm, '__module__') and 'nojoin' not in tqdm.tqdm.__module__:
            if tqdm.tqdm.__name__ != 'tqdm':
                conflicts.append("external_tqdm_patch")
                
        # Check for whisper-specific patches
        try:
            import whisper.transcribe
            if hasattr(whisper.transcribe, 'tqdm') and whisper.transcribe.tqdm != tqdm:
                conflicts.append("whisper_specific_patch")
        except ImportError:
            pass
            
        # Check for our own patches in different modules
        if hasattr(tqdm.tqdm, '_nojoin_patched'):
            current_context = getattr(tqdm.tqdm, '_nojoin_context', 'unknown')
            active_contexts = self.get_active_contexts()
            if current_context not in active_contexts:
                conflicts.append("orphaned_nojoin_patch")
                
        return conflicts
        
    def safe_patch_tqdm(self, context_type: ContextType) -> bool:
        """Safely patch TQDM, handling conflicts."""
        conflicts = self.detect_tqdm_conflicts()
        
        if conflicts:
            logger.warning(f"TQDM conflicts detected: {conflicts}")
            # For now, proceed anyway but log the conflicts
            
        try:
            # The actual patching is handled by ProgressContext
            return True
        except Exception as e:
            logger.error(f"Failed to safely patch TQDM: {e}")
            return False
            
    def restore_tqdm(self) -> None:
        """Restore original TQDM functionality."""
        try:
            # Import fresh tqdm to get original
            import importlib
            import tqdm as tqdm_module
            importlib.reload(tqdm_module)
            
            # Update global reference
            import sys
            sys.modules['tqdm'].tqdm = tqdm_module.tqdm
            
            logger.info("TQDM restored to original state")
            
        except Exception as e:
            logger.error(f"Failed to restore TQDM: {e}")
            
    def reset_tqdm_state(self) -> bool:
        """Reset TQDM state to resolve conflicts."""
        try:
            logger.info("Resetting TQDM state to resolve conflicts")
            
            # Clear all active contexts
            with self._context_lock:
                self._active_contexts.clear()
                
            # Reset patch count
            self._tqdm_patch_count = 0
            
            # Restore original TQDM
            self.restore_tqdm()
            
            # Clear any cached modules that might have patched TQDM
            import sys
            modules_to_reload = []
            for module_name in sys.modules:
                if 'tqdm' in module_name.lower() or 'progress' in module_name.lower():
                    modules_to_reload.append(module_name)
                    
            for module_name in modules_to_reload:
                try:
                    if module_name in sys.modules:
                        importlib.reload(sys.modules[module_name])
                except Exception as e:
                    logger.debug(f"Could not reload module {module_name}: {e}")
                    
            logger.info("TQDM state reset completed")
            return True
            
        except Exception as e:
            logger.error(f"Failed to reset TQDM state: {e}")
            return False
            
    def handle_download_retry(self, model_size: str, device: str, max_retries: int = 3) -> bool:
        """Handle automatic retry logic for failed downloads."""
        import time
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Download attempt {attempt + 1}/{max_retries} for model: {model_size}")
                
                # Reset TQDM state before retry
                if attempt > 0:
                    self.reset_tqdm_state()
                    time.sleep(2)  # Brief delay between retries
                
                # Attempt download
                import whisper
                model = whisper.load_model(model_size, device=device)
                
                logger.info(f"Download successful on attempt {attempt + 1}")
                return True
                
            except Exception as e:
                logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                
                if attempt == max_retries - 1:
                    logger.error(f"All {max_retries} download attempts failed for model: {model_size}")
                    return False
                    
                # Exponential backoff
                wait_time = 2 ** attempt
                logger.info(f"Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
                
        return False
            
    def _register_context(self, context: ProgressContext) -> None:
        """Register an active progress context."""
        with self._context_lock:
            self._active_contexts[context.thread_id] = context
            logger.debug(f"Registered context: {context.context_type.value} (thread: {context.thread_id})")
            
    def _unregister_context(self, context: ProgressContext) -> None:
        """Unregister a progress context."""
        with self._context_lock:
            if context.thread_id in self._active_contexts:
                del self._active_contexts[context.thread_id]
                logger.debug(f"Unregistered context: {context.context_type.value} (thread: {context.thread_id})")
                
    def _should_patch_tqdm(self, context_type: ContextType) -> bool:
        """Determine if TQDM should be patched for this context type."""
        # Always patch for model downloads and transcription
        return context_type in [ContextType.MODEL_DOWNLOAD, ContextType.TRANSCRIPTION, ContextType.DIARIZATION]
        
    def _log_system_state(self) -> None:
        """Log current system state for monitoring."""
        try:
            import sys
            import platform
            
            logger.info("Progress Manager System State:")
            logger.info(f"  Python version: {sys.version}")
            logger.info(f"  Platform: {platform.platform()}")
            logger.info(f"  Thread count: {threading.active_count()}")
            
            # Log TQDM state
            tqdm_info = self._get_tqdm_info()
            logger.info(f"  TQDM state: {tqdm_info}")
            
            # Log available memory (if psutil is available)
            try:
                import psutil
                memory = psutil.virtual_memory()
                logger.info(f"  Available memory: {memory.available / (1024**3):.1f} GB")
            except ImportError:
                logger.debug("psutil not available for memory monitoring")
                
        except Exception as e:
            logger.error(f"Error logging system state: {e}")
            
    def _get_tqdm_info(self) -> Dict[str, Any]:
        """Get information about current TQDM state."""
        info = {
            "is_patched": self.is_tqdm_patched(),
            "module": getattr(tqdm.tqdm, '__module__', 'unknown'),
            "class_name": tqdm.tqdm.__name__,
            "active_contexts": len(self._active_contexts),
            "patch_count": self._tqdm_patch_count
        }
        
        if hasattr(tqdm.tqdm, '_nojoin_context'):
            info["current_context"] = tqdm.tqdm._nojoin_context
            
        return info
        
    def _record_progress_event(self, event: ProgressEvent) -> None:
        """Record a progress event for monitoring."""
        # Add to events list
        self._progress_events.append(event)
        
        # Trim old events if we exceed max
        if len(self._progress_events) > self._max_events:
            self._progress_events = self._progress_events[-self._max_events:]
            
        # Log significant progress milestones
        if event.percent in [0, 25, 50, 75, 100]:
            logger.info(f"Progress milestone: {event.percent}% - {event.context} (thread: {event.thread_id})")
            
    def get_progress_statistics(self) -> Dict[str, Any]:
        """Get statistics about progress events."""
        if not self._progress_events:
            return {"total_events": 0}
            
        # Calculate statistics
        contexts = {}
        total_events = len(self._progress_events)
        
        for event in self._progress_events:
            if event.context not in contexts:
                contexts[event.context] = {
                    "count": 0,
                    "avg_percent": 0,
                    "max_percent": 0,
                    "min_percent": 100
                }
                
            ctx_stats = contexts[event.context]
            ctx_stats["count"] += 1
            ctx_stats["avg_percent"] = (ctx_stats["avg_percent"] * (ctx_stats["count"] - 1) + event.percent) / ctx_stats["count"]
            ctx_stats["max_percent"] = max(ctx_stats["max_percent"], event.percent)
            ctx_stats["min_percent"] = min(ctx_stats["min_percent"], event.percent)
            
        return {
            "total_events": total_events,
            "contexts": contexts,
            "active_contexts": len(self._active_contexts),
            "tqdm_info": self._get_tqdm_info()
        }
        
    def log_debug_info(self) -> None:
        """Log comprehensive debug information."""
        logger.info("=== Progress Manager Debug Information ===")
        
        # System state
        self._log_system_state()
        
        # Active contexts
        with self._context_lock:
            logger.info(f"Active contexts ({len(self._active_contexts)}):")
            for thread_id, context in self._active_contexts.items():
                logger.info(f"  Thread {thread_id}: {context.context_type.value}")
                
        # TQDM conflicts
        conflicts = self.detect_tqdm_conflicts()
        if conflicts:
            logger.warning(f"TQDM conflicts detected: {conflicts}")
        else:
            logger.info("No TQDM conflicts detected")
            
        # Progress statistics
        stats = self.get_progress_statistics()
        logger.info(f"Progress statistics: {stats}")
        
        # Recent events
        recent_events = self._progress_events[-10:] if self._progress_events else []
        logger.info(f"Recent progress events ({len(recent_events)}):")
        for event in recent_events:
            logger.info(f"  {event.timestamp.strftime('%H:%M:%S')} - {event.context}: {event.percent:.1f}%")
            
        logger.info("=== End Debug Information ===")
        
    def monitor_health(self) -> Dict[str, Any]:
        """Monitor system health and return status."""
        health_status = {
            "status": "healthy",
            "issues": [],
            "warnings": [],
            "info": {}
        }
        
        try:
            # Check for TQDM conflicts
            conflicts = self.detect_tqdm_conflicts()
            if conflicts:
                health_status["issues"].extend(conflicts)
                health_status["status"] = "degraded"
                
            # Check for stuck contexts
            current_time = datetime.now()
            stuck_contexts = []
            
            with self._context_lock:
                for thread_id, context in self._active_contexts.items():
                    # Check if context has been active for too long (>10 minutes)
                    # This would indicate a potential stuck operation
                    try:
                        import threading
                        thread = None
                        for t in threading.enumerate():
                            if str(t.ident) == thread_id:
                                thread = t
                                break
                                
                        if thread and not thread.is_alive():
                            stuck_contexts.append(thread_id)
                    except Exception:
                        pass
                        
            if stuck_contexts:
                health_status["warnings"].append(f"Potentially stuck contexts: {stuck_contexts}")
                
            # Check memory usage if available
            try:
                import psutil
                memory = psutil.virtual_memory()
                if memory.percent > 90:
                    health_status["warnings"].append(f"High memory usage: {memory.percent}%")
            except ImportError:
                pass
                
            # Add general info
            health_status["info"] = {
                "active_contexts": len(self._active_contexts),
                "total_events": len(self._progress_events),
                "tqdm_patched": self.is_tqdm_patched()
            }
            
        except Exception as e:
            health_status["status"] = "error"
            health_status["issues"].append(f"Health check failed: {e}")
            
        return health_status


# Global instance
_progress_manager = None

class FallbackProgressReporter:
    """Fallback progress reporting when TQDM patching fails."""
    
    def __init__(self, callback: Callable[[int], None]):
        self.callback = callback
        self.last_update = 0
        self.start_time = datetime.now()
        self._progress_patterns = [
            # Common progress patterns in logs
            r'(\d+)%',  # "50%"
            r'(\d+)/(\d+)',  # "50/100"
            r'(\d+\.\d+)%',  # "50.5%"
        ]
        
    def report_progress(self, message: str) -> None:
        """Extract progress from log messages or use time-based estimation."""
        import re
        
        # Try to extract progress from message
        for pattern in self._progress_patterns:
            match = re.search(pattern, message)
            if match:
                try:
                    if '/' in pattern:
                        # Format: current/total
                        current = int(match.group(1))
                        total = int(match.group(2))
                        percent = int((current / total) * 100) if total > 0 else 0
                    else:
                        # Format: percentage
                        percent = int(float(match.group(1)))
                    
                    if percent != self.last_update:
                        self.last_update = percent
                        if self.callback:
                            self.callback(min(percent, 100))
                    return
                except (ValueError, IndexError):
                    continue
                    
        # Fallback: time-based estimation
        self._estimate_progress_by_time()
        
    def _estimate_progress_by_time(self) -> None:
        """Provide time-based progress estimation."""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        
        # Simple time-based estimation (assumes 2-minute download for large models)
        estimated_duration = 120  # seconds
        estimated_percent = min(int((elapsed / estimated_duration) * 100), 95)
        
        if estimated_percent > self.last_update:
            self.last_update = estimated_percent
            if self.callback:
                self.callback(estimated_percent)
                
    def complete(self) -> None:
        """Mark progress as complete."""
        if self.callback:
            self.callback(100)


def get_progress_manager() -> ProgressManager:
    """Get the global ProgressManager instance."""
    global _progress_manager
    if _progress_manager is None:
        _progress_manager = ProgressManager()
    return _progress_manager