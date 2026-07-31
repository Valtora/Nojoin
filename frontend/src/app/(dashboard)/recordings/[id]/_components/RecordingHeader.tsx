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
    <header className={`sticky top-0 z-[var(--z-sticky)] shrink-0 border-b border-surface-border bg-surface-card ${isMobile ? "space-y-3 px-4 pb-3 pt-[calc(env(safe-area-inset-top)+4.75rem)]" : "space-y-4 p-4 md:p-5 lg:p-6"}`}>
      {isMobile ? (
        <>
          <div className="rounded-2xl border border-surface-border bg-surface-card px-4 py-3 shadow-card">
            <div className="min-w-0 pt-0.5">
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-contrast-icon-muted">
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
                  className="mt-1 w-full border-b-2 border-action bg-transparent pb-1 text-lg font-bold text-foreground focus:outline-none"
                />
              ) : (
                <h1
                  className="mt-1 flex cursor-pointer items-start gap-2 text-lg font-bold text-foreground hover:text-action-text group"
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
            <div className="fixed right-4 top-[calc(env(safe-area-inset-top)+4.5rem)] z-40 w-[min(18rem,calc(100vw-2rem))] rounded-2xl border border-action-border bg-action-tint p-2.5 shadow-float">
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
                className="density-heading-section mb-2 w-full border-b-2 border-action bg-transparent text-xl font-bold text-foreground focus:outline-none md:text-2xl"
              />
            ) : (
              <h1
                className="density-heading-section group mb-2 flex cursor-pointer items-start gap-2 text-xl font-bold text-foreground hover:text-action-text md:text-2xl"
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
