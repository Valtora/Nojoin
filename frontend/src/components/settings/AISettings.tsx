'use client';

import { useState, useEffect } from 'react';
import { Settings } from '@/types';
import { Eye, EyeOff, Check, X, Loader2, Download, Trash2, HelpCircle, Info, RefreshCw, Cpu, Key, MessageSquare, Layers, HardDrive } from 'lucide-react';
import { fuzzyMatch } from '@/lib/searchUtils';
import { validateLLM, validateHF, getModelStatus, downloadModels, deleteModel, getTaskStatus, listModels } from '@/lib/api';
import { useNotificationStore } from '@/lib/notificationStore';

const WHISPER_MODELS = [
  { id: 'tiny', label: 'Tiny', params: '39 M', vram: '~1 GB', speed: '~10x' },
  { id: 'base', label: 'Base', params: '74 M', vram: '~1 GB', speed: '~7x' },
  { id: 'small', label: 'Small', params: '244 M', vram: '~2 GB', speed: '~4x' },
  { id: 'medium', label: 'Medium', params: '769 M', vram: '~5 GB', speed: '~2x' },
  { id: 'large', label: 'Large', params: '1550 M', vram: '~10 GB', speed: '1x' },
  { id: 'turbo', label: 'Turbo', params: '809 M', vram: '~6 GB', speed: '~8x' },
];

interface AISettingsProps {
  settings: Settings;
  onUpdate: (newSettings: Settings) => void;
  searchQuery?: string;
  isAdmin?: boolean;
}

