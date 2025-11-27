'use client';

import { useEffect, useState } from 'react';
import { X, CheckCircle, AlertTriangle, Info, AlertCircle } from 'lucide-react';
import { useNotificationStore, Notification, NotificationType } from '@/lib/notificationStore';

const icons = {
  success: <CheckCircle className="w-5 h-5 text-green-500" />,
  error: <AlertCircle className="w-5 h-5 text-red-500" />,
  warning: <AlertTriangle className="w-5 h-5 text-yellow-500" />,
  info: <Info className="w-5 h-5 text-blue-500" />,
};

const bgColors = {
  success: 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-900',
  error: 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-900',
  warning: 'bg-yellow-50 dark:bg-yellow-900/20 border-yellow-200 dark:border-yellow-900',
  info: 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-900',
};

export default function NotificationToast() {
  const { activeNotifications, dismissToast } = useNotificationStore();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-md w-full pointer-events-none">
      {activeNotifications.map((notification) => (
        <div
          key={notification.id}
          className={`
            pointer-events-auto
            flex items-start gap-3 p-4 rounded-lg border shadow-lg transition-all duration-300 animate-in slide-in-from-right
            ${bgColors[notification.type]}
          `}
        >
          <div className="flex-shrink-0 mt-0.5">
            {icons[notification.type]}
          </div>
          <div className="flex-1">
            <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
              {notification.message}
            </p>
          </div>
          <button
            onClick={() => dismissToast(notification.id)}
            className="flex-shrink-0 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      ))}
    </div>
  );
}
