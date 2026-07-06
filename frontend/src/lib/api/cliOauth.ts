import type { CliOAuthStatus } from "@/types";
import api from "./client";

export const getCliOAuthStatus = async (): Promise<CliOAuthStatus> => {
  const response = await api.get<CliOAuthStatus>("/cli-oauth/status");
  return response.data;
};

export const connectCliOAuth = async (
  token: string,
): Promise<CliOAuthStatus> => {
  const response = await api.put<CliOAuthStatus>("/cli-oauth/token", { token });
  return response.data;
};

export const disconnectCliOAuth = async (): Promise<CliOAuthStatus> => {
  const response = await api.delete<CliOAuthStatus>("/cli-oauth/token");
  return response.data;
};
