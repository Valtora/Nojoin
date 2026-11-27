"""Keep the developer's own configuration out of the suite (#1056).

``backend.core.db``, ``backend.core.redis`` and ``backend.utils.config_manager``
call ``load_dotenv()`` on import, and python-dotenv looks for ``.env`` from the
calling module's directory upwards, so it finds the checkout's ``.env`` wherever
pytest runs. CI has no ``.env``, so a developer's copy was the one difference.
With ``LLM_PROVIDER=anthropic`` in it, config_manager made anthropic the
install-wide provider as it loaded, that outranks the provider a test gives the
owner, and 48 live naming tests failed with no name ever asked for.

``conftest.py`` imports this module ahead of the backend, because config_manager
reads the environment as it loads, and does two things:

- ``PYTHON_DOTENV_DISABLED`` turns every ``load_dotenv()`` call in the test
  process, and in the subprocesses a test starts, into a no-op. The running app
  never sets it, so it still reads ``.env`` as before.
- The LLM provider variables are removed from the environment, so one exported
  in the developer's shell, or passed to a test container, cannot choose the
  provider either. A test that needs one sets it with ``monkeypatch.setenv``.
  ``test_suite_env.py`` checks that the pattern covers every provider variable
  the backend reads.

Lives outside ``conftest.py`` for the reason ``data_dir.py`` gives.
"""

import os
import re

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

# LLM_PROVIDER, every variable of a provider family (GEMINI_API_KEY,
# OPENAI_MODEL, OLLAMA_API_URL, and the SDKs' own, such as OPENAI_BASE_URL) and
# the SECONDARY_ form of each.
PROVIDER_ENV_VARIABLE = re.compile(
    r"(?:SECONDARY_)?(?:LLM_PROVIDER|(?:GEMINI|OPENAI|ANTHROPIC|OLLAMA)_\w+)"
)


def is_provider_variable(name: str) -> bool:
    return PROVIDER_ENV_VARIABLE.fullmatch(name) is not None


for _name in [name for name in os.environ if is_provider_variable(name)]:
    del os.environ[_name]
