// Curated CLI OAuth models (a subscription exposes no models endpoint). Ids
// mirror the backend's _CLI_MODELS (most-capable first); `label` is the full,
// unambiguous model name shown in pickers and usage tables — never a bare
// "Claude Sonnet", since the list now holds two Sonnets.
export interface CliModelOption {
  id: string;
  label: string;
}

export const CLI_MODEL_OPTIONS: CliModelOption[] = [
  { id: "claude-opus-4-8", label: "Claude Opus 4.8" },
  { id: "claude-sonnet-5", label: "Claude Sonnet 5" },
  { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
  { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
];

/** Full model name for a stored CLI model id, falling back to the raw id. */
export function cliModelLabel(id?: string | null): string {
  if (!id) return "";
  return CLI_MODEL_OPTIONS.find((model) => model.id === id)?.label ?? id;
}
