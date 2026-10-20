import { useState, useEffect } from "react";
import {
  X,
  Check,
  Loader2,
  Info,
  Cpu,
  Trash2,
} from "lucide-react";
import Button from "../ui/Button";
import Modal from "../ui/Modal";
import Select from "../ui/Select";
import { SystemModelStatus } from "@/types";
import {
  getModelsStatus,
  deleteModel,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { WHISPER_MODELS, describeWhisperModel } from "@/lib/whisperModels";

interface WhisperModelModalProps {
  isOpen: boolean;
  onClose: () => void;
  currentSize: string;
  isAdmin: boolean;
  onUpdate: (newSize: string) => void;
}

export default function WhisperModelModal({
  isOpen,
  onClose,
  currentSize,
  isAdmin,
  onUpdate,
}: WhisperModelModalProps) {
  const [selectedModel, setSelectedModel] = useState(currentSize);
  const [status, setStatus] = useState<SystemModelStatus | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);

  // Deleting State
  const [deleting, setDeleting] = useState(false);
  const { addNotification } = useNotificationStore();

  // Initial sync
  useEffect(() => {
    if (isOpen) {
      setSelectedModel(currentSize);
    }
  }, [isOpen, currentSize]);

  // Check status whenever selected model changes
  useEffect(() => {
    if (!isOpen) return;

    const checkStatus = async () => {
      setLoadingStatus(true);
      try {
        const res = await getModelsStatus(selectedModel);
        setStatus(res);

            } catch (e: unknown) {
        console.error("Failed to check model status", e);
      } finally {
        setLoadingStatus(false);
      }
    };

    checkStatus();

  }, [selectedModel, isOpen]);

  const isDownloaded = status?.whisper?.downloaded;
  const modelInfo = WHISPER_MODELS.find((m) => m.id === selectedModel);

  const handleClearCache = async () => {
    if (
      !confirm(
        `Delete the downloaded ${modelInfo?.label ?? selectedModel} model? It has to be downloaded again before it can be used.`,
      )
    )
      return;

    setDeleting(true);
    try {
      await deleteModel("whisper", selectedModel);
      const res = await getModelsStatus(selectedModel);
      setStatus(res);

        } catch (e: unknown) {
      console.error(e);
      addNotification({ type: "error", message: "Could not delete the model files." });
    } finally {
      setDeleting(false);
    }
  };

  const handleSave = () => {
    onUpdate(selectedModel);
    onClose();
  };


  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="lg"
      title={
        <span className="flex items-center gap-2">
          <Cpu aria-hidden="true" className="w-5 h-5 text-action-text" />
          Whisper size
        </span>
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleSave}
            disabled={loadingStatus || !isAdmin}
          >
            Use this size
          </Button>
        </>
      }
    >
      <div className="space-y-6">
        {/* Recommendation */}
        <div className="flex gap-3 rounded-lg border border-status-info-border bg-status-info-bg p-4">
          <Info aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-status-info-fg" />
          <div>
            <h4 className="font-medium text-status-info-fg">
              Turbo suits most installations
            </h4>
            <p className="mt-1 text-sm text-status-info-fg">
              It needs about 6 GB of GPU memory and runs about 8 times as fast
              as Large, which is only slightly more accurate and needs about
              10 GB.
            </p>
          </div>
        </div>

        <Select
          label="Size"
          hint={modelInfo ? `${describeWhisperModel(modelInfo)}.` : undefined}
          fieldSize="lg"
          value={selectedModel}
          onChange={(e) => setSelectedModel(e.target.value)}
          disabled={!isAdmin}
        >
          {WHISPER_MODELS.map((model) => (
            <option key={model.id} value={model.id}>
              {model.label} ({model.vram.replace("~", "about ")})
            </option>
          ))}
        </Select>

        {/* Status & Actions */}
        <div className="rounded-lg border border-surface-border bg-surface-inset p-5">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-contrast-muted">
                On the server:
              </span>
              {loadingStatus ? (
                <span className="flex items-center gap-1 text-sm text-contrast-helper">
                  <Loader2 aria-hidden="true" className="w-3 h-3 animate-spin" /> Checking...
                </span>
              ) : isDownloaded ? (
                <span className="flex items-center gap-1 rounded-pill bg-status-success-bg px-2 py-0.5 text-sm font-medium text-status-success-fg">
                  <Check aria-hidden="true" className="w-3 h-3" /> Downloaded
                </span>
              ) : (
                <span className="flex items-center gap-1 rounded-pill bg-status-danger-bg px-2 py-0.5 text-sm font-medium text-status-danger-fg">
                  <X aria-hidden="true" className="w-3 h-3" /> Not downloaded
                </span>
              )}
            </div>

            {/* Clear Cache Button */}
            {isDownloaded && (
              <Button
                size="sm"
                variant="danger"
                onClick={handleClearCache}
                disabled={deleting || !isAdmin}
                loading={deleting}
                iconLeft={<Trash2 aria-hidden="true" className="w-3 h-3" />}
              >
                Delete download
              </Button>
            )}
          </div>

          {!isDownloaded && !loadingStatus && (
            <p className="text-sm text-contrast-helper">
              The {modelInfo?.label} model is not on the server yet. Nojoin
              asks whether to download it once you use this size.
            </p>
          )}
        </div>
      </div>
    </Modal>
  );
}
