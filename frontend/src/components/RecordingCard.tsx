"use client";

import Link from "next/link";
import { Recording, RecordingStatus } from "@/types";
import {
  Calendar,
  Clock,
  Loader2,
  AlertCircle,
  HelpCircle,
  Pause,
} from "lucide-react";
import RecordingInfoModal from "./RecordingInfoModal";
import ReprocessDialog from "./ReprocessDialog";
import { useState } from "react";
import ContextMenu from "./ContextMenu";
import { useRecordingActions } from "./recordings/_hooks/useRecordingActions";
import { useNotificationStore } from "@/lib/notificationStore";
import { useRouter } from "next/navigation";

interface RecordingCardProps {
  recording: Recording;
}

const formatDuration = (recording: Recording) => {
  if (
    recording.status === RecordingStatus.UPLOADING ||
    recording.status === RecordingStatus.PAUSED
  ) {
    return "--";
  }
  const seconds = recording.duration_seconds;
  if (!seconds) return "00:00";
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return `${minutes.toString().padStart(2, "0")}:${remainingSeconds.toString().padStart(2, "0")}`;
};

const formatDate = (dateString: string, recording: Recording) => {
  const start = new Date(dateString).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  if (
    recording.status === RecordingStatus.UPLOADING ||
    recording.status === RecordingStatus.PAUSED
  ) {
    return `${start} - --:--`;
  }

  return start;
};

const StatusBadge = ({ recording }: { recording: Recording }) => {
  const { status, transcript } = recording;

  switch (status) {
    case RecordingStatus.PROCESSED:
      return null;
    case RecordingStatus.QUEUED:
      return (
        <span
          className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200 cursor-help"
          title="Meeting is in queue to be processed..."
        >
          <Loader2 className="w-3 h-3 mr-1 animate-spin" />
          Queued
        </span>
      );
    case RecordingStatus.PROCESSING:
      if (
        transcript?.transcript_status === "completed" &&
        transcript?.notes_status === "generating"
      ) {
        return (
          <span
            className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200"
            title="Transcript ready. Generating notes..."
          >
            <Loader2 className="w-3 h-3 mr-1 animate-spin" />
            Generating Notes
          </span>
        );
      }
      return (
        <span
          className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200 cursor-help"
          title="Processing audio: transcription, diarization, and voiceprint extraction. Tip: Disable 'Auto-create Voiceprints' in Settings for faster processing if you prefer manual speaker management."
        >
          <Loader2 className="w-3 h-3 mr-1 animate-spin" />
          Processing
        </span>
      );
    case RecordingStatus.ERROR:
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200">
          <AlertCircle className="w-3 h-3 mr-1" />
          Error
        </span>
      );
    case RecordingStatus.PAUSED:
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200">
          <Pause className="w-3 h-3 mr-1" />
          Paused
        </span>
      );
    case RecordingStatus.CANCELLED:
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300">
          <AlertCircle className="w-3 h-3 mr-1" />
          Cancelled
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300">
          <HelpCircle className="w-3 h-3 mr-1" />
          {status}
        </span>
      );
  }
};

