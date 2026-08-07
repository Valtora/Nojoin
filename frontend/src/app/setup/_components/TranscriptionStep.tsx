import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle,
  RefreshCw,
} from "lucide-react";

import Button from "@/components/ui/Button";
import Select from "@/components/ui/Select";
import { WHISPER_MODELS } from "@/lib/whisperModels";

interface TranscriptionStepProps {
  whisperModelSize: string;
  hfTokenPresent: boolean;
  pyannoteModelsReady: boolean;
  bundledPyannoteModelsReady: boolean;
  reloadingConfig: boolean;
  onInputChange: (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
  ) => void;
  onReloadConfig: () => void;
  onBack: () => void;
  onSubmit: () => void;
}

/**
 * Transcription model and speaker-diarization readiness. This runs before the
 * account is created because the chosen Whisper size is what the owner-creation
 * call queues model preparation for.
 */
export default function TranscriptionStep({
  whisperModelSize,
  hfTokenPresent,
  pyannoteModelsReady,
  bundledPyannoteModelsReady,
  reloadingConfig,
  onInputChange,
  onReloadConfig,
  onBack,
  onSubmit,
}: TranscriptionStepProps) {
  const diarizationMissing = !hfTokenPresent && !pyannoteModelsReady;

  return (
    <div className="space-y-6">
      <div className="text-center mb-6">
        <h2 className="text-xl font-semibold text-foreground">
          Transcription &amp; Speaker Models
        </h2>
        <p className="text-sm text-contrast-helper mt-2">
          Choose the transcription model and review speaker identification
        </p>
      </div>

      <Select
        id="setup-whisper-model"
        name="setup-whisper-model"
        data-field-key="whisper_model_size"
        label="Transcription model"
        value={whisperModelSize}
        onChange={onInputChange}
        hint="Turbo (default) suits a server with an NVIDIA GPU. On a CPU-only deployment, Small or Base processes far faster. You can change this later in Settings > Transcription."
      >
        {WHISPER_MODELS.map((model) => (
          <option key={model.id} value={model.id}>
            {model.label} — {model.params} params, {model.vram} VRAM, {model.speed}{" "}
            speed
          </option>
        ))}
      </Select>

      {diarizationMissing ? (
        <>
          <div className="p-4 bg-status-warning-bg border border-status-warning-border rounded-xl flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-status-warning-fg shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-status-warning-fg">
                Speaker labels will be unavailable
              </p>
              <p className="text-xs text-status-warning-fg mt-1 leading-relaxed">
                No Hugging Face token and no local Pyannote assets were found.
                Nojoin will record and transcribe meetings, but it will not be
                able to separate who said what.
              </p>
            </div>
          </div>

          <p className="text-xs text-contrast-helper leading-relaxed">
            To enable it: create a read token on{" "}
            <a
              href="https://huggingface.co/settings/tokens"
              target="_blank"
              rel="noopener noreferrer"
              className="text-action-text hover:text-action-text-hover"
            >
              Hugging Face
            </a>
            , accept the terms for{" "}
            <code className="bg-surface-inset px-1 rounded">
              pyannote/speaker-diarization-community-1
            </code>
            , set{" "}
            <code className="bg-surface-inset px-1 rounded">HF_TOKEN</code> in the
            server&apos;s <code className="bg-surface-inset px-1 rounded">.env</code>,
            and restart the stack. Keep this tab open: your progress is kept.
          </p>

          <Button
            variant="secondary"
            fullWidth
            loading={reloadingConfig}
            onClick={onReloadConfig}
            iconLeft={<RefreshCw className="w-4 h-4" />}
          >
            Check config again
          </Button>
        </>
      ) : (
        <div className="p-4 bg-status-success-bg border border-status-success-border rounded-xl flex items-start gap-3">
          <CheckCircle className="w-5 h-5 text-status-success-fg shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-status-success-fg">
              Speaker identification ready
            </p>
            <p className="text-xs text-status-success-fg mt-1 leading-relaxed">
              {hfTokenPresent
                ? "Using the Hugging Face token from the server environment."
                : bundledPyannoteModelsReady
                  ? "Using the bundled Pyannote assets. No Hugging Face token needed."
                  : "Using the cached Pyannote assets. No Hugging Face token needed."}
            </p>
          </div>
        </div>
      )}

      <div className="flex gap-3">
        <Button
          variant="ghost"
          onClick={onBack}
          iconLeft={<ArrowLeft className="w-4 h-4" />}
        >
          Back
        </Button>
        <Button
          variant="primary"
          className="flex-1"
          onClick={onSubmit}
          iconRight={<ArrowRight className="w-4 h-4" />}
        >
          Next Step
        </Button>
      </div>
    </div>
  );
}
