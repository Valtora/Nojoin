from rapidfuzz import fuzz, utils

class SearchEngine:
    """
    Provides fuzzy, partial, and typo-tolerant search over meetings using RapidFuzz.
    """
    def __init__(self, fuzzy_threshold=75):
        self.fuzzy_threshold = fuzzy_threshold

    def search(self, query, meetings_data):
        """
        Search meetings by query string.
        Args:
            query (str): The search query.
            meetings_data (list of dict): Each dict must have 'id', 'original_data', and 'searchable_text'.
        Returns:
            list of dict: The original_data dicts for meetings that match the query.
        """
        if not query or not meetings_data:
            return [m['original_data'] for m in meetings_data]
        query_proc = utils.default_process(query)
        results = []
        for m in meetings_data:
            text = m.get('searchable_text', '')
            text_proc = utils.default_process(text)
            # Direct substring match (case-insensitive)
            if query_proc in text_proc:
                results.append(m['original_data'])
                continue
            # Fuzzy match (token_set_ratio and partial_ratio)
            score_token = fuzz.token_set_ratio(query_proc, text_proc)
            score_partial = fuzz.partial_ratio(query_proc, text_proc)
            if max(score_token, score_partial) >= self.fuzzy_threshold:
                results.append(m['original_data'])
        return results 