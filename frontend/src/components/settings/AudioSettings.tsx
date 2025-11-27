'use client';

import { Settings, CompanionDevices } from '@/types';
import { Mic, Speaker } from 'lucide-react';
import { fuzzyMatch } from '@/lib/searchUtils';
import { AUDIO_KEYWORDS } from './keywords';

interface AudioSettingsProps {
  settings: Settings;
  onUpdateSettings: (newSettings: Settings) => void;
  companionConfig: { api_url: string } | null;
  onUpdateCompanionConfig: (config: { api_url: string }) => void;
  companionDevices: CompanionDevices | null;
  selectedInputDevice: string | null;
  onSelectInputDevice: (device: string | null) => void;
  selectedOutputDevice: string | null;
  onSelectOutputDevice: (device: string | null) => void;
  searchQuery?: string;
}

export default function AudioSettings({
  settings,
  onUpdateSettings,
  companionConfig,
  onUpdateCompanionConfig,
  companionDevices,
  selectedInputDevice,
  onSelectInputDevice,
  selectedOutputDevice,
  onSelectOutputDevice,
  searchQuery = '',
}: AudioSettingsProps) {
  const showDevices = fuzzyMatch(searchQuery, AUDIO_KEYWORDS);

  if (!showDevices && searchQuery) {
    return <div className="text-gray-500">No matching settings found.</div>;
  }

  return (
    <div className="space-y-8">
      {showDevices && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Devices</h3>
          <div className="max-w-xl space-y-4">
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
                    onChange={(e) => onSelectInputDevice(e.target.value || null)}
                    className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
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
                    onChange={(e) => onSelectOutputDevice(e.target.value || null)}
                    className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
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
          </div>
        </div>
      )}
    </div>
  );
}
