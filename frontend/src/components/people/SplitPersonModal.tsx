"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import {
  X,
  Split,
  AlertCircle,
  Play,
  Pause,
  Check,
  UserPlus,
  Volume2,
} from "lucide-react";
import { GlobalSpeaker, RecordingId, SpeakerSegment } from "@/types";
import {
  getSpeakerSegments,
  splitSpeaker,
  getRecordingStreamUrl,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { getErrorMessage } from "@/lib/errors";

interface SplitPersonModalProps {
  isOpen: boolean;
  onClose: () => void;
  speaker: GlobalSpeaker | null;
  localSpeaker?: {
    recordingId: RecordingId;
    label: string;
    name: string;
  } | null;
  initialSegments?: SpeakerSegment[];
  onComplete: () => void;
}

type SegmentStatus = "selected" | "unselected";

export default function SplitPersonModal({
  isOpen,
  onClose,
  speaker,
  localSpeaker,
  initialSegments,
  onComplete,
}: SplitPersonModalProps) {
  const { addNotification } = useNotificationStore();
  const [mounted, setMounted] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [segments, setSegments] = useState<SpeakerSegment[]>([]);
  const [segmentStates, setSegmentStates] = useState<
    Record<number, SegmentStatus>
  >({});
  const [newSpeakerName, setNewSpeakerName] = useState("");
  const [success, setSuccess] = useState(false);

  // Audio preview state
  const [playingIndex, setPlayingIndex] = useState<number | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    setMounted(true);
    return () => {
      setMounted(false);
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.src = "";
      }
    };
  }, []);

  const fetchSegments = useCallback(async () => {
    if (initialSegments) {
      setSegments(initialSegments);
      setSegmentStates({});
      setNewSpeakerName("");
      return;
    }

    if (!speaker) return;
    setIsLoading(true);
    try {
      const data = await getSpeakerSegments(speaker.id);
      setSegments(data);
      setSegmentStates({});
      setNewSpeakerName("");

        } catch (error: unknown) {
      console.error("Failed to fetch segments", error);
      addNotification({
        type: "error",
        message: "Failed to load speaker segments.",
      });
    } finally {
      setIsLoading(false);
    }
  }, [speaker, initialSegments, addNotification]);

  useEffect(() => {
    if (isOpen && (speaker || localSpeaker)) {
      fetchSegments();
      setSuccess(false);
      setPlayingIndex(null);
    }
  }, [isOpen, speaker, localSpeaker, fetchSegments]);

  const handlePlay = (index: number, segment: SpeakerSegment) => {
    if (playingIndex === index && audioRef.current) {
      audioRef.current.pause();
      setPlayingIndex(null);
      return;
    }

    if (audioRef.current) {
      audioRef.current.pause();
    }

    const url = getRecordingStreamUrl(segment.recording_id);
    const audio = new Audio(url);
    audio.preload = "auto";

    const stopTime = segment.end;

    const seekAndPlay = () => {
      audio.currentTime = segment.start;
      audio.play().catch((e) => {
        console.error("Audio play error", e);
        addNotification({ type: "error", message: "Failed to play audio." });
      });
    };

    if (audio.readyState >= 1) {
      seekAndPlay();
    } else {
      audio.addEventListener("loadedmetadata", seekAndPlay, { once: true });
    }

    const timeUpdateHandler = () => {
      if (audio.currentTime >= stopTime) {
        audio.pause();
        setPlayingIndex(null);
        audio.removeEventListener("timeupdate", timeUpdateHandler);
      }
    };

    audio.addEventListener("timeupdate", timeUpdateHandler);

    audio.onended = () => {
      setPlayingIndex(null);
      audio.removeEventListener("timeupdate", timeUpdateHandler);
    };

    audio.onpause = () => {
      if (playingIndex === index) setPlayingIndex(null);
      audio.removeEventListener("timeupdate", timeUpdateHandler);
    };

    audioRef.current = audio;
    setPlayingIndex(index);
  };

  const toggleSegment = (index: number) => {
    setSegmentStates((prev) => ({
      ...prev,
      [index]: prev[index] === "selected" ? "unselected" : "selected",
    }));
  };

  const selectedCount = Object.values(segmentStates).filter(
    (s) => s === "selected",
  ).length;

  const handleSplit = async () => {
    if (
      (!speaker && !localSpeaker) ||
      selectedCount === 0 ||
      !newSpeakerName.trim()
    )
      return;

    setIsSubmitting(true);
    try {
      const selectedSegments = segments
        .filter((_, idx) => segmentStates[idx] === "selected")
        .map((s) => ({
          recording_id: s.recording_id,
          start: s.start,
          end: s.end,
        }));

      if (speaker) {
        // Global Split
        await splitSpeaker(speaker.id, newSpeakerName, selectedSegments);
      } else if (localSpeaker) {
        // Local Split
        const { splitLocalSpeaker } = await import("@/lib/api");
        await splitLocalSpeaker(
          localSpeaker.recordingId,
          localSpeaker.label,
          newSpeakerName,
          selectedSegments,
        );
      }

      setSuccess(true);
      addNotification({
        type: "success",
        message: `${newSpeakerName} has been created and segments moved!`,
      });

        } catch (error: unknown) {
      console.error("Split failed", error);
      addNotification({
        type: "error",
        message: getErrorMessage(error, "Failed to split speaker."),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!isOpen || !mounted || (!speaker && !localSpeaker)) return null;
  const currentName = speaker ? speaker.name : localSpeaker?.name;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4">
      <div className="bg-surface-card rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden border border-surface-border">
        {/* Header */}
        <div className="px-6 py-5 border-b border-surface-border bg-surface-inset z-10 flex justify-between items-start">
          <div>
            <h2 className="text-xl font-bold text-foreground flex items-center gap-2">
              <Split className="w-6 h-6 text-action-text" />
              Split / Unmerge Speaker
            </h2>
            <p className="mt-1 text-sm text-contrast-helper">
              Select clips that belong to a <b>different person</b> to separate
              them from {currentName}.
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-2 -mr-2 text-contrast-icon-muted hover:text-contrast-helper hover:text-contrast-icon-muted rounded-full hover:bg-surface-inset transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 scrollbar-thin bg-surface-inset">
          {success ? (
            <div className="flex flex-col items-center justify-center h-full py-12 animate-in fade-in zoom-in duration-300">
              <div className="w-20 h-20 bg-status-success-bg rounded-full flex items-center justify-center mb-6 shadow-sm">
                <Check className="w-10 h-10 text-status-success-fg" />
              </div>
              <h3 className="text-2xl font-bold text-foreground mb-2">
                Split Complete
              </h3>
              <p className="text-contrast-helper max-w-sm text-center mb-8">
                <b>{newSpeakerName}</b> has been created and the selected
                segments have been moved. Both speakers have been recalibrated.
              </p>
              <button
                onClick={() => {
                  onComplete();
                  onClose();
                }}
                className="px-6 py-3 bg-status-info-bg hover:bg-status-info-bg text-foreground rounded-xl shadow-lg transition-transform hover:scale-[1.02]"
              >
                Done
              </button>
            </div>
          ) : isLoading ? (
            <div className="flex flex-col items-center justify-center h-64">
              <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-action mb-4"></div>
              <p className="text-contrast-helper animate-pulse">
                Finding audio segments...
              </p>
            </div>
          ) : segments.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 text-center">
              <AlertCircle className="w-12 h-12 text-contrast-icon-muted mb-3" />
              <p className="text-contrast-helper font-medium">
                No sufficient audio segments found for this speaker.
              </p>
            </div>
          ) : (
            <>
              {/* Name Input */}
              <div className="mb-6 bg-surface-card p-4 rounded-xl border border-surface-border shadow-sm">
                <label className="block text-sm font-medium text-contrast-muted mb-2">
                  New Speaker Name <span className="text-danger-text">*</span>
                </label>
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <UserPlus className="absolute left-3 top-2.5 w-5 h-5 text-contrast-icon-muted" />
                    <input
                      type="text"
                      className="w-full pl-10 pr-4 py-2 bg-surface-inset border border-surface-border rounded-lg focus:outline-hidden focus:ring-2 focus-visible:outline-focus-ring focus:ring-action-border focus:border-transparent transition-all"
                      placeholder="e.g. Jane Doe"
                      value={newSpeakerName}
                      onChange={(e) => setNewSpeakerName(e.target.value)}
                    />
                  </div>
                </div>
              </div>

              {/* Grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {segments.map((seg, idx) => {
                  const isSelected = segmentStates[idx] === "selected";
                  const isPlaying = playingIndex === idx;

                  return (
                    <div
                      key={`${seg.recording_id}-${idx}`}
                      onClick={() => toggleSegment(idx)}
                      className={`
                        relative group flex flex-col rounded-xl border-2 transition-all duration-200 overflow-hidden cursor-pointer
                        ${
                          isSelected
                            ? "border-action bg-action-tint shadow-md ring-1 ring-action"
                            : "bg-surface-card border-transparent shadow-sm hover:shadow-md hover:border-control-border"
                        }
                      `}
                    >
                      <div className="p-4 flex-1">
                        <div className="mb-3 flex items-center justify-between">
                          <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-surface-card text-xs font-medium text-contrast-helper">
                            <Volume2 className="w-3 h-3" />
                            {(seg.end - seg.start).toFixed(1)}s
                          </div>
                          {isSelected && (
                            <div className="w-5 h-5 bg-action rounded-full flex items-center justify-center animate-in zoom-in duration-200">
                              <Check className="w-3 h-3 text-foreground" />
                            </div>
                          )}
                        </div>
                        <p className="text-sm text-contrast-muted line-clamp-3 leading-relaxed">
                          &quot;{seg.text}&quot;
                        </p>
                      </div>

                      <div className="p-3 border-t border-surface-border flex items-center justify-between">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handlePlay(idx, seg);
                          }}
                          className={`
                             w-8 h-8 rounded-full flex items-center justify-center transition-all
                             ${
                               isPlaying
                                 ? "bg-action text-action-on shadow-md scale-105"
                                 : "bg-surface-inset text-contrast-helper hover:bg-action-tint hover:text-action-text"
                             }
                           `}
                        >
                          {isPlaying ? (
                            <Pause className="w-3.5 h-3.5 fill-current" />
                          ) : (
                            <Play className="w-3.5 h-3.5 fill-current ml-0.5" />
                          )}
                        </button>
                        <span
                          className={`text-xs font-medium transition-colors ${isSelected ? "text-action-text" : "text-contrast-icon-muted"}`}
                        >
                          {isSelected ? "Moving" : "Keep"}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        {!success && (
          <div className="px-6 py-4 bg-surface-card border-t border-surface-border flex justify-between items-center z-20">
            <div className="text-sm text-contrast-helper">
              <span className="font-medium text-foreground text-contrast-icon-muted">
                {selectedCount}
              </span>{" "}
              segments selected to move
            </div>

            <div className="flex gap-3">
              <button
                onClick={onClose}
                className="px-4 py-2 text-sm text-contrast-helper hover:text-foreground hover:text-contrast-icon-muted"
              >
                Cancel
              </button>
              <button
                onClick={handleSplit}
                disabled={
                  isSubmitting || selectedCount === 0 || !newSpeakerName.trim()
                }
                className={`
                   px-6 py-2 text-sm font-medium text-white rounded-lg flex items-center gap-2 shadow-sm transition-all
                   ${
                     isSubmitting ||
                     selectedCount === 0 ||
                     !newSpeakerName.trim()
                       ? "bg-surface-card cursor-not-allowed"
                       : "bg-action hover:bg-action-hover hover:shadow-md hover:-translate-y-px"
                   }
                 `}
              >
                {isSubmitting ? "Processing..." : "Create & Move"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
