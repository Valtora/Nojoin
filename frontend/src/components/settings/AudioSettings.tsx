'use client';

import { Settings, CompanionDevices } from '@/types';
import { Mic, Speaker, AlertCircle } from 'lucide-react';
import { fuzzyMatch } from '@/lib/searchUtils';
import { AUDIO_KEYWORDS } from './keywords';
import { sanitizeIntegerString } from '@/lib/validation';
import { useState } from 'react';

interface AudioSettingsProps {
  settings: Settings;
  onUpdateSettings: (newSettings: Settings) => void;
  companionConfig: { api_port: number; local_port: number; min_meeting_length?: number } | null;
  onUpdateCompanionConfig: (config: { api_port?: number; min_meeting_length?: number }) => void;
  companionDevices: CompanionDevices | null;
  selectedInputDevice: string | null;
  onSelectInputDevice: (device: string | null) => void;
  selectedOutputDevice: string | null;
  onSelectOutputDevice: (device: string | null) => void;
  searchQuery?: string;
}

export default function AudioSettings({
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
  const [localError, setLocalError] = useState<string | null>(null);

  const handleMinLengthChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    const sanitized = sanitizeIntegerString(val);
    const num = parseInt(sanitized, 10);

    if (isNaN(num)) {
      onUpdateCompanionConfig({ min_meeting_length: 0 });
      setLocalError(null);
      return;
    }

    if (num > 1440) {
      setLocalError("Maximum length is 1440 minutes (24 hours).");
    } else {
      setLocalError(null);
    }
    
    // Update with the number, but we might want to let the user type freely if we were using local state for the input value.
    // Since we are controlled by props, we pass the number up.
    // If we pass a number > 1440, it will be saved if we don't block it in SettingsPage.
    // But we want to show feedback here.
    onUpdateCompanionConfig({ min_meeting_length: num });
  };

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
                    className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
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
                    className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
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
                
                <div className="pt-4 border-t border-gray-200 dark:border-gray-700">
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Minimum Meeting Length (Minutes)
                  </label>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={companionConfig?.min_meeting_length?.toString() || '0'}
                    onChange={handleMinLengthChange}
                    className={`w-full p-2 rounded-lg border ${localError ? 'border-red-500 focus:ring-red-500' : 'border-gray-400 dark:border-gray-600 focus:ring-orange-500'} bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:border-transparent`}
                  />
                  {localError && (
                    <p className="text-xs text-red-500 mt-1 flex items-center gap-1">
                      <AlertCircle className="w-3 h-3" />
                      {localError}
                    </p>
                  )}
                  <p className="text-xs text-gray-500 mt-1">
                    Recordings shorter than this will be automatically discarded. Set to 0 to disable.
                  </p>
                </div>
              </>
            ) : (
              <div className="p-4 bg-yellow-100 dark:bg-yellow-900/20 border border-yellow-300 dark:border-yellow-800 rounded-lg">
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
