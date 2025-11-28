'use client';

import { useState } from 'react';
import { Settings } from '@/types';
import { Eye, EyeOff } from 'lucide-react';
import { fuzzyMatch } from '@/lib/searchUtils';
import { AI_KEYWORDS } from './keywords';

interface AISettingsProps {
  settings: Settings;
  onUpdate: (newSettings: Settings) => void;
  searchQuery?: string;
}

export default function AISettings({ settings, onUpdate, searchQuery = '' }: AISettingsProps) {
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [showOpenAIKey, setShowOpenAIKey] = useState(false);
  const [showAnthropicKey, setShowAnthropicKey] = useState(false);
  const [showHfToken, setShowHfToken] = useState(false);

  // We use the centralized keywords for the main section check, but specific checks for subsections
  const showProvider = fuzzyMatch(searchQuery, ['provider', 'llm', 'gemini', 'openai', 'anthropic', 'model', 'ai']);
  const showGemini = fuzzyMatch(searchQuery, ['gemini', 'api key', 'google']);
  const showOpenAI = fuzzyMatch(searchQuery, ['openai', 'api key', 'gpt']);
  const showAnthropic = fuzzyMatch(searchQuery, ['anthropic', 'api key', 'claude']);
  const showHf = fuzzyMatch(searchQuery, ['hugging face', 'token', 'diarization', 'pyannote', 'speaker', 'separation']);
  const showVoiceprints = fuzzyMatch(searchQuery, ['voiceprint', 'auto-create', 'speaker', 'identification', 'recognition']);
  const showWhisper = fuzzyMatch(searchQuery, ['whisper', 'model', 'transcription', 'speech to text']);

  const showLLMSection = showProvider;
  const showAPIKeysSection = showGemini || showOpenAI || showAnthropic;
  const showDiarizationSection = showHf;
  const showProcessingSection = showVoiceprints || showWhisper;

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
            {showGemini && (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Gemini API Key
                </label>
                <div className="relative">
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
                <p className="text-xs text-gray-500 mt-1">Your secret API key for Google Gemini.</p>
              </div>
            )}

            {showOpenAI && (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  OpenAI API Key
                </label>
                <div className="relative">
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
                <p className="text-xs text-gray-500 mt-1">Your secret API key for OpenAI.</p>
              </div>
            )}

            {showAnthropic && (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Anthropic API Key
                </label>
                <div className="relative">
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
              <div className="relative">
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
              <p className="text-xs text-gray-500 mt-1">Required for speaker diarization (Pyannote). You must accept Pyannote user conditions on Hugging Face.</p>
            </div>
          </div>
        </div>
      )}

      {showProcessingSection && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Processing</h3>
          <div className="max-w-xl space-y-4">
            {showVoiceprints && (
              <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-200 dark:border-gray-700">
                <div className="flex-1">
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                    Auto-create Voiceprints
                  </label>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Automatically generate voice fingerprints for speaker recognition. Disable for faster processing if you prefer manual speaker management.
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer ml-4">
                  <input
                    type="checkbox"
                    checked={settings.enable_auto_voiceprints !== false}
                    onChange={(e) => onUpdate({ ...settings, enable_auto_voiceprints: e.target.checked })}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-gray-300 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-orange-500 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-orange-600"></div>
                </label>
              </div>
            )}

            {showWhisper && (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Whisper Model Size
                </label>
                <select
                  value={settings.whisper_model_size || 'turbo'}
                  onChange={(e) => onUpdate({ ...settings, whisper_model_size: e.target.value })}
                  className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                >
                  <option value="tiny">Tiny (Fastest, Low Accuracy)</option>
                  <option value="base">Base</option>
                  <option value="small">Small</option>
                  <option value="medium">Medium</option>
                  <option value="large-v3">Large v3 (Slowest, Best Accuracy)</option>
                  <option value="turbo">Turbo (Balanced)</option>
                </select>
                <p className="text-xs text-gray-500 mt-1">Select the size of the transcription model. Larger models are more accurate but slower.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
