"use client";

import { memo, type KeyboardEvent, type MouseEvent } from "react";
import Link from "next/link";
import {
  AlertCircle,
  HelpCircle,
  Loader2,
  MoreHorizontal,
  NotebookPen,
  Pause,
  UploadCloud,
} from "lucide-react";

import { InputBase } from "@/components/ui/nativeBases";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import IconButton from "@/components/ui/IconButton";
import { TOUCH_TARGET_SQUARE } from "@/components/ui/touchTarget";
import { Badge } from "@/components/ui/Badge";
import RecordingAttentionBadge from "@/components/RecordingAttentionBadge";
import RecordingTagDots, { tagDotsLayout } from "./RecordingTagDots";
import type { TouchLongPress } from "./_hooks/useRecordingSelection";
import { cn } from "@/lib/cn";
import { parseServerTimestamp } from "@/lib/serverTimestamp";
import { formatTimeZoneDay, formatTimeZoneLocale } from "@/lib/timezone";
import { Recording, RecordingId, RecordingStatus } from "@/types";

const formatDurationString = (seconds?: number) => {
  if (!seconds) return "0s";
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);

  if (hours > 0) {
    return `${hours}hr ${minutes}${minutes === 1 ? "min" : "mins"}`;
  }
  return `${minutes}${minutes === 1 ? "min" : "mins"}`;
};

// A card's text stops short of the actions button (right-1 and 28px wide, or
// the 44px touch target) and, on a tagged card, of the dots 4px left of it: a
// column of 0.375rem dots, two with a 0.25rem gap, and a second one as wide as
// a two-digit count when its last slot holds one. Index: the number of columns.
const CARD_RIGHT_PADDING = [
  "pr-9 pointer-coarse:pr-[52px]",
  "pr-11.5 pointer-coarse:pr-[62px]",
  "pr-14 pointer-coarse:pr-[72px]",
] as const;
const CARD_RIGHT_PADDING_WITH_COUNT = "pr-17.5 pointer-coarse:pr-[86px]";

const cardRightPadding = (tagCount: number) => {
  const { columns, more } = tagDotsLayout(tagCount);
  return more > 0 ? CARD_RIGHT_PADDING_WITH_COUNT : CARD_RIGHT_PADDING[columns];
};

const formatDate = (dateString: string, timeZone: string) => {
  const date = parseServerTimestamp(dateString);
  if (!date) return "Unknown date";
  return formatTimeZoneDay(date, timeZone);
};

