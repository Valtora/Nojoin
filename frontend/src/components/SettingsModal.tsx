'use client';

import { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { getSettings, updateSettings } from '@/lib/api';
import { Settings, CompanionDevices } from '@/types';
import { Save, Loader2, X, Eye, EyeOff, Mic, Speaker } from 'lucide-react';
import { useTheme, Theme } from '@/lib/ThemeProvider';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const [settings, setSettings] = useState<Settings>({});
  const [companionConfig, setCompanionConfig] = useState<{ api_url: string } | null>(null);
  const [companionDevices, setCompanionDevices] = useState<CompanionDevices | null>(null);
  const [selectedInputDevice, setSelectedInputDevice] = useState<string | null>(null);
  const [selectedOutputDevice, setSelectedOutputDevice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  
  // Visibility toggles
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [showOpenAIKey, setShowOpenAIKey] = useState(false);
  const [showAnthropicKey, setShowAnthropicKey] = useState(false);
  const [showHfToken, setShowHfToken] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
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
              
              // Fetch available devices
              const devicesRes = await fetch(`${companionUrl}/devices`);
              if (devicesRes.ok) {
                  const devicesData: CompanionDevices = await devicesRes.json();
                  setCompanionDevices(devicesData);
                  setSelectedInputDevice(devicesData.selected_input);
                  setSelectedOutputDevice(devicesData.selected_output);
              }
          } catch (e) {
              console.error("Failed to load companion config/devices", e);
              setCompanionDevices(null);
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
      
      // Save companion config (api_url) if available
      if (companionConfig) {
          await fetch(`${companionUrl}/config`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ api_url: companionConfig.api_url })
          });
      }
      
      // Save device selections if companion is connected
      if (companionDevices) {
          await fetch(`${companionUrl}/config`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                  input_device_name: selectedInputDevice,
                  output_device_name: selectedOutputDevice
              })
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
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-2xl max-h-[80vh] flex flex-col border border-gray-300 dark:border-gray-800">
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
              <ThemeSection />

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
                    {/* Audio Device Selection */}
                    {companionDevices ? (
                      <>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                            <div className="flex items-center gap-2">
                              <Mic className="w-4 h-4" />
                              Input Device (Microphone)
                            </div>
                          </label>
                          <select
                            value={selectedInputDevice || ''}
                            onChange={(e) => setSelectedInputDevice(e.target.value || null)}
                            className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                          >
                            <option value="">System Default</option>
                            {companionDevices.input_devices.map((device) => (
                              <option key={device.name} value={device.name}>
                                {device.name}{device.is_default ? ' (Default)' : ''}
                              </option>
                            ))}
                          </select>
                          <p className="text-xs text-gray-500 mt-1">The microphone to capture your voice.</p>
                        </div>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                            <div className="flex items-center gap-2">
                              <Speaker className="w-4 h-4" />
                              Output Device (System Audio)
                            </div>
                          </label>
                          <select
                            value={selectedOutputDevice || ''}
                            onChange={(e) => setSelectedOutputDevice(e.target.value || null)}
                            className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                          >
                            <option value="">System Default</option>
                            {companionDevices.output_devices.map((device) => (
                              <option key={device.name} value={device.name}>
                                {device.name}{device.is_default ? ' (Default)' : ''}
                              </option>
                            ))}
                          </select>
                          <p className="text-xs text-gray-500 mt-1">The audio output to capture system sounds (loopback).</p>
                        </div>
                      </>
                    ) : (
                      <div className="p-4 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg">
                        <p className="text-sm text-yellow-800 dark:text-yellow-200">
                          Companion app not connected. Start the Companion app to configure audio devices.
                        </p>
                      </div>
                    )}

                    <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg">
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
                                onChange={(e) => setSettings({ ...settings, enable_auto_voiceprints: e.target.checked })}
                                className="sr-only peer"
                            />
                            <div className="w-11 h-6 bg-gray-300 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-500 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                        </label>
                    </div>

                    {/* Advanced Section Toggle */}
                    <div className="flex items-center gap-2 pt-2">
                        <input
                            type="checkbox"
                            id="show-advanced"
                            checked={showAdvanced}
                            onChange={(e) => setShowAdvanced(e.target.checked)}
                            className="w-4 h-4 rounded border-gray-300 text-orange-600 focus:ring-orange-500"
                        />
                        <label htmlFor="show-advanced" className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer">
                            Show Advanced Settings
                        </label>
                    </div>

                    {/* Advanced Settings (Collapsible) */}
                    {showAdvanced && (
                      <div className="space-y-4 pl-4 border-l-2 border-orange-200 dark:border-orange-800">
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
                    )}
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

// Separate component to use the useTheme hook
function ThemeSection() {
  const { theme, setTheme } = useTheme();
  
  const handleThemeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setTheme(e.target.value as Theme);
  };

  return (
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
            value={theme}
            onChange={handleThemeChange}
            className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
          >
            <option value="system">System Default</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
          <p className="text-xs text-gray-500 mt-1">Choose your preferred visual theme.</p>
        </div>
      </div>
    </div>
  );
}
