import logging
import os

logger = logging.getLogger(__name__)

# ONNX Runtime's Linux builds upload usage telemetry to Microsoft from 1.29 on
# unless ORT_DISABLE_TELEMETRY is set before it initialises. Setting it here,
# ahead of any onnxruntime import, covers installs outside the worker image too.
# setdefault leaves an operator's explicit value alone.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

# huggingface_hub 1.32+ stores Xet downloads in a cache-wide store sharded by
# hash. An fp32 ONNX graph and its external weights then resolve into different
# directories, and ONNX Runtime refuses external data outside the graph's own
# (#448). huggingface_hub reads this once, on import, so it is set here first.
os.environ.setdefault("HF_HUB_DISABLE_SHARED_BLOBS", "1")


def setup_secure_umask():
    # Default secure umask (owner only)
    default_umask_str = "0077"
    # Compose declares NOJOIN_UMASK on every service, so an operator who leaves
    # it blank sends an empty string rather than nothing at all. Treat that as
    # unset: read as a get() default it reached int() and logged an "invalid
    # value" warning on every start of an otherwise correctly configured stack.
    umask_env = os.getenv("NOJOIN_UMASK", "").strip() or default_umask_str

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
