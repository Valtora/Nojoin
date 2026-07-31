"use client";

import {
  TranscriptSegment,
  RecordingSpeaker,
  GlobalSpeaker,
  RecordingId,
  TranscriptSpeakerAssignment,
} from "@/types";
import { useCallback, useEffect, useMemo, useState } from "react";
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
import { useNotificationStore } from "@/lib/notificationStore";
import { useTranscriptSearch } from "./transcript/_hooks/useTranscriptSearch";
import { useTranscriptScroll } from "./transcript/_hooks/useTranscriptScroll";
import {
  buildTranscriptGroups,
  indexSegments,
} from "./transcript/transcriptGroups";

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
  onActiveEditUtteranceChange?: (utteranceId: string | null) => void;
  pendingRemoteUtteranceIds?: string[];
}

const formatTime = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return `${minutes.toString().padStart(2, "0")}:${remainingSeconds.toString().padStart(2, "0")}`;
};

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
  onActiveEditUtteranceChange,
  pendingRemoteUtteranceIds = [],
}: TranscriptViewProps) {
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

  // Find & Replace toolbar visibility (search match state lives in the hook)
  const [showSearch, setShowSearch] = useState(false);
  const [showReplace, setShowReplace] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

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

  const activeSegmentIndex = segments.findIndex(
    (s) => currentTime >= s.start && currentTime < s.end,
  );
  const activeSegmentKey =
    activeSegmentIndex >= 0
      ? getTranscriptSegmentKey(segments[activeSegmentIndex], activeSegmentIndex)
      : null;

  const {
    scrollContainerRef,
    activeSegmentRef,
    scrollSegmentIntoView,
    updateScrollAnchor,
  } = useTranscriptScroll({ segments, activeSegmentKey });

  const {
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
    nextMatch,
    prevMatch,
    renderHighlightedText,
    resetFindReplace,
  } = useTranscriptSearch({ segments, showSearch, scrollSegmentIntoView });

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

  const getSpeakerColor = (speakerLabel: string) => {
    // Get the color key from speakerColors, default to 'gray' if not found
    const colorKey = speakerColors[speakerLabel] || "gray";
    const colorOption = getColorByKey(colorKey);
    // Return combined bg, border classes for the chat bubble
    return `${colorOption.bg} ${colorOption.border}`;
  };


  const isRecentlyUpdatedSegment = useCallback((segment: TranscriptSegment) => {
    return (
      typeof segment.updated_at === "string" &&
      Date.now() - new Date(segment.updated_at).getTime() < 15000
    );
  }, []);


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
          scope: "utterance_only",
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
      resetFindReplace();
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

        } catch (e: unknown) {
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
  const indexedSegments = indexSegments(segments);

  const speakerFilteredSegments = hasKnownSpeakers
    ? indexedSegments.filter(
        ({ segment }) =>
          segment.speaker !== "UNKNOWN" || segment.provisional === true,
      )
    : indexedSegments;
  const displaySegments = speakerFilteredSegments;

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
  const trackGroups = buildTranscriptGroups(displaySegments);

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
    const bubbleColor = isActive
      ? "border-2 border-status-success-border bg-status-success-bg"
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
              className="text-sm font-bold text-status-info-fg bg-surface-card border border-status-info-border rounded px-1 py-0.5 focus:outline-none focus:ring-2 focus:ring-status-info-border"
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
              className="text-sm font-bold text-status-success-fg bg-surface-card border border-status-success-border rounded px-1 py-0.5 focus:outline-none focus:ring-2 focus:ring-status-success-border"
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
                  isProvisional ? speakerColor.text : "text-contrast-muted"
                } ${
                  isSegmentReadOnly
                    ? "cursor-default"
                    : "hover:text-action-text"
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
            {segment.text_manually_edited && (
              <span className="rounded-full border border-surface-border bg-surface-inset px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-contrast-muted">
                Manual text
              </span>
            )}
            {isSpeakerLowConfidence && (
              <span className="rounded-full border border-status-danger-border bg-status-danger-bg px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-danger-fg">
                Low confidence
              </span>
            )}
            {isRecentlyUpdated && !isEditingText && !isEditingSegmentSpeaker && (
              <span className="rounded-full border border-status-success-border bg-status-success-bg px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-success-fg">
                Revised
              </span>
            )}
            {hasPendingRemoteUpdate && (
              <span className="rounded-full border border-status-info-border bg-status-info-bg px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-info-fg">
                Pending update
              </span>
            )}
          </div>
        </div>

        {/* Transcript Text */}
        <div
          id={`segment-${segmentId}`}
          className={`p-3 rounded-2xl rounded-tl-none w-full transition-colors border ${bubbleColor} ${
            isEditingText ? "ring-2 ring-status-info-border" : ""
          } ${
            isProvisional
              ? "border-dashed border-2 shadow-card"
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
              className="w-full bg-transparent resize-none outline-none text-foreground leading-relaxed"
              rows={Math.max(2, Math.ceil(editValue.length / 80))}
            />
          ) : (
            <p
              className={`leading-relaxed whitespace-pre-wrap text-contrast-muted ${
                isSegmentReadOnly
                  ? ""
                  : "cursor-text hover:text-foreground"
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
      <div className="z-10 flex flex-col border-b-2 border-surface-border bg-surface-inset shadow-card">
        {/* Row 1: Header & Global Actions */}
        <div className="flex items-center justify-end overflow-x-auto px-2 py-2 sm:px-4 sm:py-2.5 md:px-5 md:py-3">
          <div className="flex items-center gap-0.5 sm:gap-1">
            <button
              onClick={onUndo}
              disabled={!canUndo}
              className="rounded-lg p-1.5 text-contrast-helper transition-colors hover:bg-surface-inset hover:text-contrast-muted disabled:cursor-not-allowed disabled:opacity-30 sm:p-2"
              title="Undo"
            >
              <Undo2 className="w-4 h-4" />
            </button>
            <button
              onClick={onRedo}
              disabled={!canRedo}
              className="rounded-lg p-1.5 text-contrast-helper transition-colors hover:bg-surface-inset hover:text-contrast-muted disabled:cursor-not-allowed disabled:opacity-30 sm:p-2"
              title="Redo"
            >
              <Redo2 className="w-4 h-4" />
            </button>
            <div className="mx-0.5 h-4 w-px bg-surface-card sm:mx-1" />
            <button
              onClick={onExport}
              disabled={exportDisabled}
              aria-label={exportDisabled ? "Export transcript disabled" : "Export transcript"}
              className="rounded-lg p-1.5 text-contrast-helper transition-colors hover:bg-surface-inset hover:text-contrast-muted sm:p-2"
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
              className={`rounded-lg p-1.5 transition-colors sm:p-2 ${showSearch && !showReplace ? "bg-action-tint text-action-text" : "text-contrast-helper hover:bg-surface-inset hover:text-action-text"}`}
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
              className={`rounded-lg p-1.5 transition-colors sm:p-2 ${showReplace ? "bg-action-tint text-action-text" : "text-contrast-helper hover:bg-surface-inset hover:text-action-text"}`}
              title="Find & Replace"
            >
              <ArrowRightLeft className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Row 2: Search & Replace Controls */}
        {(showSearch || showReplace) && (
          <div className="animate-in slide-in-from-top-2 flex flex-wrap items-center gap-1.5 border-t border-control-border px-2 pb-2 pt-2 duration-200 sm:gap-2 sm:px-4 sm:pb-3 sm:pt-3 md:px-5">
            <div className="relative min-w-40 flex-[1_1_11rem] sm:min-w-48 sm:flex-[1_1_14rem]">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-contrast-icon-muted" />
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
                className="w-full pl-8 pr-28 py-1.5 text-sm rounded-md border border-control-border bg-surface-inset focus:ring-2 focus:ring-action outline-none min-w-0"
                autoFocus
              />
              {matches.length > 0 && (
                <div className="absolute right-1 top-1/2 -translate-y-1/2 flex items-center gap-1 text-xs text-contrast-helper whitespace-nowrap bg-surface-card rounded px-1">
                  <span>
                    {currentMatchIndex + 1} of {matches.length}
                  </span>
                  <button
                    onClick={prevMatch}
                    className="p-1 hover:bg-surface-inset rounded"
                  >
                    <ChevronUp className="w-3 h-3" />
                  </button>
                  <button
                    onClick={nextMatch}
                    className="p-1 hover:bg-surface-inset rounded"
                  >
                    <ChevronDown className="w-3 h-3" />
                  </button>
                </div>
              )}
            </div>
            {showReplace && (
              <div className="relative min-w-40 flex-[1_1_11rem] sm:min-w-48 sm:flex-[1_1_14rem]">
                <ArrowRightLeft className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-contrast-icon-muted" />
                <input
                  placeholder="Replace..."
                  value={replaceText}
                  onChange={(e) => setReplaceText(e.target.value)}
                  className="w-full pl-8 pr-2 py-1.5 text-sm rounded-md border border-control-border bg-surface-inset focus:ring-2 focus:ring-action outline-none min-w-0"
                />
              </div>
            )}
            {showReplace && (
              <div className="flex min-w-0 flex-[1_0_auto] flex-wrap items-center justify-end gap-1.5 sm:gap-2">
                {/* Settings Toggle */}
                <div className="relative">
                  <button
                    onClick={() => setShowSettings(!showSettings)}
                    className={`rounded-md p-1.5 transition-colors ${showSettings ? "bg-action-tint text-action-text" : "text-contrast-helper hover:bg-surface-inset hover:text-contrast-muted"}`}
                    title="Advanced Search Settings"
                  >
                    <Settings className="w-4 h-4" />
                  </button>

                  {/* Settings Dropdown */}
                  {showSettings && (
                    <>
                      <div
                        className="fixed inset-0 z-[var(--z-dropdown)]"
                        onClick={() => setShowSettings(false)}
                      />
                      <div className="absolute right-0 top-full mt-2 w-48 bg-surface-card rounded-lg shadow-float border border-surface-border p-2 z-[var(--z-dropdown)] flex flex-col gap-1">
                        <div className="text-xs font-semibold text-contrast-icon-muted px-2 py-1 mb-1 border-b border-surface-border">
                          Search Options
                        </div>
                        <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-surface-inset rounded cursor-pointer">
                          <input
                            type="checkbox"
                            checked={caseSensitive}
                            onChange={(e) => setCaseSensitive(e.target.checked)}
                            className="rounded border-control-border text-action-text focus:ring-action w-4 h-4"
                          />
                          <span className="text-sm text-contrast-muted">
                            Case Sensitive
                          </span>
                        </label>
                        <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-surface-inset rounded cursor-pointer">
                          <input
                            type="checkbox"
                            checked={isFuzzy}
                            onChange={(e) => {
                              setIsFuzzy(e.target.checked);
                              if (e.target.checked) setUseRegex(false);
                            }}
                            className="rounded border-control-border text-action-text focus:ring-action w-4 h-4"
                          />
                          <span className="text-sm text-contrast-muted">
                            Fuzzy Match
                          </span>
                        </label>
                        <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-surface-inset rounded cursor-pointer">
                          <input
                            type="checkbox"
                            checked={useRegex}
                            onChange={(e) => {
                              setUseRegex(e.target.checked);
                              if (e.target.checked) setIsFuzzy(false);
                            }}
                            className="rounded border-control-border text-action-text focus:ring-action w-4 h-4"
                          />
                          <span className="text-sm text-contrast-muted">
                            Regex
                          </span>
                        </label>
                      </div>
                    </>
                  )}
                </div>

                <div className="mx-0.5 h-4 w-px bg-surface-card sm:mx-1" />

                <button
                  onClick={nextMatch}
                  disabled={matches.length === 0}
                  className="whitespace-nowrap rounded-md border border-surface-border bg-surface-inset px-2.5 py-1.5 text-xs text-contrast-muted shadow-card hover:bg-surface-inset disabled:cursor-not-allowed disabled:opacity-50 sm:px-3 sm:text-sm"
                >
                  <span className="sm:hidden">Next</span>
                  <span className="hidden sm:inline">Find Next</span>
                </button>
                <button
                  onClick={handleReplaceCurrent}
                  disabled={matches.length === 0 || isSubmitting}
                  className="whitespace-nowrap rounded-md border border-surface-border bg-surface-inset px-2.5 py-1.5 text-xs text-contrast-muted shadow-card hover:bg-surface-inset disabled:cursor-not-allowed disabled:opacity-50 sm:px-3 sm:text-sm"
                >
                  Replace
                </button>
                <button
                  onClick={handleFindReplaceSubmit}
                  disabled={!findText || isSubmitting}
                  className="whitespace-nowrap rounded-md bg-action px-2.5 py-1.5 text-xs text-action-on shadow-card hover:bg-action-hover disabled:cursor-not-allowed disabled:opacity-50 sm:px-3 sm:text-sm"
                >
                  <span className="sm:hidden">All</span>
                  <span className="hidden sm:inline">Replace All</span>
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
              <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-action-border bg-action-tint text-action-text">
                <Radio className="h-4 w-4" />
              </div>
              <div className="text-sm font-semibold text-foreground">
                {emptyStateTitle}
              </div>
              {emptyStateDescription && (
                <div className="mt-1 text-sm leading-5 text-contrast-helper">
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
                <span className="text-sm text-contrast-icon-muted font-mono mb-1">
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
                    className={`p-2 md:p-1.5 rounded-full transition-colors shadow-card ${
                      isGroupActive
                        ? "bg-status-success-bg text-foreground hover:bg-status-success-bg"
                        : "bg-surface-inset text-contrast-helper hover:bg-action hover:text-action-on"
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
                  <div className="grid gap-3 md:gap-4 w-full border border-surface-border bg-surface-inset p-3 md:p-4 rounded-xl  md:grid-cols-[repeat(auto-fit,minmax(0,1fr))]">
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
                               className={`min-w-0 flex flex-col rounded-xl border ${laneColor.border} bg-surface-card`}
                             >
                         <div className={`px-3 py-2 text-xs font-semibold uppercase tracking-[0.16em] ${laneColor.text} border-b border-surface-divider`}>
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
                                     <div className="h-full w-full min-h-[50px] flex items-center justify-center border border-dashed border-control-border rounded-xl bg-surface-card">
                                         <div className="flex flex-col items-center gap-0.5 opacity-60">
                                             <div className="font-semibold text-contrast-helper text-sm">{speakerMap[speaker] || speaker}</div>
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
