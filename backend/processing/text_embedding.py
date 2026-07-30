import logging
import os
from typing import Any, List, Union

from .onnx_providers import verify_gpu_providers
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

                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
                self._model = TextEmbedding(
                    model_name=MODEL_NAME,
                    providers=providers,
                )
                # A TextEmbedding built with an unloadable CUDA provider still
                # succeeds, on CPU, so the exception handler below never fires.
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
