import logging
import os
from typing import Any, List, Union

from .onnx_providers import gpu_is_present, verify_gpu_providers
from .text_embedding_version import TEXT_EMBEDDING_MODEL

logger = logging.getLogger(__name__)

# Model configuration. The name and its version stamp live together in
# text_embedding_version so the API image -- which has no fastembed -- can
# still reason about which stored vectors are comparable without importing
# this module.
MODEL_NAME = TEXT_EMBEDDING_MODEL

os.environ.setdefault("ORT_LOG_SEVERITY_LEVEL", "1")


class TextEmbeddingService:
    _instance = None
    _model: Any = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        if self._model is None:
            logger.info(f"Loading text embedding model: {MODEL_NAME}")
            try:
                from fastembed import TextEmbedding

                # Ask for CUDA only when a GPU is actually attached to this
                # process. Requesting it on a CPU-only lane does not degrade
                # gracefully: onnxruntime-gpu loads its CUDA provider library,
                # finds no device, and takes the process down with SIGSEGV.
                # A segfault cannot be caught, so the fallback below never runs
                # and the Celery worker dies mid-task with WorkerLostError.
                #
                # This matters because text embedding runs on the io and parse
                # lanes, neither of which is granted a GPU by compose --
                # get_available_providers() is no help, since it reports what
                # onnxruntime was compiled with rather than what is usable.
                providers = ["CPUExecutionProvider"]
                if gpu_is_present():
                    providers.insert(0, "CUDAExecutionProvider")

                self._model = TextEmbedding(
                    model_name=MODEL_NAME,
                    providers=providers,
                )
                # Separate concern: with a GPU attached, CUDA can still be
                # dropped for a missing shared library, and that silent CPU
                # fallback is worth shouting about.
                verify_gpu_providers(
                    self._model,
                    component=f"Text embedding model {MODEL_NAME}",
                    requested=providers,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"Failed to load text embedding model with GPU, falling back to CPU: {e}"
                )
                try:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(model_name=MODEL_NAME)
                except Exception as e_cpu:
                    logger.error(f"Failed to load text embedding model: {e_cpu}")
                    raise

    @classmethod
    def release_model(cls):
        """Releases the embedding model from memory."""
        if cls._instance:
            logger.info("Releasing TextEmbeddingService model.")
            cls._instance._model = None
            cls._instance = None

    def embed(self, texts: Union[str, List[str]]) -> List[List[float]]:
        """
        Generate embeddings for a list of texts.
        Returns a list of vectors (list of floats).
        """
        if isinstance(texts, str):
            texts = [texts]

        try:
            # fastembed returns a generator of vectors
            embeddings = list(self._model.embed(texts))
            # fastembed returns numpy arrays
            return [e.tolist() for e in embeddings]
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to generate embeddings: {e}")
            return []


# Global instance accessor
_service = None


def get_text_embedding_service():
    global _service
    if _service is None:
        _service = TextEmbeddingService()
    return _service


def release_embedding_model():
    """Global function to release the text embedding model."""
    global _service
    TextEmbeddingService.release_model()
    _service = None
