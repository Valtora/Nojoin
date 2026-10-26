import { ButtonBase } from "@/components/ui/nativeBases";
import { Download, Loader2, Trash2 } from "lucide-react";

import {
  DownloadProgress,
  ModelPreparationTarget,
  ModelSource,
  ModelStatusInfo,
  SystemModelStatus,
} from "@/types";
import { cn } from "@/lib/cn";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import SettingsStatusBadge from "./SettingsStatusBadge";
import { SETTINGS_BUTTON_SECONDARY } from "./settingsControls";
import { loadingButtonProps } from "@/components/ui/loadingButton";

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
];

/** Why Delete is not offered for a model kept where Nojoin does not delete. */
const UNDELETABLE_REASON: Partial<Record<ModelSource, string>> = {
  bundled: "Bundled with Nojoin, so it cannot be deleted here.",
  external: "Kept in a cache outside Nojoin's, so it is used but not deleted from here.",
};

function undeletableReason(status?: ModelStatusInfo): string | undefined {
  return status?.source ? UNDELETABLE_REASON[status.source] : undefined;
}

const ICON_BUTTON = cn(SETTINGS_BUTTON_SECONDARY, "h-9 w-9 px-0");

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
      description="Required models download on first run. A missing model downloads when it is first needed, which holds up live transcription and Meeting Edge until it arrives."
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

      {DEPENDENCY_MODELS.map((model) => {
        const status = modelStatus?.[model.id];
        const reason = undeletableReason(status);
        const busy = deleting === model.id;

        return (
          <SettingsRow
            key={model.id}
            label={model.label}
            description={
              <>
                {model.desc}
                {status?.downloaded && reason && (
                  <span className="block">{reason}</span>
                )}
                {!status?.downloaded && (status?.checked_paths?.length ?? 0) > 0 && (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-action-text">
                      Paths checked
                    </summary>
                    <ul className="mt-1 space-y-0.5 font-mono break-all">
                      {status?.checked_paths.map((path) => <li key={path}>{path}</li>)}
                    </ul>
                  </details>
                )}
              </>
            }
            badge={
              status?.downloaded ? (
                <SettingsStatusBadge tone="success">
                  {status.source === "bundled"
                    ? "Bundled"
                    : status.source === "external"
                      ? "External"
                      : "Ready"}
                </SettingsStatusBadge>
              ) : (
                <SettingsStatusBadge tone="error">Missing</SettingsStatusBadge>
              )
            }
            controlClassName="@min-[26rem]:min-w-0"
          >
            <div className="flex flex-wrap items-center gap-2 @min-[26rem]:justify-end">
              {status?.downloaded ? (
                // The description says why a bundled or external model has no
                // Delete, so there is no disabled button to explain on hover.
                reason === undefined && (
                  <ButtonBase
                    data-ui-fill="card"
                    data-ui-hover="inset"
                    data-ui-active="inset"
                    data-ui-border="full"
                    chrome="action"
                    type="button"
                    {...loadingButtonProps(busy, !isAdmin, () => handleDeleteModel(model.id))}
                    title="Delete this model"
                    aria-label={`Delete ${model.label}`}
                    className={ICON_BUTTON}
                  >
                    {busy ? (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    )}
                  </ButtonBase>
                )
              ) : (
                <>
                  {status?.partial && (
                    <ButtonBase
                      data-ui-fill="card"
                      data-ui-hover="inset"
                      data-ui-active="inset"
                      data-ui-border="full"
                      chrome="action"
                      type="button"
                      {...loadingButtonProps(busy, !isAdmin || preparationRunning, () => handleDeleteModel(model.id))}
                      title={
                        preparationRunning
                          ? "A model preparation is running and may still be writing this download"
                          : "Clear the partial download left in the model cache"
                      }
                      aria-label={`Clear partial download of ${model.label}`}
                      className={ICON_BUTTON}
                    >
                      {busy ? (
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                      ) : (
                        <Trash2 className="h-4 w-4" aria-hidden="true" />
                      )}
                    </ButtonBase>
                  )}
                  <ButtonBase
                    data-ui-fill="card"
                    data-ui-hover="inset"
                    data-ui-active="inset"
                    data-ui-border="full"
                    chrome="action"
                    type="button"
                    onClick={() => void startPreparation(model.target)}
                    disabled={!isAdmin || preparationRunning}
                    className={SETTINGS_BUTTON_SECONDARY}
                  >
                    <Download className="h-4 w-4" aria-hidden="true" />
                    Download
                  </ButtonBase>
                </>
              )}
            </div>
          </SettingsRow>
        );
      })}
    </SettingsCard>
  );
}
