import { useState, useEffect } from "react";
import {
  X,
  Check,
  Loader2,
  AlertTriangle,
  Cpu,
  Trash2,
} from "lucide-react";
import { SystemModelStatus } from "@/types";
import {
  getModelsStatus,
  deleteModel,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";

interface WhisperModelModalProps {
  isOpen: boolean;
  onClose: () => void;
  currentSize: string;
  isAdmin: boolean;
  onUpdate: (newSize: string) => void;
}

const WHISPER_MODELS = [
  { id: "tiny", label: "Tiny", params: "39 M", vram: "~1 GB", speed: "~10x" },
  { id: "base", label: "Base", params: "74 M", vram: "~1 GB", speed: "~7x" },
  { id: "small", label: "Small", params: "244 M", vram: "~2 GB", speed: "~4x" },
  {
    id: "medium",
    label: "Medium",
    params: "769 M",
    vram: "~5 GB",
    speed: "~2x",
  },
  {
    id: "large",
    label: "Large",
    params: "1550 M",
    vram: "~10 GB",
    speed: "1x",
  },
  { id: "turbo", label: "Turbo", params: "809 M", vram: "~6 GB", speed: "~8x" },
];

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

  if (!isOpen) return null;

  const isDownloaded = status?.whisper?.downloaded;
  const modelInfo = WHISPER_MODELS.find((m) => m.id === selectedModel);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto border border-gray-200 dark:border-gray-700 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-xl font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <Cpu className="w-5 h-5 text-orange-500" />
            Transcription Model Selection
          </h2>
          <button
            onClick={onClose}
            className="contrast-helper hover:text-gray-900 dark:hover:text-white"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6 flex-1">
          {/* Recommendation */}
          <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4 flex gap-3">
            <AlertTriangle className="w-5 h-5 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" />
            <div>
              <h4 className="font-medium text-blue-900 dark:text-blue-200">
                Recommended: Turbo
              </h4>
              <p className="text-sm text-blue-800 dark:text-blue-300 mt-1">
                The <strong>Turbo</strong> model is selected as the default
                because it offers the best balance between VRAM usage (~6GB),
                accuracy, and transcription speed. Larger models like
                &apos;Large&apos; are only marginally more accurate but
                significantly slower and require more VRAM (10GB+).
              </p>
            </div>
          </div>

          {/* Selector */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Select Whisper Model
            </label>
            <select
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              disabled={!isAdmin}
              className="w-full p-3 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none text-base transition-all"
            >
              {WHISPER_MODELS.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.label} — {model.params} Params, {model.vram} VRAM,{" "}
                  {model.speed} Speed
                </option>
              ))}
            </select>
          </div>

          {/* Status & Actions */}
          <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-5 border border-gray-200 dark:border-gray-700">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Status:
                </span>
                {loadingStatus ? (
                  <span className="text-sm text-gray-500 flex items-center gap-1">
                    <Loader2 className="w-3 h-3 animate-spin" /> Checking...
                  </span>
                ) : isDownloaded ? (
                  <span className="text-sm font-medium text-green-600 dark:text-green-400 flex items-center gap-1 bg-green-100 dark:bg-green-900/30 px-2 py-0.5 rounded-full">
                    <Check className="w-3 h-3" /> Ready to use
                  </span>
                ) : (
                  <span className="text-sm font-medium text-red-600 dark:text-red-400 flex items-center gap-1 bg-red-100 dark:bg-red-900/30 px-2 py-0.5 rounded-full">
                    <X className="w-3 h-3" /> Preparation pending
                  </span>
                )}
              </div>

              {/* Clear Cache Button */}
              {isDownloaded && (
                <button
                  onClick={handleClearCache}
                  disabled={deleting || !isAdmin}
                  className="text-xs text-red-500 hover:text-red-700 dark:hover:text-red-300 flex items-center gap-1 hover:underline disabled:opacity-50"
                >
                  {deleting ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                  ) : (
                    <Trash2 className="w-3 h-3" />
                  )}
                  Clear {modelInfo?.label} Cache
                </button>
              )}
            </div>

            {!isDownloaded && !loadingStatus && (
              <p className="text-sm contrast-helper">
                The {modelInfo?.label} model is not cached yet. Nojoin will
                prepare it automatically after this change is saved.
              </p>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="p-6 border-t border-gray-200 dark:border-gray-700 flex justify-end gap-3 bg-gray-50 dark:bg-gray-800/50 rounded-b-xl">
          <button
            onClick={onClose}
            className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg font-medium transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={loadingStatus || !isAdmin}
            className="px-6 py-2 bg-orange-600 hover:bg-orange-700 text-white rounded-lg font-medium shadow-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Apply Changes
          </button>
        </div>
      </div>
    </div>
  );
}
