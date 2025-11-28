import numpy as np
from typing import List

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

def find_matching_global_speaker(
    embedding: List[float],
    global_speakers: List,
    threshold: float = 0.65
):
    """
    Find the best matching GlobalSpeaker for a given embedding.
    
    Args:
        embedding: The embedding vector to match.
        global_speakers: List of GlobalSpeaker objects with embeddings.
        threshold: Minimum similarity score to consider a match.
        
    Returns:
        Tuple of (best_matching_speaker, similarity_score).
        Returns (None, 0.0) if no match above threshold.
    """
    import re
    placeholder_pattern = re.compile(r"^(SPEAKER_\d+|Speaker \d+|Unknown|New Voice .*)$", re.IGNORECASE)
    
    best_match = None
    best_score = 0.0
    
    for gs in global_speakers:
        # Skip placeholder names and speakers without embeddings
        if not gs.embedding or placeholder_pattern.match(gs.name):
            continue
            
        score = cosine_similarity(embedding, gs.embedding)
        if score > best_score:
            best_score = score
            best_match = gs
    
    if best_match and best_score >= threshold:
        return best_match, best_score
    
    return None, best_score