const formatTime = (date: Date, timeZone: string) => {
  return formatTimeZoneLocale(date, timeZone, "time", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
};

const StatusIcon = ({ recording }: { recording: Recording }) => {
  if (recording.import_stalled) {
    return (
      <span className="cursor-help" title="This import did not finish uploading.">
        <AlertCircle className="w-3 h-3 text-status-warning-fg" />
      </span>
    );
  }
  switch (recording.status) {
    case RecordingStatus.PROCESSED:
      return null;
    case RecordingStatus.QUEUED:
      return (
        <span
          className="cursor-help"
          title="Meeting is in queue to be processed..."
        >
          <Loader2 className="w-3 h-3 text-status-info-fg animate-spin" />
        </span>
      );
    case RecordingStatus.PROCESSING:
      return (
        <span
          className="cursor-help"
          title="Processing: transcription, diarization, voiceprints."
        >
          <Loader2 className="w-3 h-3 text-status-info-fg animate-spin" />
        </span>
      );
    case RecordingStatus.UPLOADING:
      return <UploadCloud className="w-3 h-3 text-action-text animate-pulse" />;
    case RecordingStatus.PAUSED:
      return <Pause className="w-3 h-3 text-status-warning-fg" />;
    case RecordingStatus.ERROR:
      return (
        <span
          role="img"
          aria-label="Processing failed"
          className="cursor-help"
          title="Processing failed. Open the recording to see why and retry."
        >
          <AlertCircle className="w-3 h-3 text-danger-text" />
        </span>
      );
    default:
      return <HelpCircle className="w-3 h-3 text-rail-fg-muted" />;
  }
};

export const rowMenuLabel = (recording: Recording) => `Actions for ${recording.name}`;

/**
 * What a row does, as one object whose identity never changes: the list
 * re-reads its rows every 15 s, and a row whose recording, state and handlers
 * are all unchanged is then not drawn again.
 */
export interface RecordingRailRowHandlers {
  onClick: (event: MouseEvent, id: RecordingId) => void;
  onKeyDown: (event: KeyboardEvent, id: RecordingId) => void;
  onContextMenu: (event: MouseEvent<HTMLElement>, recording: Recording) => void;
  onToggleMenu: (button: HTMLButtonElement, recording: Recording) => void;
  onToggleSelected: (id: RecordingId) => void;
  onExtendSelection: (id: RecordingId) => void;
  onRenameStart: (id: RecordingId, currentName: string) => void;
  onRenameValueChange: (value: string) => void;
  onRenameSubmit: (id: RecordingId) => void;
  onRenameCancel: () => void;
  longPressProps: TouchLongPress["bind"];
}

interface RecordingRailRowProps {
  recording: Recording;
  isActive: boolean;
  isSelected: boolean;
  isSelecting: boolean;
  isMenuOpen: boolean;
  hasUnsentNotes: boolean;
  /** Set only on the row being renamed, so typing redraws that row alone. */
  renameValue: string | null;
  userTimeZone: string;
  handlers: RecordingRailRowHandlers;
}

function RecordingRailRow({
  recording,
  isActive,
  isSelected,
  isSelecting,
  isMenuOpen,
  hasUnsentNotes,
  renameValue,
  userTimeZone,
  handlers,
}: RecordingRailRowProps) {
  const startDate = parseServerTimestamp(recording.created_at);
  // A capture's length is known only once Stop has joined its
  // audio, so until then the row shows when it started and no
  // length, rather than 0s and an end time equal to the start.
  const lengthPending = !recording.duration_seconds &&
    recording.status !== RecordingStatus.PROCESSED;
  const endDate = startDate && !lengthPending
    ? new Date(startDate.getTime() + (recording.duration_seconds || 0) * 1000)
    : null;
  const isHighlighted = isSelected || (isActive && !isSelecting);

  return (
    <li className="relative group">
      {renameValue !== null ? (
        <SurfaceBase data-ui-fill="card" data-ui-border="full" kind="card" className="p-3 rounded-lg border bg-surface-card border-action">
          <InputBase
            autoFocus
            type="text"
            value={renameValue}
            onChange={(e) => handlers.onRenameValueChange(e.target.value)}
            onBlur={() => handlers.onRenameSubmit(recording.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handlers.onRenameSubmit(recording.id);
              if (e.key === "Escape") {
                e.stopPropagation();
                handlers.onRenameCancel();
              }
            }}
            className="w-full text-sm font-semibold bg-transparent focus:outline-none"
          />
        </SurfaceBase>
      ) : (
        <>
          {/* Shown on hover or focus where there is a pointer that
              hovers, and on every row once anything is selected.
              On touch it stays hidden, but readable by a screen
              reader, until a long press starts a selection. */}
          <InputBase
            type="checkbox"
            aria-label={`Select ${recording.name}`}
            checked={isSelected}
            // Handled on click, which knows whether Shift is held.
            onChange={() => undefined}
            onClick={(e) => {
              if (e.shiftKey) {
                handlers.onExtendSelection(recording.id);
              } else {
                handlers.onToggleSelected(recording.id);
              }
            }}
            className={`absolute left-3 top-3.5 z-10 h-4 w-4 cursor-pointer rounded border-control-border text-action-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring ${
              isSelecting
                ? ""
                : "pointer-coarse:sr-only pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 pointer-fine:group-focus-within:opacity-100"
            }`}
          />
          <Link
            data-ui-fill={isHighlighted ? "action-tint" : "card"}
            data-ui-hover={isHighlighted ? undefined : "action-tint"}
            data-ui-border="full"
            data-ui="surface"
            data-ui-surface="card"
            data-recording-link={recording.id}
            href={`/recordings/${recording.id}`}
            id={
              recording.name === "Welcome to Nojoin"
                ? "demo-recording-card"
                : undefined
            }
            aria-current={isActive ? "page" : undefined}
            onClick={(e) => handlers.onClick(e, recording.id)}
            onKeyDown={(e) => handlers.onKeyDown(e, recording.id)}
            onMouseDown={(e) => {
              // Shift-click extends the selection, not a text selection.
              if (e.shiftKey) e.preventDefault();
            }}
            onContextMenu={(e) => handlers.onContextMenu(e, recording)}
            {...handlers.longPressProps(recording.id)}
            // The left padding keeps the text clear of the checkbox
            // wherever it can show, which on touch is only while
            // selecting; the right of the tag dots and the button.
            className={`block py-3 ${cardRightPadding(recording.tags?.length ?? 0)} rounded-lg border transition-all [-webkit-touch-callout:none] pointer-coarse:select-none ${
              isSelecting ? "pl-9" : "pl-9 pointer-coarse:pl-3"
            } ${
              isHighlighted
                ? isSelected
                  ? "bg-action-tint border-action-border"
                  : "bg-action-tint border-action"
                : "bg-surface-card border-surface-border hover:border-action-border hover:bg-action-tint"
            }`}
          >
            <div className="flex justify-between items-start mb-1">
              <h3
                className={`min-w-0 text-sm font-semibold line-clamp-2 ${isActive && !isSelecting ? "text-action-text" : "text-foreground"}`}
                title="Double-click to rename"
                onDoubleClick={(e) => {
                  if (isSelecting) return;
                  e.preventDefault();
                  e.stopPropagation();
                  handlers.onRenameStart(recording.id, recording.name);
                }}
              >
                {recording.name}
              </h3>
              <StatusIcon recording={recording} />
            </div>

            <div className="flex items-center overflow-hidden text-xs text-contrast-helper gap-2">
              <span className="whitespace-nowrap">
                {formatDate(recording.created_at, userTimeZone)}
              </span>
              {/* The time range yields first when the rail narrows; date and duration stay. */}
              <span className="hidden whitespace-nowrap text-contrast-icon-muted @min-[19rem]:inline">
                |
              </span>
              <span className="hidden whitespace-nowrap @min-[19rem]:inline">
                {!startDate
                  ? "Time unavailable"
                  : endDate
                    ? `${formatTime(startDate, userTimeZone)} - ${formatTime(endDate, userTimeZone)}`
                    : `Started ${formatTime(startDate, userTimeZone)}`}
              </span>
              {!lengthPending && (
                <>
                  <span className="whitespace-nowrap text-contrast-icon-muted">
                    |
                  </span>
                  <span className="whitespace-nowrap">
                    {formatDurationString(recording.duration_seconds)}
                  </span>
                </>
              )}
            </div>
            {/* Centred in the right-hand gutter, just left of the
                actions button, so they never meet it or the
                selection checkbox. */}
            <RecordingTagDots
              tags={recording.tags}
              className="absolute right-9 top-1/2 -translate-y-1/2 pointer-coarse:right-[52px]"
            />
            <RecordingAttentionBadge recordingId={recording.id} className="mt-2" />
            {/* The open recording lists its own unsent notes. */}
            {!isActive && hasUnsentNotes && (
              <Badge
                tone="warning"
                size="sm"
                icon={<NotebookPen aria-hidden="true" />}
                className="mt-2"
              >
                Unsent notes
              </Badge>
            )}
          </Link>
          {/* Beside the link, not in it, so a press here never
              opens, selects or long-presses the row. Shown on hover
              or focus where the pointer hovers, and always on touch. */}
          <IconButton
            size="sm"
            icon={<MoreHorizontal aria-hidden="true" />}
            aria-label={rowMenuLabel(recording)}
            title="Recording actions"
            aria-haspopup="menu"
            aria-expanded={isMenuOpen}
            onClick={(e) => handlers.onToggleMenu(e.currentTarget, recording)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown" && !isMenuOpen) {
                e.preventDefault();
                handlers.onToggleMenu(e.currentTarget, recording);
              }
            }}
            className={cn(
              "absolute right-1 top-1/2 z-10 h-7 w-7 -translate-y-1/2",
              TOUCH_TARGET_SQUARE,
              isMenuOpen
                ? "opacity-100"
                : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:opacity-100",
            )}
          />
        </>
      )}
    </li>
  );
}

export default memo(RecordingRailRow);
