"use client";

import {
  TranscriptSegment,
  RecordingSpeaker,
  GlobalSpeaker,
  RecordingId,
  SpeakerCorrectionScope,
  TranscriptSpeakerAssignment,
} from "@/types";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Play,
  Pause,
  Search,
  ArrowRightLeft,
  Download,
  ChevronUp,
  ChevronDown,
  Undo2,
  Redo2,
  Settings,
  Radio,
} from "lucide-react";
import { getColorByKey } from "@/lib/constants";
import { getTranscriptSegmentKey } from "@/lib/transcriptSegments";
import SpeakerAssignmentPopover from "./SpeakerAssignmentPopover";
import Fuse from "fuse.js";
import { useNotificationStore } from "@/lib/notificationStore";

interface TranscriptViewProps {
  recordingId: RecordingId;
  segments: TranscriptSegment[];
  currentTime: number;
  onPlaySegment: (start: number, end: number) => void;
  isPlaying: boolean;
  onPause: () => void;
  onResume: () => void;
  speakerMap: Record<string, string>;
  speakers: RecordingSpeaker[];
  globalSpeakers: GlobalSpeaker[];
  onRenameSpeaker: (label: string, newName: string) => void | Promise<void>;
  onUpdateSegmentSpeaker: (
    segment: TranscriptSegment,
    assignment: TranscriptSpeakerAssignment,
  ) => void | Promise<void>;
  onUpdateSegmentText: (
    segment: TranscriptSegment,
    text: string,
  ) => void | Promise<void>;
  onFindAndReplace: (
    find: string,
    replace: string,
    options?: { caseSensitive?: boolean; useRegex?: boolean },
  ) => void | Promise<void>;
  speakerColors: Record<string, string>;
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  onExport: () => void;
  readOnly?: boolean;
  allowProvisionalEdits?: boolean;
  disableSegmentPlayback?: boolean;
  emptyStateTitle?: string;
  emptyStateDescription?: string;
  trimStartS?: number | null;
  trimEndS?: number | null;
  onActiveEditUtteranceChange?: (utteranceId: string | null) => void;
  pendingRemoteUtteranceIds?: string[];
}

const formatTime = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return `${minutes.toString().padStart(2, "0")}:${remainingSeconds.toString().padStart(2, "0")}`;
};

const getDefaultSpeakerCorrectionScope = (
  speakerLabel: string,
): SpeakerCorrectionScope =>
  speakerLabel.startsWith("LIVE_")
    ? "from_this_utterance_forward"
    : "speaker_everywhere_in_recording";

