import { Check, Download, Loader2, Trash2, X } from "lucide-react";

import {
  DownloadProgress,
  ModelPreparationTarget,
  SystemModelStatus,
} from "@/types";
import SettingsPanel from "./SettingsPanel";
import SettingsSection from "./SettingsSection";
import SettingsStatusBadge from "./SettingsStatusBadge";

interface AiModelDependenciesSectionProps {
  modelStatus: SystemModelStatus | null;
  deleting: string | null;
  handleDeleteModel: (modelName: string) => void;
  isAdmin: boolean;
  downloadProgress: DownloadProgress | null;
  preparationRunning: boolean;
  startPreparation: (target: ModelPreparationTarget) => Promise<boolean>;
}

/** `target` is what a repair of this row queues. The Whisper and Pyannote
 * assets are prepared as one core batch, so each of those rows maps to it. */
const DEPENDENCY_MODELS: {
  id: string;
  label: string;
  desc: string;
  target: ModelPreparationTarget;
}[] = [
  {
    id: "whisper",
    label: "Whisper (Transcription)",
    desc: "OpenAI Whisper model for speech-to-text. (MIT License)",
    target: "core",
  },
  {
    id: "parakeet",
    label: "Parakeet ASR Model (Transcription)",
    desc: "NVIDIA FastConformer ASR model.",
    target: "parakeet",
  },
  {
    id: "canary",
    label: "Canary ASR Model (Transcription)",
    desc: "NVIDIA Canary 1B multi-lingual ASR model.",
    target: "canary",
  },
  {
    id: "pyannote",
    label: "Pyannote (Diarization)",
    desc: "Speaker diarization model weights.",
    target: "core",
  },
  {
    id: "embedding",
    label: "Voice Embedding",
    desc: "Speaker identification model weights.",
    target: "core",
  },
  {
    id: "segmentation",
    label: "Segmentation Refinement",
    desc: "Pyannote segmentation-3.0 model weights.",
    target: "core",
  },
];

/** Admin-only "Model dependencies" section. Extracted verbatim from
 * {@link AISettings} so behaviour is unchanged. */
export default function AiModelDependenciesSection({
  modelStatus,
  deleting,
  handleDeleteModel,
  isAdmin,
  downloadProgress,
  preparationRunning,
  startPreparation,
}: AiModelDependenciesSectionProps) {
  const progressPercent = Math.min(
    100,
    Math.max(0, downloadProgress?.progress ?? 0),
  );

  return (
    <SettingsSection
      eyebrow="Administration"
      title="Model dependencies"
      description="Inspect and manage local AI model assets on the server."
    >
      <SettingsPanel className="mx-auto max-w-3xl space-y-6">
        {preparationRunning && (
          <div className="rounded-lg border border-orange-300 dark:border-orange-800 bg-orange-50 dark:bg-orange-950/40 p-4">
            <div className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-white">
              <Loader2 className="w-4 h-4 animate-spin text-orange-600" />
              Preparing models
              <span className="ml-auto tabular-nums">{progressPercent}%</span>
            </div>
            <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-orange-100 dark:bg-orange-900/60">
              <div
                className="h-full rounded-full bg-orange-600 transition-all duration-500"
                style={{ width: `${progressPercent}%` }}
              />
            </div>
            <p className="mt-2 text-xs contrast-helper">
              {downloadProgress?.message || "Waiting for the worker..."}
              {downloadProgress?.eta ? ` (${downloadProgress.eta} left)` : ""}
            </p>
          </div>
        )}

        <div className="bg-gray-50 dark:bg-gray-900/50 p-4 rounded-lg border border-gray-200 dark:border-gray-700">
          <div className="space-y-3">
            {DEPENDENCY_MODELS.map((model) => (
              <div
                key={model.id}
                className="flex justify-between items-center p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm"
              >
                <div>
                  <div className="text-sm font-medium text-gray-900 dark:text-white">
                    {model.label}
                  </div>
                  <div className="text-xs contrast-helper">{model.desc}</div>
                </div>
                <div className="flex items-center gap-3">
                  {modelStatus?.[model.id]?.downloaded ? (
                    <>
                      <SettingsStatusBadge tone="success" className="gap-1">
                        <Check className="w-3 h-3" /> Ready
                      </SettingsStatusBadge>
                      {modelStatus?.[model.id]?.source === "bundled" && (
                        <SettingsStatusBadge tone="info">
                          Bundled
                        </SettingsStatusBadge>
                      )}
                      <button
                        onClick={() => handleDeleteModel(model.id)}
                        disabled={
                          deleting === model.id ||
                          !isAdmin ||
                          modelStatus?.[model.id]?.source === "bundled"
                        }
                        className="text-gray-500 dark:text-gray-400 hover:text-red-500 transition-colors p-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md disabled:opacity-50"
                        title={
                          modelStatus?.[model.id]?.source === "bundled"
                            ? "Bundled repo asset"
                            : "Delete Model"
                        }
                      >
                        {deleting === model.id ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Trash2 className="w-4 h-4" />
                        )}
                      </button>
                    </>
                  ) : (
                    <div className="flex flex-col items-end">
                      <div className="flex items-center gap-3">
                        <SettingsStatusBadge tone="error" className="gap-1">
                          <X className="w-3 h-3" /> Missing
                        </SettingsStatusBadge>
                        <button
                          onClick={() => void startPreparation(model.target)}
                          disabled={!isAdmin || preparationRunning}
                          className="flex items-center gap-1.5 rounded-md border border-gray-300 px-2.5 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
                          title={
                            preparationRunning
                              ? "A model preparation is already running"
                              : "Download this model now"
                          }
                        >
                          <Download className="w-3.5 h-3.5" />
                          Download
                        </button>
                      </div>
                      {modelStatus?.[model.id]?.checked_paths &&
                        modelStatus[model.id].checked_paths.length > 0 && (
                          <span
                            className="mt-1 max-w-[200px] truncate cursor-help text-[10px] text-gray-500 dark:text-gray-400"
                            title={`Checked paths:\n${modelStatus[model.id].checked_paths.join("\n")}`}
                          >
                            Hover for debug info
                          </span>
                        )}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>

          <p className="mt-6 pt-4 border-t border-gray-200 dark:border-gray-700 text-center text-xs contrast-helper">
            Required default models are prepared on first run. After that,
            changing the transcription model asks whether to download it now,
            and a missing model can be fetched here at any time. Anything left
            missing is downloaded on first use, which delays live transcription
            and Meeting Edge until it is ready.
          </p>
        </div>
      </SettingsPanel>
    </SettingsSection>
  );
}
