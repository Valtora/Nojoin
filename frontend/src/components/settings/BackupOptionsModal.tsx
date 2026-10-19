import { useState } from "react";
import { Download, AlertTriangle, FileAudio } from "lucide-react";
import {
  downloadBackupFile,
  requestBackup,
  type ArchiveQuality,
} from "@/lib/api";
import { archiveWarningNotifications, formatBackupTime } from "@/lib/backupMessages";
import { useBackupStore } from "@/lib/backupStore";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import { Switch } from "@/components/ui/Switch";

import Button from "../ui/Button";
import Modal from "../ui/Modal";

interface BackupOptionsModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Called once the server has answered, whichever way. */
  onRequested?: () => void;
}

export default function BackupOptionsModal({
  isOpen,
  onClose,
  onRequested,
}: BackupOptionsModalProps) {
  const [includeAudio, setIncludeAudio] = useState(true);
  const [archiveQuality, setArchiveQuality] =
    useState<ArchiveQuality>("compressed");
  const [isProcessing, setIsProcessing] = useState(false);
  const { setTaskId } = useBackupStore();
  const { addNotification } = useNotificationStore();

  const handleExport = async () => {
    try {
      setIsProcessing(true);
      const result = await requestBackup(includeAudio, archiveQuality);

      if (result.status === "ready") {
        // Nothing has changed since the cached archive was made, so it is the backup.
        // The click that asked for it is still recent enough to start a download.
        downloadBackupFile(result.export.export_id);
        archiveWarningNotifications(result.export.warnings).forEach(addNotification);
        addNotification({
          type: "info",
          message: `Nothing has changed since your backup from ${formatBackupTime(result.export.created_at)}, so that one is downloading now.`,
        });
      } else {
        // The global poller follows it and says when it is ready.
        setTaskId(result.task_id);
        addNotification({
          type: "info",
          message:
            "Preparing your backup. You can keep using Nojoin, and a notification will appear when it is ready to download.",
        });
      }

      onRequested?.();
      onClose();
    } catch (error: unknown) {
      console.error("Backup export failed:", error);
      addNotification({
        type: "error",
        message: getErrorMessage(
          error,
          "Could not start the backup. Try again.",
        ),
      });
    } finally {
      setIsProcessing(false);
    }
  };

  const qualityClass = (selected: boolean) =>
    `flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
      selected
        ? "border-action bg-action-tint"
        : "border-surface-border hover:bg-surface-inset"
    }`;

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="lg"
      title={
        <span className="flex items-center gap-3">
          <span className="rounded-lg bg-action-tint p-3">
            <Download aria-hidden="true" className="h-6 w-6 text-action-tint-fg" />
          </span>
          <span>
            <span className="block text-xl">New backup</span>
            <span className="block text-sm font-normal text-contrast-helper">
              A zip of everything on this installation
            </span>
          </span>
        </span>
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleExport}
            loading={isProcessing}
            iconLeft={<Download aria-hidden="true" className="h-4 w-4" />}
          >
            {isProcessing ? "Starting..." : "Create backup"}
          </Button>
        </>
      }
    >
      <div className="space-y-6">
        <div className="flex items-start gap-4 rounded-lg border border-surface-border bg-surface-inset p-4">
          <div className="mt-1">
            <FileAudio aria-hidden="true" className="h-5 w-5 text-contrast-icon-muted" />
          </div>
          <div className="flex-1 space-y-2">
            <div className="flex items-center justify-between">
              <label
                htmlFor="include-audio"
                className="cursor-pointer font-medium text-foreground"
              >
                Include audio
              </label>
              <Switch
                id="include-audio"
                checked={includeAudio}
                onCheckedChange={setIncludeAudio}
              />
            </div>
            <p className="text-sm text-contrast-helper">
              Adds each recording&apos;s audio, so restored meetings can be played.
            </p>
          </div>
        </div>

        {includeAudio && (
          <div className="rounded-lg border border-surface-border p-4">
            <h3 className="mb-3 text-sm font-semibold text-foreground">
              Audio quality
            </h3>
            <div className="space-y-3">
              <label className={qualityClass(archiveQuality === "compressed")}>
                <input
                  type="radio"
                  name="archive_quality"
                  className="mt-1 accent-action"
                  checked={archiveQuality === "compressed"}
                  onChange={() => setArchiveQuality("compressed")}
                />
                <div>
                  <span className="block text-sm font-medium text-foreground">
                    Compressed
                  </span>
                  <span className="mt-1 block text-xs text-contrast-helper">
                    Re-encodes audio to Opus for a much smaller archive. Suits
                    routine backups you only need to listen to.
                  </span>
                </div>
              </label>

              <label className={qualityClass(archiveQuality === "original")}>
                <input
                  type="radio"
                  name="archive_quality"
                  className="mt-1 accent-action"
                  checked={archiveQuality === "original"}
                  onChange={() => setArchiveQuality("original")}
                />
                <div>
                  <span className="block text-sm font-medium text-foreground">
                    Original
                  </span>
                  <span className="mt-1 block text-xs text-contrast-helper">
                    Stores audio exactly as recorded. A much larger archive,
                    but restored meetings can be reprocessed without any
                    quality loss.
                  </span>
                </div>
              </label>
            </div>
          </div>
        )}

        {!includeAudio && (
          <div className="flex items-start gap-3 rounded-lg bg-status-warning-bg p-4 text-sm text-status-warning-fg">
            <AlertTriangle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" />
            <p>
              Without audio the backup is much smaller, but restored meetings
              <strong> cannot be played</strong>. Their transcripts, notes and
              details are kept.
            </p>
          </div>
        )}

        <div className="flex items-start gap-3 rounded-lg bg-status-warning-bg p-4 text-sm text-status-warning-fg">
          <AlertTriangle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" />
          <p>
            Keep the file somewhere safe. It holds the calendar sign-in
            credentials and each person&apos;s calendar connections, so
            calendars work again after a restore. AI and Hugging Face keys are
            left out.
          </p>
        </div>
      </div>
    </Modal>
  );
}
