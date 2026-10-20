import { Settings } from "@/types";
import { listModels } from "@/lib/api";
import { getServerProviderGap } from "@/lib/aiAvailability";
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
  getModelOptionsForProvider,
  getSelectedModelForProvider,
  parseContextWindow,
  withSelectedModelForProvider,
} from "./aiSettingsModels";
import { SETTINGS_INPUT_CLASS, SETTINGS_VALUE_CLASS, SETTINGS_CONTROL_COLUMN_WIDE } from "./settingsControls";

interface ServerProviderSectionProps {
  settings: Settings;
  /** Debounced apply (continuous controls such as the context window). */
  onUpdate: (newSettings: Settings) => void;
  /** Apply and save immediately (discrete controls such as the model). */
  onPersist: (newSettings: Settings) => void;
  models: AISettingsModels;
}

/**
 * The install's default provider. Every field is install-wide (see
 * config_manager.INSTALL_WIDE_AI_SETTING_KEYS) and the provider and its key come
 * from the server's .env, so the page renders this section only for admins.
 */
export default function ServerProviderSection({
  settings,
  onUpdate,
  onPersist,
  models,
}: ServerProviderSectionProps) {
  const gap = getServerProviderGap(settings);
  // The model list needs only the credential, so the model controls stay
  // usable while the model itself is what is missing.
  const hasCredential = gap !== "credential";
  const provider = settings.llm_provider || "gemini";
  const providerLabel = aiProviderLabel(provider);
  const isOllama = provider === "ollama";
  const modelOptions = getModelOptionsForProvider(
    settings,
    models.availableModels,
    "main",
  );
  const contextWindow =
    settings.ollama_context_window || DEFAULT_OLLAMA_CONTEXT_WINDOW;

  const refreshModels = () => {
    const url = isOllama ? settings.ollama_api_url : undefined;
    models.setFetchingModels(true);
    listModels(provider, "", url)
      .then((res) => models.setAvailableModels(res.models))
      .catch(console.error)
      .finally(() => models.setFetchingModels(false));
  };

  return (
    <SettingsCard
      id="ai-providers-primary"
      title="Default provider"
      description="Used by everyone who has not connected their own subscription. The provider and its key come from the server's .env file."
    >
      <SettingsRow
        label="Provider"
        description="Set by LLM_PROVIDER in .env."
        badge={<ProviderStatusBadge gap={gap} />}
      >
        <p className={SETTINGS_VALUE_CLASS}>{providerLabel}</p>
      </SettingsRow>

      {gap === "model" && (
        <SettingsBlock>
          <SettingsCallout tone="warning" title="AI is off until a model is chosen">
            Notes, titles, speaker names, Meeting Chat and Meeting Edge fail
            until a model is selected.
            {modelOptions.length === 0 &&
              " If the list is empty, reload it or test the connection."}
          </SettingsCallout>
        </SettingsBlock>
      )}

      {gap === "credential" && (
        <SettingsBlock>
          <SettingsCallout tone="warning" title={`${providerLabel} is not set up`}>
            {isOllama
              ? "Set OLLAMA_API_URL in .env and restart Nojoin."
              : `Add the ${providerLabel} key to .env and restart Nojoin.`}
          </SettingsCallout>
        </SettingsBlock>
      )}

      <SettingsRow
        label="Model"
        controlId="ai-primary-model"
        controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
        description="Used for notes, titles, speaker names and Meeting Chat."
      >
        <ModelPicker
          id="ai-primary-model"
          value={getSelectedModelForProvider(settings, "main")}
          options={modelOptions}
          placeholder="Choose a model"
          disabled={modelOptions.length === 0 || !hasCredential}
          onChange={(value) =>
            onPersist(withSelectedModelForProvider(settings, "main", value))
          }
          onRefresh={refreshModels}
          refreshing={models.fetchingModels}
          refreshDisabled={!hasCredential}
        />
      </SettingsRow>

      {isOllama && (
        <>
          <SettingsRow
            id="ai-providers-ollama"
            label="Ollama address"
            description="Set by OLLAMA_API_URL in .env."
          >
            <p className={`${SETTINGS_VALUE_CLASS} font-mono`}>
              {settings.ollama_api_url || "http://host.docker.internal:11434"}
            </p>
          </SettingsRow>

          <SettingsRow
            label="Context window"
            controlId="ai-primary-context-window"
        controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
            description={<ContextWindowHelp tokens={contextWindow} />}
          >
            <input
              id="ai-primary-context-window"
              aria-describedby="ai-primary-context-window-help"
              type="number"
              inputMode="numeric"
              min={1024}
              step={1024}
              value={contextWindow}
              onChange={(event) =>
                onUpdate({
                  ...settings,
                  ollama_context_window: parseContextWindow(event.target.value),
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
          description="Checks the key or address and reloads the model list. Nothing is saved."
          descriptionId="ai-primary-test-help"
        >
          <TestConnection
            id="ai-primary-test"
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
