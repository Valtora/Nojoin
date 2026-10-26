import {
  DownloadProgress,
  ModelPreparationTarget,
  SystemModelStatus,
} from "@/types";
import AiModelDependencyRow, { type DependencyModel } from "./AiModelDependencyRow";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";

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
const DEPENDENCY_MODELS: DependencyModel[] = [
  {
    id: "whisper",
    label: "Whisper",
    desc: "OpenAI's speech-to-text model, MIT licence.",
    target: "core",
  },
  {
    id: "parakeet",
    label: "Parakeet",
    desc: "NVIDIA's FastConformer speech-to-text model.",
    target: "parakeet",
  },
  {
    id: "canary",
    label: "Canary 1B",
    desc: "NVIDIA's multilingual speech-to-text model.",
    target: "canary",
  },
  {
    id: "pyannote",
    label: "Speaker separation",
    desc: "Pyannote's diarisation model.",
    target: "core",
  },
  {
    id: "embedding",
    label: "Voice embedding",
    desc: "Recognises speakers from their voiceprints.",
    target: "core",
  },
  {
    id: "segmentation",
    label: "Overlap detection",
    desc: "Pyannote's model for two people speaking at once.",
    target: "core",
  },
  {
    id: "nemotron",
    label: "Nemotron",
    desc: "Optional speaker diarisation. Used only on CUDA workers with the automatic speaker engine.",
    target: "nemotron",
  },
];

/** The speech and speaker models on the server. Admins only. */
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
    <SettingsCard
      id="ai-providers-model-assets"
      title="Model downloads"
      description="Required speech models download on first run or when first needed, holding up live transcription and Meeting Edge until they arrive. Optional Nemotron speaker models prepare during the final pass after capture ends."
    >
      {preparationRunning && (
        <SettingsBlock label="Preparing models" aside={<span className="text-sm tabular-nums text-foreground">{progressPercent}%</span>}>
          <div
            role="progressbar"
            aria-label="Model preparation"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progressPercent}
            className="h-2 w-full overflow-hidden rounded-full bg-action-tint"
          >
            <div
              className="h-full rounded-full bg-action transition-all duration-500"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
          <p className="mt-2 text-xs contrast-helper">
            {downloadProgress?.message || "Waiting for the worker..."}
            {downloadProgress?.eta ? ` (${downloadProgress.eta} left)` : ""}
          </p>
        </SettingsBlock>
      )}

      {DEPENDENCY_MODELS.map((model) => (
        <AiModelDependencyRow
          key={model.id}
          model={model}
          status={modelStatus?.[model.id]}
          deleting={deleting}
          handleDeleteModel={handleDeleteModel}
          isAdmin={isAdmin}
          preparationRunning={preparationRunning}
          startPreparation={startPreparation}
        />
      ))}
    </SettingsCard>
  );
}
