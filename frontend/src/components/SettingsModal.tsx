'use client';

import { useState, useEffect } from 'react';
import { getSettings, updateSettings } from '@/lib/api';
import { Settings } from '@/types';
import { Save, Loader2, X } from 'lucide-react';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const [settings, setSettings] = useState<Settings>({});
  const [companionConfig, setCompanionConfig] = useState<{ api_url: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (isOpen) {
      const load = async () => {
        try {
          const data = await getSettings();
          setSettings(data);
        } catch (e) {
          console.error("Failed to load settings", e);
        }

        try {
            const res = await fetch('http://localhost:12345/config');
            if (res.ok) {
                const data = await res.json();
                setCompanionConfig(data);
            }
        } catch (e) {
            console.error("Failed to load companion config", e);
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
      
      if (companionConfig) {
          await fetch('http://localhost:12345/config', {
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

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
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
                    </div>

                    {settings.llm_provider === 'gemini' && (
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                Gemini API Key
                            </label>
                            <input
                                type="password"
                                value={settings.gemini_api_key || ''}
                                onChange={(e) => setSettings({ ...settings, gemini_api_key: e.target.value })}
                                className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                                placeholder="AIza..."
                            />
                        </div>
                    )}

                    {settings.llm_provider === 'openai' && (
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                OpenAI API Key
                            </label>
                            <input
                                type="password"
                                value={settings.openai_api_key || ''}
                                onChange={(e) => setSettings({ ...settings, openai_api_key: e.target.value })}
                                className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                                placeholder="sk-..."
                            />
                        </div>
                    )}

                    {settings.llm_provider === 'anthropic' && (
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                Anthropic API Key
                            </label>
                            <input
                                type="password"
                                value={settings.anthropic_api_key || ''}
                                onChange={(e) => setSettings({ ...settings, anthropic_api_key: e.target.value })}
                                className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                                placeholder="sk-ant-..."
                            />
                        </div>
                    )}

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                            Hugging Face Token (for Pyannote Diarization)
                        </label>
                        <input
                            type="password"
                            value={settings.hf_token || ''}
                            onChange={(e) => setSettings({ ...settings, hf_token: e.target.value })}
                            className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                            placeholder="hf_..."
                        />
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
                    </div>
                </div>
              </div>

              {/* Companion App */}
              {companionConfig && (
                  <div>
                    <h3 className="text-sm font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-4 border-b border-gray-200 dark:border-gray-800 pb-2">
                        Companion App Configuration
                    </h3>
                    <div className="space-y-4">
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
                    </div>
                  </div>
              )}
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
    </div>
  );
}
