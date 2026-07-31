import { AlertTriangle, ArrowRight, CheckCircle, Loader2, RefreshCw } from "lucide-react";

import { WHISPER_MODELS } from "@/lib/whisperModels";

interface HuggingFaceStepProps {
  formData: {
    hf_token: string;
    whisper_model_size: string;
  };
  loading: boolean;
  pyannoteModelsReady: boolean;
  bundledPyannoteModelsReady: boolean;
  onInputChange: (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
  ) => void;
  onReloadConfig: () => void;
  onSubmit: () => void;
}

export default function HuggingFaceStep({
  formData,
  loading,
  pyannoteModelsReady,
  bundledPyannoteModelsReady,
  onInputChange,
  onReloadConfig,
  onSubmit,
}: HuggingFaceStepProps) {
  const diarizationMissing = !formData.hf_token && !pyannoteModelsReady;

  return (
    <div className="space-y-4">
      <div className="space-y-6">
        <div className="text-center mb-6">
          <h2 className="text-xl font-semibold text-foreground">
            Transcription &amp; Speaker Models
          </h2>
          <p className="text-sm text-contrast-helper mt-2">
            Choose the transcription model and review speaker identification
          </p>
        </div>

        <div>
          <label
            htmlFor="setup-whisper-model"
            className="block text-sm font-medium text-contrast-muted mb-1"
          >
            Transcription model
          </label>
          <select
            id="setup-whisper-model"
            name="setup-whisper-model"
            data-field-key="whisper_model_size"
            value={formData.whisper_model_size}
            onChange={onInputChange}
            className="w-full px-4 py-2 rounded-lg border border-control-border bg-control-bg text-foreground focus:ring-2 focus-visible:outline-focus-ring outline-none"
          >
            {WHISPER_MODELS.map((model) => (
              <option key={model.id} value={model.id}>
                {model.label} — {model.params} params, {model.vram} VRAM,{" "}
                {model.speed} speed
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-contrast-helper">
            Turbo (default) is recommended for GPU servers. On CPU-only
            deployments, Small or Base processes much faster. You can change
            this later in Settings &gt; AI.
          </p>
        </div>

        {diarizationMissing ? (
          <>
            <div className="p-4 bg-status-warning-bg border border-status-warning-border rounded-xl flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-status-warning-fg shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-semibold text-status-warning-fg">
                  Speaker Diarization Disabled
                </p>
                <p className="text-xs text-status-warning-fg mt-1 leading-relaxed">
                  Without a Hugging Face token configured in your server environment (.env), Nojoin will record and transcribe meetings, but it will not be able to identify who is speaking (diarization).
                </p>
              </div>
            </div>

            <div className="p-4 bg-surface-inset/40 border border-surface-border rounded-xl text-xs space-y-2 text-contrast-helper">
              <p className="font-semibold text-foreground">To enable speaker identification:</p>
              <ol className="list-decimal list-inside space-y-1">
                <li>Create a read token on <a href="https://huggingface.co/settings/tokens" target="_blank" rel="noopener noreferrer" className="underline font-semibold text-status-info-fg">Hugging Face</a>.</li>
                <li>Accept the terms of service for <code className="bg-surface-inset px-1 rounded">pyannote/speaker-diarization-community-1</code>.</li>
                <li>Add <code className="bg-surface-inset px-1 rounded">HF_TOKEN=your_token</code> to your <code className="bg-surface-inset px-1 rounded">.env</code>.</li>
                <li>Restart your docker containers.</li>
              </ol>
            </div>

            <div className="flex flex-col gap-3 mt-6">
              <button
                type="button"
                onClick={onReloadConfig}
                disabled={loading}
                className="w-full border border-control-border bg-surface-card hover:bg-surface-inset text-foreground font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
              >
                {loading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <RefreshCw className="w-4 h-4" />
                )}
                Check Config Again
              </button>
              <button
                type="button"
                onClick={onSubmit}
                className="w-full border border-control-border text-contrast-muted hover:bg-surface-inset py-2.5 rounded-lg font-medium transition-colors"
              >
                Finish Setup (Disable Speaker Diarization)
              </button>
            </div>
          </>
        ) : (
          <>
            {!formData.hf_token ? (
              <>
                <div className="p-4 bg-status-success-bg border border-status-success-border rounded-xl flex items-start gap-3">
                  <CheckCircle className="w-5 h-5 text-status-success-fg shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-semibold text-status-success-fg">
                      Speaker Diarization Ready
                    </p>
                    <p className="text-xs text-status-success-fg mt-1 leading-relaxed">
                      Nojoin found local Pyannote model assets on the server, so speaker diarization can run without a Hugging Face token.
                    </p>
                  </div>
                </div>

                <div className="p-4 bg-surface-inset/40 border border-surface-border rounded-xl text-xs space-y-2 text-contrast-helper">
                  <p className="font-semibold text-foreground">
                    {bundledPyannoteModelsReady
                      ? "Bundled repo models are available."
                      : "Local cached models are available."}
                  </p>
                  <p>
                    A Hugging Face token is optional here. You only need one later if you want to refresh these Pyannote assets from upstream.
                  </p>
                </div>
              </>
            ) : (
              <>
                <div className="p-4 bg-status-success-bg border border-status-success-border rounded-xl flex items-center gap-3">
                  <CheckCircle className="w-6 h-6 text-status-success-fg shrink-0" />
                  <div>
                    <p className="text-sm font-semibold text-status-success-fg">
                      Hugging Face Token Configured
                    </p>
                    <p className="text-xs text-status-success-fg mt-0.5">
                      Your Hugging Face token was found in the server environment (.env).
                    </p>
                  </div>
                </div>

                <div className="p-4 bg-status-info-bg rounded-xl text-xs text-status-info-fg">
                  Nojoin will use this token to prepare Pyannote speaker diarization and voice embedding models before your first recording.
                </div>
              </>
            )}

            <div className="flex gap-3 mt-6">
              <button
                type="button"
                onClick={onSubmit}
                className="w-full bg-action hover:bg-action-hover text-action-on font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
              >
                Finish Setup <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
