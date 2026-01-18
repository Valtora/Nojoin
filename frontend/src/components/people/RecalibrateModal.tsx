"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import {
  X,
  Fingerprint,
  RefreshCw,
  AlertCircle,
  Play,
  Pause,
  Check,
  Ban, // For reject
  Lock,
  Volume2,
  Telescope,
} from "lucide-react";
import { GlobalSpeaker, SpeakerSegment } from "@/types";
import {
  getSpeakerSegments,
  recalibrateSpeaker,
  getRecordingStreamUrl,
  scanMatches,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import SplitPersonModal from "./SplitPersonModal";

interface RecalibrateModalProps {
  isOpen: boolean;
  onClose: () => void;
  speaker: GlobalSpeaker | null;
  onComplete: () => void;
}

type SegmentStatus = "pending" | "approved" | "rejected";

export default function RecalibrateModal({
  isOpen,
  onClose,
  speaker,
  onComplete,
}: RecalibrateModalProps) {
  const { addNotification } = useNotificationStore();
  const [mounted, setMounted] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [segments, setSegments] = useState<SpeakerSegment[]>([]);
  const [segmentStates, setSegmentStates] = useState<
    Record<number, SegmentStatus>
  >({});
  // Split Modal State
  const [isSplitModalOpen, setIsSplitModalOpen] = useState(false);
  const [success, setSuccess] = useState(false);
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
    if (!speaker) return;
    setIsLoading(true);
    try {
      // Fetch safe amount to fill grid (e.g., 9-18 items)
      // Limit to 27 to allow for some cycling/rejecting
      const data = await getSpeakerSegments(speaker.id);
      setSegments(data);
      // Reset states
      setSegmentStates({});
    } catch (error) {
      console.error("Failed to fetch segments", error);
      addNotification({
        type: "error",
        message: "Failed to load speaker segments.",
      });
    } finally {
      setIsLoading(false);
    }
  }, [speaker, addNotification]);

  useEffect(() => {
    if (isOpen && speaker) {
      fetchSegments();
      setSuccess(false);
      setPlayingIndex(null);
    }
  }, [isOpen, speaker, fetchSegments]);

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
    audio.currentTime = segment.start;

    // Play audio
    audio.play().catch((e) => {
      console.error("Audio play error", e);
      addNotification({ type: "error", message: "Failed to play audio." });
    });

    // Stop at end
    const stopTime = segment.end;

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
      if (playingIndex === index) setPlayingIndex(null); // Clears only if currently playing.
      audio.removeEventListener("timeupdate", timeUpdateHandler);
    };

    audioRef.current = audio;
    setPlayingIndex(index);
  };

  const handleStatusChange = (
    index: number,
    status: SegmentStatus,
    e?: React.MouseEvent,
  ) => {
    e?.stopPropagation();
    setSegmentStates((prev) => ({
      ...prev,
      [index]: status === prev[index] ? "pending" : status, // Toggle off if clicked again
    }));
  };

  const activeCount = Object.values(segmentStates).filter(
    (s) => s === "approved",
  ).length;

  const handleRecalibrate = async () => {
    if (!speaker || activeCount === 0) return;

    setIsSubmitting(true);
    try {
      const selectedSegments = segments
        .filter((_, idx) => segmentStates[idx] === "approved")
        .map((s) => ({
          recording_id: s.recording_id,
          start: s.start,
          end: s.end,
        }));

      await recalibrateSpeaker(speaker.id, selectedSegments);
      setSuccess(true);
      addNotification({
        type: "success",
        message:
          "Voiceprint recalibrated successfully! You can now scan for matches.",
      });
      // Do not auto close, let user decide
    } catch (error: any) {
      console.error("Recalibration failed", error);
      addNotification({
        type: "error",
        message:
          error.response?.data?.detail || "Failed to recalibrate voiceprint.",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!isOpen || !mounted || !speaker) return null;

  return createPortal(
    <>
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
        <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden border border-gray-200 dark:border-gray-800">
          {/* Header - Fixed */}
          <div className="px-6 py-5 border-b border-gray-200 dark:border-gray-800 bg-gray-50/80 dark:bg-gray-900/80 backdrop-blur-md z-10 flex justify-between items-start">
            <div>
              <h2 className="text-xl font-bold text-gray-900 dark:text-white flex items-center gap-2">
                <RefreshCw className="w-6 h-6 text-blue-600 dark:text-blue-500" />
                Voiceprint Trainer
              </h2>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Only approve{" "}
                <span className="font-semibold text-gray-900 dark:text-gray-200">
                  clear, isolated speech
                </span>{" "}
                samples. Aim for{" "}
                <span className="font-semibold text-blue-600 dark:text-blue-400">
                  3-5 samples
                </span>{" "}
                to create a &quot;Gold Standard&quot; voiceprint.
              </p>
            </div>
            <button
              onClick={onClose}
              className="p-2 -mr-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded-full hover:bg-gray-200 dark:hover:bg-gray-800 transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Content - Scrollable */}
          <div className="flex-1 overflow-y-auto p-6 scrollbar-thin bg-gray-50 dark:bg-black/20">
            {success ? (
              <div className="flex flex-col items-center justify-center h-full py-12 animate-in fade-in zoom-in duration-300">
                <div className="w-20 h-20 bg-green-100 dark:bg-green-900/30 rounded-full flex items-center justify-center mb-6 shadow-sm">
                  <Lock className="w-10 h-10 text-green-600 dark:text-green-400" />
                </div>
                <h3 className="text-2xl font-bold text-gray-900 dark:text-white mb-2">
                  Training Complete
                </h3>
                <p className="text-gray-500 dark:text-gray-400 max-w-sm text-center mb-8">
                  {speaker.name}&apos;s voiceprint has been recalibrated and
                  locked to prevent automatic degradation.
                </p>

                <div className="flex flex-col gap-3 w-full max-w-xs">
                  <button
                    onClick={async () => {
                      setIsSubmitting(true);
                      try {
                        const res = await scanMatches(speaker.id);
                        addNotification({
                          type: "success",
                          message: `Found and linked ${res.matches_found} matches in ${res.recordings_updated} recordings.`,
                        });
                        onComplete();
                        onClose();
                      } catch (e) {
                        console.error(e);
                        addNotification({
                          type: "error",
                          message: "Scan failed.",
                        });
                        setIsSubmitting(false);
                      }
                    }}
                    disabled={isSubmitting}
                    className="w-full px-4 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-xl shadow-lg shadow-blue-500/20 flex items-center justify-center gap-2 transition-all hover:scale-[1.02]"
                  >
                    {isSubmitting ? (
                      <>Scanning Library...</>
                    ) : (
                      <>
                        <Telescope className="w-5 h-5" />
                        Scan Library for Matches
                      </>
                    )}
                  </button>
                  <button
                    onClick={() => {
                      onComplete();
                      onClose();
                    }}
                    disabled={isSubmitting}
                    className="w-full px-4 py-3 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                  >
                    Done
                  </button>
                </div>
              </div>
            ) : isLoading ? (
              <div className="flex flex-col items-center justify-center h-64">
                <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 mb-4"></div>
                <p className="text-gray-500 animate-pulse">
                  Finding audio segments...
                </p>
              </div>
            ) : segments.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-64 text-center">
                <AlertCircle className="w-12 h-12 text-gray-300 mb-3" />
                <p className="text-gray-500 font-medium">
                  No sufficient audio segments found.
                </p>
                <p className="text-sm text-gray-400 mt-1">
                  Try processing more recordings first.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {segments.map((seg, idx) => {
                  const status = segmentStates[idx] || "pending";
                  const isPlaying = playingIndex === idx;

                  return (
                    <div
                      key={`${seg.recording_id}-${idx}`}
                      className={`
                      relative group flex flex-col rounded-xl border-2 transition-all duration-200 overflow-hidden bg-white dark:bg-gray-800
                      ${
                        status === "approved"
                          ? "border-green-500 shadow-md ring-1 ring-green-500/20"
                          : status === "rejected"
                            ? "border-gray-200 dark:border-gray-700 opacity-60 grayscale-[0.5] scale-[0.98]"
                            : "border-transparent shadow-sm hover:shadow-md hover:border-blue-200 dark:hover:border-blue-800"
                      }
                    `}
                    >
                      {/* Header / Info */}
                      <div className="p-4 flex-1">
                        <div className="mb-3 flex items-center justify-between">
                          <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-700/50 text-xs font-medium text-gray-600 dark:text-gray-300">
                            <Volume2 className="w-3 h-3" />
                            {(seg.end - seg.start).toFixed(1)}s
                          </div>
                          {status === "approved" && (
                            <Check className="w-5 h-5 text-green-600 animate-in fade-in zoom-in" />
                          )}
                        </div>
                        <div className="relative">
                          <p className="text-sm text-gray-700 dark:text-gray-300 font-medium line-clamp-3 leading-relaxed">
                            &quot;{seg.text}&quot;
                          </p>
                          {/* Gradient fade for long text */}
                          <div className="absolute inset-x-0 bottom-0 h-4 bg-linear-to-t from-white dark:from-gray-800 to-transparent pointer-events-none" />
                        </div>
                      </div>

                      {/* Controls Footer */}
                      <div className="p-3 bg-gray-50 dark:bg-gray-800/80 border-t border-gray-100 dark:border-gray-700 flex items-center justify-between gap-2">
                        {/* Play Button */}
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handlePlay(idx, seg);
                          }}
                          className={`
                           shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-all
                           ${
                             isPlaying
                               ? "bg-blue-600 text-white shadow-lg scale-105"
                               : "bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-600 hover:bg-blue-50 dark:hover:bg-gray-600 hover:text-blue-600"
                           }
                         `}
                        >
                          {isPlaying ? (
                            <Pause className="w-4 h-4 fill-current" />
                          ) : (
                            <Play className="w-4 h-4 fill-current ml-0.5" />
                          )}
                        </button>

                        {/* Action Buttons */}
                        <div className="flex items-center gap-1.5  p-1 bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm">
                          <button
                            onClick={(e) =>
                              handleStatusChange(idx, "rejected", e)
                            }
                            className={`p-2 rounded-md transition-colors ${status === "rejected" ? "bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400" : "text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20"}`}
                            title="Reject / Not this speaker"
                          >
                            <Ban className="w-4 h-4" />
                          </button>
                          <div className="w-px h-4 bg-gray-200 dark:bg-gray-700"></div>
                          <button
                            onClick={(e) =>
                              handleStatusChange(idx, "approved", e)
                            }
                            className={`p-2 rounded-md transition-colors ${status === "approved" ? "bg-green-100 text-green-600 dark:bg-green-900/30 dark:text-green-400" : "text-gray-400 hover:text-green-500 hover:bg-green-50 dark:hover:bg-green-900/20"}`}
                            title="Verify / Approve"
                          >
                            <Check className="w-4 h-4" />
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Footer Actions */}
          {!success && (
            <div className="px-6 py-4 bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-800 flex justify-between items-center z-20">
              <div className="flex flex-col items-start gap-1">
                <div className="text-sm text-gray-500 dark:text-gray-400">
                  <span className="font-medium text-gray-900 dark:text-gray-200">
                    {activeCount}
                  </span>{" "}
                  samples selected
                </div>

                {/* Entry Point for Unmerge (Split) */}
                <button
                  onClick={() => setIsSplitModalOpen(true)}
                  className="text-xs text-blue-600 dark:text-blue-400 hover:underline hover:text-blue-700 dark:hover:text-blue-300 font-medium"
                >
                  Not this person? Split into new speaker...
                </button>
              </div>

              <div className="flex gap-3">
                <button
                  onClick={() => {
                    setSegmentStates({});
                  }}
                  disabled={Object.keys(segmentStates).length === 0}
                  className="px-4 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 disabled:opacity-50"
                >
                  Reset
                </button>
                <button
                  onClick={handleRecalibrate}
                  disabled={isSubmitting || activeCount < 1}
                  className={`
                   px-6 py-2 text-sm font-medium text-white rounded-lg flex items-center gap-2 shadow-sm transition-all
                   ${
                     isSubmitting || activeCount < 1
                       ? "bg-gray-300 dark:bg-gray-700 cursor-not-allowed"
                       : "bg-blue-600 hover:bg-blue-700 hover:shadow-md hover:-translate-y-px"
                   }
                 `}
                >
                  {isSubmitting ? (
                    "Training..."
                  ) : (
                    <>
                      <Fingerprint className="w-4 h-4" />
                      Recalibrate
                    </>
                  )}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Split Modal */}
      {isSplitModalOpen && (
        <SplitPersonModal
          isOpen={isSplitModalOpen}
          onClose={() => setIsSplitModalOpen(false)}
          speaker={speaker}
          onComplete={() => {
            setIsSplitModalOpen(false);
            onComplete();
            onClose();
          }}
        />
      )}
    </>,
    document.body,
  );
}
