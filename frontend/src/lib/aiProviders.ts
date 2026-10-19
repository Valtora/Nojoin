/**
 * Display names for the install's AI providers. Settings stored them lower-case
 * and pages capitalised them with CSS, which printed "Openai".
 */
export const AI_PROVIDER_LABELS: Record<string, string> = {
  gemini: "Gemini",
  openai: "OpenAI",
  anthropic: "Anthropic",
  ollama: "Ollama",
};

export function aiProviderLabel(provider: string | null | undefined): string {
  if (!provider) {
    return "None";
  }
  return AI_PROVIDER_LABELS[provider] ?? provider;
}

/**
 * Rough VRAM an Ollama context window costs on top of the model, from the
 * DEPLOYMENT.md rule of about 190 KiB per token for a 14B model. Smaller
 * models need less and larger ones more, so callers present it as an estimate.
 */
export function estimateContextVramGiB(tokens: number): number {
  return (tokens * 190) / (1024 * 1024);
}
