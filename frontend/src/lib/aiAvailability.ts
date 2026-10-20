import type { CliOAuthStatus, Settings } from "@/types";

/**
 * Single source of truth for "can AI actually run for this account?".
 *
 * This lives in `lib/` rather than under `components/settings/` because the
 * question is not settings-specific: any surface that gates a feature on AI
 * being usable (Settings badges, the chat composer) must reach the same answer
 * (issue #138).
 */

/**
 * What a provider still lacks before it can answer a request, or `null` when it
 * has everything. A missing credential is reported before a missing model,
 * since the model list cannot be fetched without one.
 */
export type ProviderConfigGap = "credential" | "model";

function providerGap(
  credential: string | null | undefined,
  model: string | null | undefined,
): ProviderConfigGap | null {
  if (!credential) return "credential";
  if (!model) return "model";
  return null;
}

/**
 * The install-wide (server default) provider's gap. No hosted provider has a
 * default model, so a credential alone is not a usable configuration.
 *
 * An unset `llm_provider` resolves to gemini, matching the backend's
 * `DEFAULT_USER_SETTINGS`. Unrecognised providers are treated as unconfigured.
 */
export function getServerProviderGap(
  settings: Settings,
): ProviderConfigGap | null {
  const provider = settings.llm_provider || "gemini";

  switch (provider) {
    case "gemini":
      return providerGap(settings.gemini_api_key, settings.gemini_model);
    case "openai":
      return providerGap(settings.openai_api_key, settings.openai_model);
    case "anthropic":
      return providerGap(settings.anthropic_api_key, settings.anthropic_model);
    // Ollama needs no key, but a bare URL is not a usable configuration: the
    // request still has to name a model, and `ollama_model` has no default.
    case "ollama":
      return providerGap(settings.ollama_api_url, settings.ollama_model);
    default:
      return "credential";
  }
}

/**
 * Whether the install-wide (server default) provider has everything it needs to
 * answer a request: its credential *and* a model.
 */
export function isServerProviderConfigured(settings: Settings): boolean {
  return getServerProviderGap(settings) === null;
}

/**
 * The fallback provider's gap, or `null` when it is usable or none is set.
 * The backend drops a fallback with a gap (`ResolvedLLMConfig.has_secondary`),
 * so requests then run on the primary alone.
 */
export function getSecondaryProviderGap(
  settings: Settings,
): ProviderConfigGap | null {
  switch (settings.secondary_llm_provider) {
    case "gemini":
      return providerGap(
        settings.secondary_gemini_api_key,
        settings.secondary_gemini_model,
      );
    case "openai":
      return providerGap(
        settings.secondary_openai_api_key,
        settings.secondary_openai_model,
      );
    case "anthropic":
      return providerGap(
        settings.secondary_anthropic_api_key,
        settings.secondary_anthropic_model,
      );
    case "ollama":
      return providerGap(
        settings.secondary_ollama_api_url,
        settings.secondary_ollama_model,
      );
    default:
      return null;
  }
}

export type AiUnavailableReason =
  | "server_unconfigured"
  | "subscription_disconnected";

export interface AiAvailability {
  available: boolean;
  /** Only set when `available` is false; drives which guidance the UI shows. */
  reason?: AiUnavailableReason;
}

/**
 * Whether AI can serve a request for this user, across both routing modes.
 *
 * On `usage_model: "cli_oauth"` the backend builds a three-tier chain — the
 * user's own subscription, then the server's primary provider, then its
 * secondary (see backend/utils/llm_config.py and the CLI backend's
 * CliOAuthUnavailableError degradation). So a disconnected subscription is not
 * on its own a blocker: a configured server provider still answers.
 *
 * `cliStatus` may be null for users who are not on cli_oauth; callers should
 * only fetch it when they need it.
 */
export function resolveAiAvailability(
  settings: Settings,
  cliStatus: CliOAuthStatus | null,
): AiAvailability {
  const serverConfigured = isServerProviderConfigured(settings);

  if (settings.usage_model !== "cli_oauth") {
    return serverConfigured
      ? { available: true }
      : { available: false, reason: "server_unconfigured" };
  }

  // Only one subscription can be connected at a time, so any connected entry
  // is the active one (mirrors AiRoutingSection's own resolution).
  const subscriptionConnected = (cliStatus?.providers ?? []).some(
    (entry) => entry.connected,
  );

  if (subscriptionConnected || serverConfigured) {
    return { available: true };
  }
  // Telling a subscription user to add an API key is misleading advice; name
  // the thing they actually need to fix.
  return { available: false, reason: "subscription_disconnected" };
}
