import type {
  DownloadProgress,
  ModelPreparationResponse,
  ModelPreparationTarget,
  SystemModelStatus,
} from "@/types";
import api, { buildFirstRunRequestConfig } from "./client";

export const listModels = async (
  provider: string,
  apiKey: string,
  apiUrl?: string,
  bootstrapPassword?: string,
  /** Whose key and address to list with: the default provider's or the fallback's. */
  providerRole: "primary" | "secondary" = "primary",
): Promise<{ models: string[] }> => {
  const response = await api.post<{ models: string[] }>(
    "/setup/list-models",
    {
      provider,
      api_key: apiKey,
      api_url: apiUrl,
      provider_role: providerRole,
    },
    buildFirstRunRequestConfig(bootstrapPassword),
  );
  return response.data;
};

export interface ThinkingSupport {
  model: string | null;
  /**
   * null means Ollama could not be asked or did not say. Treat it as unknown,
   * never as a refusal.
   */
  supported: boolean | null;
}

/**
 * Whether an Ollama model can think. The server asks Ollama; `secondary` asks
 * the fallback provider's server instead of the default one.
 */
export const getThinkingSupport = async (
  model: string,
  secondary = false,
): Promise<ThinkingSupport> => {
  const response = await api.get<ThinkingSupport>("/llm/thinking-support", {
    params: { model, ...(secondary ? { secondary: true } : {}) },
  });
  return response.data;
};

export const fetchProxyModels = async (
  provider: string,
  apiUrl?: string,
  apiKey?: string,
): Promise<string[]> => {
  const params = new URLSearchParams();
  params.append("provider", provider);
  if (apiUrl) params.append("api_url", apiUrl);
  if (apiKey) params.append("api_key", apiKey);

  const response = await api.get<string[]>(`/llm/models?${params.toString()}`);
  return response.data;
};

export const getModelsStatus = async (
  whisperModelSize?: string,
): Promise<SystemModelStatus> => {
  const params = new URLSearchParams();
  if (whisperModelSize) params.append("whisper_model_size", whisperModelSize);
  const response = await api.get<SystemModelStatus>(
    `/system/models/status?${params.toString()}`,
  );
  return response.data;
};

export const getDownloadProgress = async (): Promise<DownloadProgress> => {
  const response = await api.get<DownloadProgress>("/system/download-progress");
  return response.data;
};

export const prepareModels = async (
  target: ModelPreparationTarget = "active",
): Promise<ModelPreparationResponse> => {
  const response = await api.post<ModelPreparationResponse>(
    "/system/models/prepare",
    { target },
  );
  return response.data;
};

export const deleteModel = async (
  modelId: string,
  whisperModelSize?: string,
): Promise<void> => {
  const params = new URLSearchParams();
  if (whisperModelSize) params.append("variant", whisperModelSize);
  await api.delete(`/system/models/${modelId}?${params.toString()}`);
};
