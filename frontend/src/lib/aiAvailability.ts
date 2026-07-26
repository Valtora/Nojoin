import type { Settings } from "@/types";

/**
 * Single source of truth for "can AI actually run for this account?".
 *
 * This lives in `lib/` rather than under `components/settings/` because the
 * question is not settings-specific: any surface that gates a feature on AI
 * being usable (Settings badges, the chat composer) must reach the same answer.
 * Two independent implementations previously disagreed, which is how a user on
 * their own subscription ended up with chat blocked while notes generation
 * worked (issue #138).
 */

/**
 * Whether the install-wide (server default) provider has everything it needs to
 * answer a request: its credential *and* a model.
 *
 * An unset `llm_provider` resolves to gemini, matching the backend's
 * `DEFAULT_USER_SETTINGS`. Unrecognised providers are treated as unconfigured.
 */
export function isServerProviderConfigured(settings: Settings): boolean {
  const provider = settings.llm_provider || "gemini";

  switch (provider) {
    case "gemini":
      return Boolean(settings.gemini_api_key && settings.gemini_model);
    case "openai":
      return Boolean(settings.openai_api_key && settings.openai_model);
    case "anthropic":
      return Boolean(settings.anthropic_api_key && settings.anthropic_model);
    // Ollama needs no key, but a bare URL is not a usable configuration: the
    // request still has to name a model, and `ollama_model` has no default.
    case "ollama":
      return Boolean(settings.ollama_api_url && settings.ollama_model);
    default:
      return false;
  }
}
