"use client";

import { useState, useEffect, useCallback } from "react";
import { Settings, SystemModelStatus } from "@/types";
import {
  Eye,
  EyeOff,
  Check,
  X,
  Loader2,
  Download,
  Trash2,
  HelpCircle,
  Info,
  RefreshCw,
  Cpu,
  Server,
} from "lucide-react";
import { fuzzyMatch } from "@/lib/searchUtils";
import {
  clampMeetingEdgeContextLevel,
  MEETING_EDGE_CONTEXT_OPTIONS,
} from "@/lib/meetingEdgeContext";
import {
  validateLLM,
  validateHF,
  getModelsStatus,
  downloadModels,
  deleteModel,
  getTaskStatus,
  listModels,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { trimString, sanitizeUrl } from "@/lib/validation";
import Tooltip from "@/components/ui/Tooltip";
import { Switch } from "@/components/ui/Switch";
import SettingsCallout from "./SettingsCallout";
import SettingsPanel from "./SettingsPanel";
import SettingsSection from "./SettingsSection";
import WhisperModelModal from "./WhisperModelModal";

const WHISPER_MODELS = [
  { id: "tiny", label: "Tiny", params: "39 M", vram: "~1 GB", speed: "~10x" },
  { id: "base", label: "Base", params: "74 M", vram: "~1 GB", speed: "~7x" },
  { id: "small", label: "Small", params: "244 M", vram: "~2 GB", speed: "~4x" },
  {
    id: "medium",
    label: "Medium",
    params: "769 M",
    vram: "~5 GB",
    speed: "~2x",
  },
  {
    id: "large",
    label: "Large",
    params: "1550 M",
    vram: "~10 GB",
    speed: "1x",
  },
  { id: "turbo", label: "Turbo", params: "809 M", vram: "~6 GB", speed: "~8x" },
];

function isMaskedSecret(value: string | null | undefined): boolean {
  return Boolean(value && (value.includes("...") || value.includes("***")));
}

interface AISettingsProps {
  settings: Settings;
  onUpdate: (newSettings: Settings) => void;
  onPersist?: (newSettings: Settings) => Promise<void>;
  searchQuery?: string;
  isAdmin?: boolean;
}

export default function AISettings({
  settings,
  onUpdate,
  onPersist,
  searchQuery = "",
  isAdmin = false,
}: AISettingsProps) {
  const { addNotification } = useNotificationStore();
  const [showWhisperModal, setShowWhisperModal] = useState(false);

  // Validation & Model State
  const [validating, setValidating] = useState<string | null>(null);
  const [validationMsg, setValidationMsg] = useState<{
    type: "success" | "error";
    msg: string;
    provider: string;
  } | null>(null);
  const [modelStatus, setModelStatus] = useState<SystemModelStatus | null>(
    null,
  );
  const [downloading, setDownloading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState<{
    percent: number;
    message: string;
    speed?: string;
    eta?: string;
  } | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  // Dynamic Model Lists
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);

  const persistSettingsUpdate = (updates: Settings) => {
    onUpdate(updates);
    if (!onPersist) {
      return;
    }

    void onPersist(updates).catch((error) => {
      console.error("Failed to persist AI settings update", error);
    });
  };

  const checkLlmConfigured = () => {
    const provider = settings.llm_provider || "gemini";
    if (provider === "gemini") return Boolean(settings.gemini_api_key);
    if (provider === "openai") return Boolean(settings.openai_api_key);
    if (provider === "anthropic") return Boolean(settings.anthropic_api_key);
    if (provider === "ollama") return Boolean(settings.ollama_api_url);
    return false;
  };

  const getSelectedModelForProvider = (kind: "main" | "live") => {
    const provider = settings.llm_provider || "gemini";
    if (provider === "openai") {
      return kind === "live"
        ? settings.openai_live_model || ""
        : settings.openai_model || "";
    }
    if (provider === "anthropic") {
      return kind === "live"
        ? settings.anthropic_live_model || ""
        : settings.anthropic_model || "";
    }
    if (provider === "ollama") {
      return kind === "live"
        ? settings.ollama_live_model || ""
        : settings.ollama_model || "";
    }
    return kind === "live"
      ? settings.gemini_live_model || ""
      : settings.gemini_model || "";
  };

  const updateSelectedModelForProvider = (
    kind: "main" | "live",
    value: string,
  ) => {
    const provider = settings.llm_provider || "gemini";
    const updates: Settings = { ...settings };

    if (provider === "openai") {
      if (kind === "live") updates.openai_live_model = value || null;
      else updates.openai_model = value;
    } else if (provider === "anthropic") {
      if (kind === "live") updates.anthropic_live_model = value || null;
      else updates.anthropic_model = value;
    } else if (provider === "ollama") {
      if (kind === "live") updates.ollama_live_model = value || null;
      else updates.ollama_model = value;
    } else {
      if (kind === "live") updates.gemini_live_model = value || null;
      else updates.gemini_model = value;
    }

    persistSettingsUpdate(updates);
  };

  const getModelOptionsForProvider = (kind: "main" | "live") => {
    const selectedModel = getSelectedModelForProvider(kind);
    if (!selectedModel) {
      return availableModels;
    }

    return [
      selectedModel,
      ...availableModels.filter((model) => model !== selectedModel),
    ];
  };

  useEffect(() => {
    getModelsStatus(settings.whisper_model_size)
      .then(setModelStatus)
      .catch(console.error);
  }, [settings.whisper_model_size]);

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
        } catch (e) {
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
  }, [
    settings.llm_provider,
    settings.ollama_api_url,
  ]);

  const handleValidate = async (provider: string) => {
    setValidating(provider);
    setValidationMsg(null);
    try {
      const url = provider === "ollama" ? settings.ollama_api_url || "" : "";

      const res = await validateLLM(provider, "", url);
      // If models are returned (e.g. from Ollama), update the list
      if (res.models) {
        setAvailableModels(res.models);
      } else {
        // Otherwise refresh models explicitly
        const modelsRes = await listModels(provider, "", url);
        setAvailableModels(modelsRes.models);
      }

      if (onPersist) {
        await onPersist(settings);
      }

      setValidationMsg({
        type: "success",
        msg: `${res.message || "Validation successful"}${onPersist ? " Settings saved." : ""}`,
        provider,
      });
    } catch (e: any) {
      setValidationMsg({
        type: "error",
        msg: e.response?.data?.detail || e.message,
        provider,
      });
    } finally {
      setValidating(null);
    }
  };

  const handleDownloadModels = async () => {
    setDownloading(true);
    setDownloadProgress({ percent: 0, message: "Starting download..." });
    try {
      const { task_id } = await downloadModels({
        hf_token: settings.hf_token,
        whisper_model_size: settings.whisper_model_size,
      });

      // Poll for status
      const pollInterval = setInterval(async () => {
        try {
          const status = await getTaskStatus(task_id);
          if (status.status === "SUCCESS") {
            clearInterval(pollInterval);
            setDownloading(false);
            setDownloadProgress(null);
            refreshStatus();
            addNotification({
              type: "success",
              message: "Models downloaded successfully!",
            });
          } else if (status.status === "FAILURE") {
            clearInterval(pollInterval);
            setDownloading(false);
            setDownloadProgress(null);
            addNotification({
              type: "error",
              message: `Download failed: ${status.result}`,
            });
          } else if (status.status === "PROCESSING") {
            if (status.result) {
              setDownloadProgress({
                percent: status.result.progress || 0,
                message: status.result.message || "Downloading...",
                speed: status.result.speed,
                eta: status.result.eta,
              });
            }
          }
        } catch (e) {
          console.error("Polling error", e);
          clearInterval(pollInterval);
          setDownloading(false);
          setDownloadProgress(null);
        }
      }, 1000);
    } catch (e) {
      console.error(e);
      addNotification({
        type: "error",
        message: "Failed to start download.",
      });
      setDownloading(false);
      setDownloadProgress(null);
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
    } catch (e: any) {
      console.error(e);
      addNotification({
        type: "error",
        message: `Failed to delete model: ${e.response?.data?.detail || e.message}`,
      });
    } finally {
      setDeleting(null);
    }
  };

  const refreshStatus = () => {
    getModelsStatus(settings.whisper_model_size)
      .then(setModelStatus)
      .catch(console.error);
  };

  // Search Logic
  const showLLM = fuzzyMatch(searchQuery, [
    "llm",
    "provider",
    "gemini",
    "openai",
    "anthropic",
    "meeting edge",
    "technical context",
    "glossary",
    "verbosity",
    "threshold",
    "jargon",
    "live model",
    "live assistant",
    "api key",
    "model",
  ]);
  const showHF = fuzzyMatch(searchQuery, [
    "hugging face",
    "token",
    "diarization",
  ]);
  const showAutomaticEnhancement = fuzzyMatch(searchQuery, [
    "automatic enhancement",
    "meeting intelligence",
    "short titles",
    "title",
    "titles",
  ]);
  const showTranscription = fuzzyMatch(searchQuery, [
    "transcription",
    "whisper",
    "speech to text",
    "parakeet",
    "engine",
  ]);
  const showDependencies = fuzzyMatch(searchQuery, [
    "dependencies",
    "models",
    "download",
    "status",
  ]);

  const hasSearch = !!searchQuery;
  const showLLMSection = !hasSearch || showLLM;
  const showAutomaticEnhancementSection = !hasSearch || showAutomaticEnhancement;
  const showHFSection = isAdmin && (!hasSearch || showHF);
  const showTranscriptionSection = isAdmin && (!hasSearch || showTranscription);
  const showDependenciesSection = isAdmin && (!hasSearch || showDependencies);
  const mainModelOptions = getModelOptionsForProvider("main");
  const liveModelOptions = getModelOptionsForProvider("live");
  const meetingEdgeContextLevel = clampMeetingEdgeContextLevel(
    settings.meeting_edge_context_level,
  );
  const selectedMeetingEdgeContextOption =
    MEETING_EDGE_CONTEXT_OPTIONS.find(
      (option) => option.value === meetingEdgeContextLevel,
    ) ?? MEETING_EDGE_CONTEXT_OPTIONS[1];

  if (
    !showLLMSection &&
    !showAutomaticEnhancementSection &&
    !showHFSection &&
    !showTranscriptionSection &&
    !showDependenciesSection
  ) {
    return (
      <SettingsCallout
        tone="neutral"
        title="No matching settings"
        message="Try a broader search term for providers, models, tokens, or local model downloads."
      />
    );
  }

  return (
    <div className="space-y-8">
      {showLLMSection && (
        <SettingsSection
          eyebrow="AI"
          title="Provider and model preferences"
          description={
            isAdmin
              ? "Configure provider credentials, default models, and local endpoints."
              : "Choose which configured provider, model, and local endpoint this account prefers."
          }
          width="wide"
        >
          <div className="mx-auto max-w-3xl space-y-4">
            <SettingsPanel className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Provider Information (Read-only) */}
              <div className="col-span-2 p-4 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl flex items-center justify-between">
                <div>
                  <div className="text-sm font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                    Active AI Provider: <span className="capitalize text-orange-600 dark:text-orange-400">{settings.llm_provider || "None"}</span>
                  </div>
                  <p className="text-xs text-gray-500 mt-1">
                    AI services are configured globally in the server's environment variable file (<code className="bg-gray-100 dark:bg-gray-800 px-1 py-0.5 rounded">.env</code>).
                  </p>
                </div>
                <div>
                  {checkLlmConfigured() ? (
                    <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-green-100 text-green-800 dark:bg-green-950/40 dark:text-green-400">
                      Configured via Server (.env)
                    </span>
                  ) : (
                    <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-yellow-100 text-yellow-800 dark:bg-yellow-950/40 dark:text-yellow-400">
                      Not Configured
                    </span>
                  )}
                </div>
              </div>

              {/* API URL Display for Ollama */}
              {settings.llm_provider === "ollama" && (
                <div className="col-span-2 md:col-span-1">
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Ollama API URL
                  </label>
                  <div className="relative">
                    <input
                      type="text"
                      value={settings.ollama_api_url || "http://host.docker.internal:11434"}
                      disabled={true}
                      className="w-full pl-10 pr-4 py-2.5 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-100/50 dark:bg-gray-800/50 text-gray-500 dark:text-gray-400 cursor-not-allowed outline-none"
                    />
                    <Server className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                  </div>
                  <p className="mt-1 text-xs text-yellow-600 dark:text-yellow-400 flex items-center gap-1">
                    <Info className="w-3 h-3" />
                    Local models run on your hardware. Performance depends on your GPU/CPU.
                  </p>
                </div>
              )}

              {/* Model */}
              <div className={settings.llm_provider === "ollama" ? "" : "col-span-2 md:col-span-1"}>
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 flex justify-between">
                  <Tooltip
                    content="Select the specific model to use for this provider."
                    position="right"
                  >
                    <span className="flex items-center gap-1 cursor-help">
                      Model <HelpCircle className="w-3 h-3 text-gray-500 dark:text-gray-400" />
                    </span>
                  </Tooltip>
                  <button
                    onClick={() => {
                      const provider = settings.llm_provider || "gemini";
                      const url = provider === "ollama" ? settings.ollama_api_url : undefined;
                      setFetchingModels(true);
                      listModels(provider, "", url)
                        .then((res) => setAvailableModels(res.models))
                        .catch(console.error)
                        .finally(() => setFetchingModels(false));
                    }}
                    disabled={fetchingModels || !checkLlmConfigured()}
                    className="text-xs text-orange-500 hover:text-orange-600 flex items-center gap-1 disabled:opacity-50"
                  >
                    <RefreshCw
                      className={`w-3 h-3 ${fetchingModels ? "animate-spin" : ""}`}
                    />{" "}
                    Refresh
                  </button>
                </label>
                <select
                  value={getSelectedModelForProvider("main")}
                  onChange={(e) =>
                    updateSelectedModelForProvider("main", e.target.value)
                  }
                  disabled={mainModelOptions.length === 0 || !checkLlmConfigured()}
                  className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none transition-all disabled:opacity-50"
                >
                  <option value="" disabled>
                    Select a model...
                  </option>
                  {mainModelOptions.map((model) => (
                    <option key={model} value={model}>
                      {model}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <label className="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-1">
                    <Tooltip
                      content="Optional separate model for Meeting Edge live meeting guidance. Leave it blank to reuse the main AI model."
                      position="right"
                    >
                      <span className="flex items-center gap-1 cursor-help">
                        Meeting Edge model <HelpCircle className="w-3 h-3 text-gray-500 dark:text-gray-400" />
                      </span>
                    </Tooltip>
                  </label>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-gray-600 dark:text-gray-300">
                      Enable Meeting Edge
                    </span>
                    <Switch
                      checked={settings.enable_meeting_edge !== false}
                      onCheckedChange={(checked) =>
                        onUpdate({ ...settings, enable_meeting_edge: checked })
                      }
                    />
                  </div>
                </div>
                <select
                  value={getSelectedModelForProvider("live")}
                  onChange={(e) =>
                    updateSelectedModelForProvider("live", e.target.value)
                  }
                  disabled={
                    settings.enable_meeting_edge === false ||
                    liveModelOptions.length === 0 ||
                    !checkLlmConfigured()
                  }
                  className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none transition-all disabled:opacity-50"
                >
                  <option value="">Use main model</option>
                  {liveModelOptions.map((model) => (
                    <option key={`meeting-edge-${model}`} value={model}>
                      {model}
                    </option>
                  ))}
                </select>
              </div>

              <div className="md:col-span-2 rounded-xl border border-orange-200/70 bg-orange-50/45 p-4 dark:border-orange-500/20 dark:bg-orange-500/5">
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-gray-900 dark:text-white">
                      Meeting Edge Technical Context
                    </div>
                    <p className="mt-1 text-xs leading-5 text-gray-600 dark:text-gray-300">
                      Control how readily Meeting Edge explains terms in the Technical Context section.
                    </p>
                  </div>
                  <span className="inline-flex items-center rounded-full border border-orange-200 bg-white px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-orange-700 dark:border-orange-500/20 dark:bg-gray-900 dark:text-orange-300">
                    {selectedMeetingEdgeContextOption.label}
                  </span>
                </div>

                <input
                  type="range"
                  min={1}
                  max={5}
                  step={1}
                  value={meetingEdgeContextLevel}
                  onChange={(e) =>
                    onUpdate({
                      ...settings,
                      meeting_edge_context_level: Number(e.target.value),
                    })
                  }
                  disabled={settings.enable_meeting_edge === false}
                  aria-label="Meeting Edge Technical Context sensitivity"
                  className="mt-4 w-full accent-orange-500 disabled:cursor-not-allowed disabled:opacity-50"
                />

                <div className="mt-3 grid grid-cols-5 gap-2 text-center text-[11px] font-medium text-gray-500 dark:text-gray-400">
                  {MEETING_EDGE_CONTEXT_OPTIONS.map((option) => (
                    <span key={option.value}>{option.label}</span>
                  ))}
                </div>

                <p className="mt-3 text-xs leading-5 text-gray-600 dark:text-gray-300">
                  {selectedMeetingEdgeContextOption.description}
                </p>
              </div>

              {/* Validation Connection Button */}
              {checkLlmConfigured() && (
                <div className="md:col-span-2">
                  <button
                    onClick={() => handleValidate(settings.llm_provider || "gemini")}
                    disabled={Boolean(validating)}
                    className="px-4 py-2.5 bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-200 border border-gray-300 dark:border-gray-700 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors disabled:opacity-50 flex items-center gap-2 text-sm font-semibold"
                  >
                    {validating === settings.llm_provider ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Check className="w-4 h-4" />
                    )}
                    Validate API Connection
                  </button>
                  {validationMsg && validationMsg.provider === settings.llm_provider && (
                    <p
                      className={`mt-2 text-sm font-semibold ${validationMsg.type === "success" ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}
                    >
                      {validationMsg.msg}
                    </p>
                  )}
                </div>
              )}
            </div>
            </SettingsPanel>
          </div>
        </SettingsSection>
      )}

      {showAutomaticEnhancementSection && (
        <SettingsSection
          eyebrow="AI"
          title="Automatic enhancement"
          description="Control how AI-generated titles are written for your meetings and summaries."
          width="compact"
        >
          <SettingsPanel variant="field" className="mx-auto max-w-2xl flex items-start gap-3">
            <div className="flex-1">
              <div className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-white">
                <Cpu className="h-4 w-4 text-orange-500" />
                Prefer short titles
              </div>
              <p className="mt-2 text-xs contrast-helper">
                Use concise 3-5 word AI-generated meeting titles instead of longer descriptive ones.
              </p>
            </div>
            <Switch
              checked={settings.prefer_short_titles !== false}
              onCheckedChange={(checked) =>
                onUpdate({ ...settings, prefer_short_titles: checked })
              }
            />
          </SettingsPanel>
        </SettingsSection>
      )}

      {showHFSection && (
        <SettingsSection
          eyebrow="Administration"
          title="Hugging Face access"
          description="View status of the installation token required for diarization and related model downloads."
          width="regular"
        >
          <SettingsPanel className="mx-auto max-w-3xl space-y-4">
            <div className="flex items-center justify-between p-4 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl">
              <div>
                <div className="text-sm font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                  Hugging Face Integration
                </div>
                <p className="text-xs text-gray-500 mt-1">
                  The access token is configured globally in the server's environment variable file (<code className="bg-gray-100 dark:bg-gray-800 px-1 py-0.5 rounded">.env</code>).
                </p>
              </div>
              <div>
                {settings.hf_token ? (
                  <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-green-100 text-green-800 dark:bg-green-950/40 dark:text-green-400">
                    Configured via Server (.env)
                  </span>
                ) : (
                  <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-yellow-100 text-yellow-800 dark:bg-yellow-950/40 dark:text-yellow-400">
                    Missing Config
                  </span>
                )}
              </div>
            </div>
            <p className="text-xs contrast-helper">
              Required for Pyannote speaker diarization. Ensure you have accepted the user agreement for <code>pyannote/speaker-diarization-community-1</code> on Hugging Face.
            </p>
          </SettingsPanel>
        </SettingsSection>
      )}

      {showTranscriptionSection && (
        <SettingsSection
          eyebrow="Administration"
          title="Transcription model"
          description="Choose the engine Nojoin uses for live and final transcription during normal recording."
          width="regular"
        >
          <SettingsPanel className="mx-auto max-w-3xl space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                <Tooltip
                  content="Select the transcription engine used for speech to text."
                  position="right"
                >
                  <span className="flex items-center gap-1 cursor-help">
                    Transcription engine{" "}
                    <HelpCircle className="w-3 h-3 text-gray-500 dark:text-gray-400" />
                  </span>
                </Tooltip>
              </label>
              <select
                value={settings.transcription_backend || "whisper"}
                onChange={(e) =>
                  onUpdate({
                    ...settings,
                    transcription_backend: e.target.value,
                  })
                }
                disabled={!isAdmin}
                className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none transition-all"
              >
                <option value="whisper">Whisper</option>
                <option value="parakeet">Parakeet (NVIDIA)</option>
                <option value="canary">Canary 1B (NVIDIA)</option>
              </select>
            </div>
            {(settings.transcription_backend || "whisper") === "parakeet" ? (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Parakeet Model
                </label>
                <div className="flex items-center gap-4 p-4 rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700">
                  <div className="flex-1">
                    <div className="font-semibold text-gray-900 dark:text-white">
                      {settings.parakeet_model || "parakeet-tdt-0.6b-v3"}
                    </div>
                    <p className="mt-1 text-xs contrast-helper">
                      Fast NVIDIA transcription with slightly lower accuracy and fewer supported languages than Whisper.
                    </p>
                  </div>
                </div>
              </div>
            ) : (settings.transcription_backend || "whisper") === "canary" ? (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Canary Model
                </label>
                <div className="flex items-center gap-4 p-4 rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700">
                  <div className="flex-1">
                    <div className="font-semibold text-gray-900 dark:text-white">
                      {settings.canary_model || "nemo-canary-1b-v2"}
                    </div>
                    <p className="mt-1 text-xs contrast-helper">
                      Current active model for transcription.
                    </p>
                  </div>
                </div>
              </div>
            ) : (
            <div>
              <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-2">
                Whisper Model Size
                <div className="group relative">
                  <HelpCircle className="w-4 h-4 text-gray-500 dark:text-gray-400 cursor-help" />
                  <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-2 hidden group-hover:block w-80 p-4 bg-gray-900 text-white text-xs rounded-lg shadow-xl z-50 pointer-events-none">
                    <div className="font-bold mb-2 text-sm">
                      Available Models
                    </div>
                    <div className="grid grid-cols-5 gap-2 border-b border-gray-700 pb-2 mb-2 font-semibold">
                      <div className="col-span-1">Size</div>
                      <div className="col-span-1">Params</div>
                      <div className="col-span-1">VRAM</div>
                      <div className="col-span-1">Speed</div>
                    </div>
                    {WHISPER_MODELS.map((m) => (
                      <div key={m.id} className="grid grid-cols-5 gap-2 mb-1">
                        <div className="col-span-1 font-medium text-orange-400">
                          {m.label}
                        </div>
                        <div className="col-span-1 text-gray-300">
                          {m.params}
                        </div>
                        <div className="col-span-1 text-gray-300">{m.vram}</div>
                        <div className="col-span-1 text-gray-300">
                          {m.speed}
                        </div>
                      </div>
                    ))}
                    <div className="mt-2 text-gray-300 italic">
                      Turbo is the recommended default for best balance of speed
                      and accuracy.
                    </div>
                  </div>
                </div>
              </label>

              <div className="flex items-center gap-4 p-4 rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-gray-900 dark:text-white">
                      {WHISPER_MODELS.find(
                        (m) =>
                          m.id === (settings.whisper_model_size || "turbo"),
                      )?.label || settings.whisper_model_size}
                    </span>
                    <span className="text-sm text-gray-500">
                      (
                      {
                        WHISPER_MODELS.find(
                          (m) =>
                            m.id === (settings.whisper_model_size || "turbo"),
                        )?.vram
                      }{" "}
                      VRAM)
                    </span>
                  </div>
                  <p className="mt-1 text-xs contrast-helper">
                    Current active model for transcription.
                  </p>
                </div>
                <button
                  onClick={() => setShowWhisperModal(true)}
                  disabled={!isAdmin}
                  className="px-4 py-2 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors shadow-sm disabled:opacity-50"
                >
                  Change Model
                </button>
              </div>
              <p className="mt-2 text-xs contrast-helper">
                Click &apos;Change Model&apos; to select a different Whisper
                model variant. You may need to download the new model.
              </p>
            </div>
            )}
            <WhisperModelModal
              isOpen={showWhisperModal}
              onClose={() => setShowWhisperModal(false)}
              currentSize={settings.whisper_model_size || "turbo"}
              isAdmin={isAdmin}
              hfToken={settings.hf_token}
              onUpdate={(newSize) =>
                onUpdate({ ...settings, whisper_model_size: newSize })
              }
            />
          </SettingsPanel>
        </SettingsSection>
      )}

      {showDependenciesSection && (
        <SettingsSection
          eyebrow="Administration"
          title="Model dependencies"
          description="Inspect and manage locally downloaded AI model assets on the server."
        >
          <SettingsPanel className="mx-auto max-w-3xl space-y-6">
            <div className="bg-gray-50 dark:bg-gray-900/50 p-4 rounded-lg border border-gray-200 dark:border-gray-700">
              <div className="space-y-3">
                {[
                  {
                    id: "whisper",
                    label: "Whisper (Transcription)",
                    desc: "OpenAI Whisper model for speech-to-text. (MIT License)",
                  },
                  {
                    id: "parakeet",
                    label: "Parakeet ASR Model (Transcription)",
                    desc: "NVIDIA FastConformer ASR model.",
                  },
                  {
                    id: "canary",
                    label: "Canary ASR Model (Transcription)",
                    desc: "NVIDIA Canary 1B multi-lingual ASR model.",
                  },
                  {
                    id: "pyannote",
                    label: "Pyannote (Diarization)",
                    desc: "Speaker diarization model weights.",
                  },
                  {
                    id: "embedding",
                    label: "Voice Embedding",
                    desc: "Speaker identification model weights.",
                  },
                ].map((model) => (
                  <div
                    key={model.id}
                    className="flex justify-between items-center p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm"
                  >
                    <div>
                      <div className="text-sm font-medium text-gray-900 dark:text-white">
                        {model.label}
                      </div>
                      <div className="text-xs contrast-helper">{model.desc}</div>
                    </div>
                    <div className="flex items-center gap-3">
                      {modelStatus?.[model.id]?.downloaded ? (
                        <>
                          <span className="text-xs bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-400 px-2.5 py-1 rounded-full flex items-center gap-1 font-medium">
                            <Check className="w-3 h-3" /> Ready
                          </span>
                          <button
                            onClick={() => handleDeleteModel(model.id)}
                            disabled={
                              deleting === model.id || downloading || !isAdmin
                            }
                            className="text-gray-500 dark:text-gray-400 hover:text-red-500 transition-colors p-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md disabled:opacity-50"
                            title="Delete Model"
                          >
                            {deleting === model.id ? (
                              <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                              <Trash2 className="w-4 h-4" />
                            )}
                          </button>
                        </>
                      ) : (
                        <div className="flex flex-col items-end">
                          <span className="text-xs bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-400 px-2.5 py-1 rounded-full flex items-center gap-1 font-medium">
                            <X className="w-3 h-3" /> Missing
                          </span>
                          {modelStatus?.[model.id]?.checked_paths && modelStatus[model.id].checked_paths.length > 0 && (
                            <span
                              className="mt-1 max-w-[200px] truncate cursor-help text-[10px] text-gray-500 dark:text-gray-400"
                              title={`Checked paths:\n${modelStatus[model.id].checked_paths.join("\n")}`}
                            >
                              Hover for debug info
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {downloading && downloadProgress && (
                <div className="mt-6 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-100 dark:border-blue-800">
                  <div className="flex justify-between text-sm mb-2">
                    <span className="font-medium text-blue-700 dark:text-blue-300">
                      {downloadProgress.message}
                    </span>
                    <span className="text-blue-600 dark:text-blue-400 font-bold">
                      {downloadProgress.percent}%
                    </span>
                  </div>
                  <div className="w-full bg-blue-200 dark:bg-blue-800 rounded-full h-2.5 mb-2">
                    <div
                      className="bg-blue-600 h-2.5 rounded-full transition-all duration-300"
                      style={{ width: `${downloadProgress.percent}%` }}
                    ></div>
                  </div>
                  <div className="flex justify-between text-xs contrast-helper">
                    <span>
                      {downloadProgress.speed || "Calculating speed..."}
                    </span>
                    <span>ETA: {downloadProgress.eta || "..."}</span>
                  </div>
                </div>
              )}

              <div className="mt-6 pt-4 border-t border-gray-200 dark:border-gray-700">
                <button
                  onClick={handleDownloadModels}
                  disabled={downloading || !isAdmin}
                  className="w-full flex items-center justify-center gap-2 bg-orange-600 hover:bg-orange-700 text-white py-3 px-4 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed font-medium shadow-sm"
                >
                  {downloading ? (
                    <Loader2 className="w-5 h-5 animate-spin" />
                  ) : (
                    <Download className="w-5 h-5" />
                  )}
                  {downloading
                    ? "Downloading Models..."
                    : "Download / Update All Models"}
                </button>
                <p className="mt-3 text-center text-xs contrast-helper">
                  This will download any missing models to the server. Large
                  files (2GB+) may take a while.
                </p>
              </div>
            </div>
          </SettingsPanel>
        </SettingsSection>
      )}
    </div>
  );
}
