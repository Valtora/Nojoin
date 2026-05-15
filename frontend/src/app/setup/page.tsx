"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import {
  setupSystem,
  downloadModels,
  login,
  validateLLM,
  validateHF,
  getDownloadProgress,
  listModels,
  checkFFmpeg,
  getInitialConfig,
  getCurrentUser,
} from "@/lib/api";
import {
  Loader2,
  CheckCircle,
  Check,
  X,
  AlertTriangle,
  ArrowRight,
} from "lucide-react";
import ConfirmationModal from "@/components/ConfirmationModal";

export default function SetupPage() {
  const router = useRouter();
  const bootstrapPasswordRef = useRef("");
  const [loading, setLoading] = useState(true);
  const [step, setStep] = useState(0); // 0: Legal, 1: Account, 2: LLM, 3: HuggingFace, 4: Download
  const [initialConfigLoaded, setInitialConfigLoaded] = useState(false);

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
  const [validatingHF, setValidatingHF] = useState(false);
  const [llmValidationMsg, setLlmValidationMsg] = useState<{
    valid: boolean;
    msg: string;
  } | null>(null);
  const [hfValidationMsg, setHfValidationMsg] = useState<{
    valid: boolean;
    msg: string;
  } | null>(null);
  const [error, setError] = useState("");

  // Model Selection State
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [llmSkipped, setLlmSkipped] = useState(false);

  // Download State
  const [downloadProgress, setDownloadProgress] = useState(0);
  const [downloadStage, setDownloadStage] = useState("");
  const [downloadMessage, setDownloadMessage] = useState(
    "Checking download status...",
  );
  const [downloadComplete, setDownloadComplete] = useState(false);

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
        } catch (err: any) {
          if (err?.response?.status !== 401) {
            throw err;
          }
        }

        const ffmpegStatus = await checkFFmpeg().catch((err: any) => {
          if (err?.response?.status === 401 || err?.response?.status === 403) {
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
      } catch (err) {
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
      setHfValidationMsg(null);
    }
    if (fieldKey === "llm_provider") {
      setLlmValidationMsg(null);
      setAvailableModels([]);
      setFormData((prev) => ({ ...prev, selected_model: "" }));
    }
  };

  // --- Step 0: Legal Disclaimer ---
  const handleLegalSubmit = () => {
    setStep(1);
  };

  const getBootstrapPassword = () => bootstrapPasswordRef.current;

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
      }

      setInitialConfigLoaded(true);
      return true;
    } catch (err: any) {
      if (err.response?.status === 403) {
        setError(
          "First-run setup access denied. Check FIRST_RUN_PASSWORD or use the login page.",
        );
        return false;
      }
      setError(
        err.response?.data?.detail ||
          "Failed to unlock first-run setup. Check FIRST_RUN_PASSWORD.",
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
  const getCurrentCredentials = () => {
    if (formData.llm_provider === "gemini")
      return { key: formData.gemini_api_key };
    if (formData.llm_provider === "openai")
      return { key: formData.openai_api_key };
    if (formData.llm_provider === "anthropic")
      return { key: formData.anthropic_api_key };
    if (formData.llm_provider === "ollama")
      return {
        url: formData.ollama_api_url || "http://host.docker.internal:11434",
      };
    return {};
  };

  const validateAndFetchModels = async () => {
    const creds = getCurrentCredentials();
    if (!creds.key && !creds.url) {
      setError("Please enter an API key or URL");
      return;
    }

    setValidatingLLM(true);
    setError("");
    setLlmValidationMsg(null);

    try {
      // 1. Validate Key/URL
      const res = await validateLLM(
        formData.llm_provider,
        creds.key || "",
        creds.url,
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
        creds.key || "",
        creds.url,
        getBootstrapPassword(),
      );
      setAvailableModels(modelsRes.models);

      if (modelsRes.models.length > 0) {
        setFormData((prev) => ({
          ...prev,
          selected_model: modelsRes.models[0],
        }));
      } else {
        setError(
          "No models found for this provider. Please check your configuration.",
        );
      }
    } catch (err: any) {
      setLlmValidationMsg({
        valid: false,
        msg: err.response?.data?.detail || err.message,
      });
    } finally {
      setValidatingLLM(false);
    }
  };

  const handleLLMSubmit = () => {
    if (llmSkipped) {
      setStep(3);
      return;
    }

    const creds = getCurrentCredentials();
    if (!creds.key && !creds.url) {
      setError("Please enter an API key/URL or skip this step.");
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

  // --- Step 3: HuggingFace ---
  const validateHFToken = async () => {
    if (!formData.hf_token) {
      setError("Please enter a token");
      return;
    }

    setValidatingHF(true);
    setError("");
    setHfValidationMsg(null);

    try {
      const res = await validateHF(formData.hf_token, getBootstrapPassword());
      setHfValidationMsg({
        valid: true,
        msg: res.message || "Validation successful",
      });
    } catch (err: any) {
      setHfValidationMsg({
        valid: false,
        msg: err.response?.data?.detail || err.message,
      });
    } finally {
      setValidatingHF(false);
    }
  };

  const handleHFSubmit = async () => {
    // If token provided, must be valid
    if (formData.hf_token && !hfValidationMsg?.valid) {
      setError("Please validate your token first.");
      return;
    }

    // Proceed to create account and start download
    await createAccountAndStartDownload();
  };

  const createAccountAndStartDownload = async () => {
    setStep(4);

    try {
      // 1. Create Admin Account & Save Settings
      await setupSystem({
        username: formData.username,
        password: formData.password,
        llm_provider: formData.llm_provider,
        gemini_api_key: formData.gemini_api_key,
        openai_api_key: formData.openai_api_key,
        anthropic_api_key: formData.anthropic_api_key,
        ollama_api_url: formData.ollama_api_url,
        hf_token: formData.hf_token,
        // Save selected model
        selected_model: formData.selected_model,
      }, getBootstrapPassword());

      // 2. Login
      await login(formData.username, formData.password);

      // 3. Start Download
      startModelDownload();
    } catch (err: any) {
      console.error("Setup failed:", err);
      if (err.response?.status === 403) {
        setError(
          "First-run setup access denied. Check FIRST_RUN_PASSWORD or use the login page.",
        );
      } else {
        setError(err.response?.data?.detail || "Setup failed. Please try again.");
      }
      setStep(3); // Go back
    }
  };

  // --- Step 4: Download ---
  const startModelDownload = async () => {
    setDownloadMessage("Checking download status...");

    try {
      // Check existing progress
      const sharedProgress = await getDownloadProgress();
      if (sharedProgress.status === "complete") {
        completeSetupAndRedirect();
        return;
      }

      if (sharedProgress.in_progress) {
        pollDownloadProgress();
        return;
      }

      // Start new download
      await downloadModels({
        hf_token: formData.hf_token,
        whisper_model_size: "turbo", // Default
      });

      pollDownloadProgress();
    } catch (err: any) {
      console.error("Download start failed:", err);
      setDownloadMessage(`Error starting download: ${err.message}`);
    }
  };

  const pollDownloadProgress = () => {
    const interval = setInterval(async () => {
      try {
        const progress = await getDownloadProgress();

        if (progress.status === "complete") {
          clearInterval(interval);
          completeSetupAndRedirect();
        } else if (progress.status === "error") {
          clearInterval(interval);
          setDownloadMessage(`Download failed: ${progress.message}`);
        } else {
          setDownloadProgress(progress.progress || 0);
          setDownloadStage(progress.stage || "");
          setDownloadMessage(progress.message || "Downloading...");
        }
      } catch (err) {
        console.error("Polling error:", err);
      }
    }, 1000);
  };

  const completeSetupAndRedirect = () => {
    setDownloadProgress(100);
    setDownloadStage("complete");
    setDownloadMessage("All models ready!");
    setDownloadComplete(true);
    // Removed automatic redirect
  };

  const handleCompleteSetup = () => {
    router.push("/");
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-200 dark:bg-gray-900">
        <Loader2 className="w-8 h-8 animate-spin text-orange-500" />
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gray-200 dark:bg-gray-900 p-4">
      <ConfirmationModal
        isOpen={showSkipLLMModal}
        onClose={() => setShowSkipLLMModal(false)}
        onConfirm={confirmSkipLLM}
        title="Skip AI Setup?"
        message="Without an AI provider and model, Nojoin will still record and transcribe meetings, but automatic meeting enhancement will be skipped. Generate Notes, meeting chat, and Retry Speaker Inference will work after you configure AI later in Settings."
        confirmText="Skip AI Configuration"
        isDangerous={true}
      />

      <div className="w-full max-w-md bg-white dark:bg-gray-800 rounded-2xl shadow-xl overflow-hidden">
        {/* Header */}
        <div className="bg-orange-600 p-6 text-center">
          <div className="flex justify-center mb-4">
            <div className="bg-white p-3 rounded-full shadow-lg">
              <Image
                src="/assets/NojoinLogo.png"
                alt="Nojoin"
                width={48}
                height={48}
                className="w-12 h-12"
              />
            </div>
          </div>
          <h1 className="text-2xl font-bold text-white">Welcome to Nojoin</h1>
          <p className="text-orange-100 mt-2">Initial System Setup</p>
        </div>

        {/* Progress Steps */}
        <div className="flex border-b border-gray-200 dark:border-gray-700">
          {[0, 1, 2, 3, 4].map((s) => (
            <div
              key={s}
              className={`flex-1 h-1 ${s <= step ? "bg-blue-600" : "bg-gray-200 dark:bg-gray-700"}`}
            />
          ))}
        </div>

        <div className="p-8">
          {error && (
            <div
              id="setup-error"
              role="alert"
              aria-live="polite"
              className="mb-6 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg flex items-start gap-3"
            >
              <AlertTriangle className="w-5 h-5 text-red-600 dark:text-red-400 shrink-0 mt-0.5" />
              <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
            </div>
          )}

          {ffmpegMissing && (
            <div className="mb-6 p-4 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-yellow-600 dark:text-yellow-400 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-yellow-800 dark:text-yellow-300">
                  FFmpeg not detected
                </p>
                <p className="text-sm text-yellow-600 dark:text-yellow-400 mt-1">
                  FFmpeg/FFprobe were not found in your system PATH. Audio
                  processing features will not work correctly. Please install
                  FFmpeg and restart the application.
                </p>
              </div>
            </div>
          )}

          {/* Step 0: Legal Disclaimer */}
          {step === 0 && (
            <div className="space-y-6">
              <div className="text-center mb-6">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
                  Legal Disclaimer
                </h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  Please review and accept the terms of use
                </p>
              </div>

              <div className="prose prose-sm dark:prose-invert max-w-none bg-gray-50 dark:bg-gray-900/50 p-4 rounded-lg border border-gray-200 dark:border-gray-700 max-h-64 overflow-y-auto">
                <h3 className="text-base font-semibold mt-0">
                  1. Compliance with Laws
                </h3>
                <p>
                  You acknowledge that many legal jurisdictions require the
                  consent of all parties before a conversation can be recorded.
                  It is your sole responsibility to ensure compliance with all
                  applicable laws and regulations regarding audio recording and
                  transcription in your jurisdiction.
                </p>

                <h3 className="text-base font-semibold">
                  2. Data Privacy & Local Processing
                </h3>
                <p>Nojoin is designed with a privacy-first architecture.</p>
                <ul className="list-disc pl-4 space-y-1">
                  <li>
                    Nojoin does not store or transmit audio data to third
                    parties without your explicit consent.
                  </li>
                  <li>
                    All audio processing (transcription, diarization, etc.) is
                    performed locally on your machine or your self-hosted
                    server, unless you explicitly configure an external
                    provider.
                  </li>
                </ul>

                <p className="font-medium mt-4">
                  By proceeding, you agree to these terms and accept full
                  responsibility for the lawful use of this software.
                </p>
              </div>

              <button
                onClick={handleLegalSubmit}
                className="w-full bg-orange-600 hover:bg-orange-700 text-white font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
              >
                I Accept & Continue <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          )}

          {/* Step 1: Account */}
          {step === 1 && (
            <form
              id="setup-admin-account-form"
              name="setup-admin-account-form"
              method="post"
              onSubmit={handleAccountSubmit}
              className="space-y-4"
              autoComplete="on"
            >
              <div className="text-center mb-6">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
                  Create Admin Account
                </h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  Set up your administrator credentials
                </p>
              </div>

              <div>
                <label htmlFor="setup-bootstrap-password" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Bootstrap Password
                </label>
                <input
                  id="setup-bootstrap-password"
                  type="password"
                  name="setup-bootstrap-password"
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  aria-describedby={error ? "setup-error" : undefined}
                  aria-invalid={Boolean(error)}
                  required
                  onChange={(e) => {
                    bootstrapPasswordRef.current = e.target.value;
                    setError("");
                  }}
                  className="w-full px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none"
                  placeholder="Enter first-run bootstrap password"
                />
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  Set FIRST_RUN_PASSWORD before the first deployment. This password is only used for initialisation.
                </p>
              </div>

              <div>
                <label htmlFor="setup-admin-username" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Username
                </label>
                <input
                  id="setup-admin-username"
                  type="text"
                  name="setup-admin-username"
                  data-field-key="username"
                  autoComplete="section-setup-admin username"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  aria-describedby={error ? "setup-error" : undefined}
                  aria-invalid={Boolean(error)}
                  required
                  value={formData.username}
                  onChange={handleInputChange}
                  className="w-full px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none"
                  placeholder="admin"
                />
              </div>

              <div>
                <label htmlFor="setup-admin-new-password" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Password
                </label>
                <input
                  id="setup-admin-new-password"
                  type="password"
                  name="setup-admin-new-password"
                  data-field-key="password"
                  autoComplete="section-setup-admin new-password"
                  aria-describedby={error ? "setup-error" : undefined}
                  aria-invalid={Boolean(error)}
                  required
                  minLength={8}
                  value={formData.password}
                  onChange={handleInputChange}
                  className="w-full px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none"
                  placeholder="••••••••"
                />
              </div>

              <div>
                <label htmlFor="setup-admin-confirm-password" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Confirm Password
                </label>
                <input
                  id="setup-admin-confirm-password"
                  type="password"
                  name="setup-admin-confirm-password"
                  data-field-key="confirmPassword"
                  autoComplete="section-setup-admin new-password"
                  aria-describedby={error ? "setup-error" : undefined}
                  aria-invalid={Boolean(error)}
                  required
                  minLength={8}
                  value={formData.confirmPassword}
                  onChange={handleInputChange}
                  className="w-full px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none"
                  placeholder="••••••••"
                />
              </div>

              <button
                type="submit"
                className="w-full mt-6 bg-orange-600 hover:bg-orange-700 text-white font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
              >
                Next Step <ArrowRight className="w-4 h-4" />
              </button>
            </form>
          )}

          {/* Step 2: LLM Setup */}
          {step === 2 && (
            <div className="space-y-4">
              <div className="text-center mb-6">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
                  AI Configuration
                </h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  Configure an AI provider for automatic meeting enhancement,
                  notes, chat, and speaker inference
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Provider
                </label>
                <select
                  name="llm_provider"
                  value={formData.llm_provider}
                  onChange={handleInputChange}
                  className="w-full px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none"
                >
                  <option value="gemini">Google Gemini</option>
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="ollama">Ollama (Local)</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  {["ollama"].includes(formData.llm_provider)
                    ? "API URL"
                    : "API Key"}
                </label>
                <div className="flex gap-2">
                  <input
                    type={
                      ["ollama"].includes(formData.llm_provider)
                        ? "text"
                        : "password"
                    }
                    name={
                      formData.llm_provider === "ollama"
                        ? "ollama_api_url"
                        : `${formData.llm_provider}_api_key`
                    }
                    value={
                      formData.llm_provider === "ollama"
                        ? formData.ollama_api_url || ""
                        : getCurrentCredentials().key || ""
                    }
                    onChange={handleInputChange}
                    className="flex-1 px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none"
                    placeholder={
                      formData.llm_provider === "ollama"
                        ? "http://host.docker.internal:11434"
                        : `Enter ${formData.llm_provider} API Key`
                    }
                  />
                  <button
                    type="button"
                    onClick={validateAndFetchModels}
                    disabled={
                      validatingLLM ||
                      (!getCurrentCredentials().key &&
                        !getCurrentCredentials().url)
                    }
                    className="px-4 py-2 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded-lg font-medium disabled:opacity-50 transition-colors"
                  >
                    {validatingLLM ? (
                      <Loader2 className="w-5 h-5 animate-spin" />
                    ) : (
                      "Validate"
                    )}
                  </button>
                </div>
                {["ollama"].includes(formData.llm_provider) && (
                  <p className="mt-1 text-xs text-yellow-600 dark:text-yellow-400 flex items-center gap-1">
                    <AlertTriangle className="w-3 h-3" />
                    Local models run on your hardware. Performance depends on
                    your GPU/CPU.
                  </p>
                )}
                {llmValidationMsg && (
                  <p
                    className={`text-xs mt-1 flex items-center gap-1 ${llmValidationMsg.valid ? "text-green-600" : "text-red-600"}`}
                  >
                    {llmValidationMsg.valid ? (
                      <Check className="w-3 h-3" />
                    ) : (
                      <X className="w-3 h-3" />
                    )}
                    {llmValidationMsg.msg}
                  </p>
                )}
              </div>

              {/* Model Selection - Only shown after validation */}
              {availableModels.length > 0 && (
                <div className="animate-in fade-in slide-in-from-top-2 duration-300">
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Select Model
                  </label>
                  <select
                    name="selected_model"
                    value={formData.selected_model}
                    onChange={handleInputChange}
                    className="w-full px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none"
                  >
                    {availableModels.map((model) => (
                      <option key={model} value={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-gray-500 mt-1">
                    Select the model to use for chat and notes.
                  </p>

                  {/* Model Recommendations */}
                  {formData.llm_provider === "gemini" && (
                    <div className="mt-2 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-100 dark:border-blue-800 text-xs text-blue-800 dark:text-blue-200">
                      <p className="font-semibold mb-1">Recommended Models:</p>
                      <ul className="list-disc list-inside space-y-0.5">
                        <li>
                          <strong>gemini-flash-latest</strong>: Faster
                          responses, good for simple transcripts.
                        </li>
                        <li>
                          <strong>gemini-pro-latest</strong>: Better reasoning,
                          recommended for complex meetings.
                        </li>
                      </ul>
                    </div>
                  )}
                  {formData.llm_provider === "openai" && (
                    <div className="mt-2 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-100 dark:border-blue-800 text-xs text-blue-800 dark:text-blue-200">
                      <p className="font-semibold mb-1">Recommended Models:</p>
                      <ul className="list-disc list-inside space-y-0.5">
                        <li>
                          <strong>GPT-5 mini (or later)</strong>: Faster,
                          cost-effective for simple chat tasks.
                        </li>
                        <li>
                          <strong>GPT-5.1 (or later)</strong>: Higher
                          intelligence, recommended for complex analysis.
                        </li>
                      </ul>
                    </div>
                  )}
                  {formData.llm_provider === "anthropic" && (
                    <div className="mt-2 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-100 dark:border-blue-800 text-xs text-blue-800 dark:text-blue-200">
                      <p className="font-semibold mb-1">Recommended Models:</p>
                      <ul className="list-disc list-inside space-y-0.5">
                        <li>
                          <strong>Claude Haiku</strong>: Fast and efficient for
                          simple chats.
                        </li>
                        <li>
                          <strong>Claude Sonnet</strong>: Good reasoning, best
                          for medium complexity meetings.
                        </li>
                        <li>
                          <strong>Claude Opus</strong>: Strong reasoning, best
                          for complex meetings and topics.
                        </li>
                      </ul>
                    </div>
                  )}
                </div>
              )}

              <div className="flex gap-3 mt-6">
                <button
                  type="button"
                  onClick={handleSkipLLM}
                  className="flex-1 px-4 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-lg font-medium transition-colors"
                >
                  Skip for now
                </button>
                <button
                  type="button"
                  onClick={handleLLMSubmit}
                  disabled={
                    !llmValidationMsg?.valid || !formData.selected_model
                  }
                  className="flex-1 bg-orange-600 hover:bg-orange-700 disabled:bg-gray-400 disabled:cursor-not-allowed text-white font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
                >
                  Next Step <ArrowRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}

          {/* Step 3: HuggingFace */}
          {step === 3 && (
            <div className="space-y-4">
              <div className="text-center mb-6">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
                  Hugging Face Token
                </h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  Optional: Required for speaker identification
                </p>
              </div>

              <div className="bg-blue-50 dark:bg-blue-900/20 p-4 rounded-lg mb-4">
                <p className="text-sm text-blue-800 dark:text-blue-200">
                  To enable speaker identification (who said what), you need a
                  Hugging Face token with access to{" "}
                  <code>pyannote/speaker-diarization-community-1</code>.
                  <a
                    href="https://huggingface.co/settings/tokens"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline ml-1 font-medium"
                  >
                    Get one here
                  </a>
                  .
                </p>
                <p className="text-xs text-blue-600 dark:text-blue-300 mt-2">
                  You can skip this step if you don&apos;t need speaker labels.
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Access Token
                </label>
                <div className="flex gap-2">
                  <input
                    type="password"
                    name="hf_token"
                    value={formData.hf_token}
                    onChange={handleInputChange}
                    className="flex-1 px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none"
                    placeholder="hf_..."
                  />
                  <button
                    type="button"
                    onClick={validateHFToken}
                    disabled={validatingHF || !formData.hf_token}
                    className="px-4 py-2 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded-lg font-medium disabled:opacity-50 transition-colors"
                  >
                    {validatingHF ? (
                      <Loader2 className="w-5 h-5 animate-spin" />
                    ) : (
                      "Validate"
                    )}
                  </button>
                </div>
                {hfValidationMsg && (
                  <p
                    className={`text-xs mt-1 flex items-center gap-1 ${hfValidationMsg.valid ? "text-green-600" : "text-red-600"}`}
                  >
                    {hfValidationMsg.valid ? (
                      <Check className="w-3 h-3" />
                    ) : (
                      <X className="w-3 h-3" />
                    )}
                    {hfValidationMsg.msg}
                  </p>
                )}
              </div>

              <div className="flex gap-3 mt-6">
                <button
                  type="button"
                  onClick={handleHFSubmit}
                  className="w-full bg-orange-600 hover:bg-orange-700 text-white font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
                >
                  {formData.hf_token ? "Validate & Finish" : "Skip & Finish"}{" "}
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}

          {/* Step 4: Download */}
          {step === 4 && (
            <div className="space-y-6">
              <div className="text-center mb-6">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
                  {downloadComplete ? "Setup Complete!" : "Downloading Models"}
                </h2>
                <p className="text-gray-500 dark:text-gray-400 text-sm">
                  {downloadMessage}
                </p>
              </div>

              <div className="space-y-3">
                {/* Whisper */}
                <div className="flex items-center gap-4 p-4 bg-gray-50 dark:bg-gray-700/50 rounded-lg border border-gray-100 dark:border-gray-700">
                  <div className="shrink-0">
                    {["pyannote", "embedding", "complete"].includes(
                      downloadStage,
                    ) ? (
                      <CheckCircle className="w-6 h-6 text-green-500" />
                    ) : downloadStage === "whisper" ||
                      downloadStage === "whisper_loading" ? (
                      <Loader2 className="w-6 h-6 text-orange-500 animate-spin" />
                    ) : (
                      <div className="w-6 h-6 rounded-full border-2 border-gray-200 dark:border-gray-600" />
                    )}
                  </div>
                  <div className="flex-1">
                    <div className="flex justify-between mb-1">
                      <h3 className="font-medium text-gray-900 dark:text-white">
                        Transcription Model
                      </h3>
                      {downloadStage === "whisper" && (
                        <span className="text-xs text-orange-600 font-medium">
                          {Math.round(downloadProgress)}%
                        </span>
                      )}
                      {downloadStage === "whisper_loading" && (
                        <span className="text-xs text-orange-600 font-medium">
                          Loading...
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-gray-500">
                      OpenAI Whisper (Turbo)
                    </p>

                    {/* Download Bar */}
                    {(downloadStage === "whisper" ||
                      downloadStage === "whisper_loading") && (
                      <div className="w-full bg-gray-200 dark:bg-gray-600 rounded-full h-1.5 mt-2">
                        <div
                          className="bg-orange-500 h-1.5 rounded-full transition-all duration-300"
                          style={{
                            width:
                              downloadStage === "whisper_loading"
                                ? "100%"
                                : `${downloadProgress}%`,
                          }}
                        />
                      </div>
                    )}

                    {/* Loading Bar */}
                    {downloadStage === "whisper_loading" && (
                      <div className="mt-2 animate-in fade-in slide-in-from-top-1 duration-300">
                        <div className="flex justify-between mb-1">
                          <span className="text-xs text-gray-500">
                            Loading into memory...
                          </span>
                        </div>
                        <div className="w-full bg-gray-200 dark:bg-gray-600 rounded-full h-1.5 overflow-hidden">
                          <div className="bg-orange-400/50 h-1.5 rounded-full w-full animate-pulse" />
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                {/* Pyannote */}
                <div className="flex items-center gap-4 p-4 bg-gray-50 dark:bg-gray-700/50 rounded-lg border border-gray-100 dark:border-gray-700">
                  <div className="shrink-0">
                    {["embedding", "complete"].includes(downloadStage) ? (
                      <CheckCircle className="w-6 h-6 text-green-500" />
                    ) : downloadStage === "pyannote" ? (
                      <Loader2 className="w-6 h-6 text-orange-500 animate-spin" />
                    ) : (
                      <div className="w-6 h-6 rounded-full border-2 border-gray-200 dark:border-gray-600" />
                    )}
                  </div>
                  <div className="flex-1">
                    <div className="flex justify-between mb-1">
                      <h3 className="font-medium text-gray-900 dark:text-white">
                        Speaker Diarization
                      </h3>
                      {downloadStage === "pyannote" && (
                        <span className="text-xs text-orange-600 font-medium">
                          {Math.round(downloadProgress)}%
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-gray-500">Pyannote Audio</p>
                    {downloadStage === "pyannote" && (
                      <div className="w-full bg-gray-200 dark:bg-gray-600 rounded-full h-1.5 mt-2">
                        <div
                          className="bg-orange-500 h-1.5 rounded-full transition-all duration-300"
                          style={{ width: `${downloadProgress}%` }}
                        />
                      </div>
                    )}
                  </div>
                </div>

                {/* Embedding */}
                <div className="flex items-center gap-4 p-4 bg-gray-50 dark:bg-gray-700/50 rounded-lg border border-gray-100 dark:border-gray-700">
                  <div className="shrink-0">
                    {downloadStage === "complete" ? (
                      <CheckCircle className="w-6 h-6 text-green-500" />
                    ) : downloadStage === "embedding" ? (
                      <Loader2 className="w-6 h-6 text-orange-500 animate-spin" />
                    ) : (
                      <div className="w-6 h-6 rounded-full border-2 border-gray-200 dark:border-gray-600" />
                    )}
                  </div>
                  <div className="flex-1">
                    <div className="flex justify-between mb-1">
                      <h3 className="font-medium text-gray-900 dark:text-white">
                        Voice Embedding
                      </h3>
                      {downloadStage === "embedding" && (
                        <span className="text-xs text-orange-600 font-medium">
                          {Math.round(downloadProgress)}%
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-gray-500">SpeechBrain</p>
                    {downloadStage === "embedding" && (
                      <div className="w-full bg-gray-200 dark:bg-gray-600 rounded-full h-1.5 mt-2">
                        <div
                          className="bg-orange-500 h-1.5 rounded-full transition-all duration-300"
                          style={{ width: `${downloadProgress}%` }}
                        />
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {downloadComplete && (
                <div className="animate-in fade-in slide-in-from-bottom-4 duration-500 text-center">
                  <button
                    onClick={handleCompleteSetup}
                    className="w-full bg-orange-600 hover:bg-orange-700 text-white font-medium py-3 rounded-lg flex items-center justify-center gap-2 shadow-lg hover:shadow-xl transform hover:-translate-y-0.5 transition-all"
                  >
                    Complete Setup <ArrowRight className="w-5 h-5" />
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
