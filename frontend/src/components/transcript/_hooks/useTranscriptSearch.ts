"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Fuse from "fuse.js";

import { TranscriptSegment } from "@/types";
import { getTranscriptSegmentKey } from "@/lib/transcriptSegments";

import { sameDecorations, type SearchDecoration } from "../textDecorations";

const NO_DECORATIONS: readonly SearchDecoration[] = [];

type DecorationsByLine = ReadonlyMap<string, readonly SearchDecoration[]>;

/**
 * `next`, with each line's list swapped for the one it had before when the
 * two hold the same decorations. A change to any line finds the matches
 * again, which rebuilds every line's list; this keeps the lines whose matches
 * didn't move from redrawing.
 */
function useKeptDecorations(next: DecorationsByLine): DecorationsByLine {
  const [state, setState] = useState({ seen: next, kept: next });
  if (state.seen === next) return state.kept;
  const kept = new Map<string, readonly SearchDecoration[]>();
  next.forEach((decorations, segmentId) => {
    const before = state.kept.get(segmentId);
    kept.set(
      segmentId,
      before && sameDecorations(before, decorations) ? before : decorations,
    );
  });
  // Stored during render, as React's docs do for a value derived from the
  // previous render; this render already returns the result.
  setState({ seen: next, kept });
  return kept;
}

export interface TranscriptSearchMatch {
  segmentId: string;
  orderIndex: number;
  startIndex: number;
  length: number;
}

export interface UseTranscriptSearchOptions {
  segments: TranscriptSegment[];
  showSearch: boolean;
  scrollSegmentIntoView: (
    segmentKey: string,
    behavior?: ScrollBehavior,
  ) => void;
}

export interface TranscriptSearch {
  findText: string;
  setFindText: (value: string) => void;
  replaceText: string;
  setReplaceText: (value: string) => void;
  caseSensitive: boolean;
  setCaseSensitive: (value: boolean) => void;
  isFuzzy: boolean;
  setIsFuzzy: (value: boolean) => void;
  useRegex: boolean;
  setUseRegex: (value: boolean) => void;
  matches: TranscriptSearchMatch[];
  currentMatchIndex: number;
  setCurrentMatchIndex: React.Dispatch<React.SetStateAction<number>>;
  nextMatch: () => void;
  prevMatch: () => void;
  /** The line's matches, as decorations for its text. */
  searchDecorations: (segmentId: string) => readonly SearchDecoration[];
  /** Reset find/replace inputs (used after a Replace All completes). */
  resetFindReplace: () => void;
}

/**
 * Owns the find/replace search state and match computation for
 * {@link TranscriptView}: literal, regex and fuzzy matching, current-index
 * management across result changes, and scroll-to-current-match.
 */
