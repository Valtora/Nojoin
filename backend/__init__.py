import logging
import os

logger = logging.getLogger(__name__)


def setup_secure_umask():
    # Default secure umask (owner only)
    default_umask_str = "0077"
    umask_env = os.getenv("NOJOIN_UMASK", default_umask_str).strip()

    try:
        # Support octal strings (e.g. "0077" or "0o077") or decimal integers
        if umask_env.startswith("0o") or umask_env.startswith("0O"):
            umask_val = int(umask_env, 8)
        elif umask_env.startswith("0") and len(umask_env) > 1:
            umask_val = int(umask_env, 8)
        else:
            umask_val = int(umask_env)
    except ValueError:
        logger.warning(
            "Invalid NOJOIN_UMASK value '%s'. Falling back to default '%s'.",
            umask_env,
            default_umask_str,
        )
        umask_val = int(default_umask_str, 8)

    old_umask = os.umask(umask_val)
    logger.info("Configured process umask to 0o%04o (was 0o%04o)", umask_val, old_umask)


setup_secure_umask()
