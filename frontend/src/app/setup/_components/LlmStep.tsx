import { AlertTriangle, ArrowRight, Loader2, RefreshCw } from "lucide-react";

interface LlmStepProps {
  formData: {
    llm_provider: string;
    selected_model: string;
  };
  loading: boolean;
  llmConfigMissing: boolean;
  validatingLLM: boolean;
  llmValidationMsg: { valid: boolean; msg: string } | null;
  availableModels: string[];
  onInputChange: (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
  ) => void;
  onReloadConfig: () => void;
  onConfirmSkip: () => void;
  onSkip: () => void;
  onSubmit: () => void;
}

export default function LlmStep({
  formData,
  loading,
  llmConfigMissing,
  validatingLLM,
  llmValidationMsg,
  availableModels,
  onInputChange,
  onReloadConfig,
  onConfirmSkip,
  onSkip,
  onSubmit,
}: LlmStepProps) {
  return (
    <div className="space-y-4">
      {llmConfigMissing ? (
        <div className="space-y-6">
          <div className="text-center mb-6">
            <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
              AI Provider Configuration Missing
            </h2>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">
              No API key or URL was detected in the server environment (<code className="bg-gray-100 dark:bg-gray-900 px-1 py-0.5 rounded">.env</code>) for provider: <strong className="capitalize">{formData.llm_provider || "none"}</strong>.
            </p>
          </div>

          <div className="p-4 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-xl flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-yellow-600 dark:text-yellow-400 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-yellow-800 dark:text-yellow-300">
                Important Features Will Be Disabled
              </p>
              <p className="text-xs text-yellow-700 dark:text-yellow-400 mt-1 leading-relaxed">
                Without a properly configured LLM provider and API key, you will not be able to use key features like <strong>Meeting Edge</strong> (live intelligence), <strong>Meeting Note generation</strong>, and <strong>Speaker Inference</strong>.
              </p>
            </div>
          </div>

          <div className="p-4 bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-700 rounded-xl text-xs space-y-2 text-gray-600 dark:text-gray-300">
            <p className="font-semibold text-gray-900 dark:text-white">To resolve this:</p>
            <ol className="list-decimal list-inside space-y-1">
              <li>Open your server&apos;s <code className="bg-gray-200 dark:bg-gray-800 px-1 rounded">.env</code> file.</li>
              <li>Set <code className="bg-gray-200 dark:bg-gray-800 px-1 rounded">LLM_PROVIDER</code> (e.g. <code className="bg-gray-200 dark:bg-gray-800 px-1 rounded">gemini</code>, <code className="bg-gray-200 dark:bg-gray-800 px-1 rounded">openai</code>, <code className="bg-gray-200 dark:bg-gray-800 px-1 rounded">anthropic</code>, or <code className="bg-gray-200 dark:bg-gray-800 px-1 rounded">ollama</code>).</li>
              <li>Set the corresponding API key variable (e.g. <code className="bg-gray-200 dark:bg-gray-800 px-1 rounded">GEMINI_API_KEY</code>).</li>
              <li>Restart your docker containers (e.g. <code className="bg-gray-200 dark:bg-gray-800 px-1 rounded">docker compose restart</code>).</li>
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
              onClick={onConfirmSkip}
              className="w-full border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 py-2.5 rounded-lg font-medium transition-colors"
            >
              Proceed Without LLM
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="text-center mb-6">
            <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
              AI Configuration
            </h2>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">
              Active LLM Provider: <strong className="capitalize">{formData.llm_provider}</strong>
            </p>
            <span className="inline-flex items-center mt-2 px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400">
              Configured via Server Environment (.env)
            </span>
          </div>

          {validatingLLM ? (
            <div className="flex flex-col items-center justify-center py-8 space-y-3">
              <Loader2 className="w-8 h-8 animate-spin text-orange-500" />
              <p className="text-sm text-gray-500">Connecting to provider and loading models...</p>
            </div>
          ) : (
            <>
              {llmValidationMsg && !llmValidationMsg.valid && (
                <div className="p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl flex items-start gap-3">
                  <AlertTriangle className="w-5 h-5 text-red-600 dark:text-red-400 shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-semibold text-red-800 dark:text-red-300">
                      Connection Validation Failed
                    </p>
                    <p className="text-xs text-red-700 dark:text-red-400 mt-1">
                      {llmValidationMsg.msg}
                    </p>
                    <p className="text-xs text-gray-500 mt-2">
                      Please verify your API key in the server&apos;s <code className="bg-gray-100 dark:bg-gray-900 px-1 py-0.5 rounded">.env</code> and restart the server if needed.
                    </p>
                  </div>
                </div>
              )}

              {availableModels.length > 0 && (
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Select Model
                    </label>
                    <select
                      name="selected_model"
                      value={formData.selected_model}
                      onChange={onInputChange}
                      className="w-full px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none"
                    >
                      {availableModels.map((model) => (
                        <option key={model} value={model}>
                          {model}
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-gray-500 mt-1">
                      Select the model to use for chat and notes.
                    </p>
                  </div>

                  {/* Model Recommendations */}
                  {formData.llm_provider === "gemini" && (
                    <div className="mt-2 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-100 dark:border-blue-800 text-xs text-blue-800 dark:text-blue-200">
                      <p className="font-semibold mb-1">Recommended Models:</p>
                      <ul className="list-disc list-inside space-y-0.5">
                        <li>
                          <strong>gemini-flash-latest</strong>: Faster responses, good for simple transcripts.
                        </li>
                        <li>
                          <strong>gemini-pro-latest</strong>: Better reasoning, recommended for complex meetings.
                        </li>
                      </ul>
                    </div>
                  )}
                  {formData.llm_provider === "openai" && (
                    <div className="mt-2 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-100 dark:border-blue-800 text-xs text-blue-800 dark:text-blue-200">
                      <p className="font-semibold mb-1">Recommended Models:</p>
                      <ul className="list-disc list-inside space-y-0.5">
                        <li>
                          <strong>GPT-5 mini (or later)</strong>: Faster, cost-effective for simple chat.
                        </li>
                        <li>
                          <strong>GPT-5.1 (or later)</strong>: Higher intelligence, recommended for complex analysis.
                        </li>
                      </ul>
                    </div>
                  )}
                  {formData.llm_provider === "anthropic" && (
                    <div className="mt-2 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-100 dark:border-blue-800 text-xs text-blue-800 dark:text-blue-200">
                      <p className="font-semibold mb-1">Recommended Models:</p>
                      <ul className="list-disc list-inside space-y-0.5">
                        <li>
                          <strong>Claude Haiku</strong>: Fast and efficient for simple chats.
                        </li>
                        <li>
                          <strong>Claude Sonnet</strong>: Good reasoning, best for medium complexity.
                        </li>
                        <li>
                          <strong>Claude Opus</strong>: Strong reasoning, best for complex meetings.
                        </li>
                      </ul>
                    </div>
                  )}
                </div>
              )}

              <div className="flex gap-3 mt-6">
                <button
                  type="button"
                  onClick={onSkip}
                  className="flex-1 px-4 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-lg font-medium transition-colors"
                >
                  Skip for now
                </button>
                <button
                  type="button"
                  onClick={onSubmit}
                  disabled={!llmValidationMsg?.valid || !formData.selected_model}
                  className="flex-1 bg-orange-600 hover:bg-orange-700 disabled:bg-gray-400 disabled:cursor-not-allowed text-white font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
                >
                  Next Step <ArrowRight className="w-4 h-4" />
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
