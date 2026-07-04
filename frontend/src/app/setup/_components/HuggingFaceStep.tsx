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
          <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
            Transcription &amp; Speaker Models
          </h2>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">
            Choose the transcription model and review speaker identification
          </p>
        </div>

        <div>
          <label
            htmlFor="setup-whisper-model"
            className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
          >
            Transcription model
          </label>
          <select
            id="setup-whisper-model"
            name="setup-whisper-model"
            data-field-key="whisper_model_size"
            value={formData.whisper_model_size}
            onChange={onInputChange}
            className="w-full px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none"
          >
            {WHISPER_MODELS.map((model) => (
              <option key={model.id} value={model.id}>
                {model.label} — {model.params} params, {model.vram} VRAM,{" "}
                {model.speed} speed
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            Turbo (default) is recommended for GPU servers. On CPU-only
            deployments, Small or Base processes much faster. You can change
            this later in Settings &gt; AI.
          </p>
        </div>

        {diarizationMissing ? (
          <>
            <div className="p-4 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-xl flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-yellow-600 dark:text-yellow-400 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-semibold text-yellow-800 dark:text-yellow-300">
                  Speaker Diarization Disabled
                </p>
                <p className="text-xs text-yellow-700 dark:text-yellow-400 mt-1 leading-relaxed">
                  Without a Hugging Face token configured in your server environment (.env), Nojoin will record and transcribe meetings, but it will not be able to identify who is speaking (diarization).
                </p>
              </div>
            </div>

            <div className="p-4 bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-700 rounded-xl text-xs space-y-2 text-gray-600 dark:text-gray-300">
              <p className="font-semibold text-gray-900 dark:text-white">To enable speaker identification:</p>
              <ol className="list-decimal list-inside space-y-1">
                <li>Create a read token on <a href="https://huggingface.co/settings/tokens" target="_blank" rel="noopener noreferrer" className="underline font-semibold text-blue-600 dark:text-blue-400">Hugging Face</a>.</li>
                <li>Accept the terms of service for <code className="bg-gray-200 dark:bg-gray-800 px-1 rounded">pyannote/speaker-diarization-community-1</code>.</li>
                <li>Add <code className="bg-gray-200 dark:bg-gray-800 px-1 rounded">HF_TOKEN=your_token</code> to your <code className="bg-gray-200 dark:bg-gray-800 px-1 rounded">.env</code>.</li>
                <li>Restart your docker containers.</li>
              </ol>
            </div>

            <div className="flex flex-col gap-3 mt-6">
              <button
                type="button"
                onClick={onReloadConfig}
                disabled={loading}
                className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
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
                className="w-full border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 py-2.5 rounded-lg font-medium transition-colors"
              >
                Finish Setup (Disable Speaker Diarization)
              </button>
            </div>
          </>
        ) : (
          <>
            {!formData.hf_token ? (
              <>
                <div className="p-4 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-xl flex items-start gap-3">
                  <CheckCircle className="w-5 h-5 text-green-600 dark:text-green-400 shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-semibold text-green-800 dark:text-green-300">
                      Speaker Diarization Ready
                    </p>
                    <p className="text-xs text-green-700 dark:text-green-400 mt-1 leading-relaxed">
                      Nojoin found local Pyannote model assets on the server, so speaker diarization can run without a Hugging Face token.
                    </p>
                  </div>
                </div>

                <div className="p-4 bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-700 rounded-xl text-xs space-y-2 text-gray-600 dark:text-gray-300">
                  <p className="font-semibold text-gray-900 dark:text-white">
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
                <div className="p-4 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-xl flex items-center gap-3">
                  <CheckCircle className="w-6 h-6 text-green-500 shrink-0" />
                  <div>
                    <p className="text-sm font-semibold text-green-800 dark:text-green-300">
                      Hugging Face Token Configured
                    </p>
                    <p className="text-xs text-green-700 dark:text-green-400 mt-0.5">
                      Your Hugging Face token was found in the server environment (.env).
                    </p>
                  </div>
                </div>

                <div className="p-4 bg-blue-50 dark:bg-blue-900/20 rounded-xl text-xs text-blue-800 dark:text-blue-200">
                  Nojoin will use this token to prepare Pyannote speaker diarization and voice embedding models before your first recording.
                </div>
              </>
            )}

            <div className="flex gap-3 mt-6">
              <button
                type="button"
                onClick={onSubmit}
                className="w-full bg-orange-600 hover:bg-orange-700 text-white font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
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
