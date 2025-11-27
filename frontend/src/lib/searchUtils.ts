import Fuse from 'fuse.js';

/**
 * Performs a fuzzy match check against a list of keywords.
 * Returns true if the query matches any of the keywords.
 */
export const fuzzyMatch = (query: string, keywords: string[], threshold = 0.3): boolean => {
  if (!query) return true;
  
  const fuse = new Fuse(keywords, {
    threshold, // 0.0 = perfect match, 1.0 = match anything
    distance: 100,
  });
  
  const results = fuse.search(query);
  return results.length > 0;
};

/**
 * Returns the best match score for a query against a list of keywords.
 * Lower score is better. Returns 1.0 if no match.
 */
export const getMatchScore = (query: string, keywords: string[]): number => {
  if (!query) return 0;

  const fuse = new Fuse(keywords, {
    threshold: 0.4,
    distance: 100,
    includeScore: true,
  });

  const results = fuse.search(query);
  if (results.length === 0) return 1.0;
  
  // Return the lowest (best) score
  return Math.min(...results.map(r => r.score || 1.0));
};

/**
 * Creates a configured Fuse instance for searching objects.
 */
export const createFuse = <T>(list: T[], keys: string[]) => {
  return new Fuse(list, {
    keys,
    threshold: 0.4,
    distance: 100,
    ignoreLocation: true, // Search anywhere in the string
  });
};
