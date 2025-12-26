import { useState, useEffect } from 'react';
import { Shield, Mail, Cpu, Server, Database } from 'lucide-react';
import { Settings } from '@/types';
import UsersTab from './UsersTab';
import InvitesTab from './InvitesTab';
import AISettings from './AISettings';
import BackupRestore from './BackupRestore';
import { fuzzyMatch } from '@/lib/searchUtils';

interface AdminSettingsProps {
  settings: Settings;
  onUpdateSettings: (newSettings: Settings) => void;
  isAdmin: boolean;
  searchQuery?: string;
}

export default function AdminSettings({ settings, onUpdateSettings, isAdmin, searchQuery = '' }: AdminSettingsProps) {
  const [activeTab, setActiveTab] = useState<'users' | 'invites' | 'ai' | 'system' | 'backup'>('users');

  useEffect(() => {
    if (!searchQuery) return;

    if (fuzzyMatch(searchQuery, ['users', 'roles', 'permissions'])) setActiveTab('users');
    if (fuzzyMatch(searchQuery, ['invite', 'token', 'link'])) setActiveTab('invites');
    if (fuzzyMatch(searchQuery, ['ai', 'llm', 'api key', 'provider', 'model', 'gemini', 'openai'])) setActiveTab('ai');
    if (fuzzyMatch(searchQuery, ['system', 'infrastructure', 'docker', 'port', 'redis', 'worker'])) setActiveTab('system');
    if (fuzzyMatch(searchQuery, ['backup', 'restore', 'export', 'import', 'data'])) setActiveTab('backup');
  }, [searchQuery]);

  const tabs = [
    { id: 'users', label: 'Users', icon: Shield },
    { id: 'invites', label: 'Invites', icon: Mail },
    { id: 'ai', label: 'AI Configuration', icon: Cpu },
    { id: 'system', label: 'System', icon: Server },
    { id: 'backup', label: 'Backup & Restore', icon: Database },
  ] as const;

  return (
    <div className="space-y-6">
      <div className="flex border-b border-gray-200 dark:border-gray-700 overflow-x-auto">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`
                            flex items-center gap-2 px-4 py-2 text-sm font-medium whitespace-nowrap transition-colors
                            ${activeTab === tab.id
                ? 'border-b-2 border-orange-500 text-orange-600 dark:text-orange-400 bg-orange-50/50 dark:bg-orange-900/10'
                : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800/50'}
                        `}
          >
            <tab.icon className="w-4 h-4" />
            {tab.label}
          </button>
        ))}
      </div>

      <div className="mt-6">
        {activeTab === 'users' && <UsersTab />}
        {activeTab === 'invites' && <InvitesTab />}
        {activeTab === 'ai' && (
          <AISettings
            settings={settings}
            onUpdate={(newSettings) => onUpdateSettings(newSettings)}
            isAdmin={isAdmin}
            searchQuery={searchQuery}
          />
        )}
        {activeTab === 'system' && (
          <div className="space-y-6">
            <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Infrastructure</h3>
            <div className="max-w-xl space-y-4">
              <p className="text-sm text-gray-500 dark:text-gray-400">
                Infrastructure settings are configured via Docker Compose environment variables.
              </p>
              <div className="bg-gray-100 dark:bg-gray-800/50 p-4 rounded-lg text-xs font-mono">
                <div>Broker: Redis</div>
                <div>Database: PostgreSQL</div>
                <div>Worker: Celery</div>
              </div>
            </div>
          </div>
        )}
        {activeTab === 'backup' && <BackupRestore />}
      </div>
    </div>
  );
}
