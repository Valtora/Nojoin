import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { updatePasswordMe, updateUserMe } from '@/lib/api';
import { getErrorMessage } from '@/lib/errors';
import { fuzzyMatch } from '@/lib/searchUtils';
import { Loader2, User, Lock } from 'lucide-react';
import { useNotificationStore } from '@/lib/notificationStore';
import { trimString } from '@/lib/validation';
import CalendarConnectionsSettings from './CalendarConnectionsSettings';
import ConnectedAppsSettings from './ConnectedAppsSettings';
import SettingsBlock from './SettingsBlock';
import SettingsCallout from './SettingsCallout';
import SettingsCard from './SettingsCard';
import SettingsRow from './SettingsRow';
import {
  SETTINGS_BUTTON_PRIMARY,
  SETTINGS_INPUT_CLASS,
} from './settingsControls';
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

  const showConnectedApps =
    includeCalendarConnections &&
    !forcePasswordChange &&
    (!searchQuery ||
      fuzzyMatch(searchQuery, [
        'connected apps',
        'connections',
        'connector',
        'mcp',
        'claude',
        'integrations',
        'oauth',
      ]));

  if (!showProfile && !showSecurity && !showCalendars && !showConnectedApps && searchQuery) {
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

        } catch (err: unknown) {
      addNotification({ message: getErrorMessage(err, 'Failed to update password'), type: 'error' });
    } finally {
      setPasswordLoading(false);
    }
  };

  return (
    <>
      {showProfile && (
        <SettingsCard
          id="profile-username"
          title="Profile"
          description="The name shown across your workspace."
        >
          <SettingsRow
            label="Username"
            icon={<User className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
          >
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
              className={SETTINGS_INPUT_CLASS}
              disabled={initialUsername === null}
              required
            />
          </SettingsRow>
        </SettingsCard>
      )}

      {showSecurity && (
        <SettingsCard
          id="profile-password"
          title="Password"
          description="Change the password used to sign in to this account."
        >
          {forcePasswordChange && (
            <SettingsBlock>
              <SettingsCallout
                tone="warning"
                message="You must choose a new password before continuing to the rest of the application."
              />
            </SettingsBlock>
          )}

          {/* One form spanning the rows, so the browser still treats these as a
              single credential change and password managers behave. */}
          <form
            id="account-password-form"
            name="account-password-form"
            onSubmit={handlePasswordUpdate}
            className="settings-card-body"
            autoComplete="on"
          >
            <SettingsRow
              label="Current password"
              icon={<Lock className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
            >
              <input
                id="account-current-password"
                name="account-current-password"
                type="password"
                autoComplete="section-account-password current-password"
                value={passwordData.current_password}
                onChange={(e) =>
                  setPasswordData({ ...passwordData, current_password: e.target.value })
                }
                className={SETTINGS_INPUT_CLASS}
                required
              />
            </SettingsRow>

            <SettingsRow
              label="New password"
              description="At least 8 characters."
              icon={<Lock className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
            >
              <input
                id="account-new-password"
                name="account-new-password"
                type="password"
                autoComplete="section-account-password new-password"
                value={passwordData.new_password}
                onChange={(e) =>
                  setPasswordData({ ...passwordData, new_password: e.target.value })
                }
                className={SETTINGS_INPUT_CLASS}
                required
                minLength={8}
              />
            </SettingsRow>

            <SettingsRow
              label="Confirm new password"
              icon={<Lock className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
            >
              <input
                id="account-confirm-password"
                name="account-confirm-password"
                type="password"
                autoComplete="section-account-password new-password"
                value={passwordData.confirm_password}
                onChange={(e) =>
                  setPasswordData({ ...passwordData, confirm_password: e.target.value })
                }
                className={SETTINGS_INPUT_CLASS}
                required
                minLength={8}
              />
            </SettingsRow>

            <SettingsBlock className="flex justify-end">
              <button
                type="submit"
                disabled={passwordLoading}
                className={SETTINGS_BUTTON_PRIMARY}
              >
                {passwordLoading && (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                )}
                Update password
              </button>
            </SettingsBlock>
          </form>
        </SettingsCard>
      )}

      {showCalendars && <CalendarConnectionsSettings />}

      {showConnectedApps && <ConnectedAppsSettings />}
    </>
  );
}
