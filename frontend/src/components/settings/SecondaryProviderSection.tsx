import { Check, HelpCircle, Loader2, RefreshCw, Server } from "lucide-react";

import { Settings } from "@/types";
import { listModels } from "@/lib/api";
import Tooltip from "@/components/ui/Tooltip";
import SettingsCallout from "./SettingsCallout";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsStatusBadge from "./SettingsStatusBadge";
import type { AISettingsModels } from "./useAISettingsModels";
import {
  DEFAULT_OLLAMA_CONTEXT_WINDOW,
  getSecondaryProviderApiKey,
  getSecondaryProviderLiveModel,
  getSecondaryProviderModel,
  parseContextWindow,
  withSecondaryProviderLiveModel,
  withSecondaryProviderModel,
} from "./aiSettingsModels";

const SELECT_CLASS =
  "w-full p-2.5 rounded-lg border border-control-border bg-control-bg text-foreground focus:ring-2 focus:ring-action outline-none transition-all disabled:opacity-50";

interface SecondaryProviderSectionProps {
  settings: Settings;
  /** Debounced apply (Ollama URL and context window). */
  onUpdate: (newSettings: Settings) => void;
  /** Apply and save immediately (model selects). */
  onPersist: (newSettings: Settings) => void;
  models: AISettingsModels;
}

/**
 * Admin-only "Fallback provider" section. All secondary_* fields are
 * install-wide (and some are .env-overridden), so the orchestrator renders this
 * only for admins. Used automatically when the primary provider — or a user's
 * CLI subscription — is unavailable.
 */
