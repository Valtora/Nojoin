'use client';

import { Settings } from '@/types';

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
  const matchesSearch = (text: string) => {
    return text.toLowerCase().includes(searchQuery.toLowerCase());
  };

  const showInfrastructure = matchesSearch('infrastructure') || matchesSearch('worker') || matchesSearch('redis') || matchesSearch('url');
  const showCompanion = matchesSearch('companion') || matchesSearch('app') || matchesSearch('backend') || matchesSearch('api');

  if (!showInfrastructure && !showCompanion && searchQuery) {
    return <div className="text-gray-500">No matching settings found.</div>;
  }

  return (
    <div className="space-y-6">
      {showInfrastructure && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Infrastructure</h3>
          <div className="max-w-xl space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Worker URL (Redis)
              </label>
              <input
                type="text"
                value={settings.worker_url || ''}
                onChange={(e) => onUpdate({ ...settings, worker_url: e.target.value })}
                className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                placeholder="redis://localhost:6379/0"
              />
              <p className="text-xs text-gray-500 mt-1">Connection string for the Redis broker used by the background worker.</p>
            </div>
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
                className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
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
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
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
