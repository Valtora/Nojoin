import { useEffect, useState } from "react";
import { FileAudio, Server, Monitor } from "lucide-react";
import { getRecordingInfo } from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { Recording, RecordingInfo } from "@/types";
import Button from "./ui/Button";
import Modal from "./ui/Modal";

interface RecordingInfoModalProps {
  isOpen: boolean;
  onClose: () => void;
  recording: Recording;
}

export default function RecordingInfoModal({
  isOpen,
  onClose,
  recording,
}: RecordingInfoModalProps) {
  const [info, setInfo] = useState<RecordingInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const { addNotification } = useNotificationStore();

  useEffect(() => {
    if (isOpen) {
      setLoading(true);
      setUnavailable(false);
      getRecordingInfo(recording.id)
        .then((nextInfo) => {
          setInfo(nextInfo);
          setUnavailable(false);
        })
        .catch((err) => {
          console.error(err);
          setUnavailable(true);
          addNotification({
            type: "error",
            message: "Failed to load recording info.",
          });
        })
        .finally(() => setLoading(false));
    }
  }, [addNotification, isOpen, recording.id]);

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="md"
      title={
        <span className="flex items-center gap-2">
          <FileAudio aria-hidden="true" className="w-5 h-5 text-action-text" />
          Recording Details
        </span>
      }
      footer={
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="@container space-y-6">
        {/* General Info */}
        <div className="rounded-lg bg-surface-inset p-4">
          <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
            <Monitor aria-hidden="true" className="w-4 h-4 text-status-info-fg" />
            General
          </h4>
          {/* 22rem covers two columns wide enough for a locale timestamp,
              plus the gap and the card's own padding. */}
          <div className="grid gap-4 text-sm @min-[22rem]:grid-cols-2">
            <div>
              <span className="block text-xs text-contrast-helper">Name</span>
              <span
                className="block truncate font-medium text-foreground"
                title={recording.name}
              >
                {recording.name}
              </span>
            </div>
            <div>
              <span className="block text-xs text-contrast-helper">ID</span>
              <span className="break-all font-mono text-contrast-muted">
                {recording.id}
              </span>
            </div>
            <div>
              <span className="block text-xs text-contrast-helper">Created At</span>
              <span className="text-contrast-muted">
                {new Date(recording.created_at).toLocaleString()}
              </span>
            </div>
            <div>
              <span className="block text-xs text-contrast-helper">Status</span>
              <span className="capitalize text-contrast-muted">{recording.status}</span>
            </div>
          </div>
        </div>

        {loading ? (
          <div className="flex justify-center p-8">
            <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-action" />
          </div>
        ) : unavailable ? (
          <div className="rounded-lg border border-surface-border bg-surface-inset p-4 text-center text-sm text-contrast-helper">
            Recording details are temporarily unavailable.
          </div>
        ) : info ? (
          /* Proxy File */
          <div className="rounded-lg border border-surface-border p-4">
            <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
              <Server aria-hidden="true" className="w-4 h-4 text-status-success-fg" />
              Proxy Audio (Web optimized)
            </h4>
            {info.proxy ? (
              <div className="grid gap-x-4 gap-y-3 text-sm @min-[13rem]:grid-cols-2">
                <div>
                  <span className="block text-xs text-contrast-helper">Format</span>
                  <span className="uppercase text-foreground">
                    {info.proxy.format || "N/A"}
                  </span>
                </div>
                <div>
                  <span className="block text-xs text-contrast-helper">Bitrate</span>
                  <span className="text-foreground">
                    {info.proxy.bitrate
                      ? `${Math.round(info.proxy.bitrate / 1000)} kbps`
                      : "N/A"}
                  </span>
                </div>
                <div>
                  <span className="block text-xs text-contrast-helper">Channels</span>
                  <span className="text-foreground">{info.proxy.channels} (Mono)</span>
                </div>
                <div>
                  <span className="block text-xs text-contrast-helper">Size</span>
                  <span className="text-foreground">
                    {info.proxy.size
                      ? `${(info.proxy.size / 1024 / 1024).toFixed(2)} MB`
                      : "N/A"}
                  </span>
                </div>
              </div>
            ) : (
              <p className="text-sm italic text-contrast-helper">
                Proxy file not generated yet.
              </p>
            )}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
