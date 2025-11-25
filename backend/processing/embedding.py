import logging
import torch
import numpy as np
from pyannote.audio import Inference, Model
from pyannote.core import Segment
from typing import Dict, List, Optional
from backend.utils.config_manager import config_manager

logger = logging.getLogger(__name__)

# Default embedding model
DEFAULT_EMBEDDING_MODEL = "pyannote/wespeaker-voxceleb-resnet34-LM"

_embedding_model_cache = {}

def load_embedding_model(device_str: str):
    """Load pyannote embedding model."""
    try:
        hf_token = config_manager.get("hf_token")
        if not hf_token:
            raise ValueError("Hugging Face token (hf_token) not found in configuration.")

        # Explicitly load the model first using Model.from_pretrained which accepts 'token'
        # Then pass the loaded model object to Inference, which doesn't accept token arguments in __init__
        logger.info(f"Loading embedding model: {DEFAULT_EMBEDDING_MODEL}")
        loaded_model = Model.from_pretrained(DEFAULT_EMBEDDING_MODEL, token=hf_token)
        
        model = Inference(loaded_model, window="sliding")
        model.to(torch.device(device_str))
        return model
    except Exception as e:
        logger.error(f"Failed to load embedding model: {e}", exc_info=True)
        raise RuntimeError("Could not load embedding model.") from e

def extract_embeddings(audio_path: str, diarization_result, device_str: str = "cpu") -> Dict[str, List[float]]:
    """
    Extracts embeddings for each speaker in the diarization result.
    Returns a dictionary mapping speaker label to embedding vector (list of floats).
    """
    if diarization_result is None:
        logger.warning("Diarization result is None, skipping embedding extraction")
        return {}

    logger.info(f"Starting embedding extraction for {audio_path}")
    
    try:
        cache_key = (DEFAULT_EMBEDDING_MODEL, device_str)
        if cache_key not in _embedding_model_cache:
            _embedding_model_cache[cache_key] = load_embedding_model(device_str)
        model = _embedding_model_cache[cache_key]
        
        embeddings = {}
        
        # Group segments by speaker
        speaker_segments = {}
        for turn, _, label in diarization_result.itertracks(yield_label=True):
            if label not in speaker_segments:
                speaker_segments[label] = []
            speaker_segments[label].append(turn)
            
        # For each speaker, extract embedding from the longest segment(s)
        # We can average embeddings from multiple segments for better robustness
        for label, segments in speaker_segments.items():
            # Sort by duration, take top 3
            segments.sort(key=lambda s: s.duration, reverse=True)
            top_segments = segments[:3]
            
            speaker_embeddings = []
            for seg in top_segments:
                # Inference takes a path and a window (Segment)
                # But pyannote Inference usually works on the whole file with a sliding window OR a specific crop.
                # model.crop(audio_path, seg) returns the embedding for that segment.
                try:
                    # Pyannote 3.1 / SpeechBrain 1.0+ change:
                    # model(path) returns SlidingWindowFeature
                    # We need to crop it manually or use the inference object correctly.
                    # The 'Inference' class from pyannote.audio is a wrapper.
                    # Calling model.crop(path, segment) is the correct way for the Inference wrapper.
                    
                    emb = model.crop(audio_path, seg)
                    
                    # Handle SlidingWindowFeature (it might be a wrapper around numpy)
                    # If it has no shape, it might be a SlidingWindowFeature object that behaves like an array but fails hasattr check?
                    # Or maybe it's returning something else.
                    # Let's force conversion to numpy if possible.
                    
                    if hasattr(emb, 'data'):
                        emb = emb.data
                        
                    # Ensure it's a numpy array
                    emb = np.array(emb)
                    
                    # emb is (1, dimension) or (dimension,) or (frames, dimension)
                    # If it returns multiple frames for the segment, we should average them.
                    if len(emb.shape) == 2:
                        emb = np.mean(emb, axis=0)
                        
                    speaker_embeddings.append(emb)
                except Exception as e:
                    logger.warning(f"Failed to extract embedding for speaker {label} segment {seg}: {e}")
            
            if speaker_embeddings:
                # Average the embeddings
                avg_embedding = np.mean(np.array(speaker_embeddings), axis=0)
                embeddings[label] = avg_embedding.tolist()
                
        return embeddings

    except Exception as e:
        logger.error(f"Embedding extraction failed: {e}", exc_info=True)
        return {}

def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a = np.array(v1)
    b = np.array(v2)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))

def merge_embeddings(current_embedding: List[float], new_embedding: List[float], alpha: float = 0.1) -> List[float]:
    """
    Merges a new embedding into an existing one using a weighted moving average.
    
    Args:
        current_embedding: The existing embedding vector.
        new_embedding: The new embedding vector to merge.
        alpha: The weight of the new embedding (0.0 to 1.0). 
               Higher alpha means the new embedding has more influence.
               
    Returns:
        The merged embedding vector.
    """
    if not current_embedding:
        return new_embedding
    
    curr_arr = np.array(current_embedding)
    new_arr = np.array(new_embedding)
    
    # Weighted average
    merged = (1 - alpha) * curr_arr + alpha * new_arr
    
    return merged.tolist()
