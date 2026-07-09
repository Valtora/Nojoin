// Curated CLI OAuth models per provider (a subscription exposes no models
// endpoint). Ids mirror the backend's _CLAUDE_CLI_MODELS / _CODEX_CLI_MODELS
// (most-capable first); `label` is the full, unambiguous name shown in pickers
// and usage tables. Codex ids are curated — VERIFY against the live Codex set.
import type { CliProvider } from "@/types";

export interface CliModelOption {
  id: string;
  label: string;
}

export const CLAUDE_CLI_MODEL_OPTIONS: CliModelOption[] = [
  { id: "claude-opus-4-8", label: "Claude Opus 4.8" },
  { id: "claude-sonnet-5", label: "Claude Sonnet 5" },
  { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
  { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
];

// Fallback only — the live catalogue comes from `codex debug models` via
// GET /cli-oauth/codex/models (getCodexModels). Kept roughly current so the
// picker is sensible before the live list loads.
export const CODEX_CLI_MODEL_OPTIONS: CliModelOption[] = [
  { id: "gpt-5.6-sol", label: "GPT-5.6-Sol" },
  { id: "gpt-5.5", label: "GPT-5.5" },
  { id: "gpt-5.4", label: "GPT-5.4" },
  { id: "gpt-5.4-mini", label: "GPT-5.4-Mini" },
];

/** Back-compat alias (Claude was the only provider originally). */
export const CLI_MODEL_OPTIONS = CLAUDE_CLI_MODEL_OPTIONS;

/** The curated model list for a given subscription provider. */
export function cliModelOptions(provider: CliProvider): CliModelOption[] {
  return provider === "codex"
    ? CODEX_CLI_MODEL_OPTIONS
    : CLAUDE_CLI_MODEL_OPTIONS;
}

/** Full model name for a stored CLI model id (any provider), else the raw id. */
export function cliModelLabel(id?: string | null): string {
  if (!id) return "";
  const all = [...CLAUDE_CLI_MODEL_OPTIONS, ...CODEX_CLI_MODEL_OPTIONS];
  return all.find((model) => model.id === id)?.label ?? id;
}