export default function SecondaryProviderSection({
  settings,
  onUpdate,
  onPersist,
  models,
}: SecondaryProviderSectionProps) {
  const sp = settings.secondary_llm_provider;

  if (!sp) {
    return (
      <SettingsCard
        title="Fallback Provider"
        badge={<SettingsStatusBadge tone="neutral">Managed by server</SettingsStatusBadge>}
        description="Used automatically when the primary provider is unavailable."
      >
        <SettingsCallout tone="neutral" className="space-y-4">
          No fallback provider is configured. Set{" "}
          <code className="bg-surface-inset px-1 py-0.5 rounded">
            SECONDARY_LLM_PROVIDER
          </code>{" "}
          in the server&apos;s{" "}
          <code className="bg-surface-inset px-1 py-0.5 rounded">
            .env
          </code>{" "}
          file to enable automatic fallback for all AI features when the primary
          provider fails.
        </SettingsCallout>
      </SettingsCard>
    );
  }

  const isOllama = sp === "ollama";
  const configured = isOllama
    ? Boolean(settings.secondary_ollama_api_url)
    : Boolean(getSecondaryProviderApiKey(settings, sp));
  const secondaryModel = getSecondaryProviderModel(settings, sp);
  const secondaryLiveModel = getSecondaryProviderLiveModel(settings, sp);

  const refreshModels = () => {
    const url = isOllama ? settings.secondary_ollama_api_url : undefined;
    models.setSecondaryFetchingModels(true);
    listModels(sp, "", url)
      .then((res) => models.setSecondaryAvailableModels(res.models))
      .catch(console.error)
      .finally(() => models.setSecondaryFetchingModels(false));
  };

  const applyModel = (value: string) => {
    const updates = withSecondaryProviderModel(settings, sp, value);
    if (updates) onPersist(updates);
  };
  const applyLiveModel = (value: string) => {
    const updates = withSecondaryProviderLiveModel(settings, sp, value);
    if (updates) onPersist(updates);
  };

  return (
    <SettingsCard
      title="Fallback Provider"
      badge={<SettingsStatusBadge tone="neutral">Managed by server</SettingsStatusBadge>}
      description="Used automatically when the primary provider — or a user's own Claude or ChatGPT subscription — is unavailable."
    >
      <div className="space-y-4">
        <SettingsBlock className="space-y-6">
          <div className="p-4 bg-surface-inset border border-surface-border rounded-xl flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="text-sm font-semibold text-foreground flex items-center gap-2">
                Secondary AI provider:{" "}
                <span className="capitalize text-action-text">
                  {sp}
                </span>
              </div>
              <p className="text-xs text-contrast-helper mt-1">
                Configured via{" "}
                <code className="bg-surface-inset px-1 py-0.5 rounded">
                  SECONDARY_LLM_PROVIDER
                </code>{" "}
                in the server&apos;s{" "}
                <code className="bg-surface-inset px-1 py-0.5 rounded">
                  .env
                </code>{" "}
                file.
              </p>
            </div>
            <SettingsStatusBadge tone={configured ? "success" : "warning"}>
              {configured ? "Configured" : "Not configured"}
            </SettingsStatusBadge>
          </div>

          {isOllama && (
            <div className="grid grid-cols-1 @min-[42rem]:grid-cols-2 gap-6">
              <div>
                <label className="block text-sm font-medium text-contrast-muted mb-2">
                  Secondary Ollama API URL
                </label>
                <div className="relative">
                  <input
                    type="text"
                    value={
                      settings.secondary_ollama_api_url ||
                      "http://host.docker.internal:11434"
                    }
                    onChange={(e) =>
                      onUpdate({
                        ...settings,
                        secondary_ollama_api_url: e.target.value,
                      })
                    }
                    className="w-full pl-10 pr-4 py-2.5 rounded-lg border border-surface-border bg-surface-inset text-foreground focus:ring-2 focus:ring-action outline-none transition-all"
                  />
                  <Server className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-contrast-icon-muted" />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-contrast-muted mb-2">
                  Secondary Ollama context window
                </label>
                <input
                  type="number"
                  min={1024}
                  step={1024}
                  value={
                    settings.secondary_ollama_context_window ||
                    DEFAULT_OLLAMA_CONTEXT_WINDOW
                  }
                  onChange={(e) =>
                    onUpdate({
                      ...settings,
                      secondary_ollama_context_window: parseContextWindow(
                        e.target.value,
                      ),
                    })
                  }
                  className="w-full px-4 py-2.5 rounded-lg border border-surface-border bg-surface-inset text-foreground focus:ring-2 focus:ring-action outline-none transition-all"
                />
              </div>
            </div>
          )}

          <div>
            <label className="text-sm font-medium text-contrast-muted mb-2 flex justify-between">
              <Tooltip
                content="The model for the fallback provider. Used when the primary is unavailable."
                position="right"
              >
                <span className="flex items-center gap-1 cursor-help">
                  Model{" "}
                  <HelpCircle className="w-3 h-3 text-contrast-helper" />
                </span>
              </Tooltip>
              <button
                onClick={refreshModels}
                disabled={models.secondaryFetchingModels}
                className="text-xs text-action-text hover:text-action-text flex items-center gap-1 disabled:opacity-50"
              >
                <RefreshCw
                  className={`w-3 h-3 ${models.secondaryFetchingModels ? "animate-spin" : ""}`}
                />{" "}
                Refresh
              </button>
            </label>
            <select
              value={secondaryModel}
              onChange={(e) => applyModel(e.target.value)}
              disabled={models.secondaryAvailableModels.length === 0}
              className={SELECT_CLASS}
            >
              <option value="" disabled>
                Select a model...
              </option>
              {models.secondaryAvailableModels.map((model) => (
                <option key={`secondary-${model}`} value={model}>
                  {model}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-sm font-medium text-contrast-muted mb-2 flex items-center gap-1">
              <Tooltip
                content="Optional separate model for Meeting Edge when using the fallback provider. Leave blank to reuse the fallback model."
                position="right"
              >
                <span className="flex items-center gap-1 cursor-help">
                  Meeting Edge model{" "}
                  <HelpCircle className="w-3 h-3 text-contrast-helper" />
                </span>
              </Tooltip>
            </label>
            <select
              value={secondaryLiveModel}
              onChange={(e) => applyLiveModel(e.target.value)}
              disabled={models.secondaryAvailableModels.length === 0}
              className={SELECT_CLASS}
            >
              <option value="">Use fallback model</option>
              {models.secondaryAvailableModels.map((model) => (
                <option key={`secondary-live-${model}`} value={model}>
                  {model}
                </option>
              ))}
            </select>
          </div>

          {configured && (
            <div>
              <button
                onClick={() => models.handleValidate(sp || "gemini")}
                disabled={Boolean(models.validating)}
                className="px-4 py-2.5 bg-surface-inset text-foreground border border-control-border rounded-lg hover:bg-surface-inset transition-colors disabled:opacity-50 flex items-center gap-2 text-sm font-semibold"
              >
                {models.validating === sp ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Check className="w-4 h-4" />
                )}
                Validate fallback connection
              </button>
              {models.validationMsg && models.validationMsg.provider === sp && (
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
