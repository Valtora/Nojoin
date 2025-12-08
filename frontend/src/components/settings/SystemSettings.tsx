'use client';

import { Settings } from '@/types';
import { fuzzyMatch } from '@/lib/searchUtils';
import BackupRestore from './BackupRestore';

interface SystemSettingsProps {
  settings: Settings;
  onUpdate: (newSettings: Settings) => void;
  companionConfig?: { api_port: number; local_port: number } | null;
  onUpdateCompanionConfig?: (config: { api_port: number }) => void;
  searchQuery?: string;
  isAdmin?: boolean;
}

export default function SystemSettings({ 
  companionConfig, 
  onUpdateCompanionConfig, 
  searchQuery = '',
  isAdmin = false
}: SystemSettingsProps) {
  const showInfrastructure = isAdmin && fuzzyMatch(searchQuery, ['infrastructure', 'worker', 'redis', 'url', 'broker', 'connection']);
  const showCompanion = isAdmin && fuzzyMatch(searchQuery, ['companion', 'app', 'backend', 'api', 'port', 'address']);
  const showBackup = fuzzyMatch(searchQuery, ['backup', 'restore', 'export', 'import', 'data', 'zip']);

  if (!showInfrastructure && !showCompanion && !showBackup && searchQuery) {
    return <div className="text-gray-500">No matching settings found.</div>;
  }

  return (
    <div className="space-y-6">
      {showInfrastructure && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Infrastructure</h3>
          <div className="max-w-xl space-y-4">
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Infrastructure settings are configured via Docker Compose environment variables.
            </p>
          </div>
        </div>
      )}

      {showCompanion && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Companion App</h3>
          <div className="max-w-xl space-y-4">
            <div className="p-4 bg-gray-100 dark:bg-gray-800/50 rounded-lg border border-gray-300 dark:border-gray-600">
              <p className="text-sm text-gray-600 dark:text-gray-300 mb-2">
                The Companion App always runs on <code className="bg-gray-200 dark:bg-gray-700 px-1 rounded">localhost:12345</code> and connects to the backend via HTTPS.
              </p>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                For remote access, configure a reverse proxy (like Nginx) on the client machine.
              </p>
            </div>
            
            {companionConfig && onUpdateCompanionConfig && (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Backend API Port
                </label>
                <input
                  type="number"
                  min={1}
                  max={65535}
                  value={companionConfig.api_port}
                  onChange={(e) => onUpdateCompanionConfig({ api_port: parseInt(e.target.value) || 14443 })}
                  className="w-32 p-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                  placeholder="14443"
                />
                <p className="text-xs text-gray-500 mt-1">
                  The HTTPS port where the backend API is accessible (default: 14443).
                </p>
              </div>
            )}

            {!companionConfig && (
              <div className="p-4 bg-yellow-100 dark:bg-yellow-900/20 border border-yellow-300 dark:border-yellow-800 rounded-lg">
                <p className="text-sm text-yellow-800 dark:text-yellow-200">
                  Companion App not connected. Start the Companion App to configure settings.
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {showBackup && <BackupRestore />}
    </div>
  );
}
