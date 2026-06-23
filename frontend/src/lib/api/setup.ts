import api, { buildFirstRunRequestConfig } from "./client";

export const getSystemStatus = async (): Promise<{ initialized: boolean }> => {
  const response = await api.get<{ initialized: boolean }>("/system/status");
  return response.data;
};

export const setupSystem = async (
  data: {
    username: string;
    password: string;
    selected_model?: string;
  },
  bootstrapPassword?: string,
): Promise<{ initialized: boolean; model_preparation_task_id?: string | null }> => {
  const response = await api.post<{ initialized: boolean; model_preparation_task_id?: string | null }>(
    "/system/setup",
    data,
    buildFirstRunRequestConfig(bootstrapPassword),
  );
  return response.data;
};

export const checkFFmpeg = async (): Promise<{
  ffmpeg: boolean;
  ffprobe: boolean;
  ffmpeg_path: string | null;
  ffprobe_path: string | null;
}> => {
  const response = await api.get("/system/check-ffmpeg");
  return response.data;
};

export const getInitialConfig = async (bootstrapPassword?: string): Promise<{
  llm_provider?: string;
  gemini_api_key?: string;
  openai_api_key?: string;
  anthropic_api_key?: string;
  ollama_api_url?: string;
  hf_token?: string;
  selected_model?: string;
  pyannote_models_ready?: boolean;
  bundled_pyannote_models_ready?: boolean;
}> => {
  const response = await api.get(
    "/setup/initial-config",
    buildFirstRunRequestConfig(bootstrapPassword),
  );
  return response.data;
};

export const validateLLM = async (
  provider: string,
  apiKey: string,
  apiUrl?: string,
  model?: string,
  bootstrapPassword?: string,
): Promise<{ valid: boolean; message?: string; models?: string[] }> => {
  const response = await api.post<{
    valid: boolean;
    message?: string;
    models?: string[];
  }>(
    "/setup/validate-llm",
    {
      provider,
      api_key: apiKey,
      api_url: apiUrl,
      model,
    },
    buildFirstRunRequestConfig(bootstrapPassword),
  );
  return response.data;
};

export const validateHF = async (
  token: string,
  bootstrapPassword?: string,
): Promise<{ valid: boolean; message?: string }> => {
  const response = await api.post<{ valid: boolean; message?: string }>(
    "/setup/validate-hf",
    { token },
    buildFirstRunRequestConfig(bootstrapPassword),
  );
  return response.data;
};
