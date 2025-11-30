'use client';

import { useState, useEffect } from 'react';
import { Settings } from '@/types';
import { Eye, EyeOff, Check, X, Loader2, Download, Trash2, HelpCircle } from 'lucide-react';
import { fuzzyMatch } from '@/lib/searchUtils';
import { validateLLM, validateHF, getModelStatus, downloadModels, deleteModel, getTaskStatus } from '@/lib/api';
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
}

export default function AISettings({ settings, onUpdate, searchQuery = '' }: AISettingsProps) {
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

  useEffect(() => {
    getModelStatus(settings.whisper_model_size).then(setModelStatus).catch(console.error);
  }, [settings.whisper_model_size]);

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


  // We use the centralized keywords for the main section check, but specific checks for subsections
  const showProvider = fuzzyMatch(searchQuery, ['provider', 'llm', 'gemini', 'openai', 'anthropic', 'model', 'ai']);
  const showGemini = fuzzyMatch(searchQuery, ['gemini', 'api key', 'google']);
  const showOpenAI = fuzzyMatch(searchQuery, ['openai', 'api key', 'gpt']);
  const showAnthropic = fuzzyMatch(searchQuery, ['anthropic', 'api key', 'claude']);
  const showHf = fuzzyMatch(searchQuery, ['hugging face', 'token', 'diarization', 'pyannote', 'speaker', 'separation']);
  const showVoiceprints = fuzzyMatch(searchQuery, ['voiceprint', 'auto-create', 'speaker', 'identification', 'recognition']);
  const showNotes = fuzzyMatch(searchQuery, ['notes', 'meeting notes', 'auto-generate', 'summary']);
  const showWhisper = fuzzyMatch(searchQuery, ['whisper', 'model', 'transcription', 'speech to text']);

  const showLLMSection = showProvider;
  const showAPIKeysSection = showGemini || showOpenAI || showAnthropic;
  const showDiarizationSection = showHf;
  const showProcessingSection = showVoiceprints || showWhisper || showNotes;

  if (!showLLMSection && !showAPIKeysSection && !showDiarizationSection && !showProcessingSection && searchQuery) {
    return <div className="text-gray-500">No matching settings found.</div>;
  }

  return (
    <div className="space-y-8">
      {showLLMSection && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">LLM Provider</h3>
          <div className="max-w-xl space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Provider
              </label>
              <select
                value={settings.llm_provider || 'gemini'}
                onChange={(e) => onUpdate({ ...settings, llm_provider: e.target.value })}
                className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              >
                <option value="gemini">Google Gemini</option>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
              </select>
              <p className="text-xs text-gray-500 mt-1">Select the AI provider for generating notes and chat.</p>
            </div>
          </div>
        </div>
      )}

      {showAPIKeysSection && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">API Keys</h3>
          <div className="max-w-xl space-y-4">
            {showGemini && (settings.llm_provider === 'gemini' || !settings.llm_provider) && (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Gemini API Key
                </label>
                <div className="flex gap-2">
                    <div className="relative flex-1">
                    <input
                        type={showGeminiKey ? "text" : "password"}
                        value={settings.gemini_api_key || ''}
                        onChange={(e) => onUpdate({ ...settings, gemini_api_key: e.target.value })}
                        className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white pr-10 focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                        placeholder="AIza..."
                    />
                    <button
                        type="button"
                        onClick={() => setShowGeminiKey(!showGeminiKey)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                    >
                        {showGeminiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                    </div>
                    <button
                        onClick={() => handleValidate('gemini')}
                        disabled={validating === 'gemini' || !settings.gemini_api_key}
                        className="px-3 py-2 bg-gray-200 dark:bg-gray-700 rounded-lg hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors"
                    >
                        {validating === 'gemini' ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Validate'}
                    </button>
                </div>
                {validationMsg && validationMsg.provider === 'gemini' && (
                    <p className={`text-xs mt-1 ${validationMsg.type === 'success' ? 'text-green-500' : 'text-red-500'}`}>
                        {validationMsg.msg}
                    </p>
                )}
                <p className="text-xs text-gray-500 mt-1">Your secret API key for Google Gemini.</p>
              </div>
            )}

            {showOpenAI && settings.llm_provider === 'openai' && (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  OpenAI API Key
                </label>
                <div className="flex gap-2">
                    <div className="relative flex-1">
                    <input
                        type={showOpenAIKey ? "text" : "password"}
                        value={settings.openai_api_key || ''}
                        onChange={(e) => onUpdate({ ...settings, openai_api_key: e.target.value })}
                        className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white pr-10 focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                        placeholder="sk-..."
                    />
                    <button
                        type="button"
                        onClick={() => setShowOpenAIKey(!showOpenAIKey)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                    >
                        {showOpenAIKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                    </div>
                    <button
                        onClick={() => handleValidate('openai')}
                        disabled={validating === 'openai' || !settings.openai_api_key}
                        className="px-3 py-2 bg-gray-200 dark:bg-gray-700 rounded-lg hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors"
                    >
                        {validating === 'openai' ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Validate'}
                    </button>
                </div>
                {validationMsg && validationMsg.provider === 'openai' && (
                    <p className={`text-xs mt-1 ${validationMsg.type === 'success' ? 'text-green-500' : 'text-red-500'}`}>
                        {validationMsg.msg}
                    </p>
                )}
                <p className="text-xs text-gray-500 mt-1">Your secret API key for OpenAI.</p>
              </div>
            )}

            {showAnthropic && settings.llm_provider === 'anthropic' && (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Anthropic API Key
                </label>
                <div className="flex gap-2">
                    <div className="relative flex-1">
                    <input
                        type={showAnthropicKey ? "text" : "password"}
                        value={settings.anthropic_api_key || ''}
                        onChange={(e) => onUpdate({ ...settings, anthropic_api_key: e.target.value })}
                        className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white pr-10 focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                        placeholder="sk-ant-..."
                    />
                    <button
                        type="button"
                        onClick={() => setShowAnthropicKey(!showAnthropicKey)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                    >
                        {showAnthropicKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                    </div>
                    <button
                        onClick={() => handleValidate('anthropic')}
                        disabled={validating === 'anthropic' || !settings.anthropic_api_key}
                        className="px-3 py-2 bg-gray-200 dark:bg-gray-700 rounded-lg hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors"
                    >
                        {validating === 'anthropic' ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Validate'}
                    </button>
                </div>
                {validationMsg && validationMsg.provider === 'anthropic' && (
                    <p className={`text-xs mt-1 ${validationMsg.type === 'success' ? 'text-green-500' : 'text-red-500'}`}>
                        {validationMsg.msg}
                    </p>
                )}
                <p className="text-xs text-gray-500 mt-1">Your secret API key for Anthropic.</p>
              </div>
            )}
          </div>
        </div>
      )}

      {showDiarizationSection && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Diarization</h3>
          <div className="max-w-xl space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Hugging Face Token
              </label>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <input
                    type={showHfToken ? "text" : "password"}
                    value={settings.hf_token || ''}
                    onChange={(e) => onUpdate({ ...settings, hf_token: e.target.value })}
                    className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white pr-10 focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                    placeholder="hf_..."
                  />
                  <button
                    type="button"
                    onClick={() => setShowHfToken(!showHfToken)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                  >
                    {showHfToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
                <button
                    onClick={() => handleValidate('hf')}
                    disabled={validating === 'hf' || !settings.hf_token}
                    className="px-3 py-2 bg-gray-200 dark:bg-gray-700 rounded-lg hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors"
                >
                    {validating === 'hf' ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Validate'}
                </button>
              </div>
              {validationMsg && validationMsg.provider === 'hf' && (
                    <p className={`text-xs mt-1 ${validationMsg.type === 'success' ? 'text-green-500' : 'text-red-500'}`}>
                        {validationMsg.msg}
                    </p>
              )}
              <p className="text-xs text-gray-500 mt-1">
                Required for Pyannote speaker diarization. You must accept the user agreement on Hugging Face.
              </p>
            </div>
          </div>
        </div>
      )}

      {showProcessingSection && (
        <div>
            <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Transcription</h3>
            <div className="max-w-xl space-y-4 mb-8">
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 flex items-center gap-2">
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
                        className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                    >
                        {WHISPER_MODELS.map(model => (
                            <option key={model.id} value={model.id}>
                                {model.label} ({model.vram} VRAM)
                            </option>
                        ))}
                    </select>
                    <p className="text-xs text-gray-500 mt-1">
                        Select the model size for speech-to-text transcription. Larger models are more accurate but slower and require more VRAM.
                    </p>
                </div>
            </div>

            <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Model Dependencies</h3>
            <div className="max-w-xl space-y-4">
                <div className="bg-gray-50 dark:bg-gray-800 p-4 rounded-lg border border-gray-200 dark:border-gray-700">
                    
                    <div className="space-y-3">
                        {[
                            { id: 'whisper', label: 'Whisper (Transcription)', desc: 'OpenAI Whisper model for speech-to-text.' },
                            { id: 'pyannote', label: 'Pyannote (Diarization)', desc: 'Speaker diarization pipeline.' },
                            { id: 'embedding', label: 'Voice Embedding', desc: 'Speaker identification model.' }
                        ].map((model) => (
                            <div key={model.id} className="flex justify-between items-center p-2 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-md transition-colors">
                                <div>
                                    <div className="text-sm font-medium">{model.label}</div>
                                    <div className="text-xs text-gray-500">{model.desc}</div>
                                </div>
                                <div className="flex items-center gap-3">
                                    {modelStatus?.[model.id]?.downloaded ? (
                                        <>
                                            <span className="text-xs bg-green-100 text-green-800 px-2 py-1 rounded-full flex items-center gap-1">
                                                <Check className="w-3 h-3" /> Ready
                                            </span>
                                            <button
                                                onClick={() => handleDeleteModel(model.id)}
                                                disabled={deleting === model.id || downloading}
                                                className="text-gray-400 hover:text-red-500 transition-colors p-1"
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
                                        <span className="text-xs bg-red-100 text-red-800 px-2 py-1 rounded-full flex items-center gap-1">
                                            <X className="w-3 h-3" /> Missing
                                        </span>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>

                    {downloading && downloadProgress && (
                        <div className="mt-4 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-100 dark:border-blue-800">
                            <div className="flex justify-between text-xs mb-1">
                                <span className="font-medium text-blue-700 dark:text-blue-300">{downloadProgress.message}</span>
                                <span className="text-blue-600 dark:text-blue-400">{downloadProgress.percent}%</span>
                            </div>
                            <div className="w-full bg-blue-200 dark:bg-blue-800 rounded-full h-2 mb-2">
                                <div 
                                    className="bg-blue-600 h-2 rounded-full transition-all duration-300" 
                                    style={{ width: `${downloadProgress.percent}%` }}
                                ></div>
                            </div>
                            <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400">
                                <span>{downloadProgress.speed || 'Calculating speed...'}</span>
                                <span>ETA: {downloadProgress.eta || '...'}</span>
                            </div>
                        </div>
                    )}

                    <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
                        <button
                            onClick={handleDownloadModels}
                            disabled={downloading}
                            className="w-full flex items-center justify-center gap-2 bg-orange-600 hover:bg-orange-700 text-white py-2 px-4 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            {downloading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                            {downloading ? 'Downloading Models...' : 'Download / Update All Models'}
                        </button>
                        <p className="text-xs text-gray-500 mt-2 text-center">
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
