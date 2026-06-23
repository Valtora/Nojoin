"use client";

import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import Fuse from "fuse.js";

import { TranscriptSegment } from "@/types";
import { getTranscriptSegmentKey } from "@/lib/transcriptSegments";

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
  renderHighlightedText: (text: string, segmentId: string) => ReactNode;
  /** Reset find/replace inputs (used after a Replace All completes). */
  resetFindReplace: () => void;
}

/**
 * Owns the find/replace search state and match computation for
 * {@link TranscriptView} (FE-012). Lifted verbatim from the component so the
 * literal/regex/fuzzy match logic, the smart current-index management, and the
 * scroll-to-current-match behaviour are unchanged.
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

    // Smart index management
    setCurrentMatchIndex((prevIndex) => {
      // If search term changed, reset to first match
      if (findText !== prevFindTextRef.current) {
        return newMatches.length > 0 ? 0 : -1;
      }

      // If segments updated (e.g. replace), try to maintain relative position
      if (newMatches.length === 0) return -1;
      if (prevIndex >= newMatches.length) return newMatches.length - 1;
      // If we just replaced the current match, the next one slides into this index (or close to it)
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

  const renderHighlightedText = (
    text: string,
    segmentId: string,
  ): ReactNode => {
    if (!findText || !showSearch || matches.length === 0) return text;

    const segmentMatches = matches.filter((m) => m.segmentId === segmentId);
    if (segmentMatches.length === 0) return text;

    let lastIndex = 0;
    const parts: ReactNode[] = [];

    segmentMatches.forEach((match) => {
      // Text before match
      if (match.startIndex > lastIndex) {
        parts.push(text.substring(lastIndex, match.startIndex));
      }

      // The match itself
      const isCurrent = matches[currentMatchIndex] === match;
      parts.push(
        <mark
          key={`${segmentId}-${match.startIndex}`}
          className={`${isCurrent ? "bg-orange-400 text-white" : "bg-yellow-200 dark:bg-yellow-900 text-gray-900 dark:text-gray-100"} rounded-sm px-0.5`}
        >
          {text.substring(match.startIndex, match.startIndex + match.length)}
        </mark>,
      );

      lastIndex = match.startIndex + match.length;
    });

    // Remaining text
    if (lastIndex < text.length) {
      parts.push(text.substring(lastIndex));
    }

    return parts;
  };

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
      renderHighlightedText,
      resetFindReplace,
    }),
    // renderHighlightedText/nextMatch/prevMatch close over the latest state on
    // each render; the object is rebuilt every render intentionally so the JSX
    // always reads current values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      findText,
      replaceText,
      caseSensitive,
      isFuzzy,
      useRegex,
      matches,
      currentMatchIndex,
    ],
  );
}
