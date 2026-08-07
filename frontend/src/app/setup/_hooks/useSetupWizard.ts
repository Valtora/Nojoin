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
  getSettings,
  updateSettings,
  type InitialSetupConfig,
} from "@/lib/api";
import { getErrorMessage, getErrorStatus } from "@/lib/errors";
import type { CliOAuthStatus, CliProvider } from "@/types";

// Shown for every unlock failure. The backend deliberately returns one
// generic denial whether the password is wrong, FIRST_RUN_PASSWORD is unset,
// or the system is already initialised, so the client cannot say more.
const SETUP_ACCESS_DENIED_MESSAGE =
  "Setup access denied. Check the first-run password, or sign in normally if this system is already set up.";

/**
 * The wizard's steps, in order.
 *
 * The account is created at STEP_ACCOUNT rather than at the end. That ordering
 * is what makes the rest of the wizard possible: every later step runs with a
 * real session, so the AI step can connect a Claude or ChatGPT subscription
 * (which is per-user and needs authentication) instead of only being able to
 * report what the server's .env already contained. It also means model
 * preparation, queued when the account is created, downloads in the background
 * while the operator configures AI rather than behind a progress bar they have
 * to sit and watch.
 */
export const STEP_UNLOCK = 0;
export const STEP_LEGAL = 1;
export const STEP_TRANSCRIPTION = 2;
export const STEP_ACCOUNT = 3;
export const STEP_AI = 4;
export const STEP_COMPLETE = 5;

export const SETUP_STEPS = [
  { id: STEP_LEGAL, label: "Terms" },
  { id: STEP_TRANSCRIPTION, label: "Transcription" },
  { id: STEP_ACCOUNT, label: "Account" },
  { id: STEP_AI, label: "AI" },
  { id: STEP_COMPLETE, label: "Finish" },
] as const;

/** Which route the operator takes for AI on the AI step. */
export type AiRoute = "server" | "subscription" | "later";

const PROGRESS_POLL_INTERVAL_MS = 2000;
const PROGRESS_TIMEOUT_MS = 30 * 60 * 1000;
// The progress endpoint reports "idle" both before the task has claimed the job
// and after its Redis record has gone (or when Redis is unreachable). Waiting
// out a grace period before believing an unbroken run of idles distinguishes
// "not started yet" from "nothing to wait for".
const PROGRESS_IDLE_GRACE_MS = 20 * 1000;
// Model preparation is a long download; a single failed poll means the network
// blinked, not that the download died.
const PROGRESS_MAX_CONSECUTIVE_ERRORS = 5;

function isBlank(value: string): boolean {
  return value.trim().length === 0;
}

