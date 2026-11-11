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
    whisper_model_size?: string;
    include_demo_recording?: boolean;
    enable_telemetry?: boolean;
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

export interface InitialSetupConfig {
  llm_provider?: string;
  /**
   * Whether the operator actually chose a provider. `llm_provider` always
   * resolves (gemini is the server default), so it cannot answer this on its
   * own and the wizard must not present the default as a choice the operator
   * made.
   */
  llm_provider_selected?: boolean;
  gemini_api_key?: string;
  openai_api_key?: string;
  anthropic_api_key?: string;
  ollama_api_url?: string;
  secondary_llm_provider?: string | null;
  secondary_api_key?: string | null;
  hf_token?: string;
  selected_model?: string;
  pyannote_models_ready?: boolean;
  bundled_pyannote_models_ready?: boolean;
  /**
   * The value NOJOIN_TELEMETRY_ENABLED pins telemetry to, or null when it is
   * unset and the wizard's diagnostics box is the operator's choice. A pinned
   * value outranks the box, so the wizard shows it read-only.
   */
  telemetry_pinned?: boolean | null;
}

export const getInitialConfig = async (
  bootstrapPassword?: string,
): Promise<InitialSetupConfig> => {
  const response = await api.get(
    "/setup/initial-config",
    buildFirstRunRequestConfig(bootstrapPassword),
  );
  return response.data;
};

/** What a Test connection probe found, as the server words it (#991). */
export type ModelProbeKind =
  | "ok"
  | "unknown_model"
  | "auth"
  | "quota"
  | "timeout"
  | "bad_response"
  | "error";

export type ModelProbeRole = "main" | "meeting_edge";

export interface ModelProbeRequest {
  model: string;
  role: ModelProbeRole;
}

export interface ModelProbeResult {
  model: string;
  role: ModelProbeRole;
  ok: boolean;
  kind: ModelProbeKind;
  message: string;
}

export interface ValidateLLMResult {
  /** False when any probed model failed; a refused key is a 400 instead. */
  valid: boolean;
  message?: string;
  models?: string[];
  /** One row per probed model; empty when no model was asked for. */
  results?: ModelProbeResult[];
}

export interface ValidateLLMOptions {
  /** Which configured provider to test: the default or the fallback. */
  providerRole?: "primary" | "secondary";
  /** Models to run one small generation on. Each is probed once, at most three. */
  probeModels?: ModelProbeRequest[];
}

export const validateLLM = async (
  provider: string,
  apiKey: string,
  apiUrl?: string,
  model?: string,
  bootstrapPassword?: string,
  options: ValidateLLMOptions = {},
): Promise<ValidateLLMResult> => {
  const response = await api.post<ValidateLLMResult>(
    "/setup/validate-llm",
    {
      provider,
      api_key: apiKey,
      api_url: apiUrl,
      model,
      provider_role: options.providerRole ?? "primary",
      probe_models: options.probeModels ?? [],
    },
    buildFirstRunRequestConfig(bootstrapPassword),
  );
  return response.data;
};
