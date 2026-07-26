import type { CliOAuthStatus, Settings } from "@/types";

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
 * on its own a blocker: a configured server provider still answers. Gating on
 * the subscription alone would have swapped one wrong answer for another.
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