export function useSetupWizard() {
  const router = useRouter();
  const bootstrapPasswordRef = useRef("");
  const [loading, setLoading] = useState(true);
  const [step, setStep] = useState<number>(STEP_UNLOCK);
  const [unlocking, setUnlocking] = useState(false);
  const [reloadingConfig, setReloadingConfig] = useState(false);
  const [creatingAccount, setCreatingAccount] = useState(false);
  const [savingAi, setSavingAi] = useState(false);
  // Once the owner exists the wizard is authenticated, the bootstrap password
  // stops being accepted, and the account step can no longer be revisited.
  const [accountCreated, setAccountCreated] = useState(false);
  const [serverConfig, setServerConfig] = useState<InitialSetupConfig | null>(null);

  const [formData, setFormData] = useState({
    username: "",
    password: "",
    confirmPassword: "",
    selected_model: "",
    whisper_model_size: "turbo",
  });
  const [includeDemoRecording, setIncludeDemoRecording] = useState(true);
  // Opt-out: ticked by default, and the tick is this install's telemetry
  // consent, so a first-run install never sees the later admin notice.
  const [enableTelemetry, setEnableTelemetry] = useState(true);

  // AI step
  const [aiRoute, setAiRoute] = useState<AiRoute>("server");
  const [validatingLLM, setValidatingLLM] = useState(false);
  const [llmValidationMsg, setLlmValidationMsg] = useState<{
    valid: boolean;
    msg: string;
  } | null>(null);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [modelsFetched, setModelsFetched] = useState(false);
  const [cliStatus, setCliStatus] = useState<CliOAuthStatus | null>(null);

  const [error, setError] = useState("");
  const [ffmpegMissing, setFfmpegMissing] = useState(false);

  // Model preparation
  const [modelPreparationProgress, setModelPreparationProgress] = useState(0);
  const [modelPreparationMessage, setModelPreparationMessage] = useState(
    "Preparing transcription and speaker models...",
  );
  const [modelPreparationStage, setModelPreparationStage] = useState<string | null>(
    null,
  );
  const [modelPreparationComplete, setModelPreparationComplete] = useState(false);
  const [modelPreparationWarning, setModelPreparationWarning] = useState("");

  useEffect(() => {
    const prepareSetup = async () => {
      try {
        try {
          const user = await getCurrentUser();
          router.push(user.force_password_change ? "/settings/profile" : "/");
          return;
        } catch (err: unknown) {
          if (getErrorStatus(err) !== 401) {
            throw err;
          }
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

  /** Every transition clears the banner, so an error cannot follow the operator
   * into a step it does not describe. A stale one used to reach the final step,
   * where it also surfaced a premature "Continue to Dashboard" button. */
  const goToStep = useCallback((next: number) => {
    setError("");
    setStep(next);
  }, []);

  const handleInputChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
  ) => {
    const { name, value } = e.target;
    const fieldKey = e.target.dataset.fieldKey || name;
    setFormData((prev) => ({ ...prev, [fieldKey]: value }));
  };

  const getBootstrapPassword = () => bootstrapPasswordRef.current;
  // Once the system is initialised the bootstrap password is refused and the
  // session cookie authenticates instead.
  const setupCredential = useCallback(
    () => (accountCreated ? undefined : bootstrapPasswordRef.current),
    [accountCreated],
  );

  const handleBootstrapPasswordChange = (
    e: React.ChangeEvent<HTMLInputElement>,
  ) => {
    bootstrapPasswordRef.current = e.target.value;
    setError("");
  };

  /**
   * Always refetches. The previous version early-returned on a captured
   * `initialConfigLoaded`, so "Check config again" (the one button an operator
   * without a key is told to press after editing .env) refetched nothing on the
   * first click and only worked on the second.
   */
  const loadInitialConfig = useCallback(async (): Promise<boolean> => {
    const credential = accountCreated ? undefined : bootstrapPasswordRef.current;
    if (!accountCreated && !credential) {
      setError("Enter the first-run setup password.");
      return false;
    }

    try {
      const initialConfig = await getInitialConfig(credential);
      setServerConfig(initialConfig);
      if (initialConfig.selected_model) {
        setFormData((prev) => ({
          ...prev,
          selected_model: prev.selected_model || initialConfig.selected_model || "",
        }));
      }
      return true;
    } catch (err: unknown) {
      if (getErrorStatus(err) === 403) {
        setError(SETUP_ACCESS_DENIED_MESSAGE);
        return false;
      }
      setError(
        getErrorMessage(
          err,
          "Unable to unlock setup. Check the server logs and try again.",
        ),
      );
      return false;
    }
  }, [accountCreated]);

  // --- Step 0: Unlock ---
  const handleUnlockSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setUnlocking(true);
    const unlocked = await loadInitialConfig();
    setUnlocking(false);
    if (unlocked) {
      goToStep(STEP_LEGAL);
    }
  };

  // --- Step 1: Legal ---
  const handleLegalSubmit = () => goToStep(STEP_TRANSCRIPTION);

  // --- Step 2: Transcription and speaker models ---
  const handleTranscriptionSubmit = () => goToStep(STEP_ACCOUNT);

  // --- Step 3: Account (this is where the owner is created) ---
  const handleAccountSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    // Mirrors backend/core/security.py:validate_password_policy exactly. When
    // the client was laxer than the server, a password the server rejected only
    // failed after the wizard had advanced past the field that set it.
    if (isBlank(formData.username)) {
      setError("Enter a username.");
      return;
    }
    if (formData.password.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }
    if (isBlank(formData.password)) {
      setError("Password cannot be all whitespace.");
      return;
    }
    if (formData.password !== formData.confirmPassword) {
      setError("Passwords do not match");
      return;
    }

    setError("");
    setCreatingAccount(true);
    let created = false;

    try {
      await setupSystem(
        {
          username: formData.username,
          password: formData.password,
          whisper_model_size: formData.whisper_model_size,
          include_demo_recording: includeDemoRecording,
          enable_telemetry: enableTelemetry,
        },
        getBootstrapPassword(),
      );
      created = true;

      await login(formData.username, formData.password);
      setAccountCreated(true);

      // FFmpeg availability is admin-gated, so it can only be checked once the
      // owner session exists.
      try {
        const ffmpegStatus = await checkFFmpeg();
        if (!ffmpegStatus.ffmpeg || !ffmpegStatus.ffprobe) {
          setFfmpegMissing(true);
        }
      } catch {
        // Non-fatal: admin health surfaces FFmpeg problems after setup.
      }

      goToStep(STEP_AI);
    } catch (err: unknown) {
      console.error("Account creation failed:", err);
      if (created) {
        // The owner exists but the session does not, and the system is now
        // initialised, so retrying here can only be denied. Send them to the
        // sign-in page rather than leaving them on a button that cannot work.
        setError(
          "Your account was created, but signing in automatically failed. Sign in at /login with the credentials you just set, then finish configuring AI in Settings > Your AI.",
        );
      } else {
        setError(
          getErrorStatus(err) === 403
            ? SETUP_ACCESS_DENIED_MESSAGE
            : getErrorMessage(err, "Could not create the account. Please try again."),
        );
      }
    } finally {
      setCreatingAccount(false);
    }
  };

  // --- Step 4: AI ---
  const serverProvider = serverConfig?.llm_provider || "";
  const serverProviderSelected = Boolean(serverConfig?.llm_provider_selected);
  const serverCredentialPresent = (() => {
    if (!serverConfig) return false;
    switch (serverProvider) {
      case "gemini":
        return Boolean(serverConfig.gemini_api_key);
      case "openai":
        return Boolean(serverConfig.openai_api_key);
      case "anthropic":
        return Boolean(serverConfig.anthropic_api_key);
      case "ollama":
        return Boolean(serverConfig.ollama_api_url);
      default:
        return false;
    }
  })();
  const secondaryProviderConfigured = Boolean(
    serverConfig?.secondary_llm_provider &&
      (serverConfig.secondary_llm_provider === "ollama" ||
        serverConfig.secondary_api_key),
  );
  const connectedCliProvider: CliProvider | undefined = (
    cliStatus?.providers ?? []
  ).find((entry) => entry.connected)?.provider;
  const subscriptionConnected = connectedCliProvider !== undefined;

  const validateAndFetchModels = useCallback(async () => {
    setValidatingLLM(true);
    setError("");
    setLlmValidationMsg(null);

    try {
      const res = await validateLLM(
        serverProvider,
        "", // Server reads keys from its own environment.
        serverProvider === "ollama" ? serverConfig?.ollama_api_url : undefined,
        undefined,
        setupCredential(),
      );
      setLlmValidationMsg({
        valid: true,
        msg: res.message || "Validation successful",
      });

      const modelsRes = await listModels(
        serverProvider,
        "",
        serverProvider === "ollama" ? serverConfig?.ollama_api_url : undefined,
        setupCredential(),
      );
      setAvailableModels(modelsRes.models);

      if (modelsRes.models.length > 0) {
        setFormData((prev) => ({
          ...prev,
          selected_model: modelsRes.models.includes(prev.selected_model)
            ? prev.selected_model
            : modelsRes.models[0],
        }));
      } else {
        setLlmValidationMsg({
          valid: false,
          msg: "The provider accepted the credential but returned no models.",
        });
      }
    } catch (err: unknown) {
      setLlmValidationMsg({
        valid: false,
        msg: getErrorMessage(err, "Validation failed"),
      });
    } finally {
      setModelsFetched(true);
      setValidatingLLM(false);
    }
  }, [serverProvider, serverConfig?.ollama_api_url, setupCredential]);

  useEffect(() => {
    if (step !== STEP_AI) return;
    if (!serverCredentialPresent || modelsFetched || validatingLLM) return;
    void validateAndFetchModels();
  }, [
    step,
    serverCredentialPresent,
    modelsFetched,
    validatingLLM,
    validateAndFetchModels,
  ]);

  // A server provider that answers is the default route; otherwise start the
  // operator on the route that does not need one.
  useEffect(() => {
    if (step !== STEP_AI || !modelsFetched) return;
    setAiRoute((current) =>
      current === "server" && !llmValidationMsg?.valid ? "subscription" : current,
    );
  }, [step, modelsFetched, llmValidationMsg?.valid]);

  useEffect(() => {
    if (subscriptionConnected) {
      setAiRoute("subscription");
    }
  }, [subscriptionConnected]);

  const handleReloadConfig = async () => {
    setError("");
    setReloadingConfig(true);
    setLlmValidationMsg(null);
    setAvailableModels([]);
    setModelsFetched(false);
    await loadInitialConfig();
    setReloadingConfig(false);
  };

  /** Persists the AI choice, then moves on. Nothing here is fatal to setup:
   * every one of these is changeable in Settings afterwards, so a failed write
   * reports itself and still lets the operator finish. */
  const handleAiSubmit = async () => {
    setSavingAi(true);
    setError("");

    try {
      if (aiRoute === "subscription" && connectedCliProvider) {
        const settings = await getSettings();
        await updateSettings({
          ...settings,
          usage_model: "cli_oauth",
          cli_provider: connectedCliProvider,
        });
      } else if (aiRoute === "server" && formData.selected_model) {
        const modelKey =
          serverProvider === "ollama" ? "ollama_model" : `${serverProvider}_model`;
        const settings = await getSettings();
        await updateSettings({ ...settings, [modelKey]: formData.selected_model });
      }
      goToStep(STEP_COMPLETE);
    } catch (err: unknown) {
      console.error("Failed to save the AI configuration:", err);
      setError(
        getErrorMessage(
          err,
          "Could not save the AI choice. You can set it in Settings > Your AI after finishing.",
        ),
      );
      setStep(STEP_COMPLETE);
    } finally {
      setSavingAi(false);
    }
  };

  // --- Step 5: Complete ---
  useEffect(() => {
    if (step !== STEP_COMPLETE) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const startedAt = Date.now();
    let consecutiveErrors = 0;
    let sawActivity = false;

    const finish = (warning?: string) => {
      if (cancelled) return;
      setModelPreparationProgress(100);
      setModelPreparationComplete(true);
      if (warning) setModelPreparationWarning(warning);
    };

    const tick = async () => {
      if (cancelled) return;

      try {
        const progress = await getDownloadProgress();
        if (cancelled) return;
        consecutiveErrors = 0;

        if (progress.status !== "idle") {
          sawActivity = true;
          setModelPreparationProgress(progress.progress);
          setModelPreparationMessage(progress.message);
          setModelPreparationStage(progress.stage || null);
        }

        if (progress.status === "complete") {
          finish();
          return;
        }

        if (progress.status === "error") {
          finish(
            progress.message ||
              "Model preparation reported a problem. Settings > AI can retry it.",
          );
          return;
        }

        // Idle: either the task has not claimed the job yet, or there is
        // nothing running to wait for. Only the grace window tells them apart.
        if (
          progress.status === "idle" &&
          !sawActivity &&
          Date.now() - startedAt > PROGRESS_IDLE_GRACE_MS
        ) {
          finish();
          return;
        }
      } catch (err: unknown) {
        if (cancelled) return;
        consecutiveErrors += 1;
        if (consecutiveErrors >= PROGRESS_MAX_CONSECUTIVE_ERRORS) {
          console.error("Model preparation progress unavailable:", err);
          finish(
            "Lost contact with the progress endpoint. Preparation continues on the server; Settings > AI shows the current state.",
          );
          return;
        }
      }

      if (Date.now() - startedAt > PROGRESS_TIMEOUT_MS) {
        finish(
          "Model preparation is taking longer than expected. It continues in the background on the server, and Settings > AI shows the current state.",
        );
        return;
      }

      timer = setTimeout(tick, PROGRESS_POLL_INTERVAL_MS);
    };

    void tick();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [step]);

  const handleCompleteSetup = () => {
    router.push("/");
  };

  return {
    loading,
    step,
    formData,
    error,
    unlocking,
    reloadingConfig,
    creatingAccount,
    savingAi,
    accountCreated,
    includeDemoRecording,
    setIncludeDemoRecording,
    enableTelemetry,
    setEnableTelemetry,
    ffmpegMissing,
    // AI step
    aiRoute,
    setAiRoute,
    validatingLLM,
    llmValidationMsg,
    availableModels,
    serverProvider,
    serverProviderSelected,
    serverCredentialPresent,
    secondaryProviderConfigured,
    secondaryProvider: serverConfig?.secondary_llm_provider ?? null,
    setCliStatus,
    connectedCliProvider,
    // Transcription step
    pyannoteModelsReady: Boolean(serverConfig?.pyannote_models_ready),
    bundledPyannoteModelsReady: Boolean(serverConfig?.bundled_pyannote_models_ready),
    hfTokenPresent: Boolean(serverConfig?.hf_token),
    // Completion
    modelPreparationProgress,
    modelPreparationMessage,
    modelPreparationStage,
    modelPreparationComplete,
    modelPreparationWarning,
    // Handlers
    goToStep,
    handleInputChange,
    handleBootstrapPasswordChange,
    handleUnlockSubmit,
    handleLegalSubmit,
    handleTranscriptionSubmit,
    handleAccountSubmit,
    handleReloadConfig,
    handleAiSubmit,
    handleCompleteSetup,
  };
}

export type UseSetupWizardReturn = ReturnType<typeof useSetupWizard>;
