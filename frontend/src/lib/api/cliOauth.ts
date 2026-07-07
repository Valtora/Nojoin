import type { CliOAuthStatus, CliUsageOverview } from "@/types";
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

export const startCliOAuth = async (): Promise<{ authorize_url: string }> => {
  const response = await api.post<{ authorize_url: string }>("/cli-oauth/start");
  return response.data;
};

export const completeCliOAuth = async (
  code: string,
): Promise<CliOAuthStatus> => {
  const response = await api.post<CliOAuthStatus>("/cli-oauth/complete", {
    code,
  });
  return response.data;
};

export const disconnectCliOAuth = async (): Promise<CliOAuthStatus> => {
  const response = await api.delete<CliOAuthStatus>("/cli-oauth/token");
  return response.data;
};
