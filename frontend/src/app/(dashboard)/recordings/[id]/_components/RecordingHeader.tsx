"use client";

import { ArrowLeft, Edit2, MoreHorizontal } from "lucide-react";
import type { RefObject } from "react";

import IconButton from "@/components/ui/IconButton";

import AudioPlayer from "@/components/AudioPlayer";
import RecordingTagEditor from "@/components/RecordingTagEditor";
import LinkedEventPanel from "@/components/LinkedEventPanel";
import FitText from "@/components/ui/FitText";
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
  setIsMobileHeaderActionsOpen: (
    update: boolean | ((current: boolean) => boolean),
  ) => void;
  onBack: () => void;
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
  setIsMobileHeaderActionsOpen,
  onBack,
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
    <header className={`sticky top-0 z-[var(--z-sticky)] shrink-0 border-b border-surface-border bg-surface-card ${isMobile ? "space-y-3 px-4 pb-3 pt-[calc(env(safe-area-inset-top)+0.75rem)]" : "space-y-3 p-4 md:p-5"}`}>
      {isMobile ? (
        <>
          {/* Back and actions are inline app-bar controls. A fixed overlay
              here gets painted over by this sticky header (equal z-index,
              solid background, later in the DOM), and reserving padding for
              one wastes the top of every phone screen. */}
          <div className="rounded-2xl bg-surface-inset px-2 py-2">
            <div className="flex items-center justify-between gap-1">
              <IconButton
                size="sm"
                icon={<ArrowLeft />}
                aria-label="Back to Recordings"
                title="Back to Recordings"
                onClick={onBack}
              />
              <span className="min-w-0 truncate text-[11px] font-semibold uppercase tracking-[0.18em] text-contrast-icon-muted">
                Meeting Detail
              </span>
              <IconButton
                size="sm"
                icon={<MoreHorizontal />}
                aria-label={
                  isMobileHeaderActionsOpen
                    ? "Hide meeting actions"
                    : "Show meeting actions"
                }
                title={
                  isMobileHeaderActionsOpen
                    ? "Hide meeting actions"
                    : "Show meeting actions"
                }
                onClick={() =>
                  setIsMobileHeaderActionsOpen((current) => !current)
                }
                className={
                  isMobileHeaderActionsOpen
                    ? "bg-action-tint text-action-text"
                    : undefined
                }
              />
            </div>
            <div className="min-w-0 px-2 pb-1">
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
                  className="mt-1 flex cursor-pointer items-start gap-2 font-bold leading-[1.2] text-foreground hover:text-action-text group"
                  onClick={() => setIsEditingTitle(true)}
                  title="Click to rename"
                >
                  <FitText maxRem={1.125} minRem={1} className="flex-1">
                    {recording?.name ?? ""}
                  </FitText>
                  <Edit2 className="mt-1 h-4 w-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-50" />
                </h1>
              )}
            </div>
          </div>

          {isMobileHeaderActionsOpen && (
            <div className="fixed right-4 top-[calc(env(safe-area-inset-top)+4rem)] z-[var(--z-dropdown)] w-[min(18rem,calc(100vw-2rem))] rounded-2xl border border-surface-float-border bg-surface-float p-2.5 shadow-float">
              {renderMobileHeaderActions()}
            </div>
          )}
        </>
      ) : (
        <div className="min-w-0">
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
                className="group mb-2 flex cursor-pointer items-start gap-2 font-bold leading-[1.15] text-foreground hover:text-action-text"
                onClick={() => setIsEditingTitle(true)}
                title="Click to rename"
              >
                <FitText maxRem={1.5} minRem={1.0625} className="flex-1">
                  {recording?.name ?? ""}
                </FitText>
                <Edit2 className="mt-1 h-4 w-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-50" />
              </h1>
            )}

          {/* Tags and the calendar link share one wrapping row to keep the
              header's fixed vertical cost to a single pill line. */}
          <div className="flex flex-wrap items-center gap-2">
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