export default function AISettings({ settings, onUpdate, searchQuery = '', isAdmin = false }: AISettingsProps) {
  const { addNotification } = useNotificationStore();
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [showOpenAIKey, setShowOpenAIKey] = useState(false);
  const [showAnthropicKey, setShowAnthropicKey] = useState(false);
  const [showHfToken, setShowHfToken] = useState(false);

  // Validation & Model State
  const [validating, setValidating] = useState<string | null>(null);
  const [validationMsg, setValidationMsg] = useState<{type: 'success' | 'error', msg: string, provider: string} | null>(null);
  const [modelStatus, setModelStatus] = useState<any>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState<{percent: number, message: string, speed?: string, eta?: string} | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  
  // Dynamic Model Lists
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);

  useEffect(() => {
    getModelStatus(settings.whisper_model_size).then(setModelStatus).catch(console.error);
  }, [settings.whisper_model_size]);

  // Fetch models when provider or key changes (if valid)
  useEffect(() => {
    const fetchModels = async () => {
      const provider = settings.llm_provider;
      let key = '';
      if (provider === 'gemini') key = settings.gemini_api_key || '';
      else if (provider === 'openai') key = settings.openai_api_key || '';
      else if (provider === 'anthropic') key = settings.anthropic_api_key || '';

      if (provider && key) {
        setFetchingModels(true);
        try {
          const res = await listModels(provider, key);
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
    
    // Debounce slightly to avoid too many calls while typing
    const timeout = setTimeout(fetchModels, 1000);
    return () => clearTimeout(timeout);
  }, [settings.llm_provider, settings.gemini_api_key, settings.openai_api_key, settings.anthropic_api_key]);

  const handleValidate = async (provider: string) => {
    setValidating(provider);
    setValidationMsg(null);
    try {
        let key = '';
        if (provider === 'gemini') key = settings.gemini_api_key || '';
        else if (provider === 'openai') key = settings.openai_api_key || '';
        else if (provider === 'anthropic') key = settings.anthropic_api_key || '';
        else if (provider === 'hf') key = settings.hf_token || '';

        if (!key) throw new Error("No API key/token provided");
        
        let res;
        if (provider === 'hf') {
            res = await validateHF(key);
        } else {
            res = await validateLLM(provider, key);
            // Also refresh models on explicit validate
            const modelsRes = await listModels(provider, key);
            setAvailableModels(modelsRes.models);
        }
        setValidationMsg({type: 'success', msg: res.message, provider});
    } catch (e: any) {
        setValidationMsg({type: 'error', msg: e.response?.data?.detail || e.message, provider});
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
              whisper_model_size: settings.whisper_model_size
          });
          
          // Poll for status
          const pollInterval = setInterval(async () => {
              try {
                  const status = await getTaskStatus(task_id);
                  if (status.status === 'SUCCESS') {
                      clearInterval(pollInterval);
                      setDownloading(false);
                      setDownloadProgress(null);
                      refreshStatus();
                      addNotification({
                          type: 'success',
                          message: "Models downloaded successfully!"
                      });
                  } else if (status.status === 'FAILURE') {
                      clearInterval(pollInterval);
                      setDownloading(false);
                      setDownloadProgress(null);
                      addNotification({
                          type: 'error',
                          message: `Download failed: ${status.result}`
                      });
                  } else if (status.status === 'PROCESSING') {
                      if (status.result) {
                          setDownloadProgress({
                              percent: status.result.progress || 0,
                              message: status.result.message || "Downloading...",
                              speed: status.result.speed,
                              eta: status.result.eta
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
              type: 'error',
              message: "Failed to start download."
          });
          setDownloading(false);
          setDownloadProgress(null);
      }
  };

  const handleDeleteModel = async (modelName: string) => {
      if (!confirm(`Are you sure you want to delete the ${modelName} model? You will need to download it again to use it.`)) return;
      
      setDeleting(modelName);
      try {
          await deleteModel(modelName);
          addNotification({
              type: 'success',
              message: `${modelName} model deleted successfully`
          });
          refreshStatus();
      } catch (e: any) {
          console.error(e);
          addNotification({
              type: 'error',
              message: `Failed to delete model: ${e.response?.data?.detail || e.message}`
          });
      } finally {
          setDeleting(null);
      }
  };

  const refreshStatus = () => {
      getModelStatus(settings.whisper_model_size).then(setModelStatus).catch(console.error);
  };


  // Search Logic
  const showLLM = fuzzyMatch(searchQuery, ['llm', 'provider', 'gemini', 'openai', 'anthropic', 'api key', 'model', 'instructions']);
  const showHF = fuzzyMatch(searchQuery, ['hugging face', 'token', 'diarization']);
  const showTranscription = fuzzyMatch(searchQuery, ['transcription', 'whisper', 'speech to text']);
  const showDependencies = fuzzyMatch(searchQuery, ['dependencies', 'models', 'download', 'status']);

  const hasSearch = !!searchQuery;
  const showLLMSection = !hasSearch || showLLM;
  const showHFSection = !hasSearch || showHF;
  const showTranscriptionSection = !hasSearch || showTranscription;
  const showDependenciesSection = !hasSearch || showDependencies;

  if (!showLLMSection && !showHFSection && !showTranscriptionSection && !showDependenciesSection) {
    return <div className="text-gray-500">No matching settings found.</div>;
  }

  return (
    <div className="space-y-6">
      {/* 1. LLM Settings Group */}
      {showLLMSection && (
        <div className="bg-white dark:bg-gray-800 p-6 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-6 flex items-center gap-2">
            <Cpu className="w-5 h-5 text-orange-500" /> LLM Configuration
          </h3>
          
          <div className="space-y-6 max-w-3xl">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Provider */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Provider
                  </label>
                  <select
                    value={settings.llm_provider || 'gemini'}
                    onChange={(e) => onUpdate({ ...settings, llm_provider: e.target.value, llm_model: '' })}
                    disabled={!isAdmin}
                    className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none transition-all"
                  >
                    <option value="gemini">Google Gemini</option>
                    <option value="openai">OpenAI</option>
                    <option value="anthropic">Anthropic</option>
                  </select>
                </div>

                {/* Model */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 flex justify-between">
                        Model
                        <button 
                            onClick={() => {
                                const provider = settings.llm_provider || 'gemini';
                                let key = '';
                                if (provider === 'gemini') key = settings.gemini_api_key || '';
                                else if (provider === 'openai') key = settings.openai_api_key || '';
                                else if (provider === 'anthropic') key = settings.anthropic_api_key || '';
                                if (key) listModels(provider, key).then(res => setAvailableModels(res.models));
                            }}
                            disabled={fetchingModels || !isAdmin}
                            className="text-xs text-orange-500 hover:text-orange-600 flex items-center gap-1 disabled:opacity-50"
                        >
                            <RefreshCw className={`w-3 h-3 ${fetchingModels ? 'animate-spin' : ''}`} /> Refresh
                        </button>
                    </label>
                    <select
                        value={settings.llm_model || ''}
                        onChange={(e) => onUpdate({ ...settings, llm_model: e.target.value })}
                        disabled={!isAdmin || availableModels.length === 0}
                        className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none transition-all"
                    >
                        <option value="" disabled>Select a model...</option>
                        {availableModels.map(model => (
                            <option key={model} value={model}>{model}</option>
                        ))}
                    </select>
                    
                    {/* Model Recommendations */}
                    {settings.llm_provider === 'gemini' && (
                        <div className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                            <span className="font-medium text-blue-600 dark:text-blue-400">Recommended:</span> 
                            <span className="ml-1"><strong>gemini-flash-latest</strong> (Fast) or <strong>gemini-pro-latest</strong> (Complex)</span>
                        </div>
                    )}
                    {settings.llm_provider === 'openai' && (
                        <div className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                            <span className="font-medium text-blue-600 dark:text-blue-400">Recommended:</span> 
                            <span className="ml-1"><strong>GPT-5 mini</strong> (Fast) or <strong>GPT-5.1</strong> (Complex)</span>
                        </div>
                    )}
                    {settings.llm_provider === 'anthropic' && (
                        <div className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                            <span className="font-medium text-blue-600 dark:text-blue-400">Recommended:</span> 
                            <span className="ml-1"><strong>Claude Haiku</strong> (Fast) or <strong>Claude Sonnet</strong> (Balanced) or <strong>Claude Opus</strong> (Complex)</span>
                        </div>
                    )}
                </div>
            </div>

            {/* API Key (Dynamic) */}
            <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    API Key
                </label>
                <div className="flex gap-2">
                    <div className="relative flex-1">
                        <input
                            type={
                                (settings.llm_provider === 'gemini' && showGeminiKey) ||
                                (settings.llm_provider === 'openai' && showOpenAIKey) ||
                                (settings.llm_provider === 'anthropic' && showAnthropicKey) 
                                ? "text" : "password"
                            }
                            value={
                                settings.llm_provider === 'gemini' ? (settings.gemini_api_key || '') :
                                settings.llm_provider === 'openai' ? (settings.openai_api_key || '') :
                                (settings.anthropic_api_key || '')
                            }
                            onChange={(e) => {
                                const val = e.target.value;
                                if (settings.llm_provider === 'gemini') onUpdate({ ...settings, gemini_api_key: val });
                                else if (settings.llm_provider === 'openai') onUpdate({ ...settings, openai_api_key: val });
                                else onUpdate({ ...settings, anthropic_api_key: val });
                            }}
                            disabled={!isAdmin}
                            className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white pr-10 focus:ring-2 focus:ring-orange-500 outline-none transition-all"
                            placeholder={`Enter ${settings.llm_provider === 'gemini' ? 'Gemini' : settings.llm_provider === 'openai' ? 'OpenAI' : 'Anthropic'} API Key`}
                        />
                        <button
                            type="button"
                            onClick={() => {
                                if (settings.llm_provider === 'gemini') setShowGeminiKey(!showGeminiKey);
                                else if (settings.llm_provider === 'openai') setShowOpenAIKey(!showOpenAIKey);
                                else setShowAnthropicKey(!showAnthropicKey);
                            }}
                            className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                        >
                            {(settings.llm_provider === 'gemini' ? showGeminiKey : settings.llm_provider === 'openai' ? showOpenAIKey : showAnthropicKey) 
                                ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                        </button>
                    </div>
                    <button
                        onClick={() => handleValidate(settings.llm_provider || 'gemini')}
                        disabled={validating === settings.llm_provider || !isAdmin}
                        className="px-4 py-2 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded-lg font-medium transition-colors disabled:opacity-50"
                    >
                        {validating === settings.llm_provider ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Validate'}
                    </button>
                </div>
                {validationMsg && validationMsg.provider === settings.llm_provider && (
                    <p className={`text-xs mt-2 flex items-center gap-1 ${validationMsg.type === 'success' ? 'text-green-600' : 'text-red-600'}`}>
                        {validationMsg.type === 'success' ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
                        {validationMsg.msg}
                    </p>
                )}
            </div>

            {/* Custom Instructions */}
            <div className="pt-4 border-t border-gray-200 dark:border-gray-700">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-2">
                    <MessageSquare className="w-4 h-4 text-gray-400" /> Custom Instructions
                </label>
                <div className="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg p-3 mb-3 text-xs text-yellow-800 dark:text-yellow-200 flex gap-2">
                    <Info className="w-4 h-4 flex-shrink-0" />
                    <span>These instructions are appended to every prompt sent to the LLM. Use this to define a custom persona or specific formatting rules.</span>
                </div>
                <textarea
                    value={settings.chat_custom_instructions || ''}
                    onChange={(e) => onUpdate({ ...settings, chat_custom_instructions: e.target.value })}
                    disabled={!isAdmin}
                    className="w-full p-3 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none min-h-[100px] text-sm transition-all"
                    placeholder="E.g., You are a helpful meeting assistant. Always summarize key decisions first..."
                    maxLength={1000}
                />
                <p className="text-xs text-gray-500 mt-1 text-right">
                    {settings.chat_custom_instructions?.length || 0}/1000
                </p>
            </div>
          </div>
        </div>
      )}

      {/* 2. HuggingFace Group */}
      {showHFSection && (
        <div className="bg-white dark:bg-gray-800 p-6 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-6 flex items-center gap-2">
            <Key className="w-5 h-5 text-blue-500" /> Hugging Face
          </h3>
          <div className="max-w-3xl space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Access Token
              </label>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <input
                    type={showHfToken ? "text" : "password"}
                    value={settings.hf_token || ''}
                    onChange={(e) => onUpdate({ ...settings, hf_token: e.target.value })}
                    disabled={!isAdmin}
                    className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white pr-10 focus:ring-2 focus:ring-orange-500 outline-none transition-all"
                    placeholder="hf_..."
                  />
                  <button
                    type="button"
                    onClick={() => setShowHfToken(!showHfToken)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                  >
                    {showHfToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
                <button
                    onClick={() => handleValidate('hf')}
                    disabled={validating === 'hf' || !settings.hf_token || !isAdmin}
                    className="px-4 py-2 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded-lg font-medium transition-colors disabled:opacity-50"
                >
                    {validating === 'hf' ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Validate'}
                </button>
              </div>
              {validationMsg && validationMsg.provider === 'hf' && (
                    <p className={`text-xs mt-2 flex items-center gap-1 ${validationMsg.type === 'success' ? 'text-green-600' : 'text-red-600'}`}>
                        {validationMsg.type === 'success' ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
                        {validationMsg.msg}
                    </p>
              )}
              <p className="text-xs text-gray-500 mt-2">
                Required for Pyannote speaker diarization. Ensure you have accepted the user agreement for <code>pyannote/speaker-diarization-3.1</code> on Hugging Face.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* 3. Transcription Group */}
      {showTranscriptionSection && (
        <div className="bg-white dark:bg-gray-800 p-6 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-6 flex items-center gap-2">
                <Layers className="w-5 h-5 text-purple-500" /> Transcription Settings
            </h3>
            <div className="max-w-3xl space-y-4">
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-2">
                        Whisper Model Size
                        <div className="group relative">
                            <HelpCircle className="w-4 h-4 text-gray-400 cursor-help" />
                            <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-2 hidden group-hover:block w-80 p-4 bg-gray-900 text-white text-xs rounded-lg shadow-xl z-50 pointer-events-none">
                                <div className="font-bold mb-2 text-sm">Available Models</div>
                                <div className="grid grid-cols-5 gap-2 border-b border-gray-700 pb-2 mb-2 font-semibold">
                                    <div className="col-span-1">Size</div>
                                    <div className="col-span-1">Params</div>
                                    <div className="col-span-1">VRAM</div>
                                    <div className="col-span-1">Speed</div>
                                </div>
                                {WHISPER_MODELS.map(m => (
                                    <div key={m.id} className="grid grid-cols-5 gap-2 mb-1">
                                        <div className="col-span-1 font-medium text-orange-400">{m.label}</div>
                                        <div className="col-span-1 text-gray-300">{m.params}</div>
                                        <div className="col-span-1 text-gray-300">{m.vram}</div>
                                        <div className="col-span-1 text-gray-300">{m.speed}</div>
                                    </div>
                                ))}
                                <div className="mt-2 text-gray-400 italic">
                                    Turbo is the recommended default for best balance of speed and accuracy.
                                </div>
                            </div>
                        </div>
                    </label>
                    <select
                        value={settings.whisper_model_size || 'turbo'}
                        onChange={(e) => onUpdate({ ...settings, whisper_model_size: e.target.value })}
                        disabled={!isAdmin}
                        className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none transition-all"
                    >
                        {WHISPER_MODELS.map(model => (
                            <option key={model.id} value={model.id}>
                                {model.label} ({model.vram} VRAM)
                            </option>
                        ))}
                    </select>
                    <p className="text-xs text-gray-500 mt-2">
                        Select the model size for speech-to-text transcription. Larger models are more accurate but slower and require more VRAM.
                    </p>
                </div>
            </div>
        </div>
      )}

      {/* 4. Dependencies Group */}
      {showDependenciesSection && (
        <div className="bg-white dark:bg-gray-800 p-6 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
            <div className="flex items-center justify-between mb-6">
                <h3 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                    <HardDrive className="w-5 h-5 text-green-500" /> Model Dependencies
                </h3>
                <button 
                    onClick={refreshStatus}
                    className="text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 flex items-center gap-1"
                >
                    <RefreshCw className="w-3 h-3" /> Refresh Status
                </button>
            </div>
            
            <div className="max-w-3xl space-y-6">
                <div className="bg-gray-50 dark:bg-gray-900/50 p-4 rounded-lg border border-gray-200 dark:border-gray-700">
                    <div className="space-y-3">
                        {[
                            { id: 'whisper', label: 'Whisper (Transcription)', desc: 'OpenAI Whisper model for speech-to-text.' },
                            { id: 'pyannote', label: 'Pyannote (Diarization)', desc: 'Speaker diarization pipeline.' },
                            { id: 'embedding', label: 'Voice Embedding', desc: 'Speaker identification model.' }
                        ].map((model) => (
                            <div key={model.id} className="flex justify-between items-center p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-100 dark:border-gray-700 shadow-sm">
                                <div>
                                    <div className="text-sm font-medium text-gray-900 dark:text-white">{model.label}</div>
                                    <div className="text-xs text-gray-500">{model.desc}</div>
                                </div>
                                <div className="flex items-center gap-3">
                                    {modelStatus?.[model.id]?.downloaded ? (
                                        <>
                                            <span className="text-xs bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-400 px-2.5 py-1 rounded-full flex items-center gap-1 font-medium">
                                                <Check className="w-3 h-3" /> Ready
                                            </span>
                                            <button
                                                onClick={() => handleDeleteModel(model.id)}
                                                disabled={deleting === model.id || downloading || !isAdmin}
                                                className="text-gray-400 hover:text-red-500 transition-colors p-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md disabled:opacity-50"
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
                                            {modelStatus?.[model.id]?.checked_paths && (
                                                <span 
                                                    className="text-[10px] text-gray-400 mt-1 max-w-[200px] truncate cursor-help"
                                                    title={`Checked paths:\n${modelStatus[model.id].checked_paths.join('\n')}`}
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
                                <span className="font-medium text-blue-700 dark:text-blue-300">{downloadProgress.message}</span>
                                <span className="text-blue-600 dark:text-blue-400 font-bold">{downloadProgress.percent}%</span>
                            </div>
                            <div className="w-full bg-blue-200 dark:bg-blue-800 rounded-full h-2.5 mb-2">
                                <div 
                                    className="bg-blue-600 h-2.5 rounded-full transition-all duration-300" 
                                    style={{ width: `${downloadProgress.percent}%` }}
                                ></div>
                            </div>
                            <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400">
                                <span>{downloadProgress.speed || 'Calculating speed...'}</span>
                                <span>ETA: {downloadProgress.eta || '...'}</span>
                            </div>
                        </div>
                    )}

                    <div className="mt-6 pt-4 border-t border-gray-200 dark:border-gray-700">
                        <button
                            onClick={handleDownloadModels}
                            disabled={downloading || !isAdmin}
                            className="w-full flex items-center justify-center gap-2 bg-orange-600 hover:bg-orange-700 text-white py-3 px-4 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed font-medium shadow-sm"
                        >
                            {downloading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Download className="w-5 h-5" />}
                            {downloading ? 'Downloading Models...' : 'Download / Update All Models'}
                        </button>
                        <p className="text-xs text-gray-500 mt-3 text-center">
                            This will download any missing models to the server. Large files (2GB+) may take a while.
                        </p>
                    </div>
                </div>
            </div>
        </div>
      )}
    </div>
  );
}
