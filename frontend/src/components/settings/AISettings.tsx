"use client";

import { Settings } from "@/types";
import { fuzzyMatch } from "@/lib/searchUtils";
import { useNotificationStore } from "@/lib/notificationStore";
import SettingsCallout from "./SettingsCallout";
import AiRoutingSection from "./AiRoutingSection";
import ServerProviderSection from "./ServerProviderSection";
import MeetingEdgeSection from "./MeetingEdgeSection";
import SecondaryProviderSection from "./SecondaryProviderSection";
import AiAutomaticEnhancementSection from "./AiAutomaticEnhancementSection";
import NotesTemplatesSection from "./NotesTemplatesSection";
import GlossarySection from "./GlossarySection";
import AiLanguageSection from "./AiLanguageSection";
import AiHuggingFaceSection from "./AiHuggingFaceSection";
import VoiceprintMaintenanceSection from "./VoiceprintMaintenanceSection";
import AiTranscriptionSection from "./AiTranscriptionSection";
import AiModelDependenciesSection from "./AiModelDependenciesSection";
import { useAISettingsModels } from "./useAISettingsModels";

interface AISettingsProps {
  settings: Settings;
  /** Debounced apply (1s) for continuous controls: sliders, numbers, text. */
  onUpdate: (newSettings: Settings) => void;
  /** Immediate save; wrapped by `persistNow` for discrete controls. */
  onPersist?: (newSettings: Settings) => Promise<void>;
  searchQuery?: string;
  isAdmin?: boolean;
}

/**
 * Orchestrator for the Settings > AI page. Owns the model-discovery hook and
 * the CLI connection flag, computes search visibility, and composes the AI
 * sections. Provider/model/Ollama/fallback controls are install-wide (admin
 * only) and are simply not rendered for non-admins, so nothing they could edit
 * is silently discarded on save.
 */
