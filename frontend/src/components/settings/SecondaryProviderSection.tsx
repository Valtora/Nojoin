import { Settings } from "@/types";
import { listModels } from "@/lib/api";
import { getSecondaryProviderGap } from "@/lib/aiAvailability";
import { aiProviderLabel } from "@/lib/aiProviders";
import SettingsBlock from "./SettingsBlock";
import SettingsCallout from "./SettingsCallout";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import type { AISettingsModels } from "./useAISettingsModels";
import {
  ContextWindowHelp,
  ModelPicker,
  ProviderStatusBadge,
  TestConnection,
} from "./aiProviderControls";
import {
  DEFAULT_OLLAMA_CONTEXT_WINDOW,
  getSecondaryProviderLiveModel,
  getSecondaryProviderModel,
  parseContextWindow,
  withSecondaryProviderLiveModel,
  withSecondaryProviderModel,
} from "./aiSettingsModels";
import { SETTINGS_INPUT_CLASS, SETTINGS_VALUE_CLASS, SETTINGS_CONTROL_COLUMN_WIDE } from "./settingsControls";

interface SecondaryProviderSectionProps {
  settings: Settings;
  /** Debounced apply (Ollama address and context window). */
  onUpdate: (newSettings: Settings) => void;
  /** Apply and save immediately (model selects). */
  onPersist: (newSettings: Settings) => void;
  models: AISettingsModels;
}

/**
 * The install's fallback provider, used when the default provider, or a
 * user's own subscription, cannot answer. Install-wide, so admins only.
 */
export default function SecondaryProviderSection({
  settings,
  onUpdate,
  onPersist,
  models,
}: SecondaryProviderSectionProps) {
  const provider = settings.secondary_llm_provider;
  const description =
    "Retries a request when the default provider, or someone's own subscription, cannot answer.";

  if (!provider) {
    return (
      <SettingsCard
        id="ai-providers-fallback"
        title="Fallback provider"
        description={description}
      >
        <SettingsRow
          label="Provider"
          description="Set SECONDARY_LLM_PROVIDER and its key in .env, then restart Nojoin, to turn the fallback on."
        >
          <p className={SETTINGS_VALUE_CLASS}>None</p>
        </SettingsRow>
      </SettingsCard>
    );
  }

  const providerLabel = aiProviderLabel(provider);
  const isOllama = provider === "ollama";
  const gap = getSecondaryProviderGap(settings);
  const hasCredential = gap !== "credential";
  const options = models.secondaryAvailableModels;
  const contextWindow =
    settings.secondary_ollama_context_window || DEFAULT_OLLAMA_CONTEXT_WINDOW;

  const refreshModels = () => {
    const url = isOllama ? settings.secondary_ollama_api_url : undefined;
    models.setSecondaryFetchingModels(true);
    listModels(provider, "", url)
      .then((res) => models.setSecondaryAvailableModels(res.models))
      .catch(console.error)
      .finally(() => models.setSecondaryFetchingModels(false));
  };

  const applyModel = (value: string) => {
    const updates = withSecondaryProviderModel(settings, provider, value);
    if (updates) onPersist(updates);
  };
  const applyLiveModel = (value: string) => {
    const updates = withSecondaryProviderLiveModel(settings, provider, value);
    if (updates) onPersist(updates);
  };

  return (
    <SettingsCard
      id="ai-providers-fallback"
      title="Fallback provider"
      description={description}
    >
      <SettingsRow
        label="Provider"
        description="Set by SECONDARY_LLM_PROVIDER in .env."
        badge={<ProviderStatusBadge gap={gap} />}
      >
        <p className={SETTINGS_VALUE_CLASS}>{providerLabel}</p>
      </SettingsRow>

      {gap !== null && (
        <SettingsBlock>
          <SettingsCallout tone="warning" title="The fallback is off">
            {gap === "model"
              ? "No fallback model is selected, so a failed request is not retried. Choose a model to turn the fallback on."
              : `${providerLabel} has no ${isOllama ? "address" : "key"} in .env, so a failed request is not retried.`}
          </SettingsCallout>
        </SettingsBlock>
      )}

      <SettingsRow
        label="Model"
        controlId="ai-fallback-model"
        controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
        description="Answers in place of the default provider's model."
      >
        <ModelPicker
          id="ai-fallback-model"
          value={getSecondaryProviderModel(settings, provider)}
          options={options}
          placeholder="Choose a model"
          disabled={options.length === 0 || !hasCredential}
          onChange={applyModel}
          onRefresh={refreshModels}
          refreshing={models.secondaryFetchingModels}
          refreshDisabled={!hasCredential}
        />
      </SettingsRow>

      <SettingsRow
        id="ai-providers-fallback-live-model"
        label="Meeting Edge model"
        controlId="ai-fallback-live-model"
        controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
        description="Answers live guidance when the default provider cannot. A faster model keeps it responsive."
      >
        <ModelPicker
          id="ai-fallback-live-model"
          value={getSecondaryProviderLiveModel(settings, provider)}
          options={options}
          placeholder="Same as the fallback model"
          allowEmpty
          disabled={options.length === 0 || !hasCredential}
          onChange={applyLiveModel}
        />
      </SettingsRow>

      {isOllama && (
        <>
          <SettingsRow
            label="Ollama address"
            controlId="ai-fallback-ollama-url"
        controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
            description="Where the fallback's Ollama server listens."
          >
            <input
              id="ai-fallback-ollama-url"
              aria-describedby="ai-fallback-ollama-url-help"
              type="url"
              value={
                settings.secondary_ollama_api_url ||
                "http://host.docker.internal:11434"
              }
              onChange={(event) =>
                onUpdate({
                  ...settings,
                  secondary_ollama_api_url: event.target.value,
                })
              }
              className={`${SETTINGS_INPUT_CLASS} font-mono`}
            />
          </SettingsRow>

          <SettingsRow
            label="Context window"
            controlId="ai-fallback-context-window"
        controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
            description={<ContextWindowHelp tokens={contextWindow} />}
          >
            <input
              id="ai-fallback-context-window"
              aria-describedby="ai-fallback-context-window-help"
              type="number"
              inputMode="numeric"
              min={1024}
              step={1024}
              value={contextWindow}
              onChange={(event) =>
                onUpdate({
                  ...settings,
                  secondary_ollama_context_window: parseContextWindow(
                    event.target.value,
                  ),
                })
              }
              className={SETTINGS_INPUT_CLASS}
            />
          </SettingsRow>
        </>
      )}

      {hasCredential && (
        <SettingsRow
          label="Connection"
          description="Checks the fallback's key or address and reloads its model list. Nothing is saved."
          descriptionId="ai-fallback-test-help"
        >
          <TestConnection
            id="ai-fallback-test"
            provider={provider}
            validating={models.validating}
            message={models.validationMsg}
            onTest={() => void models.handleValidate(provider)}
          />
        </SettingsRow>
      )}
    </SettingsCard>
  );
}
