"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  DownloadProgress,
  LanguageRegistry,
  ModelPreparationTarget,
  Settings,
  SystemModelStatus,
} from "@/types";
import {
  deleteModel,
  getLanguageOptions,
  getModelsStatus,
  listModels,
  validateLLM,
} from "@/lib/api";
import type { ModelProbeResult } from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { getErrorMessage } from "@/lib/errors";
import { useModelPreparation } from "./useModelPreparation";
import { probeModelsForRole, type ProviderRole } from "./aiSettingsModels";

export interface ValidationMessage {
  type: "success" | "error";
  msg: string;
  provider: string;
  /** Which card ran the test; see {@link validationKey}. */
  key: string;
  /** One row per model the test ran (#991); empty for a key-only check. */
  results: ModelProbeResult[];
}

/** Names one Test connection: the default and the fallback can share a provider. */
export function validationKey(provider: string, role: ProviderRole): string {
  return `${role}:${provider}`;
}

export interface UseAISettingsModelsOptions {
  settings: Settings;
}

export interface AISettingsModels {
  validating: string | null;
  validationMsg: ValidationMessage | null;
  modelStatus: SystemModelStatus | null;
  languageRegistry: LanguageRegistry | null;
  deleting: string | null;

  /** Live preparation progress, or null before the first read. */
  downloadProgress: DownloadProgress | null;
  /** Target of the preparation this session queued, while it is running. */
  preparing: ModelPreparationTarget | null;
  /** True whenever the server reports a preparation in flight, whoever started it. */
  preparationRunning: boolean;
  startPreparation: (target: ModelPreparationTarget) => Promise<boolean>;

  availableModels: string[];
  setAvailableModels: React.Dispatch<React.SetStateAction<string[]>>;
  fetchingModels: boolean;
  setFetchingModels: React.Dispatch<React.SetStateAction<boolean>>;

  secondaryAvailableModels: string[];
  setSecondaryAvailableModels: React.Dispatch<React.SetStateAction<string[]>>;
  secondaryFetchingModels: boolean;
  setSecondaryFetchingModels: React.Dispatch<React.SetStateAction<boolean>>;

  handleValidate: (provider: string, role?: ProviderRole) => Promise<void>;
  handleDeleteModel: (modelName: string) => Promise<void>;
  refreshStatus: () => Promise<SystemModelStatus | null>;
}

/**
 * Owns the model discovery, validation, status, and language-registry data for
 * {@link AISettings}: provider- and Ollama-aware fetching, debounced effects,
 * validation messaging, and model deletion. The `set*` setters are exposed so
 * the inline "Refresh" buttons can drive their optimistic updates.
 */
