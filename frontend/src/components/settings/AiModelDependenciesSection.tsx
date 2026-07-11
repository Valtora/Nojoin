import { Check, Loader2, Trash2, X } from "lucide-react";

import { SystemModelStatus } from "@/types";
import SettingsPanel from "./SettingsPanel";
import SettingsSection from "./SettingsSection";
import SettingsStatusBadge from "./SettingsStatusBadge";

interface AiModelDependenciesSectionProps {
  modelStatus: SystemModelStatus | null;
  deleting: string | null;
  handleDeleteModel: (modelName: string) => void;
  isAdmin: boolean;
}

const DEPENDENCY_MODELS = [
  {
    id: "whisper",
    label: "Whisper (Transcription)",
    desc: "OpenAI Whisper model for speech-to-text. (MIT License)",
  },
  {
    id: "parakeet",
    label: "Parakeet ASR Model (Transcription)",
    desc: "NVIDIA FastConformer ASR model.",
  },
  {
    id: "canary",
    label: "Canary ASR Model (Transcription)",
    desc: "NVIDIA Canary 1B multi-lingual ASR model.",
  },
  {
    id: "pyannote",
    label: "Pyannote (Diarization)",
    desc: "Speaker diarization model weights.",
  },
  {
    id: "embedding",
    label: "Voice Embedding",
    desc: "Speaker identification model weights.",
  },
  {
    id: "segmentation",
    label: "Segmentation Refinement",
    desc: "Pyannote segmentation-3.0 model weights.",
  },
];

/** Admin-only "Model dependencies" section. Extracted verbatim from
 * {@link AISettings} so behaviour is unchanged. */
export default function AiModelDependenciesSection({
  modelStatus,
  deleting,
  handleDeleteModel,
  isAdmin,
}: AiModelDependenciesSectionProps) {
  return (
    <SettingsSection
      eyebrow="Administration"
      title="Model dependencies"
      description="Inspect and manage local AI model assets on the server."
    >
      <SettingsPanel className="mx-auto max-w-3xl space-y-6">
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
                      <SettingsStatusBadge tone="error" className="gap-1">
                        <X className="w-3 h-3" /> Missing
                      </SettingsStatusBadge>
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
            Required default models are prepared automatically. Parakeet and
            Canary models are prepared after their settings are saved.
          </p>
        </div>
      </SettingsPanel>
    </SettingsSection>
  );
}
