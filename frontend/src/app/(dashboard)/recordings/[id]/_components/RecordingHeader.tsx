"use client";

import { Edit2 } from "lucide-react";
import type { RefObject } from "react";

import AudioPlayer from "@/components/AudioPlayer";
import RecordingTagEditor from "@/components/RecordingTagEditor";
import LinkedEventPanel from "@/components/LinkedEventPanel";
import { getRecording } from "@/lib/api";
import { Recording, RecordingStatus } from "@/types";

interface RecordingHeaderProps {
  recording: Recording;
  isMobile: boolean;
  isEditingTitle: boolean;
  titleValue: string;
  isMobileHeaderActionsOpen: boolean;
  currentTime: number;
  audioRef: RefObject<HTMLAudioElement | null>;
  setRecording: (recording: Recording) => void;
  setTitleValue: (value: string) => void;
  setIsEditingTitle: (editing: boolean) => void;
  onTitleSubmit: () => void;
  onTimeUpdate: () => void;
  onPlay: () => void;
  onPause: () => void;
}

export default function RecordingHeader({
  recording,
  isMobile,
  isEditingTitle,
  titleValue,
  isMobileHeaderActionsOpen,
  currentTime,
  audioRef,
  setRecording,
  setTitleValue,
  setIsEditingTitle,
  onTitleSubmit,
  onTimeUpdate,
  onPlay,
  onPause,
}: RecordingHeaderProps) {
  const renderMobileHeaderActions = () => (
    <div className="flex flex-wrap items-center gap-2">
      <RecordingTagEditor
        recordingId={recording.id}
        tags={recording.tags || []}
        compact
        onTagsUpdated={() => {
          getRecording(recording.id)
            .then(setRecording)
            .catch(console.error);
        }}
      />
      <LinkedEventPanel
        recordingId={recording.id}
        linkedEvent={recording.calendar_event}
        compact
        onLinkChanged={() => {
          getRecording(recording.id)
            .then(setRecording)
            .catch(console.error);
        }}
      />

    </div>
  );

  return (
    <header className={`sticky top-0 z-10 shrink-0 border-b-2 border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900 ${isMobile ? "space-y-3 px-4 pb-3 pt-[calc(env(safe-area-inset-top)+4.75rem)]" : "space-y-4 p-4 md:p-5 lg:p-6"}`}>
      {isMobile ? (
        <>
          <div className="rounded-2xl border border-gray-200/80 bg-white/90 px-4 py-3 shadow-sm backdrop-blur dark:border-gray-700/80 dark:bg-gray-800/90">
            <div className="min-w-0 pt-0.5">
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-400 dark:text-gray-500">
                Meeting Detail
              </div>
              {isEditingTitle ? (
                <input
                  autoFocus
                  type="text"
                  value={titleValue}
                  onChange={(e) => setTitleValue(e.target.value)}
                  onBlur={onTitleSubmit}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") onTitleSubmit();
                    if (e.key === "Escape") {
                      setIsEditingTitle(false);
                      setTitleValue(recording?.name || "");
                    }
                  }}
                  className="mt-1 w-full border-b-2 border-orange-500 bg-transparent pb-1 text-lg font-bold text-gray-900 focus:outline-none dark:text-white"
                />
              ) : (
                <h1
                  className="mt-1 flex cursor-pointer items-start gap-2 text-lg font-bold text-gray-900 hover:text-orange-600 dark:text-white dark:hover:text-orange-400 group"
                  onClick={() => setIsEditingTitle(true)}
                  title="Click to rename"
                >
                  <span className="min-w-0 break-words">{recording?.name}</span>
                  <Edit2 className="mt-1 h-4 w-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-50" />
                </h1>
              )}
            </div>
          </div>

          {isMobileHeaderActionsOpen && (
            <div className="fixed right-4 top-[calc(env(safe-area-inset-top)+4.5rem)] z-40 w-[min(18rem,calc(100vw-2rem))] rounded-2xl border border-orange-100 bg-orange-50/95 p-2.5 shadow-xl shadow-black/10 backdrop-blur dark:border-orange-500/15 dark:bg-orange-500/10 dark:shadow-black/30">
              {renderMobileHeaderActions()}
            </div>
          )}
        </>
      ) : (
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0 flex-1">
            {isEditingTitle ? (
              <input
                autoFocus
                type="text"
                value={titleValue}
                onChange={(e) => setTitleValue(e.target.value)}
                onBlur={onTitleSubmit}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onTitleSubmit();
                  if (e.key === "Escape") {
                    setIsEditingTitle(false);
                    setTitleValue(recording?.name || "");
                  }
                }}
                className="density-heading-section mb-2 w-full border-b-2 border-orange-500 bg-transparent text-xl font-bold text-gray-900 focus:outline-none dark:text-white md:text-2xl"
              />
            ) : (
              <h1
                className="density-heading-section group mb-2 flex cursor-pointer items-start gap-2 text-xl font-bold text-gray-900 hover:text-orange-600 dark:text-white dark:hover:text-orange-400 md:text-2xl"
                onClick={() => setIsEditingTitle(true)}
                title="Click to rename"
              >
                <span className="min-w-0 break-words md:truncate">
                  {recording?.name}
                </span>
                <Edit2 className="mt-1 h-4 w-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-50" />
              </h1>
            )}

            <div className="flex flex-col items-start gap-2">
              <RecordingTagEditor
                recordingId={recording.id}
                tags={recording.tags || []}
                onTagsUpdated={() => {
                  getRecording(recording.id)
                    .then(setRecording)
                    .catch(console.error);
                }}
              />
              <LinkedEventPanel
                recordingId={recording.id}
                linkedEvent={recording.calendar_event}
                onLinkChanged={() => {
                  getRecording(recording.id)
                    .then(setRecording)
                    .catch(console.error);
                }}
              />
            </div>
          </div>


        </div>
      )}

      {/* Audio Player in Header */}
      {recording &&
        recording.status !== RecordingStatus.PAUSED &&
       recording.status !== RecordingStatus.UPLOADING &&
       recording.status !== RecordingStatus.PROCESSING &&
       recording.status !== RecordingStatus.QUEUED && (
          <AudioPlayer
            recording={recording}
            audioRef={audioRef}
            currentTime={currentTime}
            onTimeUpdate={onTimeUpdate}
            onPlay={onPlay}
            onPause={onPause}
            compact={isMobile}
          />
        )}
    </header>
  );
}
