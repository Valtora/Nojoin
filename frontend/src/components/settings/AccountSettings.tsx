import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { updatePasswordMe, updateUserMe } from '@/lib/api';
import { fuzzyMatch } from '@/lib/searchUtils';
import { Loader2, User, Lock } from 'lucide-react';
import { useNotificationStore } from '@/lib/notificationStore';
import { trimString } from '@/lib/validation';
import CalendarConnectionsSettings from './CalendarConnectionsSettings';
import SettingsCallout from './SettingsCallout';
import SettingsField from './SettingsField';
import SettingsSection from './SettingsSection';
import useDebouncedAutosave, {
  type SettingsAutosaveSnapshot,
} from './useDebouncedAutosave';

interface AccountSettingsProps {
  forcePasswordChange?: boolean;
  initialUsername: string | null;
  onUsernameSaved?: (username: string) => void;
  onAutosaveStateChange?: (snapshot: SettingsAutosaveSnapshot) => void;
  searchQuery?: string;
  suppressNoMatch?: boolean;
  includeCalendarConnections?: boolean;
}

export default function AccountSettings({
  forcePasswordChange = false,
  initialUsername,
  onUsernameSaved,
  onAutosaveStateChange,
  searchQuery = '',
  suppressNoMatch = false,
  includeCalendarConnections = true,
}: AccountSettingsProps) {
  const router = useRouter();
  const [passwordLoading, setPasswordLoading] = useState(false);
  const [username, setUsername] = useState('');
  const { addNotification } = useNotificationStore();
  
  const [passwordData, setPasswordData] = useState({
    current_password: '',
    new_password: '',
    confirm_password: ''
  });

  const { markAsSaved: markProfileAsSaved } = useDebouncedAutosave({
    value: { username },
    enabled: initialUsername !== null,
    serialize: (value) =>
      JSON.stringify({ username: trimString(value.username) }),
    validate: (value) => {
      const trimmedUsername = trimString(value.username);
      if (!trimmedUsername) {
        return 'Username cannot be empty.';
      }

      return null;
    },
    save: async (value) => {
      const trimmedUsername = trimString(value.username);
      const updatedUser = await updateUserMe({ username: trimmedUsername });
      setUsername(updatedUser.username);
      localStorage.setItem('username', updatedUser.username);
      onUsernameSaved?.(updatedUser.username);
    },
    pendingMessage: 'Profile changes pending...',
    savingMessage: 'Saving profile...',
    savedMessage: 'Profile saved',
    fallbackErrorMessage: 'Failed to save profile',
    onStatusChange: onAutosaveStateChange,
  });

  useEffect(() => {
    if (initialUsername === null) {
      return;
    }

    setUsername(initialUsername);
    markProfileAsSaved({ username: initialUsername });
  }, [initialUsername, markProfileAsSaved]);

  const showProfile = !searchQuery || fuzzyMatch(searchQuery, [
    'profile',
    'username',
    'account',
    'personal',
    'user',
  ]);
  const showSecurity = !searchQuery || fuzzyMatch(searchQuery, [
    'password',
    'security',
    'credentials',
    'change password',
    'login',
  ]);
  const showCalendars =
    includeCalendarConnections &&
    !forcePasswordChange &&
    (!searchQuery ||
      fuzzyMatch(searchQuery, [
        'calendar',
        'calendars',
        'calendar connections',
        'gmail',
        'google',
        'outlook',
        'microsoft',
        'agenda',
        'events',
      ]));

  if (!showProfile && !showSecurity && !showCalendars && searchQuery) {
    return suppressNoMatch ? null : (
      <SettingsCallout
        tone="neutral"
        title="No matching settings"
        message="Try a broader search term for profile, passwords, security, or calendar connections."
      />
    );
  }

  const handlePasswordUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (passwordData.new_password !== passwordData.confirm_password) {
      addNotification({ message: 'New passwords do not match', type: 'error' });
      return;
    }
    
    setPasswordLoading(true);
    try {
      await updatePasswordMe({
        current_password: passwordData.current_password,
        new_password: passwordData.new_password
      });
      addNotification({ message: 'Password updated successfully', type: 'success' });
      setPasswordData({ current_password: '', new_password: '', confirm_password: '' });
      if (forcePasswordChange) {
        router.push('/');
      }
    } catch (err: any) {
      addNotification({ message: err.response?.data?.detail || 'Failed to update password', type: 'error' });
    } finally {
      setPasswordLoading(false);
    }
  };

  return (
    <div className="space-y-8">
      {showProfile && (
        <SettingsSection
          eyebrow="Personal"
          title="Profile"
          description="Update the name shown across your workspace."
          width="compact"
        >
          <div className="mx-auto max-w-md space-y-4">
            <SettingsField label="Username" icon={<User className="h-4 w-4" />}>
              <input
                id="account-username"
                name="account-username"
                type="text"
                autoComplete="section-account-profile username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full rounded-xl border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 focus:border-transparent focus:ring-2 focus:ring-orange-500 dark:border-gray-700 dark:bg-gray-900 dark:text-white"
                disabled={initialUsername === null}
                required
              />
            </SettingsField>
          </div>
        </SettingsSection>
      )}

      {showSecurity && (
        <SettingsSection
          eyebrow="Security"
          title="Password"
          description="Change the password used to sign in to this account."
          width="compact"
        >
          {forcePasswordChange && (
            <SettingsCallout
              tone="warning"
              message="You must choose a new password before continuing to the rest of the application."
            />
          )}
          <form
            id="account-password-form"
            name="account-password-form"
            onSubmit={handlePasswordUpdate}
            className="mx-auto max-w-md space-y-4"
            autoComplete="on"
          >
            <SettingsField label="Current Password" icon={<Lock className="h-4 w-4" />}>
              <input
                id="account-current-password"
                name="account-current-password"
                type="password"
                autoComplete="section-account-password current-password"
                value={passwordData.current_password}
                onChange={(e) => setPasswordData({...passwordData, current_password: e.target.value})}
                className="w-full rounded-xl border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 focus:border-transparent focus:ring-2 focus:ring-orange-500 dark:border-gray-700 dark:bg-gray-900 dark:text-white"
                required
              />
            </SettingsField>
            <SettingsField label="New Password" icon={<Lock className="h-4 w-4" />}>
              <input
                id="account-new-password"
                name="account-new-password"
                type="password"
                autoComplete="section-account-password new-password"
                value={passwordData.new_password}
                onChange={(e) => setPasswordData({...passwordData, new_password: e.target.value})}
                className="w-full rounded-xl border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 focus:border-transparent focus:ring-2 focus:ring-orange-500 dark:border-gray-700 dark:bg-gray-900 dark:text-white"
                required
                minLength={8}
              />
            </SettingsField>
            <SettingsField label="Confirm New Password" icon={<Lock className="h-4 w-4" />}>
              <input
                id="account-confirm-password"
                name="account-confirm-password"
                type="password"
                autoComplete="section-account-password new-password"
                value={passwordData.confirm_password}
                onChange={(e) => setPasswordData({...passwordData, confirm_password: e.target.value})}
                className="w-full rounded-xl border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 focus:border-transparent focus:ring-2 focus:ring-orange-500 dark:border-gray-700 dark:bg-gray-900 dark:text-white"
                required
                minLength={8}
              />
            </SettingsField>
            <button
              type="submit"
              disabled={passwordLoading}
              className="inline-flex items-center gap-2 rounded-xl bg-orange-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-orange-700 disabled:cursor-not-allowed disabled:bg-orange-300 dark:disabled:bg-orange-900/40"
            >
              {passwordLoading && <Loader2 className="h-4 w-4 animate-spin" />}
              Update Password
            </button>
          </form>
        </SettingsSection>
      )}

      {showCalendars && <CalendarConnectionsSettings />}
    </div>
  );
}
