import { useState, useEffect } from "react";
import {
  X,
  Check,
  Loader2,
  AlertTriangle,
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
import { WHISPER_MODELS } from "@/lib/whisperModels";

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

  const handleClearCache = async () => {
    if (
      !confirm(
        `Are you sure you want to delete the cached files for the ${selectedModel} model? You will need to download it again to use it.`,
      )
    )
      return;

    setDeleting(true);
    try {
      await deleteModel("whisper", selectedModel);
      // Refresh status
      const res = await getModelsStatus(selectedModel);
      setStatus(res);

        } catch (e: unknown) {
      console.error(e);
      addNotification({ type: "error", message: "Failed to delete model cache" });
    } finally {
      setDeleting(false);
    }
  };

  const handleSave = () => {
    onUpdate(selectedModel);
    onClose();
  };

  const isDownloaded = status?.whisper?.downloaded;
  const modelInfo = WHISPER_MODELS.find((m) => m.id === selectedModel);

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="lg"
      title={
        <span className="flex items-center gap-2">
          <Cpu aria-hidden="true" className="w-5 h-5 text-action-text" />
          Transcription Model Selection
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
            Apply Changes
          </Button>
        </>
      }
    >
      <div className="space-y-6">
        {/* Recommendation */}
        <div className="flex gap-3 rounded-lg border border-status-info-border bg-status-info-bg p-4">
          <AlertTriangle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-status-info-fg" />
          <div>
            <h4 className="font-medium text-status-info-fg">
              Recommended: Turbo
            </h4>
            <p className="mt-1 text-sm text-status-info-fg">
              The <strong>Turbo</strong> model is selected as the default
              because it offers the best balance between VRAM usage (~6GB),
              accuracy, and transcription speed. Larger models like
              &apos;Large&apos; are only marginally more accurate but
              significantly slower and require more VRAM (10GB+).
            </p>
          </div>
        </div>

        {/* Selector */}
        <Select
          label="Select Whisper Model"
          fieldSize="lg"
          value={selectedModel}
          onChange={(e) => setSelectedModel(e.target.value)}
          disabled={!isAdmin}
        >
          {WHISPER_MODELS.map((model) => (
            <option key={model.id} value={model.id}>
              {model.label} — {model.params} Params, {model.vram} VRAM,{" "}
              {model.speed} Speed
            </option>
          ))}
        </Select>

        {/* Status & Actions */}
        <div className="rounded-lg border border-surface-border bg-surface-inset p-5">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-contrast-muted">
                Status:
              </span>
              {loadingStatus ? (
                <span className="flex items-center gap-1 text-sm text-contrast-helper">
                  <Loader2 aria-hidden="true" className="w-3 h-3 animate-spin" /> Checking...
                </span>
              ) : isDownloaded ? (
                <span className="flex items-center gap-1 rounded-full bg-status-success-bg px-2 py-0.5 text-sm font-medium text-status-success-fg">
                  <Check aria-hidden="true" className="w-3 h-3" /> Ready to use
                </span>
              ) : (
                <span className="flex items-center gap-1 rounded-full bg-status-danger-bg px-2 py-0.5 text-sm font-medium text-status-danger-fg">
                  <X aria-hidden="true" className="w-3 h-3" /> Preparation pending
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
                Clear {modelInfo?.label} Cache
              </Button>
            )}
          </div>

          {!isDownloaded && !loadingStatus && (
            <p className="text-sm text-contrast-helper">
              The {modelInfo?.label} model is not cached yet. Nojoin will ask
              whether to download it once this change is saved.
            </p>
          )}
        </div>
      </div>
    </Modal>
  );
}
