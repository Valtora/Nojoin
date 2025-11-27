'use client';

import { useTheme, Theme } from '@/lib/ThemeProvider';
import { fuzzyMatch } from '@/lib/searchUtils';
import { GENERAL_KEYWORDS } from './keywords';

interface GeneralSettingsProps {
  searchQuery?: string;
}

export default function GeneralSettings({ searchQuery = '' }: GeneralSettingsProps) {
  const { theme, setTheme } = useTheme();
  
  const handleThemeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setTheme(e.target.value as Theme);
  };

  const showAppearance = fuzzyMatch(searchQuery, GENERAL_KEYWORDS);

  if (!showAppearance && searchQuery) return <div className="text-gray-500">No matching settings found.</div>;

  return (
    <div className="space-y-6">
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
                className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
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
    </div>
  );
}
