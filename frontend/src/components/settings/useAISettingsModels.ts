"use client";

import { useEffect, useState } from "react";

import {
  DownloadProgress,
  LanguageRegistry,
  ModelPreparationTarget,
  Settings,
  SystemModelStatus,
} from "@/types";
import {
  deleteModel,
  getDownloadProgress,
  getLanguageOptions,
  getModelsStatus,
  listModels,
  prepareModels,
  validateLLM,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { getErrorMessage } from "@/lib/errors";

export interface ValidationMessage {
  type: "success" | "error";
  msg: string;
  provider: string;
}

export interface UseAISettingsModelsOptions {
  settings: Settings;
  onPersist?: (newSettings: Settings) => Promise<void>;
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

  handleValidate: (provider: string) => Promise<void>;
  handleDeleteModel: (modelName: string) => Promise<void>;
  refreshStatus: () => void;
}

/**
 * Owns the model discovery, validation, status, and language-registry data for
 * {@link AISettings} (FE-012). Lifted verbatim from the component so the
 * provider/Ollama-aware fetching, debounced effects, validation messaging, and
 * model deletion behaviour are unchanged. The `set*` setters are exposed so the
 * inline "Refresh" buttons keep their existing optimistic wiring.
 */
export function useAISettingsModels(
  options: UseAISettingsModelsOptions,
): AISettingsModels {
  const { settings, onPersist } = options;
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

  const [downloadProgress, setDownloadProgress] =
    useState<DownloadProgress | null>(null);
  const [preparing, setPreparing] = useState<ModelPreparationTarget | null>(
    null,
  );
  const [pollingProgress, setPollingProgress] = useState(false);

  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [secondaryAvailableModels, setSecondaryAvailableModels] = useState<
    string[]
  >([]);
  const [secondaryFetchingModels, setSecondaryFetchingModels] = useState(false);

  useEffect(() => {
    getModelsStatus(settings.whisper_model_size)
      .then(setModelStatus)
      .catch(console.error);
  }, [settings.whisper_model_size]);

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

  const handleValidate = async (provider: string) => {
    setValidating(provider);
    setValidationMsg(null);

    const isSecondary =
      provider === settings.secondary_llm_provider &&
      provider !== settings.llm_provider;
    const url =
      provider === "ollama"
        ? isSecondary
          ? settings.secondary_ollama_api_url || ""
          : settings.ollama_api_url || ""
        : "";

    try {
      const res = await validateLLM(provider, "", url);
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

      if (onPersist) {
        await onPersist(settings);
      }

      setValidationMsg({
        type: "success",
        msg: `${res.message || "Validation successful"}${onPersist ? " Settings saved." : ""}`,
        provider,
      });
    } catch (e: unknown) {
      setValidationMsg({
        type: "error",
        msg: getErrorMessage(e, "Validation failed"),
        provider,
      });
    } finally {
      setValidating(null);
    }
  };

  const refreshStatus = () => {
    getModelsStatus(settings.whisper_model_size)
      .then(setModelStatus)
      .catch(console.error);
  };

  // One read on open, so a preparation started elsewhere (first-run setup, or
  // another admin) is visible immediately instead of only after a local action.
  useEffect(() => {
    let cancelled = false;
    getDownloadProgress()
      .then((progress) => {
        if (cancelled) return;
        setDownloadProgress(progress);
        if (progress.in_progress) setPollingProgress(true);
      })
      .catch(console.error);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!pollingProgress) return;

    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const progress = await getDownloadProgress();
        if (cancelled) return;
        setDownloadProgress(progress);

        if (progress.status === "complete") {
          setPollingProgress(false);
          setPreparing(null);
          refreshStatus();
          addNotification({
            type: "success",
            message: "Model preparation complete.",
          });
        } else if (progress.status === "error") {
          setPollingProgress(false);
          setPreparing(null);
          addNotification({
            type: "error",
            message: progress.message || "Model preparation failed.",
          });
        }
      } catch (e: unknown) {
        console.error("Failed to read model preparation progress", e);
      }
    }, 2000);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollingProgress]);

  const startPreparation = async (
    target: ModelPreparationTarget,
  ): Promise<boolean> => {
    setPreparing(target);
    try {
      await prepareModels(target);
      setPollingProgress(true);
      addNotification({
        type: "success",
        message: "Model preparation started. Progress is shown below.",
      });
      return true;
    } catch (e: unknown) {
      setPreparing(null);
      addNotification({
        type: "error",
        message: `Could not start model preparation: ${getErrorMessage(
          e,
          "Unknown error",
        )}`,
      });
      return false;
    }
  };

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
    downloadProgress,
    preparing,
    preparationRunning: pollingProgress,
    startPreparation,
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
