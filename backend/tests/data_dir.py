"""The suite's data directory, made once per test session.

The data directory holds config.json, the JWT keyring, the data encryption key,
logs, documents and recordings. The backend fixes those paths on import, and
config_manager writes config.json as it loads, so a fixture would come too
late: `conftest.py` imports this module ahead of the backend instead. A test's
subprocesses inherit the variable. The session fixture `suite_data_dir` in
`conftest.py` removes the directory.

Lives outside `conftest.py` for the reason `redis_guard.py` gives: pytest
imports `conftest.py` as the top-level module `conftest`, so a test importing
`backend.tests.conftest` runs the file a second time. Made there, a second
directory replaced NOJOIN_DATA_DIR for subprocesses after path_manager had
already resolved the first.
"""

import os
import tempfile
from pathlib import Path

SUITE_DATA_DIR = Path(tempfile.mkdtemp(prefix="nojoin-test-data-"))
os.environ["NOJOIN_DATA_DIR"] = str(SUITE_DATA_DIR)