export function useTranscriptSearch(
  options: UseTranscriptSearchOptions,
): TranscriptSearch {
  const { segments, showSearch, scrollSegmentIntoView } = options;

  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [isFuzzy, setIsFuzzy] = useState(false);
  const [useRegex, setUseRegex] = useState(false);

  const [matches, setMatches] = useState<TranscriptSearchMatch[]>([]);
  const [currentMatchIndex, setCurrentMatchIndex] = useState(-1);
  const prevFindTextRef = useRef(findText);

  // Calculate matches when findText or segments change
  useEffect(() => {
    if (!findText.trim() || !showSearch) {
      setMatches([]);
      setCurrentMatchIndex(-1);
      return;
    }

    const newMatches: TranscriptSearchMatch[] = [];

    if (isFuzzy && !useRegex) {
      const fuse = new Fuse(segments, {
        keys: ["text"],
        includeMatches: true,
        threshold: 0.4,
        ignoreLocation: true,
        isCaseSensitive: caseSensitive,
      });

      const results = fuse.search(findText);

      results.forEach((result) => {
        if (result.matches) {
          result.matches.forEach((match) => {
            if (match.key === "text" && match.indices) {
              match.indices.forEach((range) => {
                const segment = segments[result.refIndex];
                newMatches.push({
                  segmentId: getTranscriptSegmentKey(segment, result.refIndex),
                  orderIndex: result.refIndex,
                  startIndex: range[0],
                  length: range[1] - range[0] + 1,
                });
              });
            }
          });
        }
      });

      // Sort matches by segmentIndex then startIndex
      newMatches.sort((a, b) => {
        if (a.orderIndex !== b.orderIndex) return a.orderIndex - b.orderIndex;
        return a.startIndex - b.startIndex;
      });
    } else if (useRegex) {
      try {
        const flags = caseSensitive ? "g" : "gi";
        const regex = new RegExp(findText, flags);

        segments.forEach((segment, sIndex) => {
          let match;
          // Reset lastIndex for each segment if using global flag
          regex.lastIndex = 0;

          while ((match = regex.exec(segment.text)) !== null) {
            newMatches.push({
              segmentId: getTranscriptSegmentKey(segment, sIndex),
              orderIndex: sIndex,
              startIndex: match.index,
              length: match[0].length,
            });
            // Prevent infinite loop with zero-width matches
            if (match.index === regex.lastIndex) {
              regex.lastIndex++;
            }
          }
        });
      } catch {
        // Invalid regex, ignore
      }
    } else {
      segments.forEach((segment, sIndex) => {
        const text = caseSensitive ? segment.text : segment.text.toLowerCase();
        const search = caseSensitive ? findText : findText.toLowerCase();

        let pos = 0;
        while (pos < text.length) {
          const index = text.indexOf(search, pos);
          if (index === -1) break;
          newMatches.push({
            segmentId: getTranscriptSegmentKey(segment, sIndex),
            orderIndex: sIndex,
            startIndex: index,
            length: search.length,
          });
          pos = index + 1;
        }
      });
    }

    setMatches(newMatches);

    setCurrentMatchIndex((prevIndex) => {
      // If search term changed, reset to first match
      if (findText !== prevFindTextRef.current) {
        return newMatches.length > 0 ? 0 : -1;
      }

      // If segments updated (e.g. replace), try to maintain relative position
      if (newMatches.length === 0) return -1;
      if (prevIndex >= newMatches.length) return newMatches.length - 1;
      // After replacing the current match, the following one takes its index,
      // so holding the index keeps the selection roughly in place.
      return prevIndex;
    });

    prevFindTextRef.current = findText;
  }, [findText, segments, showSearch, caseSensitive, isFuzzy, useRegex]);

  // Scroll to current match
  useEffect(() => {
    if (currentMatchIndex >= 0 && matches[currentMatchIndex]) {
      const match = matches[currentMatchIndex];
      scrollSegmentIntoView(match.segmentId, "smooth");
    }
  }, [currentMatchIndex, matches, scrollSegmentIntoView]);

  const nextMatch = () => {
    if (matches.length === 0) return;
    setCurrentMatchIndex((prev) => (prev + 1) % matches.length);
  };

  const prevMatch = () => {
    if (matches.length === 0) return;
    setCurrentMatchIndex((prev) => (prev - 1 + matches.length) % matches.length);
  };

  // Each line's matches, as decorations. A line keeps the same array until its
  // own matches change, and moving the current match swaps the arrays of the
  // two lines it leaves and enters only, so the other lines need not redraw.
  const matchesByLine = useMemo(() => {
    const byLine = new Map<string, TranscriptSearchMatch[]>();
    if (!findText || !showSearch) return byLine;
    matches.forEach((match) => {
      byLine.set(match.segmentId, [...(byLine.get(match.segmentId) ?? []), match]);
    });
    return byLine;
  }, [findText, showSearch, matches]);

  // A case-insensitive match is found in the lower-cased text, which can be
  // longer than the line ("İ" lower-cases to two characters), so a match can
  // reach past the line's end. Its part on the line is marked, and a match
  // wholly past the end marks the end, as search always drew it.
  const plainDecorations = useMemo(() => {
    const byLine = new Map<string, SearchDecoration[]>();
    matchesByLine.forEach((lineMatches, segmentId) => {
      byLine.set(
        segmentId,
        lineMatches.map((match) => {
          const length = segments[match.orderIndex]?.text.length ?? 0;
          return {
            kind: "search",
            from: Math.min(match.startIndex, length),
            to: Math.min(match.startIndex + match.length, length),
            current: false,
          };
        }),
      );
    });
    return byLine;
  }, [matchesByLine, segments]);

  const currentMatch = matches[currentMatchIndex];
  const decorationsByLine = useMemo(() => {
    const lineMatches = currentMatch && matchesByLine.get(currentMatch.segmentId);
    const plain = currentMatch && plainDecorations.get(currentMatch.segmentId);
    if (!currentMatch || !lineMatches || !plain) return plainDecorations;
    const byLine = new Map(plainDecorations);
    byLine.set(
      currentMatch.segmentId,
      plain.map((decoration, index) =>
        lineMatches[index] === currentMatch ? { ...decoration, current: true } : decoration,
      ),
    );
    return byLine;
  }, [currentMatch, matchesByLine, plainDecorations]);
  const keptDecorationsByLine = useKeptDecorations(decorationsByLine);

  const searchDecorations = useCallback(
    (segmentId: string): readonly SearchDecoration[] =>
      keptDecorationsByLine.get(segmentId) ?? NO_DECORATIONS,
    [keptDecorationsByLine],
  );

  const resetFindReplace = () => {
    setFindText("");
    setReplaceText("");
  };

  return useMemo(
    () => ({
      findText,
      setFindText,
      replaceText,
      setReplaceText,
      caseSensitive,
      setCaseSensitive,
      isFuzzy,
      setIsFuzzy,
      useRegex,
      setUseRegex,
      matches,
      currentMatchIndex,
      setCurrentMatchIndex,
      nextMatch,
      prevMatch,
      searchDecorations,
      resetFindReplace,
    }),
    // Rebuilt when this state changes. nextMatch, prevMatch and
    // resetFindReplace are new functions each render, but they read only
    // `matches` and state setters, so the copy kept here is never stale.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      findText,
      replaceText,
      caseSensitive,
      isFuzzy,
      useRegex,
      matches,
      currentMatchIndex,
      searchDecorations,
    ],
  );
}
