'use client';

import { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { getSettings, updateSettings } from '@/lib/api';
import { Settings } from '@/types';
import { Save, Loader2, X, Eye, EyeOff } from 'lucide-react';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const [settings, setSettings] = useState<Settings>({});
  const [companionConfig, setCompanionConfig] = useState<{ api_url: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  
  // Visibility toggles
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [showOpenAIKey, setShowOpenAIKey] = useState(false);
  const [showAnthropicKey, setShowAnthropicKey] = useState(false);
  const [showHfToken, setShowHfToken] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  useEffect(() => {
    if (isOpen) {
      const load = async () => {
        try {
          const data = await getSettings();
          setSettings(data);

          // Try to load companion config using the loaded settings or default
          const companionUrl = data.companion_url || 'http://localhost:12345';
          try {
              const res = await fetch(`${companionUrl}/config`);
              if (res.ok) {
                  const companionData = await res.json();
                  setCompanionConfig(companionData);
              }
          } catch (e) {
              console.error("Failed to load companion config", e);
          }
        } catch (e) {
          console.error("Failed to load settings", e);
        }

        setLoading(false);
      };
      load();
    }
  }, [isOpen]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateSettings(settings);
      
      const companionUrl = settings.companion_url || 'http://localhost:12345';
      if (companionConfig) {
          await fetch(`${companionUrl}/config`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ api_url: companionConfig.api_url })
          });
      }

      onClose();
    } catch (e) {
      console.error("Failed to save settings", e);
      alert("Failed to save settings.");
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen || !mounted) return null;

  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-2xl max-h-[80vh] flex flex-col border border-gray-200 dark:border-gray-800">
        <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center">
          <h2 className="text-xl font-bold text-gray-900 dark:text-white">Settings</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
            <X className="w-6 h-6" />
          </button>
        </div>
        
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {loading ? (
            <div className="text-center py-8 text-gray-500">Loading settings...</div>
          ) : (
            <>
              {/* Appearance */}
              <div>
                <h3 className="text-sm font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-4 border-b border-gray-200 dark:border-gray-800 pb-2">
                    Appearance
                </h3>
                <div className="grid grid-cols-1 gap-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                            Theme
                        </label>
                        <select
                            value={settings.theme || 'system'}
                            onChange={(e) => setSettings({ ...settings, theme: e.target.value })}
                            className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                        >
                            <option value="light">Light</option>
                            <option value="dark">Dark</option>
                            <option value="system">System Default</option>
                        </select>
                        <p className="text-xs text-gray-500 mt-1">Choose your preferred visual theme.</p>
                    </div>
                </div>
              </div>

              {/* AI Services */}
              <div>
                <h3 className="text-sm font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-4 border-b border-gray-200 dark:border-gray-800 pb-2">
                    AI Services
                </h3>
                <div className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                            LLM Provider
                        </label>
                        <select
                            value={settings.llm_provider || 'gemini'}
                            onChange={(e) => setSettings({ ...settings, llm_provider: e.target.value })}
                            className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                        >
                            <option value="gemini">Google Gemini</option>
                            <option value="openai">OpenAI</option>
                            <option value="anthropic">Anthropic</option>
                        </select>
                        <p className="text-xs text-gray-500 mt-1">Select the AI provider for generating notes and chat.</p>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                            Gemini API Key
                        </label>
                        <div className="relative">
                            <input
                                type={showGeminiKey ? "text" : "password"}
                                value={settings.gemini_api_key || ''}
                                onChange={(e) => setSettings({ ...settings, gemini_api_key: e.target.value })}
                                className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white pr-10"
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

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                            OpenAI API Key
                        </label>
                        <div className="relative">
                            <input
                                type={showOpenAIKey ? "text" : "password"}
                                value={settings.openai_api_key || ''}
                                onChange={(e) => setSettings({ ...settings, openai_api_key: e.target.value })}
                                className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white pr-10"
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

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                            Anthropic API Key
                        </label>
                        <div className="relative">
                            <input
                                type={showAnthropicKey ? "text" : "password"}
                                value={settings.anthropic_api_key || ''}
                                onChange={(e) => setSettings({ ...settings, anthropic_api_key: e.target.value })}
                                className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white pr-10"
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

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                            Hugging Face Token (for Pyannote Diarization)
                        </label>
                        <div className="relative">
                            <input
                                type={showHfToken ? "text" : "password"}
                                value={settings.hf_token || ''}
                                onChange={(e) => setSettings({ ...settings, hf_token: e.target.value })}
                                className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white pr-10"
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
                        <p className="text-xs text-gray-500 mt-1">Required for speaker diarization. You must accept Pyannote user conditions on Hugging Face.</p>
                    </div>
                    
                    <div className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            id="infer_meeting_title"
                            checked={settings.infer_meeting_title ?? true}
                            onChange={(e) => setSettings({ ...settings, infer_meeting_title: e.target.checked })}
                            className="rounded border-gray-300 text-orange-600 focus:ring-orange-500"
                        />
                        <label htmlFor="infer_meeting_title" className="text-sm text-gray-700 dark:text-gray-300">
                            Infer Meeting Name Automatically
                        </label>
                    </div>
                </div>
              </div>

              {/* System Configuration */}
              <div>
                <h3 className="text-sm font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-4 border-b border-gray-200 dark:border-gray-800 pb-2">
                    System Configuration
                </h3>
                <div className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                            Worker URL (Redis)
                        </label>
                        <input
                            type="text"
                            value={settings.worker_url || ''}
                            onChange={(e) => setSettings({ ...settings, worker_url: e.target.value })}
                            className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                            placeholder="redis://localhost:6379/0"
                        />
                        <p className="text-xs text-gray-500 mt-1">Connection string for the Redis broker used by the background worker.</p>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                            Companion App URL
                        </label>
                        <input
                            type="text"
                            value={settings.companion_url || ''}
                            onChange={(e) => setSettings({ ...settings, companion_url: e.target.value })}
                            className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                            placeholder="http://localhost:12345"
                        />
                        <p className="text-xs text-gray-500 mt-1">The address where the local Companion App is running.</p>
                    </div>
                    {companionConfig && (
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                Backend API URL
                            </label>
                            <input
                                type="text"
                                value={companionConfig.api_url}
                                onChange={(e) => setCompanionConfig({ ...companionConfig, api_url: e.target.value })}
                                className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                                placeholder="http://localhost:8000/api/v1"
                            />
                            <p className="text-xs text-gray-500 mt-1">
                                The URL where the Companion App sends audio. Change this if your backend is on a different machine.
                            </p>
                        </div>
                    )}
                </div>
              </div>

              {/* Audio & Recording */}
              <div>
                <h3 className="text-sm font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-4 border-b border-gray-200 dark:border-gray-800 pb-2">
                    Audio & Recording
                </h3>
                <div className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                            Whisper Model Size
                        </label>
                        <select
                            value={settings.whisper_model_size || 'turbo'}
                            onChange={(e) => setSettings({ ...settings, whisper_model_size: e.target.value })}
                            className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
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
                </div>
              </div>
            </>
          )}
        </div>

        <div className="p-6 border-t border-gray-200 dark:border-gray-800 flex justify-end gap-3">
            <button
                onClick={onClose}
                className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
            >
                Cancel
            </button>
            <button
                onClick={handleSave}
                disabled={saving || loading}
                className="flex items-center justify-center px-4 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 disabled:opacity-50"
            >
                {saving ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Save className="w-4 h-4 mr-2" />}
                Save Changes
            </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
