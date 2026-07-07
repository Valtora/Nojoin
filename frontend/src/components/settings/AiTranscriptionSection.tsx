import { useState } from "react";
import { HelpCircle } from "lucide-react";

import { Settings } from "@/types";
import Tooltip from "@/components/ui/Tooltip";
import SettingsPanel from "./SettingsPanel";
import SettingsSection from "./SettingsSection";
import WhisperModelModal from "./WhisperModelModal";

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
  onPersist: (newSettings: Settings) => void;
  isAdmin: boolean;
}

/** Admin-only "Transcription model" section. */
export default function AiTranscriptionSection({
  settings,
  onPersist,
  isAdmin,
}: AiTranscriptionSectionProps) {
  const [showWhisperModal, setShowWhisperModal] = useState(false);

  return (
    <SettingsSection
      eyebrow="Administration"
      title="Transcription model"
      description="Choose the engine Nojoin uses for live and final transcription during normal recording."
      width="regular"
    >
      <SettingsPanel className="mx-auto max-w-3xl space-y-4">
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
            onChange={(e) =>
              onPersist({
                ...settings,
                transcription_backend: e.target.value,
              })
            }
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
              <div className="group relative">
                <HelpCircle className="w-4 h-4 text-gray-500 dark:text-gray-400 cursor-help" />
                <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-2 hidden group-hover:block w-80 p-4 bg-gray-900 text-white text-xs rounded-lg shadow-xl z-50 pointer-events-none">
                  <div className="font-bold mb-2 text-sm">Available Models</div>
                  <div className="grid grid-cols-5 gap-2 border-b border-gray-700 pb-2 mb-2 font-semibold">
                    <div className="col-span-1">Size</div>
                    <div className="col-span-1">Params</div>
                    <div className="col-span-1">VRAM</div>
                    <div className="col-span-1">Speed</div>
                  </div>
                  {WHISPER_MODELS.map((m) => (
                    <div key={m.id} className="grid grid-cols-5 gap-2 mb-1">
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
              </div>
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
                        (m) => m.id === (settings.whisper_model_size || "turbo"),
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
              variant. Missing model files are prepared automatically after the
              change is saved.
            </p>
          </div>
        )}
        <WhisperModelModal
          isOpen={showWhisperModal}
          onClose={() => setShowWhisperModal(false)}
          currentSize={settings.whisper_model_size || "turbo"}
          isAdmin={isAdmin}
          onUpdate={(newSize) =>
            onPersist({ ...settings, whisper_model_size: newSize })
          }
        />
      </SettingsPanel>
    </SettingsSection>
  );
}
