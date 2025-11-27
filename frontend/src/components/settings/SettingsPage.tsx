'use client';

import { useState, useEffect, useMemo } from 'react';
import { getSettings, updateSettings, getUserMe } from '@/lib/api';
import { Settings, CompanionDevices } from '@/types';
import { Save, Loader2, Settings as SettingsIcon, Cpu, Mic, Server, Search, User, Shield } from 'lucide-react';
import { getMatchScore } from '@/lib/searchUtils';
import { TAB_KEYWORDS } from './keywords';
import GeneralSettings from './GeneralSettings';
import AISettings from './AISettings';
import AudioSettings from './AudioSettings';
import SystemSettings from './SystemSettings';
import AccountSettings from './AccountSettings';
import AdminSettings from './AdminSettings';
import { useNotificationStore } from '@/lib/notificationStore';

type Tab = 'general' | 'ai' | 'audio' | 'system' | 'account' | 'admin';

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<Tab>('ai');
  const [settings, setSettings] = useState<Settings>({});
  const [companionConfig, setCompanionConfig] = useState<{ api_url: string } | null>(null);
  const [companionDevices, setCompanionDevices] = useState<CompanionDevices | null>(null);
  const [selectedInputDevice, setSelectedInputDevice] = useState<string | null>(null);
  const [selectedOutputDevice, setSelectedOutputDevice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [isAdmin, setIsAdmin] = useState(false);
  const { addNotification } = useNotificationStore();

  // Determine which tabs have matches and their scores
  const tabMatches = useMemo(() => {
    if (!searchQuery) return null;

    const matches: Record<Tab, number> = {
      general: getMatchScore(searchQuery, TAB_KEYWORDS.general),
      ai: getMatchScore(searchQuery, TAB_KEYWORDS.ai),
      audio: getMatchScore(searchQuery, TAB_KEYWORDS.audio),
      system: getMatchScore(searchQuery, TAB_KEYWORDS.system),
      account: getMatchScore(searchQuery, TAB_KEYWORDS.account),
      admin: isAdmin ? getMatchScore(searchQuery, TAB_KEYWORDS.admin) : 1,
    };

    return matches;
  }, [searchQuery, isAdmin]);

  // Auto-switch tab if current one has no matches but others do
  useEffect(() => {
    if (!tabMatches) return;

    const currentScore = tabMatches[activeTab];
    
    // Find best matching tab
    let bestTab: Tab | null = null;
    let bestScore = 1.0;

    (Object.entries(tabMatches) as [Tab, number][]).forEach(([tab, score]) => {
      if (score < bestScore) {
        bestScore = score;
        bestTab = tab;
      }
    });

    if (bestTab && bestTab !== activeTab) {
      // Priority switch: If we found an exact match (score 0) and current is not exact
      if (bestScore === 0 && currentScore > 0) {
        setActiveTab(bestTab);
        return;
      }

      // Fallback switch: If current tab is a bad match, switch to best available
      if (currentScore >= 0.8 && bestScore < 0.6) {
        setActiveTab(bestTab);
      }
    }
  }, [tabMatches, activeTab]);

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  useEffect(() => {
    const load = async () => {
      try {
        const [settingsData, userData] = await Promise.all([
          getSettings(),
          getUserMe()
        ]);
        
        // Ensure settingsData is an object (API might return null)
        const safeSettings = settingsData || {};
        setSettings(safeSettings);
        setIsAdmin(userData.is_superuser);

        // Try to load companion config using the loaded settings or default
        const companionUrl = safeSettings.companion_url || 'http://localhost:12345';
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
  }, [activeTab]);

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

      // Show success feedback (could be a toast, but for now just console)
      console.log("Settings saved successfully");
      addNotification({ type: 'success', message: 'Settings saved successfully' });
    } catch (e) {
      console.error("Failed to save settings", e);
      addNotification({ type: 'error', message: 'Failed to save settings' });
    } finally {
      setSaving(false);
    }
  };

  const tabs = useMemo(() => {
    const baseTabs = [
      { id: 'account', label: 'Account', icon: User },
      { id: 'ai', label: 'AI Services', icon: Cpu },
      { id: 'audio', label: 'Audio & Recording', icon: Mic },
      { id: 'general', label: 'General', icon: SettingsIcon },
      { id: 'system', label: 'System', icon: Server },
    ] as const;

    if (isAdmin) {
      return [
        ...baseTabs,
        { id: 'admin', label: 'Admin Panel', icon: Shield },
      ];
    }
    return baseTabs;
  }, [isAdmin]);

  if (!mounted) return null;

  return (
    <div className="flex flex-col h-full bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <div className="flex items-center justify-between px-8 py-6 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Settings</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">Manage your application preferences and configurations.</p>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <div className="w-64 bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col">
          <div className="p-4 border-b border-gray-200 dark:border-gray-700">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                placeholder="Search settings..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-4 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 text-sm focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              />
            </div>
          </div>
          
          <nav className="p-4 space-y-1 flex-1 overflow-y-auto">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              // @ts-ignore
              const hasMatch = tabMatches && tabMatches[tab.id] < 0.6;

              return (
                <button
                  key={tab.id}
                  // @ts-ignore
                  onClick={() => setActiveTab(tab.id)}
                  className={`
                    w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm font-medium transition-colors
                    ${isActive 
                      ? 'bg-orange-50 dark:bg-orange-900/20 text-orange-700 dark:text-orange-400' 
                      : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/50'
                    }
                  `}
                >
                  <div className="flex items-center gap-3">
                    <Icon className={`w-4 h-4 ${isActive ? 'text-orange-600 dark:text-orange-400' : 'text-gray-400 dark:text-gray-500'}`} />
                    {tab.label}
                  </div>
                  {hasMatch && (
                    <span className="w-2 h-2 rounded-full bg-orange-500" />
                  )}
                </button>
              );
            })}
          </nav>

          <div className="p-4 border-t border-gray-200 dark:border-gray-700">
            <button
              onClick={handleSave}
              disabled={saving || loading}
              className="w-full flex items-center justify-center px-4 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 disabled:opacity-50 transition-colors shadow-sm"
            >
              {saving ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Save className="w-4 h-4 mr-2" />}
              Save Changes
            </button>
          </div>
        </div>

        {/* Content Area */}
        <div className="flex-1 overflow-y-auto p-8">
          {loading ? (
            <div className="flex items-center justify-center h-full text-gray-500">
              <Loader2 className="w-6 h-6 animate-spin mr-2" />
              Loading settings...
            </div>
          ) : (
            <div className="max-w-4xl mx-auto">
              {activeTab === 'account' && <AccountSettings />}
              {activeTab === 'admin' && isAdmin && <AdminSettings />}
              {activeTab === 'general' && <GeneralSettings searchQuery={searchQuery} />}
              {activeTab === 'ai' && (
                <AISettings 
                  settings={settings} 
                  onUpdate={setSettings} 
                  searchQuery={searchQuery}
                />
              )}
              {activeTab === 'audio' && (
                <AudioSettings 
                  settings={settings}
                  onUpdateSettings={setSettings}
                  companionConfig={companionConfig}
                  onUpdateCompanionConfig={(config) => setCompanionConfig(prev => prev ? { ...prev, ...config } : config)}
                  companionDevices={companionDevices}
                  selectedInputDevice={selectedInputDevice}
                  onSelectInputDevice={setSelectedInputDevice}
                  selectedOutputDevice={selectedOutputDevice}
                  onSelectOutputDevice={setSelectedOutputDevice}
                  searchQuery={searchQuery}
                />
              )}
              {activeTab === 'system' && (
                <SystemSettings 
                  settings={settings} 
                  onUpdate={setSettings} 
                  companionConfig={companionConfig}
                  onUpdateCompanionConfig={(config) => setCompanionConfig(prev => prev ? { ...prev, ...config } : config)}
                  searchQuery={searchQuery}
                />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
