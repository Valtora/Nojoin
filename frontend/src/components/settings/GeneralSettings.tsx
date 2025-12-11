'use client';

import { useTheme, Theme } from '@/lib/ThemeProvider';
import { fuzzyMatch } from '@/lib/searchUtils';
import { GENERAL_KEYWORDS } from './keywords';
import { Settings } from '@/types';
import { Brain, Mic, Activity, Users, FileText, Type, PlayCircle, RefreshCw } from 'lucide-react';
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
  const showTours = fuzzyMatch(searchQuery, ['tour', 'demo', 'welcome', 'tutorial', 'guide']);

  if (!showAppearance && !showProcessing && !showTours && searchQuery) return <div className="text-gray-500">No matching settings found.</div>;

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

      {showTours && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4 flex items-center gap-2">
            <PlayCircle className="w-5 h-5 text-orange-500" /> Tours & Demos
          </h3>
          <div className="max-w-2xl space-y-4">
            <div className="flex items-center justify-between p-4 bg-gray-100 dark:bg-gray-800/50 rounded-lg border border-gray-300 dark:border-gray-600">
              <div>
                <h4 className="text-sm font-medium text-gray-900 dark:text-white">Restart Welcome Tour</h4>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Reset the &quot;Welcome to Nojoin&quot; tour and the transcript walkthrough.
                </p>
              </div>
              <button
                onClick={handleRestartTour}
                disabled={!userId}
                className="px-3 py-1.5 text-sm font-medium text-orange-600 bg-orange-100 hover:bg-orange-200 dark:text-orange-400 dark:bg-orange-900/20 dark:hover:bg-orange-900/30 rounded-md transition-colors disabled:opacity-50"
              >
                Restart Tour
              </button>
            </div>

            <div className="flex items-center justify-between p-4 bg-gray-100 dark:bg-gray-800/50 rounded-lg border border-gray-300 dark:border-gray-600">
              <div>
                <h4 className="text-sm font-medium text-gray-900 dark:text-white">Re-create Demo Meeting</h4>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  If you deleted the &quot;Welcome to Nojoin&quot; meeting, this will create it again.
                </p>
              </div>
              <button
                onClick={handleRecreateDemo}
                disabled={isSeeding}
                className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-orange-600 bg-orange-100 hover:bg-orange-200 dark:text-orange-400 dark:bg-orange-900/20 dark:hover:bg-orange-900/30 rounded-md transition-colors disabled:opacity-50"
              >
                {isSeeding && <RefreshCw className="w-3 h-3 animate-spin" />}
                {isSeeding ? 'Creating...' : 'Re-create Meeting'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