export function useAISettingsModels(
  options: UseAISettingsModelsOptions,
): AISettingsModels {
  const { settings } = options;
  const { addNotification } = useNotificationStore();

  const [validating, setValidating] = useState<string | null>(null);
  const [validationMsg, setValidationMsg] = useState<ValidationMessage | null>(
    null,
  );
  const [modelStatus, setModelStatus] = useState<SystemModelStatus | null>(
    null,
  );
  const [languageRegistry, setLanguageRegistry] =
    useState<LanguageRegistry | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const statusRead = useRef<{
    size: Settings["whisper_model_size"];
    promise: Promise<SystemModelStatus | null>;
  } | null>(null);
  const mounted = useRef(false);
  const currentSize = useRef(settings.whisper_model_size);

  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [secondaryAvailableModels, setSecondaryAvailableModels] = useState<
    string[]
  >([]);
  const [secondaryFetchingModels, setSecondaryFetchingModels] = useState(false);

  const refreshStatus = useCallback(async (): Promise<SystemModelStatus | null> => {
    const size = settings.whisper_model_size;
    while (statusRead.current) {
      if (statusRead.current.size === size) return statusRead.current.promise;
      await statusRead.current.promise;
      if (!mounted.current) return null;
    }
    const promise = getModelsStatus(size).then((status) => {
      if (mounted.current && currentSize.current === size) setModelStatus(status);
      return status;
    }).catch((error: unknown) => {
      console.error("Failed to read model status", error);
      return null;
    }).finally(() => { statusRead.current = null; });
    statusRead.current = { size, promise };
    return promise;
  }, [settings.whisper_model_size]);

  useEffect(() => {
    mounted.current = true;
    currentSize.current = settings.whisper_model_size;
    void refreshStatus();
    return () => { mounted.current = false; };
  }, [refreshStatus, settings.whisper_model_size]);

  useEffect(() => {
    getLanguageOptions().then(setLanguageRegistry).catch(console.error);
  }, []);

  // Fetch models automatically when provider or ollama_api_url changes
  useEffect(() => {
    const fetchModels = async () => {
      const provider = settings.llm_provider;
      const url = provider === "ollama" ? settings.ollama_api_url || "" : "";

      if (provider) {
        setFetchingModels(true);
        try {
          const res = await listModels(provider, "", url);
          setAvailableModels(res.models);
        } catch (e: unknown) {
          console.error("Failed to fetch models", e);
          setAvailableModels([]);
        } finally {
          setFetchingModels(false);
        }
      } else {
        setAvailableModels([]);
      }
    };

    const timeout = setTimeout(fetchModels, 1000);
    return () => clearTimeout(timeout);
  }, [settings.llm_provider, settings.ollama_api_url]);

  // Fetch secondary provider models independently
  useEffect(() => {
    const fetchSecondaryModels = async () => {
      const provider = settings.secondary_llm_provider;
      if (!provider) {
        setSecondaryAvailableModels([]);
        return;
      }
      const url =
        provider === "ollama" ? settings.secondary_ollama_api_url || "" : "";

      setSecondaryFetchingModels(true);
      try {
        const res = await listModels(provider, "", url);
        setSecondaryAvailableModels(res.models);
      } catch (e: unknown) {
        console.error("Failed to fetch secondary models", e);
        setSecondaryAvailableModels([]);
      } finally {
        setSecondaryFetchingModels(false);
      }
    };

    const timeout = setTimeout(fetchSecondaryModels, 1000);
    return () => clearTimeout(timeout);
  }, [settings.secondary_llm_provider, settings.secondary_ollama_api_url]);

  const validatingRef = useRef(false);

  const handleValidate = async (
    provider: string,
    role: ProviderRole = "primary",
  ) => {
    // One test at a time: a second click while one runs starts nothing.
    if (validatingRef.current) return;
    validatingRef.current = true;
    const key = validationKey(provider, role);
    setValidating(key);
    setValidationMsg(null);

    const isSecondary = role === "secondary";
    const url =
      provider === "ollama"
        ? isSecondary
          ? settings.secondary_ollama_api_url || ""
          : settings.ollama_api_url || ""
        : "";

    try {
      const res = await validateLLM(provider, "", url, undefined, undefined, {
        providerRole: role,
        probeModels: probeModelsForRole(settings, role),
      });
      // If models are returned (e.g. from Ollama), update the appropriate list
      if (res.models) {
        if (isSecondary) {
          setSecondaryAvailableModels(res.models);
        } else {
          setAvailableModels(res.models);
        }
      } else {
        // Otherwise refresh models explicitly
        const modelsRes = await listModels(provider, "", url);
        if (isSecondary) {
          setSecondaryAvailableModels(modelsRes.models);
        } else {
          setAvailableModels(modelsRes.models);
        }
      }

      // Validation only tests the connection. Settings save through their own
      // controls, so pressing Validate never commits an edit as a side effect.
      // A model that fails is reported beside its name; saving is never
      // blocked by it.
      setValidationMsg({
        type: res.valid ? "success" : "error",
        msg: res.message || "Connection works.",
        provider,
        key,
        results: res.results ?? [],
      });
    } catch (e: unknown) {
      setValidationMsg({
        type: "error",
        msg: getErrorMessage(e, "Validation failed"),
        provider,
        key,
        results: [],
      });
    } finally {
      validatingRef.current = false;
      setValidating(null);
    }
  };

  const statusDownloading = Object.values(modelStatus ?? {}).some((model) => model.state === "downloading");
  const preparation = useModelPreparation(refreshStatus, statusDownloading);

  const handleDeleteModel = async (modelName: string) => {
    if (
      !confirm(
        `Are you sure you want to delete the ${modelName} model? You will need to download it again to use it.`,
      )
    )
      return;

    setDeleting(modelName);
    try {
      await deleteModel(modelName);
      addNotification({
        type: "success",
        message: `${modelName} model deleted successfully`,
      });
      refreshStatus();
    } catch (e: unknown) {
      console.error(e);
      addNotification({
        type: "error",
        message: `Failed to delete model: ${getErrorMessage(e, "Unknown error")}`,
      });
    } finally {
      setDeleting(null);
    }
  };

  return {
    validating,
    validationMsg,
    modelStatus,
    languageRegistry,
    deleting,
    ...preparation,
    availableModels,
    setAvailableModels,
    fetchingModels,
    setFetchingModels,
    secondaryAvailableModels,
    setSecondaryAvailableModels,
    secondaryFetchingModels,
    setSecondaryFetchingModels,
    handleValidate,
    handleDeleteModel,
    refreshStatus,
  };
}
