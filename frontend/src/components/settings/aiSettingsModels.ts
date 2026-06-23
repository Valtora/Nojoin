import { Settings } from "@/types";

/**
 * Pure provider/model accessors and updaters extracted from
 * {@link AISettings} (FE-012). These functions read or derive a new `Settings`
 * object and never persist; the component remains responsible for calling
 * `onUpdate`/`onPersist`. Behaviour matches the original inline logic exactly.
 */

export const DEFAULT_OLLAMA_CONTEXT_WINDOW = 131072;

export type ModelKind = "main" | "live";

/** Whether the active primary provider has the credential/endpoint it needs. */
export function checkLlmConfigured(settings: Settings): boolean {
  const provider = settings.llm_provider || "gemini";
  if (provider === "gemini") return Boolean(settings.gemini_api_key);
  if (provider === "openai") return Boolean(settings.openai_api_key);
  if (provider === "anthropic") return Boolean(settings.anthropic_api_key);
  if (provider === "ollama") return Boolean(settings.ollama_api_url);
  return false;
}

export function getSelectedModelForProvider(
  settings: Settings,
  kind: ModelKind,
): string {
  const provider = settings.llm_provider || "gemini";
  if (provider === "openai") {
    return kind === "live"
      ? settings.openai_live_model || ""
      : settings.openai_model || "";
  }
  if (provider === "anthropic") {
    return kind === "live"
      ? settings.anthropic_live_model || ""
      : settings.anthropic_model || "";
  }
  if (provider === "ollama") {
    return kind === "live"
      ? settings.ollama_live_model || ""
      : settings.ollama_model || "";
  }
  return kind === "live"
    ? settings.gemini_live_model || ""
    : settings.gemini_model || "";
}

export function parseContextWindow(value: string): number {
  const parsed = Number.parseInt(value, 10);
  if (Number.isNaN(parsed)) {
    return DEFAULT_OLLAMA_CONTEXT_WINDOW;
  }
  return Math.max(1024, parsed);
}

/** Returns a new Settings with the selected primary model updated. */
export function withSelectedModelForProvider(
  settings: Settings,
  kind: ModelKind,
  value: string,
): Settings {
  const provider = settings.llm_provider || "gemini";
  const updates: Settings = { ...settings };

  if (provider === "openai") {
    if (kind === "live") updates.openai_live_model = value || null;
    else updates.openai_model = value;
  } else if (provider === "anthropic") {
    if (kind === "live") updates.anthropic_live_model = value || null;
    else updates.anthropic_model = value;
  } else if (provider === "ollama") {
    if (kind === "live") updates.ollama_live_model = value || null;
    else updates.ollama_model = value;
  } else {
    if (kind === "live") updates.gemini_live_model = value || null;
    else updates.gemini_model = value;
  }

  return updates;
}

export function getSecondaryProviderApiKey(
  settings: Settings,
  provider: Settings["secondary_llm_provider"],
): string {
  switch (provider) {
    case "openai":
      return settings.secondary_openai_api_key || "";
    case "anthropic":
      return settings.secondary_anthropic_api_key || "";
    case "gemini":
      return settings.secondary_gemini_api_key || "";
    default:
      return "";
  }
}

export function getSecondaryProviderModel(
  settings: Settings,
  provider: Settings["secondary_llm_provider"],
): string {
  switch (provider) {
    case "openai":
      return settings.secondary_openai_model || "";
    case "anthropic":
      return settings.secondary_anthropic_model || "";
    case "ollama":
      return settings.secondary_ollama_model || "";
    case "gemini":
      return settings.secondary_gemini_model || "";
    default:
      return "";
  }
}

export function getSecondaryProviderLiveModel(
  settings: Settings,
  provider: Settings["secondary_llm_provider"],
): string {
  switch (provider) {
    case "openai":
      return settings.secondary_openai_live_model || "";
    case "anthropic":
      return settings.secondary_anthropic_live_model || "";
    case "ollama":
      return settings.secondary_ollama_live_model || "";
    case "gemini":
      return settings.secondary_gemini_live_model || "";
    default:
      return "";
  }
}

/**
 * Returns a new Settings with the secondary model updated, or `null` when no
 * secondary provider is configured (the caller then performs no update).
 */
export function withSecondaryProviderModel(
  settings: Settings,
  provider: Settings["secondary_llm_provider"],
  value: string,
): Settings | null {
  if (!provider) {
    return null;
  }

  const updates: Settings = { ...settings };
  if (provider === "openai") {
    updates.secondary_openai_model = value;
  } else if (provider === "anthropic") {
    updates.secondary_anthropic_model = value;
  } else if (provider === "ollama") {
    updates.secondary_ollama_model = value;
  } else {
    updates.secondary_gemini_model = value;
  }

  return updates;
}

/**
 * Returns a new Settings with the secondary live model updated, or `null` when
 * no secondary provider is configured.
 */
export function withSecondaryProviderLiveModel(
  settings: Settings,
  provider: Settings["secondary_llm_provider"],
  value: string,
): Settings | null {
  if (!provider) {
    return null;
  }

  const updates: Settings = { ...settings };
  if (provider === "openai") {
    updates.secondary_openai_live_model = value || null;
  } else if (provider === "anthropic") {
    updates.secondary_anthropic_live_model = value || null;
  } else if (provider === "ollama") {
    updates.secondary_ollama_live_model = value || null;
  } else {
    updates.secondary_gemini_live_model = value || null;
  }

  return updates;
}

/**
 * Order the available models so the currently-selected model (if any) is first,
 * matching the original `getModelOptionsForProvider` behaviour.
 */
export function getModelOptionsForProvider(
  settings: Settings,
  availableModels: string[],
  kind: ModelKind,
): string[] {
  const selectedModel = getSelectedModelForProvider(settings, kind);
  if (!selectedModel) {
    return availableModels;
  }

  return [
    selectedModel,
    ...availableModels.filter((model) => model !== selectedModel),
  ];
}