export default function AISettings({
  settings,
  onUpdate,
  onPersist,
  searchQuery = "",
  isAdmin = false,
}: AISettingsProps) {
  const addNotification = useNotificationStore((state) => state.addNotification);
  const models = useAISettingsModels({ settings, onPersist });

  // Apply a change and save it immediately, for discrete controls (selects,
  // switches, routing radios). Continuous controls use `onUpdate` (1s debounce).
  // Returns a promise that settles when the save does, so a section whose UI is
  // rendered from server state (rather than from `settings`) can refetch once
  // the write has landed. Rejections are still handled here, so callers that
  // ignore the result behave exactly as before.
  const persistNow = (updates: Settings): Promise<void> => {
    onUpdate(updates);
    if (!onPersist) {
      return Promise.resolve();
    }
    return onPersist(updates).catch((error) => {
      console.error("Failed to persist AI settings update", error);
      addNotification({
        type: "error",
        message: "Could not save your AI settings. Please try again.",
      });
    });
  };

  // Search logic
  const showLLM = fuzzyMatch(searchQuery, [
    "llm",
    "provider",
    "gemini",
    "openai",
    "anthropic",
    "ollama",
    "ai routing",
    "routing",
    "usage model",
    "meeting edge",
    "technical context",
    "glossary",
    "verbosity",
    "threshold",
    "jargon",
    "live model",
    "live assistant",
    "api key",
    "model",
    "fallback",
    "secondary",
    "cli oauth",
    "subscription",
    "claude subscription",
    "chatgpt subscription",
    "chatgpt",
    "codex",
  ]);
  const showHF = fuzzyMatch(searchQuery, [
    "hugging face",
    "token",
    "diarization",
  ]);
  const showAutomaticEnhancement = fuzzyMatch(searchQuery, [
    "automatic enhancement",
    "meeting intelligence",
    "short titles",
    "title",
    "titles",
  ]);
  const showNotesTemplates = fuzzyMatch(searchQuery, [
    "notes structure",
    "notes template",
    "template",
    "templates",
    "prompt",
    "sections",
    "headings",
    "action items",
    "decisions",
    "custom notes",
  ]);
  const showGlossary = fuzzyMatch(searchQuery, [
    "glossary",
    "terms",
    "acronyms",
    "vocabulary",
    "jargon",
    "spelling",
    "corrections",
  ]);
  const showLanguage = fuzzyMatch(searchQuery, [
    "language",
    "transcription language",
    "notes language",
    "British English",
    "American English",
    "localization",
    "translation",
  ]);
  const showTranscription = fuzzyMatch(searchQuery, [
    "transcription",
    "whisper",
    "speech to text",
    "parakeet",
    "canary",
    "engine",
  ]);
  const showDependencies = fuzzyMatch(searchQuery, [
    "dependencies",
    "models",
    "download",
    "status",
  ]);

  const hasSearch = !!searchQuery;
  const showLLMSection = !hasSearch || showLLM;
  const showAutomaticEnhancementSection = !hasSearch || showAutomaticEnhancement;
  const showLanguageSection = !hasSearch || showLanguage;
  const showNotesTemplatesSection = !hasSearch || showNotesTemplates;
  const showGlossarySection = !hasSearch || showGlossary;
  const showHFSection = isAdmin && (!hasSearch || showHF);
  const showTranscriptionSection = isAdmin && (!hasSearch || showTranscription);
  const showDependenciesSection = isAdmin && (!hasSearch || showDependencies);

  if (
    !showLLMSection &&
    !showAutomaticEnhancementSection &&
    !showNotesTemplatesSection &&
    !showGlossarySection &&
    !showLanguageSection &&
    !showHFSection &&
    !showTranscriptionSection &&
    !showDependenciesSection
  ) {
    return (
      <SettingsCallout
        tone="neutral"
        title="No matching settings"
        message="Try a broader search term for providers, models, tokens, or local model assets."
      />
    );
  }

  return (
    <div className="space-y-8">
      {showLLMSection && (
        <AiRoutingSection
          settings={settings}
          onPersist={persistNow}
          isAdmin={isAdmin}
        />
      )}

      {showLLMSection && isAdmin && (
        <ServerProviderSection
          settings={settings}
          onUpdate={onUpdate}
          onPersist={persistNow}
          models={models}
        />
      )}

      {showLLMSection && (
        <MeetingEdgeSection
          settings={settings}
          onUpdate={onUpdate}
          onPersist={persistNow}
          isAdmin={isAdmin}
          models={models}
        />
      )}

      {showLLMSection && isAdmin && (
        <SecondaryProviderSection
          settings={settings}
          onUpdate={onUpdate}
          onPersist={persistNow}
          models={models}
        />
      )}

      {showAutomaticEnhancementSection && (
        <AiAutomaticEnhancementSection
          settings={settings}
          onPersist={persistNow}
        />
      )}

      {showNotesTemplatesSection && (
        <NotesTemplatesSection
          settings={settings}
          onPersist={persistNow}
          isAdmin={isAdmin}
        />
      )}

      {showGlossarySection && (
        <GlossarySection
          settings={settings}
          onUpdate={onUpdate}
          isAdmin={isAdmin}
        />
      )}

      {showLanguageSection && (
        <AiLanguageSection
          settings={settings}
          onUpdate={onUpdate}
          onPersist={persistNow}
          languageRegistry={models.languageRegistry}
        />
      )}

      {showHFSection && <AiHuggingFaceSection settings={settings} />}

      {showTranscriptionSection && (
        <AiTranscriptionSection
          settings={settings}
          onPersist={persistNow}
          isAdmin={isAdmin}
        />
      )}

      {showDependenciesSection && (
        <AiModelDependenciesSection
          modelStatus={models.modelStatus}
          deleting={models.deleting}
          handleDeleteModel={models.handleDeleteModel}
          isAdmin={isAdmin}
        />
      )}

      {/* Renders nothing when every stored voiceprint is current. */}
      <VoiceprintMaintenanceSection />
    </div>
  );
}
