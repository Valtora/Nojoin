'use client';

import { Settings } from '@/types';
import { fuzzyMatch } from '@/lib/searchUtils';

interface SystemSettingsProps {
  settings: Settings;
  onUpdate: (newSettings: Settings) => void;
  companionConfig?: { api_url: string } | null;
  onUpdateCompanionConfig?: (config: { api_url: string }) => void;
  searchQuery?: string;
}

export default function SystemSettings({ 
  settings, 
  onUpdate, 
  companionConfig, 
  onUpdateCompanionConfig, 
  searchQuery = '' 
}: SystemSettingsProps) {
  const showInfrastructure = fuzzyMatch(searchQuery, ['infrastructure', 'worker', 'redis', 'url', 'broker', 'connection']);
  const showCompanion = fuzzyMatch(searchQuery, ['companion', 'app', 'backend', 'api', 'port', 'address']);

  if (!showInfrastructure && !showCompanion && searchQuery) {
    return <div className="text-gray-500">No matching settings found.</div>;
  }

  return (
    <div className="space-y-6">
      {showInfrastructure && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Infrastructure</h3>
          <div className="max-w-xl space-y-4">
            {/* Worker URL removed as it is configured via ENV */}

          </div>
        </div>
      )}

      {showCompanion && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Companion App</h3>
          <div className="max-w-xl space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Companion App URL
              </label>
              <input
                type="text"
                value={settings.companion_url || ''}
                onChange={(e) => onUpdate({ ...settings, companion_url: e.target.value })}
                className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                placeholder="http://localhost:12345"
              />
              <p className="text-xs text-gray-500 mt-1">The address where the local Companion App is running.</p>
            </div>
            
            {companionConfig && onUpdateCompanionConfig && (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Backend API URL (for Companion)
                </label>
                <input
                  type="text"
                  value={companionConfig.api_url}
                  onChange={(e) => onUpdateCompanionConfig({ ...companionConfig, api_url: e.target.value })}
                  className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                  placeholder="http://localhost:8000/api/v1"
                />
                <p className="text-xs text-gray-500 mt-1">
                  The URL where the Companion App sends audio. Change this if your backend is on a different machine.
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
