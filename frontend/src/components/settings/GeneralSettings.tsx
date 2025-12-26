import { useTheme, Theme } from '@/lib/ThemeProvider';
import { fuzzyMatch } from '@/lib/searchUtils';
import { GENERAL_KEYWORDS } from './keywords';
import { Settings } from '@/types';
import { Brain, Mic, Activity, Users, FileText, Type, Info, MessageSquare } from 'lucide-react';
import { Switch } from '../ui/Switch';
import { useNavigationStore } from '@/lib/store';
import { seedDemoData } from '@/lib/api';
import { useState } from 'react';
import { useNotificationStore } from '@/lib/notificationStore';

interface GeneralSettingsProps {
  settings: Settings;
  onUpdate: (newSettings: Settings) => void;
  searchQuery?: string;
  userId: number | null;
}

export default function GeneralSettings({ settings, onUpdate, searchQuery = '', userId }: GeneralSettingsProps) {
  const { theme, setTheme } = useTheme();
  const { setHasSeenTour, setHasSeenTranscriptTour } = useNavigationStore();
  const { addNotification } = useNotificationStore();
  const [isSeeding, setIsSeeding] = useState(false);

  const handleThemeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setTheme(e.target.value as Theme);
  };

  const handleRestartTour = () => {
    if (userId) {
      setHasSeenTour(userId, false);
      setHasSeenTranscriptTour(userId, false);
      addNotification({ type: 'success', message: 'Tours reset. Go to the dashboard to start the Welcome Tour.' });
    }
  };

  const handleRecreateDemo = async () => {
    setIsSeeding(true);
    try {
      await seedDemoData();
      addNotification({ type: 'success', message: 'Demo meeting creation started. It will appear in your recordings shortly.' });
    } catch (error) {
      addNotification({ type: 'error', message: 'Failed to create demo meeting.' });
    } finally {
      setIsSeeding(false);
    }
  };

  const showAppearance = fuzzyMatch(searchQuery, GENERAL_KEYWORDS);
  const showProcessing = fuzzyMatch(searchQuery, ['processing', 'vad', 'silence', 'diarization', 'title', 'inference', 'speakers', 'notes']);

  if (!showAppearance && !showProcessing && searchQuery) return <div className="text-gray-500">No matching settings found.</div>;

  return (
    <div className="space-y-8">
      {showAppearance && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Appearance</h3>
          <div className="grid grid-cols-1 gap-4 max-w-xl">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Theme
              </label>
              <select
                value={theme}
                onChange={handleThemeChange}
                className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              >
                <option value="system">System Default</option>
                <option value="light">Light</option>
                <option value="dark">Dark</option>
              </select>
              <p className="text-xs text-gray-500 mt-1">Choose your preferred visual theme.</p>
            </div>
          </div>
        </div>
      )}

      {showProcessing && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4 flex items-center gap-2">
            <Activity className="w-5 h-5 text-orange-500" /> Processing & Intelligence
          </h3>
          <div className="max-w-2xl space-y-4">

            {/* VAD Toggle */}
            <div className="flex items-start gap-3 p-3 bg-gray-100 dark:bg-gray-800/50 rounded-lg border border-gray-300 dark:border-gray-600">
              <div className="mt-1"><Mic className="w-5 h-5 text-blue-500" /></div>
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium text-gray-900 dark:text-white">Voice Activity Detection (VAD)</label>
                  <Switch
                    checked={settings.enable_vad !== false} // Default true
                    onCheckedChange={(checked) => onUpdate({ ...settings, enable_vad: checked })}
                  />
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Filters out silence and background noise before transcription. Disabling this may increase processing time but can help if quiet speech is being cut off.
                </p>
              </div>
            </div>

            {/* Diarization Toggle */}
            <div className="flex items-start gap-3 p-3 bg-gray-100 dark:bg-gray-800/50 rounded-lg border border-gray-300 dark:border-gray-600">
              <div className="mt-1"><Users className="w-5 h-5 text-purple-500" /></div>
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium text-gray-900 dark:text-white">Speaker Diarization</label>
                  <Switch
                    checked={settings.enable_diarization !== false} // Default true
                    onCheckedChange={(checked) => onUpdate({ ...settings, enable_diarization: checked })}
                  />
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Distinguishes between different speakers (e.g., &quot;Speaker 1&quot;, &quot;Speaker 2&quot;). Disable this for single-speaker recordings to speed up processing.
                </p>
              </div>
            </div>

            {/* Title Inference Toggle */}
            <div className="flex flex-col gap-2 p-3 bg-gray-100 dark:bg-gray-800/50 rounded-lg border border-gray-300 dark:border-gray-600">
              <div className="flex items-start gap-3">
                <div className="mt-1"><Type className="w-5 h-5 text-green-500" /></div>
                <div className="flex-1">
                  <div className="flex items-center justify-between">
                    <label className="text-sm font-medium text-gray-900 dark:text-white">Infer Meeting Title</label>
                    <Switch
                      checked={settings.auto_generate_title !== false} // Default true
                      onCheckedChange={(checked) => onUpdate({ ...settings, auto_generate_title: checked })}
                    />
                  </div>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    Automatically generates a descriptive title for the meeting based on the transcript content using the configured LLM.
                  </p>
                </div>
              </div>

              {/* Sub-toggle for Short Titles */}
              {settings.auto_generate_title !== false && (
                <div className="ml-9 pl-3 border-l-2 border-gray-300 dark:border-gray-600">
                  <div className="flex items-center justify-between">
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Prefer Short Titles</label>
                    <Switch
                      checked={settings.prefer_short_titles !== false} // Default true
                      onCheckedChange={(checked) => onUpdate({ ...settings, prefer_short_titles: checked })}
                    />
                  </div>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    Generates concise 3-5 word titles instead of longer descriptive ones.
                  </p>
                </div>
              )}
            </div>

            {/* Speaker Inference Toggle */}
            <div className="flex items-start gap-3 p-3 bg-gray-100 dark:bg-gray-800/50 rounded-lg border border-gray-300 dark:border-gray-600">
              <div className="mt-1"><Brain className="w-5 h-5 text-pink-500" /></div>
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium text-gray-900 dark:text-white">Infer Speaker Names</label>
                  <Switch
                    checked={settings.auto_infer_speakers !== false} // Default true
                    onCheckedChange={(checked) => onUpdate({ ...settings, auto_infer_speakers: checked })}
                  />
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Uses the LLM to infer real names (e.g., &quot;John&quot;, &quot;Interviewer&quot;) from context and replaces generic labels like &quot;Speaker 1&quot;.
                </p>
              </div>
            </div>

            {/* Notes Generation Toggle */}
            <div className="flex items-start gap-3 p-3 bg-gray-100 dark:bg-gray-800/50 rounded-lg border border-gray-300 dark:border-gray-600">
              <div className="mt-1"><FileText className="w-5 h-5 text-yellow-500" /></div>
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium text-gray-900 dark:text-white">Auto-Generate Notes</label>
                  <Switch
                    checked={settings.auto_generate_notes !== false} // Default true
                    onCheckedChange={(checked) => onUpdate({ ...settings, auto_generate_notes: checked })}
                  />
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Automatically generates summaries, action items, and key takeaways after processing.
                </p>
              </div>
            </div>

          </div>
        </div>
      )}

      {/* Custom Instructions (Moved from AI Settings) */}
      <div>
        <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4 flex items-center gap-2">
          <MessageSquare className="w-5 h-5 text-blue-500" /> LLM Customization
        </h3>
        <div className="max-w-2xl">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-2">
            Custom Instructions
          </label>
          <div className="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg p-3 mb-3 text-xs text-yellow-800 dark:text-yellow-200 flex gap-2">
            <Info className="w-4 h-4 flex-shrink-0" />
            <span>These instructions are appended to every prompt sent to the LLM. Use this to define a custom persona or specific formatting rules.</span>
          </div>
          <textarea
            value={settings.chat_custom_instructions || ''}
            onChange={(e) => onUpdate({ ...settings, chat_custom_instructions: e.target.value })}
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
  );
}