export default function TranscriptView({
  segments,
  currentTime,
  onPlaySegment,
  isPlaying,
  onPause,
  onResume,
  speakerMap,
  speakers,
  globalSpeakers,
  onRenameSpeaker,
  onUpdateSegmentSpeaker,
  onUpdateSegmentText,
  onFindAndReplace,
  speakerColors,
  onUndo,
  onRedo,
  canUndo,
  canRedo,
  onExport,
  readOnly = false,
  allowProvisionalEdits = false,
  disableSegmentPlayback = false,
  emptyStateTitle = "No transcript segments",
  emptyStateDescription,
  trimStartS,
  trimEndS,
  onActiveEditUtteranceChange,
  pendingRemoteUtteranceIds = [],
}: TranscriptViewProps) {
  const activeSegmentRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const scrollAnchorRef = useRef<{
    segmentId: string;
    offset: number;
    orderIndex: number;
  } | null>(null);
  const { addNotification } = useNotificationStore();

  // Editing State
  const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null);
  const [editingSegmentSpeakerId, setEditingSegmentSpeakerId] = useState<
    string | null
  >(null);
  const [editingTextId, setEditingTextId] = useState<string | null>(null);

  // Popover State
  const [activePopover, setActivePopover] = useState<{
    segmentId: string;
    target: HTMLElement;
  } | null>(null);

  const [editValue, setEditValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Find & Replace State
  const [showSearch, setShowSearch] = useState(false);
  const [showReplace, setShowReplace] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [isFuzzy, setIsFuzzy] = useState(false);
  const [useRegex, setUseRegex] = useState(false);

  // Search Matches State
  const [matches, setMatches] = useState<
    {
      segmentId: string;
      orderIndex: number;
      startIndex: number;
      length: number;
    }[]
  >([]);
  const [currentMatchIndex, setCurrentMatchIndex] = useState(-1);
  const prevFindTextRef = useRef(findText);
  const pendingRemoteUtteranceIdSet = useMemo(
    () => new Set(pendingRemoteUtteranceIds),
    [pendingRemoteUtteranceIds],
  );
  const exportDisabled = Boolean(
    editingSpeaker || editingSegmentSpeakerId || editingTextId || isSubmitting,
  );
  const exportTitle = exportDisabled
    ? "Finish the current transcript edit before exporting"
    : "Export";

  useEffect(() => {
    if (!onActiveEditUtteranceChange) {
      return;
    }

    const activeEditKey = editingTextId || editingSegmentSpeakerId;
    if (!activeEditKey) {
      onActiveEditUtteranceChange(null);
      return;
    }

    const activeSegment = segments.find((segment, index) => {
      return getTranscriptSegmentKey(segment, index) === activeEditKey;
    });

    onActiveEditUtteranceChange(activeSegment?.id || null);
  }, [
    editingSegmentSpeakerId,
    editingTextId,
    onActiveEditUtteranceChange,
    segments,
  ]);

  // Calculate matches when findText or segments change
  useEffect(() => {
    if (!findText.trim() || !showSearch) {
      setMatches([]);
      setCurrentMatchIndex(-1);
      return;
    }

    const newMatches: {
      segmentId: string;
      orderIndex: number;
      startIndex: number;
      length: number;
    }[] = [];

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
        if (a.orderIndex !== b.orderIndex)
          return a.orderIndex - b.orderIndex;
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
      const element = document.getElementById(`segment-${match.segmentId}`);
      if (element) {
        element.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }
  }, [currentMatchIndex, matches]);

  const nextMatch = () => {
    if (matches.length === 0) return;
    setCurrentMatchIndex((prev) => (prev + 1) % matches.length);
  };

  const prevMatch = () => {
    if (matches.length === 0) return;
    setCurrentMatchIndex(
      (prev) => (prev - 1 + matches.length) % matches.length,
    );
  };

  const renderHighlightedText = (text: string, segmentId: string) => {
    if (!findText || !showSearch || matches.length === 0) return text;

    const segmentMatches = matches.filter((m) => m.segmentId === segmentId);
    if (segmentMatches.length === 0) return text;

    let lastIndex = 0;
    const parts = [];

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

  const getSpeakerColor = (speakerLabel: string) => {
    // Get the color key from speakerColors, default to 'gray' if not found
    const colorKey = speakerColors[speakerLabel] || "gray";
    const colorOption = getColorByKey(colorKey);
    // Return combined bg, border classes for the chat bubble
    return `${colorOption.bg} ${colorOption.border}`;
  };

  const activeSegmentIndex = segments.findIndex(
    (s) => currentTime >= s.start && currentTime < s.end,
  );
  const activeSegmentKey =
    activeSegmentIndex >= 0
      ? getTranscriptSegmentKey(segments[activeSegmentIndex], activeSegmentIndex)
      : null;

  const isRecentlyUpdatedSegment = useCallback((segment: TranscriptSegment) => {
    return (
      typeof segment.updated_at === "string" &&
      Date.now() - new Date(segment.updated_at).getTime() < 15000
    );
  }, []);

  const updateScrollAnchor = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) {
      return;
    }

    const segmentElements = Array.from(
      container.querySelectorAll<HTMLElement>("[data-segment-id]"),
    );
    if (segmentElements.length === 0) {
      scrollAnchorRef.current = null;
      return;
    }

    const firstVisible =
      segmentElements.find(
        (element) => element.offsetTop + element.offsetHeight > container.scrollTop,
      ) || segmentElements[segmentElements.length - 1];
    const segmentId = firstVisible?.dataset.segmentId;
    if (!firstVisible || !segmentId) {
      return;
    }

    scrollAnchorRef.current = {
      segmentId,
      offset: firstVisible.offsetTop - container.scrollTop,
      orderIndex: Number(firstVisible.dataset.orderIndex ?? 0),
    };
  }, []);

  useEffect(() => {
    if (activeSegmentRef.current) {
      activeSegmentRef.current.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }
  }, [activeSegmentKey]);

  useLayoutEffect(() => {
    const container = scrollContainerRef.current;
    const anchor = scrollAnchorRef.current;
    if (!container || !anchor) {
      return;
    }

    const segmentElements = Array.from(
      container.querySelectorAll<HTMLElement>("[data-segment-id]"),
    );
    if (segmentElements.length === 0) {
      return;
    }

    const anchorElement =
      segmentElements.find((element) => element.dataset.segmentId === anchor.segmentId) ||
      segmentElements.find(
        (element) => Number(element.dataset.orderIndex ?? -1) >= anchor.orderIndex,
      ) ||
      segmentElements[segmentElements.length - 1];
    if (!anchorElement) {
      return;
    }

    const nextScrollTop = Math.max(0, anchorElement.offsetTop - anchor.offset);
    if (Math.abs(container.scrollTop - nextScrollTop) > 1) {
      container.scrollTop = nextScrollTop;
    }
  }, [segments]);

  useEffect(() => {
    updateScrollAnchor();
  }, [segments, updateScrollAnchor]);

  const handleSpeakerRenameSubmit = async () => {
    if (editingSpeaker && editValue.trim()) {
      setIsSubmitting(true);
      try {
        await onRenameSpeaker(editingSpeaker, editValue.trim());
      } finally {
        setIsSubmitting(false);
        setEditingSpeaker(null);
      }
    } else {
      setEditingSpeaker(null);
    }
  };

  const handleSegmentSpeakerSubmit = async (segment: TranscriptSegment) => {
    if (editValue.trim() && !isSubmitting) {
      setIsSubmitting(true);
      try {
        await onUpdateSegmentSpeaker(segment, {
          name: editValue.trim(),
          scope: getDefaultSpeakerCorrectionScope(segment.speaker),
        });
      } finally {
        setIsSubmitting(false);
        setEditingSegmentSpeakerId(null);
      }
    } else if (!editValue.trim()) {
      setEditingSegmentSpeakerId(null);
    }
  };

  const handleTextClick = (
    segment: TranscriptSegment,
    segmentId: string,
    e: React.MouseEvent,
  ) => {
    e.stopPropagation();
    if (readOnly || (segment.provisional === true && !allowProvisionalEdits)) return;
    setEditingTextId(segmentId);
    setEditValue(segment.text);
    setEditingSpeaker(null);
    setEditingSegmentSpeakerId(null);
  };

  const handleTextSubmit = async (segment: TranscriptSegment) => {
    if (editValue !== segment.text && !isSubmitting) {
      setIsSubmitting(true);
      try {
        await onUpdateSegmentText(segment, editValue);
      } finally {
        setIsSubmitting(false);
        setEditingTextId(null);
      }
    } else {
      setEditingTextId(null);
    }
  };

  const handleFindReplaceSubmit = async () => {
    if (!findText || isSubmitting) return;

    if (findText.length > 1000) {
      addNotification({
        type: 'warning',
        message: 'Search pattern is too long (max 1000 characters).',
      });
      return;
    }

    setIsSubmitting(true);
    try {
      await onFindAndReplace(findText, replaceText, {
        caseSensitive,
        useRegex,
      });
      setFindText("");
      setReplaceText("");
      setShowReplace(false);
      setShowSearch(false);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleReplaceCurrent = async () => {
    if (matches.length === 0 || currentMatchIndex === -1 || isSubmitting)
      return;

    const match = matches[currentMatchIndex];
    const segment = segments[match.orderIndex];
    if (!segment) {
      return;
    }

    // Calculate new text
    const prefix = segment.text.substring(0, match.startIndex);
    const suffix = segment.text.substring(match.startIndex + match.length);
    const newText = prefix + replaceText + suffix;

    setIsSubmitting(true);
    try {
      await onUpdateSegmentText(segment, newText);
    } catch (e) {
      console.error("Failed to replace text", e);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleKeyDown = (
    e: React.KeyboardEvent,
    type: "segmentSpeaker" | "text",
    segment: TranscriptSegment,
  ) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (type === "segmentSpeaker") {
        handleSegmentSpeakerSubmit(segment);
      } else if (type === "text") {
        handleTextSubmit(segment);
      }
    } else if (e.key === "Escape") {
      setEditingSpeaker(null);
      setEditingSegmentSpeakerId(null);
      setEditingTextId(null);
    }
  };

  const hasKnownSpeakers = segments.some((s) => s.speaker !== "UNKNOWN");

  // Map to preserve original indices before filtering
  const indexedSegments = segments.map((segment, index) => ({
    segment,
    index,
    segmentId: getTranscriptSegmentKey(segment, index),
  }));

  const speakerFilteredSegments = hasKnownSpeakers
    ? indexedSegments.filter(
        ({ segment }) =>
          segment.speaker !== "UNKNOWN" || segment.provisional === true,
      )
    : indexedSegments;

  // Apply the non-destructive trim window (display only). A NULL bound is
  // unbounded that side; a boundary-straddling segment is kept.
  const trimLowerBound = trimStartS ?? Number.NEGATIVE_INFINITY;
  const trimUpperBound = trimEndS ?? Number.POSITIVE_INFINITY;
  const displaySegments =
    trimStartS == null && trimEndS == null
      ? speakerFilteredSegments
      : speakerFilteredSegments.filter(
          ({ segment }) =>
            segment.end > trimLowerBound && segment.start < trimUpperBound,
        );

  const speakerDisplayOrder = useMemo(() => {
    const order = new Map<string, number>();
    let nextIndex = 0;

    displaySegments.forEach(({ segment }) => {
      [segment.speaker, ...(segment.overlapping_speakers || [])].forEach(
        (speakerLabel) => {
          if (!order.has(speakerLabel)) {
            order.set(speakerLabel, nextIndex);
            nextIndex += 1;
          }
        },
      );
    });

    return order;
  }, [displaySegments]);

  // --- Grouping Logic ---
  const trackGroups: {
    start: number;
    end: number;
    involved: Set<string>;
    items: typeof displaySegments;
  }[] = [];
  let currentGroup: typeof trackGroups[0] | null = null;

  const areSetsEqual = (a: Set<string>, b: Set<string>) =>
    a.size === b.size && [...a].every((value) => b.has(value));

  for (const item of displaySegments) {
    const involvedArray = [
      item.segment.speaker,
      ...(item.segment.overlapping_speakers || []),
    ];
    const involvedSet = new Set(involvedArray);

    if (!currentGroup) {
      currentGroup = {
        start: item.segment.start,
        end: item.segment.end,
        involved: involvedSet,
        items: [item],
      };
    } else {
      const isTimeOverlap = item.segment.start < currentGroup.end;
      const isSameOverlapEvent =
        involvedSet.size > 1 &&
        currentGroup.involved.size > 1 &&
        areSetsEqual(involvedSet, currentGroup.involved);

      if (isTimeOverlap || isSameOverlapEvent) {
        currentGroup!.items.push(item);
        currentGroup!.end = Math.max(currentGroup!.end, item.segment.end);
        involvedSet.forEach((spk) => currentGroup!.involved.add(spk));
      } else {
        trackGroups.push(currentGroup!);
        currentGroup = {
          start: item.segment.start,
          end: item.segment.end,
          involved: involvedSet,
          items: [item],
        };
      }
    }
  }
  if (currentGroup) trackGroups.push(currentGroup);

  const renderSegmentContent = (item: typeof displaySegments[0]) => {
    const { segment, segmentId } = item;
    const isActive = currentTime >= segment.start && currentTime < segment.end;
    const isProvisional = segment.provisional === true;
    const isSegmentReadOnly = readOnly || (isProvisional && !allowProvisionalEdits);
    const speakerName = speakerMap[segment.speaker] || segment.speaker;
    const isEditingSpeaker = editingSpeaker === segment.speaker;
    const isEditingSegmentSpeaker = editingSegmentSpeakerId === segmentId;
    const isEditingText = editingTextId === segmentId;
    const isSpeakerLowConfidence =
      typeof segment.speaker_confidence === "number" &&
      segment.speaker_confidence < 0.6 &&
      !segment.speaker_manually_edited;
    const isRecentlyUpdated = isRecentlyUpdatedSegment(segment);
    const hasPendingRemoteUpdate =
      typeof segment.id === "string" && pendingRemoteUtteranceIdSet.has(segment.id);
    const isStableSpeaker =
      !isProvisional &&
      !isSpeakerLowConfidence &&
      !segment.speaker_manually_edited &&
      segment.speaker_state !== "manual_override" &&
      (segment.speaker_state === "stable" ||
        (segment.state === "stable" && !segment.speaker_state));

    const bubbleColor = isActive
      ? "border-2 border-green-500 dark:border-green-400 bg-green-100 dark:bg-green-900/20"
      : getSpeakerColor(segment.speaker);
    const speakerColorKey = speakerColors[segment.speaker] || "gray";
    const speakerColor = getColorByKey(speakerColorKey);

    return (
      <div
        key={segmentId}
        ref={isActive ? activeSegmentRef : null}
        data-segment-id={segmentId}
        data-order-index={item.index}
        className="flex flex-col mb-3 last:mb-0"
      >
        {/* Speaker Label */}
        <div className="flex flex-wrap items-baseline gap-2 mb-1">
          {isEditingSpeaker ? (
            <input
              autoFocus
              type="text"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={handleSpeakerRenameSubmit}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSpeakerRenameSubmit();
                if (e.key === "Escape") setEditingSpeaker(null);
              }}
              onClick={(e) => e.stopPropagation()}
              className="text-sm font-bold text-blue-600 dark:text-blue-400 bg-white dark:bg-gray-700 border border-blue-300 rounded px-1 py-0.5 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          ) : isEditingSegmentSpeaker ? (
             <input
              autoFocus
              type="text"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={() => handleSegmentSpeakerSubmit(segment)}
              onKeyDown={(e) => handleKeyDown(e, "segmentSpeaker", segment)}
              onClick={(e) => e.stopPropagation()}
              className="text-sm font-bold text-green-600 dark:text-green-400 bg-white dark:bg-gray-700 border border-green-300 rounded px-1 py-0.5 focus:outline-none focus:ring-2 focus:ring-green-500"
            />
          ) : (
            <div className="relative">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  if (isSegmentReadOnly) return;
                  if (activePopover?.segmentId === segmentId) {
                    setActivePopover(null);
                  } else {
                    setActivePopover({
                      segmentId,
                      target: e.currentTarget,
                    });
                  }
                }}
                onDoubleClick={(e) => {
                  e.stopPropagation();
                  if (isSegmentReadOnly) return;
                  setEditingSpeaker(segment.speaker);
                  setEditValue(speakerName);
                  setActivePopover(null);
                }}
                disabled={isSegmentReadOnly}
                className={`text-base font-bold transition-colors text-left ${
                  isProvisional ? speakerColor.text : "text-gray-700 dark:text-gray-300"
                } ${
                  isSegmentReadOnly
                    ? "cursor-default"
                    : "hover:text-orange-700 dark:hover:text-orange-400"
                }`}
                title={
                  isSegmentReadOnly
                    ? speakerName
                    : "Click to change speaker, Double-click to rename"
                }
              >
                {speakerName}
              </button>

              {activePopover?.segmentId === segmentId && !isSegmentReadOnly && (
                <SpeakerAssignmentPopover
                  availableSpeakers={speakers}
                  globalSpeakers={globalSpeakers}
                  currentSpeakerLabel={segment.speaker}
                  speakerColors={speakerColors}
                  targetElement={activePopover.target}
                  onSelect={(assignment) => {
                    onUpdateSegmentSpeaker(segment, assignment);
                    setActivePopover(null);
                  }}
                  onClose={() => setActivePopover(null)}
                />
              )}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-1">
            {isStableSpeaker && (
              <span className="rounded-full border border-teal-200 bg-teal-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-teal-700 dark:border-teal-500/20 dark:bg-teal-500/10 dark:text-teal-300">
                Stable speaker
              </span>
            )}
            {!allowProvisionalEdits &&
              (segment.speaker_manually_edited ||
                segment.speaker_state === "manual_override") && (
              <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-blue-700 dark:border-blue-500/20 dark:bg-blue-500/10 dark:text-blue-300">
                Manual speaker
              </span>
            )}
            {segment.text_manually_edited && (
              <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-700 dark:border-slate-500/20 dark:bg-slate-500/10 dark:text-slate-300">
                Manual text
              </span>
            )}
            {isSpeakerLowConfidence && (
              <span className="rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
                Low confidence
              </span>
            )}
            {isRecentlyUpdated && !isEditingText && !isEditingSegmentSpeaker && (
              <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">
                Revised
              </span>
            )}
            {hasPendingRemoteUpdate && (
              <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-700 dark:border-violet-500/20 dark:bg-violet-500/10 dark:text-violet-300">
                Pending update
              </span>
            )}
          </div>
        </div>

        {/* Transcript Text */}
        <div
          id={`segment-${segmentId}`}
          className={`p-3 rounded-2xl rounded-tl-none w-full transition-colors border ${bubbleColor} ${
            isEditingText ? "ring-2 ring-blue-500" : ""
          } ${
            isProvisional
              ? "border-dashed border-2 shadow-sm"
              : ""
          }`}
        >
          {isEditingText ? (
            <textarea
              autoFocus
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={() => handleTextSubmit(segment)}
              onKeyDown={(e) => handleKeyDown(e, "text", segment)}
              className="w-full bg-transparent resize-none outline-none text-gray-900 dark:text-white leading-relaxed"
              rows={Math.max(2, Math.ceil(editValue.length / 80))}
            />
          ) : (
            <p
              className={`leading-relaxed whitespace-pre-wrap text-gray-800 dark:text-gray-200 ${
                isSegmentReadOnly
                  ? ""
                  : "cursor-text hover:text-gray-900 dark:hover:text-white"
              }`}
              onClick={(e) => handleTextClick(segment, segmentId, e)}
              title={isSegmentReadOnly ? undefined : "Click to edit text"}
            >
              {renderHighlightedText(segment.text, segmentId)}
            </p>
          )}
        </div>
      </div>
    );
  };

  return (
    <div id="transcript-view" className="flex flex-col h-full relative min-h-0">
      {/* Toolbar */}
      <div className="bg-gray-50 dark:bg-gray-900/95 border-b-2 border-gray-200 dark:border-gray-700 shadow-md z-10 flex flex-col">
        {/* Row 1: Header & Global Actions */}
        <div className="px-4 md:px-6 py-3 flex items-center justify-end gap-1 md:gap-2 overflow-x-auto">
          <div className="flex items-center gap-1">
            <button
              onClick={onUndo}
              disabled={!canUndo}
              className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              title="Undo"
            >
              <Undo2 className="w-4 h-4" />
            </button>
            <button
              onClick={onRedo}
              disabled={!canRedo}
              className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              title="Redo"
            >
              <Redo2 className="w-4 h-4" />
            </button>
            <div className="w-px h-4 bg-gray-300 dark:bg-gray-700 mx-1" />
            <button
              onClick={onExport}
              disabled={exportDisabled}
              aria-label={exportDisabled ? "Export transcript disabled" : "Export transcript"}
              className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
              title={exportTitle}
            >
              <Download className="w-4 h-4" />
            </button>
            <button
              onClick={() => {
                const newState = !showSearch;
                setShowSearch(newState);
                if (!newState) setShowReplace(false);
              }}
              className={`p-2 rounded-md transition-colors ${showSearch && !showReplace ? "bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400" : "text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-orange-500"}`}
              title="Search"
            >
              <Search className="w-4 h-4" />
            </button>
            <button
              onClick={() => {
                if (showReplace) {
                  setShowReplace(false);
                  setShowSearch(false);
                } else {
                  setShowReplace(true);
                  setShowSearch(true);
                }
              }}
              className={`p-2 rounded-md transition-colors ${showReplace ? "bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400" : "text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-orange-500"}`}
              title="Find & Replace"
            >
              <ArrowRightLeft className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Row 2: Search & Replace Controls */}
        {(showSearch || showReplace) && (
          <div className="px-4 md:px-6 pb-3 flex flex-wrap items-center gap-2 animate-in fade-in slide-in-from-top-2 duration-200 border-t border-gray-400/30 dark:border-gray-700/50 pt-3">
            <div className="relative min-w-48 flex-[1_1_14rem]">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                placeholder="Find..."
                value={findText}
                onChange={(e) => setFindText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    if (e.shiftKey) prevMatch();
                    else nextMatch();
                  }
                }}
                className="w-full pl-8 pr-28 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 focus:ring-2 focus:ring-orange-500 outline-none min-w-0"
                autoFocus
              />
              {matches.length > 0 && (
                <div className="absolute right-1 top-1/2 -translate-y-1/2 flex items-center gap-1 text-xs text-gray-500 whitespace-nowrap bg-white/50 dark:bg-black/50 backdrop-blur-sm rounded px-1">
                  <span>
                    {currentMatchIndex + 1} of {matches.length}
                  </span>
                  <button
                    onClick={prevMatch}
                    className="p-1 hover:bg-gray-200 dark:hover:bg-gray-700 rounded"
                  >
                    <ChevronUp className="w-3 h-3" />
                  </button>
                  <button
                    onClick={nextMatch}
                    className="p-1 hover:bg-gray-200 dark:hover:bg-gray-700 rounded"
                  >
                    <ChevronDown className="w-3 h-3" />
                  </button>
                </div>
              )}
            </div>
            {showReplace && (
              <div className="relative min-w-48 flex-[1_1_14rem]">
                <ArrowRightLeft className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input
                  placeholder="Replace..."
                  value={replaceText}
                  onChange={(e) => setReplaceText(e.target.value)}
                  className="w-full pl-8 pr-2 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 focus:ring-2 focus:ring-orange-500 outline-none min-w-0"
                />
              </div>
            )}
            {showReplace && (
              <div className="flex min-w-0 flex-[1_0_auto] flex-wrap items-center justify-end gap-2">
                {/* Settings Toggle */}
                <div className="relative">
                  <button
                    onClick={() => setShowSettings(!showSettings)}
                    className={`p-1.5 rounded-md transition-colors ${showSettings ? "bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400" : "text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-700"}`}
                    title="Advanced Search Settings"
                  >
                    <Settings className="w-4 h-4" />
                  </button>

                  {/* Settings Dropdown */}
                  {showSettings && (
                    <>
                      <div
                        className="fixed inset-0 z-40"
                        onClick={() => setShowSettings(false)}
                      />
                      <div className="absolute right-0 top-full mt-2 w-48 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 p-2 z-50 flex flex-col gap-1">
                        <div className="text-xs font-semibold text-gray-400 px-2 py-1 mb-1 border-b border-gray-100 dark:border-gray-700">
                          Search Options
                        </div>
                        <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded cursor-pointer">
                          <input
                            type="checkbox"
                            checked={caseSensitive}
                            onChange={(e) => setCaseSensitive(e.target.checked)}
                            className="rounded border-gray-300 text-orange-600 focus:ring-orange-500 w-4 h-4"
                          />
                          <span className="text-sm text-gray-700 dark:text-gray-200">
                            Case Sensitive
                          </span>
                        </label>
                        <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded cursor-pointer">
                          <input
                            type="checkbox"
                            checked={isFuzzy}
                            onChange={(e) => {
                              setIsFuzzy(e.target.checked);
                              if (e.target.checked) setUseRegex(false);
                            }}
                            className="rounded border-gray-300 text-orange-600 focus:ring-orange-500 w-4 h-4"
                          />
                          <span className="text-sm text-gray-700 dark:text-gray-200">
                            Fuzzy Match
                          </span>
                        </label>
                        <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded cursor-pointer">
                          <input
                            type="checkbox"
                            checked={useRegex}
                            onChange={(e) => {
                              setUseRegex(e.target.checked);
                              if (e.target.checked) setIsFuzzy(false);
                            }}
                            className="rounded border-gray-300 text-orange-600 focus:ring-orange-500 w-4 h-4"
                          />
                          <span className="text-sm text-gray-700 dark:text-gray-200">
                            Regex
                          </span>
                        </label>
                      </div>
                    </>
                  )}
                </div>

                <div className="w-px h-4 bg-gray-300 dark:bg-gray-700 mx-1" />

                <button
                  onClick={nextMatch}
                  disabled={matches.length === 0}
                  className="px-3 py-1.5 bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 text-sm rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap shadow-sm border border-gray-200 dark:border-gray-700"
                >
                  Find Next
                </button>
                <button
                  onClick={handleReplaceCurrent}
                  disabled={matches.length === 0 || isSubmitting}
                  className="px-3 py-1.5 bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 text-sm rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap shadow-sm border border-gray-200 dark:border-gray-700"
                >
                  Replace
                </button>
                <button
                  onClick={handleFindReplaceSubmit}
                  disabled={!findText || isSubmitting}
                  className="px-3 py-1.5 bg-orange-600 text-white text-sm rounded-md hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap shadow-sm"
                >
                  Replace All
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      <div
        ref={scrollContainerRef}
        data-testid="transcript-scroll-region"
        className="space-y-4 px-2 md:px-4 py-3 overflow-y-auto flex-1 min-h-0"
        onScroll={updateScrollAnchor}
      >
        {trackGroups.length === 0 ? (
          <div className="flex h-full min-h-[220px] items-center justify-center px-4">
            <div className="flex max-w-sm flex-col items-center text-center">
              <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-orange-200 bg-orange-50 text-orange-600 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
                <Radio className="h-4 w-4" />
              </div>
              <div className="text-sm font-semibold text-gray-800 dark:text-gray-100">
                {emptyStateTitle}
              </div>
              {emptyStateDescription && (
                <div className="mt-1 text-sm leading-5 text-gray-500 dark:text-gray-400">
                  {emptyStateDescription}
                </div>
              )}
            </div>
          </div>
        ) : trackGroups.map((group, groupIndex) => {
          const isGroupActive =
            currentTime >= group.start && currentTime < group.end;
          const groupKey = group.items.map((item) => item.segmentId).join("|");
          const groupHasRecentRevision = group.items.some(({ segment }) =>
            isRecentlyUpdatedSegment(segment),
          );
          
          const involvedSpeakers = Array.from(group.involved).sort((left, right) => {
            const leftOrder = speakerDisplayOrder.get(left) ?? Number.MAX_SAFE_INTEGER;
            const rightOrder = speakerDisplayOrder.get(right) ?? Number.MAX_SAFE_INTEGER;

            if (leftOrder !== rightOrder) {
              return leftOrder - rightOrder;
            }

            return (speakerMap[left] || left).localeCompare(speakerMap[right] || right);
          });

          return (
            <div
              key={groupKey || groupIndex}
              className={`flex gap-3 px-2 group ${isGroupActive ? "opacity-100" : "opacity-90"} transition-opacity`}
            >
              {/* Timestamp & Play Control */}
              <div className="flex flex-col items-end min-w-16 md:min-w-[60px] pt-1 mt-1">
                <span className="text-sm text-gray-400 font-mono mb-1">
                  {formatTime(group.start)}
                </span>
                {!disableSegmentPlayback && (
                  <button
                    onClick={() => {
                      if (isGroupActive) {
                        if (isPlaying) onPause();
                        else onResume();
                      } else {
                        onPlaySegment(group.start, group.end);
                      }
                    }}
                    className={`p-2 md:p-1.5 rounded-full transition-colors shadow-sm ${
                      isGroupActive
                        ? "bg-green-500 text-white hover:bg-green-600"
                        : "bg-gray-100 text-gray-500 hover:bg-orange-600 hover:text-white dark:bg-gray-800 dark:text-gray-400"
                    }`}
                    title={
                      isGroupActive && isPlaying ? "Pause segment" : "Play segment"
                    }
                  >
                    {isGroupActive && isPlaying ? (
                      <Pause className="w-5 h-5 md:w-3 md:h-3 fill-current" />
                    ) : (
                      <Play className="w-5 h-5 md:w-3 md:h-3 fill-current" />
                    )}
                  </button>
                )}
              </div>

              {/* Content */}
              <div className="flex-1 min-w-0">
                {involvedSpeakers.length > 1 ? (
                  <div className="grid gap-3 md:gap-4 w-full border border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/20 p-3 md:p-4 rounded-xl shadow-[inset_0_1px_4px_rgba(0,0,0,0.02)] dark:shadow-[inset_0_1px_4px_rgba(0,0,0,0.2)] md:grid-cols-[repeat(auto-fit,minmax(0,1fr))]">
                     {involvedSpeakers.map(speaker => {
                         const speakerItems = group.items.filter(item => item.segment.speaker === speaker);
                     const laneColorKey = speakerColors[speaker] || "gray";
                     const laneColor = getColorByKey(laneColorKey);
                         const laneMinHeightRem = groupHasRecentRevision
                           ? Math.max(7, speakerItems.length * 4.5)
                           : undefined;
                         return (
                             <div
                               key={speaker}
                               data-testid={`overlap-lane-${speaker}`}
                               className={`min-w-0 flex flex-col rounded-xl border ${laneColor.border} bg-white/70 dark:bg-gray-900/40`}
                             >
                         <div className={`px-3 py-2 text-xs font-semibold uppercase tracking-[0.16em] ${laneColor.text} border-b border-black/5 dark:border-white/5`}>
                           {speakerMap[speaker] || speaker}
                         </div>
                                 <div
                                   data-testid={`overlap-lane-body-${speaker}`}
                                   className="p-3 flex-1 min-w-0"
                                   style={laneMinHeightRem ? { minHeight: `${laneMinHeightRem}rem` } : undefined}
                                 >
                                 {speakerItems.length > 0 ? (
                                     speakerItems.map(item => renderSegmentContent(item))
                                 ) : (
                                     <div className="h-full w-full min-h-[50px] flex items-center justify-center border border-dashed border-gray-300 dark:border-gray-700/50 rounded-xl bg-white/30 dark:bg-black/10">
                                         <div className="flex flex-col items-center gap-0.5 opacity-60">
                                             <div className="font-semibold text-gray-500 dark:text-gray-400 text-sm">{speakerMap[speaker] || speaker}</div>
                                             <div className="text-[11px] uppercase tracking-wider">(overlapping speech)</div>
                                         </div>
                                     </div>
                                 )}
                                       </div>
                             </div>
                         )
                     })}
                  </div>
                ) : (
                  <div className="flex flex-col w-full">
                     {group.items.map(item => renderSegmentContent(item))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
