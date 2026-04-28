import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { updateUserMe, updatePasswordMe, getUserMe } from '@/lib/api';
import { Loader2, User, Lock, Save } from 'lucide-react';
import { useNotificationStore } from '@/lib/notificationStore';
import { trimString } from '@/lib/validation';
import CalendarConnectionsSettings from './CalendarConnectionsSettings';

export default function AccountSettings({
  forcePasswordChange = false,
}: {
  forcePasswordChange?: boolean;
}) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [username, setUsername] = useState('');
  const { addNotification } = useNotificationStore();
  
  const [passwordData, setPasswordData] = useState({
    current_password: '',
    new_password: '',
    confirm_password: ''
  });

  useEffect(() => {
    const fetchUser = async () => {
      try {
        const data = await getUserMe();
        setUsername(data.username);
      } catch (e: any) {
        console.error(e);
        addNotification({ 
            message: e.response?.data?.detail || 'Failed to load user profile', 
            type: 'error' 
        });
      }
    };
    fetchUser();
  }, [addNotification]);

  const handleProfileUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    const trimmedUsername = trimString(username);
    try {
      await updateUserMe({ username: trimmedUsername });
      addNotification({ message: 'Profile updated successfully', type: 'success' });
      // Update local storage if username changed
      localStorage.setItem('username', trimmedUsername);
      setUsername(trimmedUsername);
    } catch (err: any) {
      addNotification({ message: err.response?.data?.detail || 'Failed to update profile', type: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const handlePasswordUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (passwordData.new_password !== passwordData.confirm_password) {
      addNotification({ message: 'New passwords do not match', type: 'error' });
      return;
    }
    
    setLoading(true);
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
      setLoading(false);
    }
  };

  return (
    <div className="space-y-8">
      {/* Profile Section */}
      <div className="bg-white dark:bg-gray-800/50 rounded-lg p-6 border border-gray-300 dark:border-gray-600">
        <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4 flex items-center gap-2">
          <User className="w-5 h-5 text-blue-600 dark:text-blue-400" />
          Profile Information
        </h3>
        <form
          id="account-profile-form"
          name="account-profile-form"
          onSubmit={handleProfileUpdate}
          className="space-y-4 max-w-md"
          autoComplete="on"
        >
          <div>
            <label htmlFor="account-username" className="block text-sm font-medium contrast-muted mb-1">Username</label>
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
              className="w-full bg-white dark:bg-gray-900 border border-gray-400 dark:border-gray-600 rounded px-3 py-2 focus:outline-none focus:border-blue-500 text-gray-900 dark:text-white"
              required
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded flex items-center gap-2 text-sm font-medium transition-colors disabled:opacity-50"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Save Profile
          </button>
        </form>
      </div>

      {/* Password Section */}
      <div className="bg-white dark:bg-gray-800/50 rounded-lg p-6 border border-gray-200 dark:border-gray-700">
        <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4 flex items-center gap-2">
          <Lock className="w-5 h-5 text-blue-600 dark:text-blue-400" />
          Change Password
        </h3>
        {forcePasswordChange && (
          <p className="mb-4 text-sm text-orange-700 dark:text-orange-300">
            You must choose a new password before continuing to the rest of the application.
          </p>
        )}
        <form
          id="account-password-form"
          name="account-password-form"
          onSubmit={handlePasswordUpdate}
          className="space-y-4 max-w-md"
          autoComplete="on"
        >
          <div>
            <label htmlFor="account-current-password" className="block text-sm font-medium contrast-muted mb-1">Current Password</label>
            <input
              id="account-current-password"
              name="account-current-password"
              type="password"
              autoComplete="section-account-password current-password"
              value={passwordData.current_password}
              onChange={(e) => setPasswordData({...passwordData, current_password: e.target.value})}
              className="w-full bg-white dark:bg-gray-900 border border-gray-400 dark:border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500 text-gray-900 dark:text-white"
              required
            />
          </div>
          <div>
            <label htmlFor="account-new-password" className="block text-sm font-medium contrast-muted mb-1">New Password</label>
            <input
              id="account-new-password"
              name="account-new-password"
              type="password"
              autoComplete="section-account-password new-password"
              value={passwordData.new_password}
              onChange={(e) => setPasswordData({...passwordData, new_password: e.target.value})}
              className="w-full bg-white dark:bg-gray-900 border border-gray-400 dark:border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500 text-gray-900 dark:text-white"
              required
              minLength={8}
            />
          </div>
          <div>
            <label htmlFor="account-confirm-password" className="block text-sm font-medium contrast-muted mb-1">Confirm New Password</label>
            <input
              id="account-confirm-password"
              name="account-confirm-password"
              type="password"
              autoComplete="section-account-password new-password"
              value={passwordData.confirm_password}
              onChange={(e) => setPasswordData({...passwordData, confirm_password: e.target.value})}
              className="w-full bg-white dark:bg-gray-900 border border-gray-400 dark:border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-blue-500 text-gray-900 dark:text-white"
              required
              minLength={8}
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded flex items-center gap-2 text-sm font-medium transition-colors disabled:opacity-50"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Update Password
          </button>
        </form>
      </div>

      {!forcePasswordChange && <CalendarConnectionsSettings />}
    </div>
  );
}
