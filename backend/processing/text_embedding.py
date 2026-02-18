import logging
from typing import List, Union
from fastembed import TextEmbedding

logger = logging.getLogger(__name__)

# Model configuration
# Default embedding model
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

class TextEmbeddingService:
    _instance = None
    _model = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        if self._model is None:
            logger.info(f"Loading text embedding model: {MODEL_NAME}")
            try:
                # threads=None uses all available threads
                self._model = TextEmbedding(model_name=MODEL_NAME)
            except Exception as e:
                logger.error(f"Failed to load text embedding model: {e}")
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
        except Exception as e:
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
