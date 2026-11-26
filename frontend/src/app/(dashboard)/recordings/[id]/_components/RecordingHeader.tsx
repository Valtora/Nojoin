"use client";

import { InputBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { ArrowLeft, Edit2, LogOut, MoreHorizontal, Share2 } from "lucide-react";
import { useState, type ReactNode, type RefObject } from "react";
import { useRouter } from "next/navigation";

import IconButton from "@/components/ui/IconButton";

import AudioPlayer from "@/components/AudioPlayer";
import RecordingTagEditor from "@/components/RecordingTagEditor";
import LinkedEventPanel from "@/components/LinkedEventPanel";
import FitText from "@/components/ui/FitText";
import ShareRecordingDialog from "@/components/ShareRecordingDialog";
import Button from "@/components/ui/Button";
import { leaveShare, requestRestore } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { isImeComposing } from "@/lib/imeComposition";
import { useNotificationStore } from "@/lib/notificationStore";
import {
  canEditRecording,
  isPutAwayByOwner,
  ownerLabel,
  ownsRecording,
} from "@/lib/sharing/access";
import { Recording, RecordingStatus } from "@/types";

interface RecordingHeaderProps {
  recording: Recording;
  playerSlot?: ReactNode;
  isMobile: boolean;
  isEditingTitle: boolean;
  titleValue: string;
  isMobileHeaderActionsOpen: boolean;
  currentTime: number;
  audioRef: RefObject<HTMLAudioElement | null>;
  onRefresh: () => void;
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
  playerSlot,
  isMobile,
  isEditingTitle,
  titleValue,
  isMobileHeaderActionsOpen,
  currentTime,
  audioRef,
  onRefresh,
  setTitleValue,
  setIsEditingTitle,
  setIsMobileHeaderActionsOpen,
  onBack,
  onTitleSubmit,
  onTimeUpdate,
  onPlay,
  onPause,
}: RecordingHeaderProps) {
  const router = useRouter();
  const { addNotification } = useNotificationStore();
  const [shareOpen, setShareOpen] = useState(false);
  const owner = ownsRecording(recording);
  const canEdit = canEditRecording(recording);
  // Renaming changes the meeting for everyone it is shared with.
  const startRename = () => {
    if (canEdit) setIsEditingTitle(true);
  };

  const handleLeave = async () => {
    try {
      await leaveShare(recording.id);
      router.push("/");
    } catch (error: unknown) {
      addNotification({
        type: "error",
        message: getErrorMessage(
          error,
          "This meeting was shared with a group you are in, so only its owner can remove you.",
        ),
      });
    }
  };

  const sharingControl = owner ? (
    <Button
      variant="secondary"
      size="sm"
      onClick={() => setShareOpen(true)}
      iconLeft={<Share2 aria-hidden="true" className="h-4 w-4" />}
    >
      Share
    </Button>
  ) : (
    <span className="inline-flex items-center gap-2 text-sm text-contrast-helper">
      <span>
        Shared by {ownerLabel(recording)}
        {recording.access === "view" ? " · view only" : ""}
      </span>
      {/* Shared through a group, it is the group's: only the owner removes it. */}
      {recording.can_leave ? (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void handleLeave()}
          iconLeft={<LogOut aria-hidden="true" className="h-4 w-4" />}
        >
          Leave
        </Button>
      ) : null}
    </span>
  );

  const [askingRestore, setAskingRestore] = useState(false);
  const askToRestore = async () => {
    setAskingRestore(true);
    try {
      const { already_asked } = await requestRestore(recording.id);
      addNotification({
        type: "success",
        message: already_asked
          ? `You already asked ${ownerLabel(recording)}. It is still in their tasks.`
          : `Asked ${ownerLabel(recording)}. It is in their tasks now.`,
      });
    } catch (error: unknown) {
      addNotification({
        type: "error",
        message: getErrorMessage(error, "Could not send the request."),
      });
    } finally {
      setAskingRestore(false);
    }
  };

  // The owner archived or binned a meeting shared with the caller, who can
  // only read it now and can ask for it back.
  const putAwayNotice = isPutAwayByOwner(recording) ? (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-status-warning-border bg-status-warning-bg px-3 py-2 text-sm text-status-warning-fg">
      <p className="min-w-0 flex-1">
        {ownerLabel(recording)} {recording.is_deleted ? "moved this meeting to the bin" : "archived this meeting"}, so you can only read it.
      </p>
      <Button
        variant="secondary"
        size="sm"
        loading={askingRestore}
        onClick={() => void askToRestore()}
      >
        {recording.is_deleted ? "Ask to restore it" : "Ask to unarchive it"}
      </Button>
    </div>
  ) : null;

  const renderMobileHeaderActions = () => (
    <div className="flex flex-wrap items-center gap-2">
      <RecordingTagEditor
        recordingId={recording.id}
        tags={recording.tags || []}
        compact
        onTagsUpdated={onRefresh}
      />
      {owner ? (
        <LinkedEventPanel
          recordingId={recording.id}
          linkedEvent={recording.calendar_event}
          compact
          onLinkChanged={onRefresh}
        />
      ) : null}
      {sharingControl}
    </div>
  );

  return (
    <header data-ui-fill="card" data-ui="surface" data-ui-surface="pane" className={`sticky top-0 z-[var(--z-sticky)] shrink-0 border-b border-surface-border bg-surface-card ${isMobile ? "space-y-3 px-4 pb-3 pt-[calc(var(--page-safe-top,env(safe-area-inset-top))+0.75rem)]" : "space-y-3 p-4 md:p-5"}`}>
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
                data-leaves-page
                onClick={onBack}
              />
              <span className="min-w-0 truncate text-[11px] font-semibold uppercase tracking-[0.18em] text-contrast-icon-muted">
                Meeting Detail
              </span>
              <IconButton
                data-ui-fill={(isMobileHeaderActionsOpen) ? "action-tint" : undefined}
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
                <InputBase
                  autoFocus
                  type="text"
                  value={titleValue}
                  onChange={(e) => setTitleValue(e.target.value)}
                  onBlur={onTitleSubmit}
                  onKeyDown={(e) => {
                    if (isImeComposing(e)) return;
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
                  onClick={startRename}
                  title={canEdit ? "Click to rename" : undefined}
                >
                  <FitText maxRem={1.125} minRem={1} className="flex-1">
                    {recording?.name ?? ""}
                  </FitText>
                  {canEdit ? <Edit2 className="mt-1 h-4 w-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-50" /> : null}
                </h1>
              )}
            </div>
          </div>

          {isMobileHeaderActionsOpen && (
            <SurfaceBase data-ui-fill="float" data-ui-border="full" kind="float" className="fixed right-4 top-[calc(var(--page-safe-top,env(safe-area-inset-top))+4rem+var(--capture-bar-h,0px))] z-[var(--z-dropdown)] w-[min(18rem,calc(100vw-2rem))] rounded-2xl border border-surface-float-border bg-surface-float p-2.5 shadow-float">
              {renderMobileHeaderActions()}
            </SurfaceBase>
          )}
        </>
      ) : (
        <div className="min-w-0">
            {isEditingTitle ? (
              <InputBase
                autoFocus
                type="text"
                value={titleValue}
                onChange={(e) => setTitleValue(e.target.value)}
                onBlur={onTitleSubmit}
                onKeyDown={(e) => {
                  if (isImeComposing(e)) return;
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
                onClick={startRename}
                title={canEdit ? "Click to rename" : undefined}
              >
                <FitText maxRem={1.5} minRem={1.0625} className="flex-1">
                  {recording?.name ?? ""}
                </FitText>
                {canEdit ? <Edit2 className="mt-1 h-4 w-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-50" /> : null}
              </h1>
            )}

          {/* Tags and the calendar link share one wrapping row to keep the
              header's fixed vertical cost to a single pill line. */}
          <div className="flex flex-wrap items-center gap-2">
            <RecordingTagEditor
              recordingId={recording.id}
              tags={recording.tags || []}
              onTagsUpdated={onRefresh}
            />
            {owner ? (
              <LinkedEventPanel
                recordingId={recording.id}
                linkedEvent={recording.calendar_event}
                onLinkChanged={onRefresh}
              />
            ) : null}
            {sharingControl}
          </div>
        </div>
      )}

      {putAwayNotice}

      {/* Audio Player in Header */}
      {playerSlot ?? (recording &&
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
        ))}
      {owner ? (
        <ShareRecordingDialog
          recordingId={recording.id}
          recordingName={recording.name}
          open={shareOpen}
          onClose={() => setShareOpen(false)}
        />
      ) : null}
    </header>
  );
}
