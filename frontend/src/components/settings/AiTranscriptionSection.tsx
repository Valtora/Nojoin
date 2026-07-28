import { useState } from "react";
import { HelpCircle } from "lucide-react";

import { Settings } from "@/types";
import { getModelsStatus } from "@/lib/api";
import Tooltip from "@/components/ui/Tooltip";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import WhisperModelModal from "./WhisperModelModal";
import ModelDownloadPromptModal from "./ModelDownloadPromptModal";
import type { AISettingsModels } from "./useAISettingsModels";

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

interface AiTranscriptionSectionProps {
  settings: Settings;
  /** Apply and save immediately (engine and model are discrete controls). */
  onPersist: (newSettings: Settings) => void | Promise<void>;
  isAdmin: boolean;
  models: AISettingsModels;
}

/** Admin-only "Transcription model" section. */
export default function AiTranscriptionSection({
  settings,
  onPersist,
  isAdmin,
  models,
}: AiTranscriptionSectionProps) {
  const [showWhisperModal, setShowWhisperModal] = useState(false);
  const [downloadPromptLabel, setDownloadPromptLabel] = useState<string | null>(
    null,
  );
  const [promptBusy, setPromptBusy] = useState(false);

  /**
   * Save the model change, then offer to fetch it.
   *
   * The save has to land first: preparation resolves the target from the saved
   * settings, and a model already on disk should not raise a prompt at all.
   */
  const applyModelChange = async (
    next: Settings,
    statusKey: "whisper" | "parakeet" | "canary",
    whisperSize: string,
    label: string,
  ) => {
    await onPersist(next);
    models.refreshStatus();

    try {
      const status = await getModelsStatus(whisperSize);
      if (!status?.[statusKey]?.downloaded) {
        setDownloadPromptLabel(label);
      }
    } catch (e: unknown) {
      // A status check that fails is not a reason to block the setting change;
      // the model still downloads lazily on first use.
      console.error("Failed to check model status after change", e);
    }
  };

  const confirmDownloadNow = async () => {
    setPromptBusy(true);
    await models.startPreparation("active");
    setPromptBusy(false);
    setDownloadPromptLabel(null);
  };

  const whisperLabelFor = (size: string) =>
    `Whisper ${WHISPER_MODELS.find((m) => m.id === size)?.label || size}`;

  return (
    <SettingsCard
      title="Transcription model"
      description="Choose the engine Nojoin uses for live and final transcription during normal recording."
    >
      <SettingsBlock className="mx-auto max-w-3xl space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            <Tooltip
              content="Select the transcription engine used for speech to text."
              position="right"
            >
              <span className="flex items-center gap-1 cursor-help">
                Transcription engine{" "}
                <HelpCircle className="w-3 h-3 text-gray-500 dark:text-gray-400" />
              </span>
            </Tooltip>
          </label>
          <select
            value={settings.transcription_backend || "whisper"}
            onChange={(e) => {
              const engine = e.target.value as "whisper" | "parakeet" | "canary";
              const whisperSize = settings.whisper_model_size || "turbo";
              const label =
                engine === "whisper"
                  ? whisperLabelFor(whisperSize)
                  : engine === "parakeet"
                    ? "Parakeet"
                    : "Canary 1B";
              void applyModelChange(
                { ...settings, transcription_backend: engine },
                engine,
                whisperSize,
                label,
              );
            }}
            disabled={!isAdmin}
            className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none transition-all"
          >
            <option value="whisper">Whisper</option>
            <option value="parakeet">Parakeet (NVIDIA)</option>
            <option value="canary">Canary 1B (NVIDIA)</option>
          </select>
        </div>
        {(settings.transcription_backend || "whisper") === "parakeet" ? (
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Parakeet Model
            </label>
            <div className="flex items-center gap-4 p-4 rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700">
              <div className="flex-1">
                <div className="font-semibold text-gray-900 dark:text-white">
                  {settings.parakeet_model || "parakeet-tdt-0.6b-v3"}
                </div>
                <p className="mt-1 text-xs contrast-helper">
                  Fast NVIDIA transcription with slightly lower accuracy and
                  fewer supported languages than Whisper.
                </p>
              </div>
            </div>
          </div>
        ) : (settings.transcription_backend || "whisper") === "canary" ? (
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Canary Model
            </label>
            <div className="flex items-center gap-4 p-4 rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700">
              <div className="flex-1">
                <div className="font-semibold text-gray-900 dark:text-white">
                  {settings.canary_model || "nemo-canary-1b-v2"}
                </div>
                <p className="mt-1 text-xs contrast-helper">
                  Current active model for transcription.
                </p>
              </div>
            </div>
          </div>
        ) : (
          <div>
            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-2">
              Whisper Model Size
              <button
                type="button"
                className="group relative inline-flex"
                aria-label="Show available Whisper models"
              >
                <HelpCircle className="w-4 h-4 text-gray-500 dark:text-gray-400 cursor-help" />
                <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-2 hidden max-w-[calc(100vw-2rem)] w-80 p-4 bg-gray-900 text-white text-xs rounded-lg shadow-xl z-50 pointer-events-none group-hover:block group-focus:block">
                  <div className="font-bold mb-2 text-sm">Available Models</div>
                  <div className="grid grid-cols-4 gap-2 border-b border-gray-700 pb-2 mb-2 font-semibold text-left">
                    <div>Size</div>
                    <div>Params</div>
                    <div>VRAM</div>
                    <div>Speed</div>
                  </div>
                  {WHISPER_MODELS.map((m) => (
                    <div
                      key={m.id}
                      className="grid grid-cols-4 gap-2 mb-1 text-left"
                    >
                      <div className="col-span-1 font-medium text-orange-400">
                        {m.label}
                      </div>
                      <div className="col-span-1 text-gray-300">{m.params}</div>
                      <div className="col-span-1 text-gray-300">{m.vram}</div>
                      <div className="col-span-1 text-gray-300">{m.speed}</div>
                    </div>
                  ))}
                  <div className="mt-2 text-gray-300 italic">
                    Turbo is the recommended default for best balance of speed
                    and accuracy.
                  </div>
                </div>
              </button>
            </label>

            <div className="flex items-center gap-4 p-4 rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700">
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-gray-900 dark:text-white">
                    {WHISPER_MODELS.find(
                      (m) => m.id === (settings.whisper_model_size || "turbo"),
                    )?.label || settings.whisper_model_size}
                  </span>
                  <span className="text-sm text-gray-500">
                    (
                    {
                      WHISPER_MODELS.find(
                        (m) =>
                          m.id === (settings.whisper_model_size || "turbo"),
                      )?.vram
                    }{" "}
                    VRAM)
                  </span>
                </div>
                <p className="mt-1 text-xs contrast-helper">
                  Current active model for transcription.
                </p>
              </div>
              <button
                onClick={() => setShowWhisperModal(true)}
                disabled={!isAdmin}
                className="px-4 py-2 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors shadow-sm disabled:opacity-50"
              >
                Change Model
              </button>
            </div>
            <p className="mt-2 text-xs contrast-helper">
              Click &apos;Change Model&apos; to select a different Whisper model
              variant. If the model is not on the server yet, Nojoin asks
              whether to download it now or on first use.
            </p>
          </div>
        )}
        <WhisperModelModal
          isOpen={showWhisperModal}
          onClose={() => setShowWhisperModal(false)}
          currentSize={settings.whisper_model_size || "turbo"}
          isAdmin={isAdmin}
          onUpdate={(newSize) => {
            void applyModelChange(
              { ...settings, whisper_model_size: newSize },
              "whisper",
              newSize,
              whisperLabelFor(newSize),
            );
          }}
        />

        <ModelDownloadPromptModal
          isOpen={downloadPromptLabel !== null}
          modelLabel={downloadPromptLabel || ""}
          busy={promptBusy}
          onDownloadNow={() => void confirmDownloadNow()}
          onLater={() => setDownloadPromptLabel(null)}
        />
      </SettingsBlock>
    </SettingsCard>
  );
}