export default function RecordingCard({ recording }: RecordingCardProps) {
  const router = useRouter();
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
  } | null>(null);
  const [showInfoModal, setShowInfoModal] = useState(false);
  const [showReprocessDialog, setShowReprocessDialog] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { addNotification } = useNotificationStore();
  const actions = useRecordingActions();

  const isDemo = recording.name === "Welcome to Nojoin";

  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY });
  };

  const handleRenameStart = (e?: React.MouseEvent) => {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    setIsRenaming(true);
    setRenameValue(recording.name);
    setContextMenu(null);
  };

  const handleRenameSubmit = async () => {
    if (!renameValue.trim() || isSubmitting) return;

    setIsSubmitting(true);
    try {
      await actions.rename(recording.id, renameValue.trim(), {
        onSuccess: () => {
          setIsRenaming(false);
          router.refresh();
        },
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm("Are you sure you want to delete this recording?")) return;
    await actions.delete(recording.id, {
      onSuccess: () => router.refresh(),
    });
  };

  const handleInferSpeakers = async () => {
    await actions.inferSpeakers(recording.id, {
      onSuccess: () => {
        window.dispatchEvent(
          new CustomEvent("recording-updated", {
            detail: { id: recording.id },
          }),
        );
        router.refresh();
      },
    });
  };

  const handleCancel = async () => {
    await actions.cancel(recording.id, {
      onSuccess: () => {
        router.refresh();
        // Force reload after short delay to ensure UI updates
        setTimeout(() => router.refresh(), 1000);
      },
    });
  };

  const showCancelOption =
    recording.status === RecordingStatus.PROCESSING ||
    recording.status === RecordingStatus.QUEUED ||
    recording.status === RecordingStatus.UPLOADING;
  const showRetryOption = !showCancelOption;

  return (
    <>
      {isRenaming ? (
        <div className="block">
          <div
            id={isDemo ? "demo-recording-card" : undefined}
            className="bg-white dark:bg-gray-800 rounded-lg shadow hover:shadow-md transition-shadow p-4 border border-gray-200 dark:border-gray-700 relative group"
            onContextMenu={handleContextMenu}
          >
            <div className="flex justify-between items-start mb-2">
              <input
                autoFocus
                type="text"
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onBlur={handleRenameSubmit}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleRenameSubmit();
                  if (e.key === "Escape") setIsRenaming(false);
                  e.stopPropagation();
                }}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                }}
                className="text-lg font-semibold text-gray-900 dark:text-white bg-white dark:bg-gray-700 border border-blue-300 rounded px-1 focus:outline-none flex-1 mr-4"
              />
              <StatusBadge recording={recording} />
            </div>

            <div className="flex items-center text-sm text-gray-500 dark:text-gray-400 space-x-4">
              <div className="flex items-center">
                <Calendar className="w-4 h-4 mr-1" />
                {formatDate(recording.created_at, recording)}
              </div>
              <div className="flex items-center">
                <Clock className="w-4 h-4 mr-1" />
                {formatDuration(recording)}
              </div>
            </div>
          </div>
        </div>
      ) : (
        <Link href={`/recordings/${recording.id}`} className="block">
          <div
            id={isDemo ? "demo-recording-card" : undefined}
            className="bg-white dark:bg-gray-800 rounded-lg shadow hover:shadow-md transition-shadow p-4 border border-gray-200 dark:border-gray-700 relative group"
            onContextMenu={handleContextMenu}
          >
            <div className="flex justify-between items-start mb-2">
              <h3
                className="text-lg font-semibold text-gray-900 dark:text-white truncate pr-4 flex-1 hover:text-blue-600 dark:hover:text-blue-400"
                title="Double-click to rename"
                onDoubleClick={handleRenameStart}
              >
                {recording.name}
              </h3>
              <StatusBadge recording={recording} />
            </div>

            <div className="flex items-center text-sm text-gray-500 dark:text-gray-400 space-x-4">
              <div className="flex items-center">
                <Calendar className="w-4 h-4 mr-1" />
                {formatDate(recording.created_at, recording)}
              </div>
              <div className="flex items-center">
                <Clock className="w-4 h-4 mr-1" />
                {formatDuration(recording)}
              </div>
            </div>
          </div>
        </Link>
      )}

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
          items={[
            {
              label: "Rename",
              onClick: handleRenameStart,
            },
            {
              label: "Recording Details",
              onClick: () => {
                setContextMenu(null);
                setShowInfoModal(true);
              },
            },
            {
              label: "Retry Speaker Inference",
              onClick: handleInferSpeakers,
            },
            ...(showCancelOption
              ? [
                  {
                    label: "Cancel Processing",
                    onClick: handleCancel,
                    className: "text-amber-600 dark:text-amber-400",
                  },
                ]
              : []),
            ...(showRetryOption
              ? [
                  {
                    label: "Retry Processing",
                    onClick: () => setShowReprocessDialog(true),
                    className: "text-blue-600 dark:text-blue-400",
                  },
                ]
              : []),
            {
              label: "Delete Recording",
              onClick: handleDelete,
              className: "text-red-600 dark:text-red-400",
            },
          ]}
        />
      )}

      <RecordingInfoModal
        isOpen={showInfoModal}
        onClose={() => setShowInfoModal(false)}
        recording={recording}
      />

      {showReprocessDialog && (
        <ReprocessDialog
          recordingId={recording.id}
          isOpen={showReprocessDialog}
          onClose={() => setShowReprocessDialog(false)}
          onReprocessed={(updatedRecording) => {
            addNotification({
              message: "Recording reprocessing started.",
              type: "success",
            });
            window.dispatchEvent(
              new CustomEvent("recording-updated", {
                detail: { id: updatedRecording.id },
              }),
            );
            router.refresh();
          }}
        />
      )}
    </>
  );
}
