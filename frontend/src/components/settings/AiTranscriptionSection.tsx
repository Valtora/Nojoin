import { useState } from "react";

import { Settings } from "@/types";
import { getModelsStatus } from "@/lib/api";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import WhisperModelModal from "./WhisperModelModal";
import ModelDownloadPromptModal from "./ModelDownloadPromptModal";
import type { AISettingsModels } from "./useAISettingsModels";
import {
  SETTINGS_BUTTON_SECONDARY,
  SETTINGS_SELECT_CLASS,
  SETTINGS_VALUE_CLASS,
} from "./settingsControls";

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

function describeWhisper(model: (typeof WHISPER_MODELS)[number] | undefined): string {
  if (!model) {
    return "Turbo suits most installations.";
  }
  const memory = model.vram.replace("~", "about ");
  const speed =
    model.id === "large"
      ? "the slowest and most accurate"
      : `about ${model.speed.replace("~", "").replace("x", "")} times as fast as Large`;
  return `${model.params} parameters, ${memory} of GPU memory, ${speed}. Turbo suits most installations.`;
}

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

  const engine = settings.transcription_backend || "whisper";
  const whisperSize = settings.whisper_model_size || "turbo";
  const whisper = WHISPER_MODELS.find((model) => model.id === whisperSize);

  return (
    <SettingsCard
      id="transcription-model"
      title="Transcription model"
      description="The speech-to-text engine every recording on this installation uses, live and after it ends. Choosing one that is not on the server asks whether to download it now."
    >
      <SettingsRow
        id="transcription-engine"
        label="Engine"
        controlId="transcription-engine-select"
        description="Whisper handles the most languages. Parakeet is faster and slightly less accurate. Canary 1B covers a set of European languages."
      >
        <select
          id="transcription-engine-select"
          aria-describedby="transcription-engine-select-help"
          value={engine}
          onChange={(e) => {
            const next = e.target.value as "whisper" | "parakeet" | "canary";
            const label =
              next === "whisper"
                ? whisperLabelFor(whisperSize)
                : next === "parakeet"
                  ? "Parakeet"
                  : "Canary 1B";
            void applyModelChange(
              { ...settings, transcription_backend: next },
              next,
              whisperSize,
              label,
            );
          }}
          disabled={!isAdmin}
          className={SETTINGS_SELECT_CLASS}
        >
          <option value="whisper">Whisper</option>
          <option value="parakeet">Parakeet (NVIDIA)</option>
          <option value="canary">Canary 1B (NVIDIA)</option>
        </select>
      </SettingsRow>

      {engine === "whisper" ? (
        <SettingsRow
          label="Whisper size"
          description={describeWhisper(whisper)}
          controlClassName="@min-[26rem]:min-w-0"
        >
          <div className="flex flex-wrap items-center gap-3 @min-[26rem]:justify-end">
            <p className={SETTINGS_VALUE_CLASS}>{whisper?.label || whisperSize}</p>
            <button
              type="button"
              onClick={() => setShowWhisperModal(true)}
              disabled={!isAdmin}
              className={SETTINGS_BUTTON_SECONDARY}
            >
              Change size
            </button>
          </div>
        </SettingsRow>
      ) : (
        <SettingsRow
          label="Model"
          description="The version this installation downloads for the engine."
        >
          <p className={`${SETTINGS_VALUE_CLASS} font-mono`}>
            {engine === "parakeet"
              ? settings.parakeet_model || "parakeet-tdt-0.6b-v3"
              : settings.canary_model || "nemo-canary-1b-v2"}
          </p>
        </SettingsRow>
      )}

      <WhisperModelModal
        isOpen={showWhisperModal}
        onClose={() => setShowWhisperModal(false)}
        currentSize={whisperSize}
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
    </SettingsCard>
  );
}
