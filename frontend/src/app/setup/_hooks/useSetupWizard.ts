import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  setupSystem,
  login,
  validateLLM,
  listModels,
  checkFFmpeg,
  getInitialConfig,
  getCurrentUser,
  getDownloadProgress,
} from "@/lib/api";
import { getErrorMessage, getErrorStatus } from "@/lib/errors";

export function useSetupWizard() {
  const router = useRouter();
  const bootstrapPasswordRef = useRef("");
  const [loading, setLoading] = useState(true);
  const [step, setStep] = useState(0); // 0: Legal, 1: Account, 2: LLM, 3: HuggingFace, 4: Complete
  const [initialConfigLoaded, setInitialConfigLoaded] = useState(false);
  const [pyannoteModelsReady, setPyannoteModelsReady] = useState(false);
  const [bundledPyannoteModelsReady, setBundledPyannoteModelsReady] = useState(false);

  // Form Data
  const [formData, setFormData] = useState({
    username: "",
    password: "",
    confirmPassword: "",
    llm_provider: "gemini",
    gemini_api_key: "",
    openai_api_key: "",
    anthropic_api_key: "",
    ollama_api_url: "http://host.docker.internal:11434",
    hf_token: "",
    selected_model: "",
  });

  // Validation State
  const [validatingLLM, setValidatingLLM] = useState(false);
  const [llmValidationMsg, setLlmValidationMsg] = useState<{
    valid: boolean;
    msg: string;
  } | null>(null);
  const [error, setError] = useState("");

  // Model Selection State
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [llmSkipped, setLlmSkipped] = useState(false);
  const [modelsFetched, setModelsFetched] = useState(false);
  const [modelPreparationProgress, setModelPreparationProgress] = useState(0);
  const [modelPreparationMessage, setModelPreparationMessage] = useState(
    "Preparing transcription and speaker models...",
  );
  const [modelPreparationStage, setModelPreparationStage] = useState<string | null>(null);
  const [modelPreparationComplete, setModelPreparationComplete] = useState(false);

  // Modals
  const [showSkipLLMModal, setShowSkipLLMModal] = useState(false);
  const [ffmpegMissing, setFfmpegMissing] = useState(false);

  useEffect(() => {
    const prepareSetup = async () => {
      try {
        try {
          const user = await getCurrentUser();
          router.push(
            user.force_password_change
              ? "/settings?tab=account&forcePasswordChange=1"
              : "/",
          );
          return;

                } catch (err: unknown) {
          if (getErrorStatus(err) !== 401) {
            throw err;
          }
        }

                const ffmpegStatus = await checkFFmpeg().catch((err: unknown) => {
          if (getErrorStatus(err) === 401 || getErrorStatus(err) === 403) {
            return {
              ffmpeg: true,
              ffprobe: true,
              ffmpeg_path: null,
              ffprobe_path: null,
            };
          }

          throw err;
        });

        if (!ffmpegStatus.ffmpeg || !ffmpegStatus.ffprobe) {
          setFfmpegMissing(true);
        }

        setLoading(false);

            } catch (err: unknown) {
        console.error(err);
        setError("Failed to connect to server");
        setLoading(false);
      }
    };
    prepareSetup();
  }, [router]);

  const handleInputChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
  ) => {
    const { name, value } = e.target;
    const fieldKey = e.target.dataset.fieldKey || name;
    setFormData((prev) => ({ ...prev, [fieldKey]: value }));

    // Reset validation messages when changing keys
    if (fieldKey.includes("api_key")) {
      setLlmValidationMsg(null);
      setAvailableModels([]);
      setFormData((prev) => ({ ...prev, selected_model: "" }));
    }
    if (fieldKey === "hf_token") {
      return;
    }
    if (fieldKey === "llm_provider") {
      setLlmValidationMsg(null);
      setAvailableModels([]);
      setFormData((prev) => ({ ...prev, selected_model: "" }));
    }
  };

  const llmConfigMissing =
    !formData.llm_provider ||
    (formData.llm_provider === "gemini" && !formData.gemini_api_key) ||
    (formData.llm_provider === "openai" && !formData.openai_api_key) ||
    (formData.llm_provider === "anthropic" && !formData.anthropic_api_key) ||
    (formData.llm_provider === "ollama" && !formData.ollama_api_url);

  // --- Step 0: Legal Disclaimer ---
  const handleLegalSubmit = () => {
    setStep(1);
  };

  const getBootstrapPassword = () => bootstrapPasswordRef.current;

  const handleBootstrapPasswordChange = (
    e: React.ChangeEvent<HTMLInputElement>,
  ) => {
    bootstrapPasswordRef.current = e.target.value;
    setError("");
  };

  const loadInitialConfig = async (): Promise<boolean> => {
    if (initialConfigLoaded) {
      return true;
    }

    const bootstrapPassword = getBootstrapPassword();
    if (!bootstrapPassword) {
      setError("Bootstrap password required.");
      return false;
    }

    try {
      const initialConfig = await getInitialConfig(bootstrapPassword);
      if (Object.keys(initialConfig).length > 0) {
        setFormData((prev) => ({
          ...prev,
          llm_provider: initialConfig.llm_provider || prev.llm_provider,
          gemini_api_key: initialConfig.gemini_api_key || prev.gemini_api_key,
          openai_api_key: initialConfig.openai_api_key || prev.openai_api_key,
          anthropic_api_key:
            initialConfig.anthropic_api_key || prev.anthropic_api_key,
          ollama_api_url: initialConfig.ollama_api_url || prev.ollama_api_url,
          hf_token: initialConfig.hf_token || prev.hf_token,
          selected_model: initialConfig.selected_model || prev.selected_model,
        }));

        if (initialConfig.selected_model) {
          setAvailableModels([initialConfig.selected_model]);
        }

        setPyannoteModelsReady(Boolean(initialConfig.pyannote_models_ready));
        setBundledPyannoteModelsReady(Boolean(initialConfig.bundled_pyannote_models_ready));
      }

      setInitialConfigLoaded(true);
      return true;

        } catch (err: unknown) {
      if (getErrorStatus(err) === 403) {
        setError(
          "First-run setup access denied. Check FIRST_RUN_PASSWORD or use the login page.",
        );
        return false;
      }
      setError(
        getErrorMessage(err, "Failed to unlock first-run setup. Check FIRST_RUN_PASSWORD."),
      );
      return false;
    }
  };

  // --- Step 1: Account ---
  const handleAccountSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (formData.password !== formData.confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (formData.password.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }

    const initialConfigReady = await loadInitialConfig();
    if (!initialConfigReady) {
      return;
    }

    setError("");
    setStep(2);
  };

  // --- Step 2: LLM ---
  const validateAndFetchModels = useCallback(async () => {
    setValidatingLLM(true);
    setError("");
    setLlmValidationMsg(null);

    try {
      // 1. Validate Key/URL (server will use keys from .env)
      const res = await validateLLM(
        formData.llm_provider,
        "", // Server reads from environment
        formData.llm_provider === "ollama" ? formData.ollama_api_url : undefined,
        undefined,
        getBootstrapPassword(),
      );
      setLlmValidationMsg({
        valid: true,
        msg: res.message || "Validation successful",
      });

      // 2. Fetch Models
      const modelsRes = await listModels(
        formData.llm_provider,
        "", // Server reads from environment
        formData.llm_provider === "ollama" ? formData.ollama_api_url : undefined,
        getBootstrapPassword(),
      );
      setAvailableModels(modelsRes.models);

      if (modelsRes.models.length > 0) {
        setFormData((prev) => {
          const existingModel = prev.selected_model;
          const nextModel = modelsRes.models.includes(existingModel) ? existingModel : modelsRes.models[0];
          return {
            ...prev,
            selected_model: nextModel,
          };
        });
      } else {
        setError(
          "No models found for this provider. Please check your configuration.",
        );
      }
      setModelsFetched(true);

        } catch (err: unknown) {
      setLlmValidationMsg({
        valid: false,
        msg: getErrorMessage(err, "Validation failed"),
      });
      setModelsFetched(true);
    } finally {
      setValidatingLLM(false);
    }
  }, [
    formData.llm_provider,
    formData.ollama_api_url,
  ]);

  const handleLLMSubmit = () => {
    if (llmSkipped) {
      setStep(3);
      return;
    }

    if (!llmValidationMsg?.valid) {
      setError("Please validate your configuration first.");
      return;
    }

    if (!formData.selected_model) {
      setError("Please select a model.");
      return;
    }

    setError("");
    setStep(3);
  };

  const handleSkipLLM = () => {
    setShowSkipLLMModal(true);
  };

  const confirmSkipLLM = () => {
    setLlmSkipped(true);
    setShowSkipLLMModal(false);
    // Clear LLM data
    setFormData((prev) => ({
      ...prev,
      gemini_api_key: "",
      openai_api_key: "",
      anthropic_api_key: "",
      selected_model: "",
    }));
    setStep(3);
  };

  const handleReloadConfig = async () => {
    setError("");
    setLoading(true);
    setInitialConfigLoaded(false);
    setModelsFetched(false);
    const success = await loadInitialConfig();
    setLoading(false);
    if (success) {
      setLlmValidationMsg(null);
      setAvailableModels([]);
    }
  };

  useEffect(() => {
    if (step === 2 && initialConfigLoaded) {
      if (!llmConfigMissing && !modelsFetched && !validatingLLM) {
        void validateAndFetchModels();
      }
    }
  }, [
    formData.anthropic_api_key,
    formData.gemini_api_key,
    formData.llm_provider,
    formData.ollama_api_url,
    formData.openai_api_key,
    initialConfigLoaded,
    llmConfigMissing,
    modelsFetched,
    step,
    validateAndFetchModels,
    validatingLLM,
  ]);

  // --- Step 3: HuggingFace ---
  const handleHFSubmit = async () => {
    await createAccountAndStartDownload();
  };

  const waitForModelPreparation = async () => {
    const startedAt = Date.now();
    const timeoutMs = 30 * 60 * 1000;

    while (Date.now() - startedAt < timeoutMs) {
      const progress = await getDownloadProgress();
      setModelPreparationProgress(progress.progress);
      setModelPreparationMessage(progress.message);
      setModelPreparationStage(progress.stage || null);

      if (progress.status === "complete") {
        setModelPreparationProgress(100);
        setModelPreparationComplete(true);
        return;
      }

      if (progress.status === "error") {
        throw new Error(progress.message || "Model preparation failed.");
      }

      await new Promise((resolve) => setTimeout(resolve, 2000));
    }

    throw new Error("Model preparation timed out. Check the worker logs and model cache status.");
  };

  const createAccountAndStartDownload = async () => {
    setStep(4);
    setModelPreparationComplete(false);
    setModelPreparationProgress(0);
    setModelPreparationMessage("Preparing transcription and speaker models...");
    setModelPreparationStage("queued");
    let loggedIn = false;

    try {
      // 1. Create Admin Account & Save Settings (Credentials only saved in .env on backend)
      await setupSystem({
        username: formData.username,
        password: formData.password,
        selected_model: formData.selected_model || undefined,
      }, getBootstrapPassword());

      // 2. Login
      await login(formData.username, formData.password);
      loggedIn = true;

      await waitForModelPreparation();

        } catch (err: unknown) {
      console.error("Setup failed:", err);
      if (getErrorStatus(err) === 403) {
        setError(
          "First-run setup access denied. Check FIRST_RUN_PASSWORD or use the login page.",
        );
      } else {
        setError(getErrorMessage(err, "Setup failed. Please try again."));
      }

      if (!loggedIn) {
        setStep(3); // Go back only if the account/login did not finish.
      }
    }
  };

  const handleCompleteSetup = () => {
    router.push("/");
  };

  return {
    loading,
    step,
    formData,
    error,
    ffmpegMissing,
    showSkipLLMModal,
    setShowSkipLLMModal,
    validatingLLM,
    llmValidationMsg,
    llmConfigMissing,
    availableModels,
    pyannoteModelsReady,
    bundledPyannoteModelsReady,
    modelPreparationProgress,
    modelPreparationMessage,
    modelPreparationStage,
    modelPreparationComplete,
    handleInputChange,
    handleBootstrapPasswordChange,
    handleLegalSubmit,
    handleAccountSubmit,
    handleLLMSubmit,
    handleSkipLLM,
    confirmSkipLLM,
    handleReloadConfig,
    handleHFSubmit,
    handleCompleteSetup,
  };
}

export type UseSetupWizardReturn = ReturnType<typeof useSetupWizard>;
