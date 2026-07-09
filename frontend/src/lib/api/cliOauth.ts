import type {
  CliOAuthPoll,
  CliOAuthStart,
  CliOAuthStatus,
  CliProvider,
  CliUsageOverview,
} from "@/types";
import api from "./client";

export const getCliOAuthStatus = async (): Promise<CliOAuthStatus> => {
  const response = await api.get<CliOAuthStatus>("/cli-oauth/status");
  return response.data;
};

/** Admin-only: per-user CLI token usage + rate-limit status (paginated). */
export const getCliUsageOverview = async (
  skip = 0,
  limit = 25,
  search = "",
): Promise<CliUsageOverview> => {
  const response = await api.get<CliUsageOverview>("/cli-oauth/admin/usage", {
    params: { skip, limit, search },
  });
  return response.data;
};

/** Begin a connect flow for a provider (Claude paste-code or Codex device). */
export const startCliOAuth = async (
  provider: CliProvider,
): Promise<CliOAuthStart> => {
  const response = await api.post<CliOAuthStart>("/cli-oauth/start", {
    provider,
  });
  return response.data;
};

/** Finish a paste-code flow (Claude): exchange the pasted authorization code. */
export const completeCliOAuth = async (
  code: string,
  provider: CliProvider = "claude_code",
): Promise<CliOAuthStatus> => {
  const response = await api.post<CliOAuthStatus>("/cli-oauth/complete", {
    code,
    provider,
  });
  return response.data;
};

/** Poll a device flow (Codex): pending until the user approves in a browser. */
export const pollCliOAuth = async (
  provider: CliProvider,
): Promise<CliOAuthPoll> => {
  const response = await api.post<CliOAuthPoll>("/cli-oauth/poll", { provider });
  return response.data;
};

export const disconnectCliOAuth = async (
  provider: CliProvider,
): Promise<CliOAuthStatus> => {
  const response = await api.delete<CliOAuthStatus>("/cli-oauth/token", {
    params: { provider },
  });
  return response.data;
};

export interface CodexModelsResponse {
  models: { id: string; label: string }[];
  // "live" (from the codex binary) or "fallback" (curated; cache warming).
  source: string;
}

/** The live Codex model catalogue for the picker (worker-io `codex debug models`). */
export const getCodexModels = async (): Promise<CodexModelsResponse> => {
  const response = await api.get<CodexModelsResponse>("/cli-oauth/codex/models");
  return response.data;
};
