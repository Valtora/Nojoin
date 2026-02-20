import numpy as np
from typing import List, Optional

def cosine_similarity(v1: Optional[List[float]], v2: Optional[List[float]]) -> float:
    """Compute cosine similarity between two vectors."""
    if v1 is None or v2 is None:
        return 0.0
        
    # Check for None values inside the lists which can cause numpy errors
    if any(x is None for x in v1) or any(x is None for x in v2):
        return 0.0

    try:
        a = np.array(v1, dtype=float)
        b = np.array(v2, dtype=float)
    except (ValueError, TypeError):
        return 0.0

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
    threshold: float = 0.75,
    margin: float = 0.05
):
    """
    Find the best matching GlobalSpeaker for a given embedding.
    
    Args:
        embedding: The embedding vector to match.
        global_speakers: List of GlobalSpeaker objects with embeddings.
        threshold: Minimum similarity score to consider a match.
        margin: The minimum difference required between the best and second best match
                to avoid ambiguous assignments.
        
    Returns:
        Tuple of (best_matching_speaker, similarity_score).
        Returns (None, 0.0) if no match above threshold or if match is ambiguous.
    """
    import re
    placeholder_pattern = re.compile(r"^(SPEAKER_\d+|Speaker \d+|Unknown|New Voice .*)$", re.IGNORECASE)
    
    best_match = None
    best_score = 0.0
    second_best_score = 0.0
    
    for gs in global_speakers:
        # Skip placeholder names and speakers without embeddings
        if not gs.embedding or placeholder_pattern.match(gs.name):
            continue
            
        score = cosine_similarity(embedding, gs.embedding)
        
        if score > best_score:
            second_best_score = best_score
            best_score = score
            best_match = gs
        elif score > second_best_score:
            second_best_score = score
            
    # Check if the best match passes the threshold
    if best_match and best_score >= threshold:
        # Check for ambiguity using the margin of victory
        if (best_score - second_best_score) >= margin:
            return best_match, best_score
        else:
            # It's an ambiguous match, better to return nothing than a false positive
            return None, 0.0
            
    return None, best_score
