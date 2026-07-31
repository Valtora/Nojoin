import { Check, HelpCircle, Info, Loader2, RefreshCw, Server } from "lucide-react";

import { Settings } from "@/types";
import { listModels } from "@/lib/api";
import { isServerProviderConfigured } from "@/lib/aiAvailability";
import Tooltip from "@/components/ui/Tooltip";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsStatusBadge from "./SettingsStatusBadge";
import type { AISettingsModels } from "./useAISettingsModels";
import {
  DEFAULT_OLLAMA_CONTEXT_WINDOW,
  getModelOptionsForProvider,
  getSelectedModelForProvider,
  parseContextWindow,
  withSelectedModelForProvider,
} from "./aiSettingsModels";

const SELECT_CLASS =
  "w-full p-2.5 rounded-lg border border-control-border bg-surface-inset text-foreground focus:ring-2 focus:ring-action outline-none transition-all disabled:opacity-50";

interface ServerProviderSectionProps {
  settings: Settings;
  /** Debounced apply (continuous controls such as the context window). */
  onUpdate: (newSettings: Settings) => void;
  /** Apply and save immediately (discrete controls such as the model). */
  onPersist: (newSettings: Settings) => void;
  models: AISettingsModels;
}

/**
 * Admin-only "Server provider" section. Every field here is install-wide (see
 * config_manager.INSTALL_WIDE_AI_SETTING_KEYS) and some are overridden by the
 * server's .env; the orchestrator renders this section only for admins so
 * non-admins never see controls whose edits would be silently discarded.
 */
export default function ServerProviderSection({
  settings,
  onUpdate,
  onPersist,
  models,
}: ServerProviderSectionProps) {
  const providerConfigured = isServerProviderConfigured(settings);
  const mainModelOptions = getModelOptionsForProvider(
    settings,
    models.availableModels,
    "main",
  );
  const selectedMainModel = getSelectedModelForProvider(settings, "main");
  const isOllama = settings.llm_provider === "ollama";
  const provider = settings.llm_provider || "gemini";

  const refreshModels = () => {
    const url = provider === "ollama" ? settings.ollama_api_url : undefined;
    models.setFetchingModels(true);
    listModels(provider, "", url)
      .then((res) => models.setAvailableModels(res.models))
      .catch(console.error)
      .finally(() => models.setFetchingModels(false));
  };

  return (
    <SettingsCard
      title="Server provider"
      badge={<SettingsStatusBadge tone="neutral">Managed by server</SettingsStatusBadge>}
      description="The provider and model this server uses for every account on the server default. Some fields are set in the server's .env file and shown here for reference."
    >
      <div className="space-y-4">
        <SettingsBlock className="space-y-6">
          <div className="p-4 bg-surface-inset border border-surface-border rounded-xl flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="text-sm font-semibold text-foreground flex items-center gap-2">
                Active AI provider:{" "}
                <span className="capitalize text-action-text">
                  {settings.llm_provider || "None"}
                </span>
              </div>
              <p className="text-xs text-contrast-helper mt-1">
                The provider and API keys are configured in the server&apos;s
                environment variable file (
                <code className="bg-surface-inset px-1 py-0.5 rounded">
                  .env
                </code>
                ).
              </p>
            </div>
            <SettingsStatusBadge tone={providerConfigured ? "success" : "warning"}>
              {providerConfigured ? "Configured via server (.env)" : "Not configured"}
            </SettingsStatusBadge>
          </div>

          {isOllama && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div>
                <label className="block text-sm font-medium text-contrast-muted mb-2">
                  Ollama API URL
                </label>
                <div className="relative">
                  <input
                    type="text"
                    value={
                      settings.ollama_api_url ||
                      "http://host.docker.internal:11434"
                    }
                    disabled
                    className="w-full pl-10 pr-4 py-2.5 rounded-lg border border-surface-border bg-surface-inset text-contrast-helper cursor-not-allowed outline-none"
                  />
                  <Server className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-contrast-icon-muted" />
                </div>
                <p className="mt-1 text-xs text-status-warning-fg flex items-center gap-1">
                  <Info className="w-3 h-3" />
                  Local models run on your hardware. Performance depends on your
                  GPU/CPU.
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-contrast-muted mb-2">
                  Ollama context window
                </label>
                <input
                  type="number"
                  min={1024}
                  step={1024}
                  value={
                    settings.ollama_context_window ||
                    DEFAULT_OLLAMA_CONTEXT_WINDOW
                  }
                  onChange={(e) =>
                    onUpdate({
                      ...settings,
                      ollama_context_window: parseContextWindow(e.target.value),
                    })
                  }
                  className="w-full px-4 py-2.5 rounded-lg border border-surface-border bg-surface-inset text-foreground focus:ring-2 focus:ring-action outline-none transition-all"
                />
                <p className="mt-1 text-xs contrast-helper">
                  Passed to Ollama as <code>num_ctx</code> for full-context
                  meeting prompts.
                </p>
              </div>
            </div>
          )}

          <div>
            <label className="text-sm font-medium text-contrast-muted mb-2 flex justify-between">
              <Tooltip
                content="The model this provider uses for notes, titles, speaker inference, and chat."
                position="right"
              >
                <span className="flex items-center gap-1 cursor-help">
                  Model{" "}
                  <HelpCircle className="w-3 h-3 text-contrast-helper" />
                </span>
              </Tooltip>
              <button
                onClick={refreshModels}
                disabled={models.fetchingModels || !providerConfigured}
                className="text-xs text-action-text hover:text-action-text flex items-center gap-1 disabled:opacity-50"
              >
                <RefreshCw
                  className={`w-3 h-3 ${models.fetchingModels ? "animate-spin" : ""}`}
                />{" "}
                Refresh
              </button>
            </label>
            <select
              value={selectedMainModel}
              onChange={(e) =>
                onPersist(withSelectedModelForProvider(settings, "main", e.target.value))
              }
              disabled={mainModelOptions.length === 0 || !providerConfigured}
              className={SELECT_CLASS}
            >
              <option value="" disabled>
                Select a model...
              </option>
              {mainModelOptions.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
          </div>

          {providerConfigured && (
            <div>
              <button
                onClick={() => models.handleValidate(settings.llm_provider || "gemini")}
                disabled={Boolean(models.validating)}
                className="px-4 py-2.5 bg-surface-inset text-foreground border border-control-border rounded-lg hover:bg-surface-inset transition-colors disabled:opacity-50 flex items-center gap-2 text-sm font-semibold"
              >
                {models.validating === settings.llm_provider ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Check className="w-4 h-4" />
                )}
                Validate API connection
              </button>
              {models.validationMsg &&
                models.validationMsg.provider === settings.llm_provider && (
                  <p
                    className={`mt-2 text-sm font-semibold ${models.validationMsg.type === "success" ? "text-status-success-fg" : "text-status-danger-fg"}`}
                  >
                    {models.validationMsg.msg}
                  </p>
                )}
            </div>
          )}
        </SettingsBlock>
      </div>
    </SettingsCard>
  );
}
