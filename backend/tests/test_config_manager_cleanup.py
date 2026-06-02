import subprocess
import sys
import pytest

def test_get_available_processing_devices_removed() -> None:
    # Ensure config_manager is imported
    import backend.utils.config_manager as config_manager
    
    # Assert that the function is no longer defined in config_manager
    assert not hasattr(config_manager, "get_available_processing_devices")

def test_import_config_manager_without_torch() -> None:
    # Run in a separate subprocess to simulate an environment where PyTorch is not installed
    # and prevent polluting the module registry for other tests in the same process.
    code = """
import sys
sys.modules['torch'] = None
try:
    import backend.utils.config_manager
    print("SUCCESS")
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd="/home/msadmin/Nojoin-dev"
    )
    assert result.returncode == 0
    assert "SUCCESS" in result.stdout
