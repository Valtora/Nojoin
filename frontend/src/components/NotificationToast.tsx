'use client';

import { useEffect, useState } from 'react';
import { X, CheckCircle, AlertTriangle, Info, AlertCircle } from 'lucide-react';
import { useNotificationStore } from '@/lib/notificationStore';

const icons = {
  success: <CheckCircle className="w-5 h-5 text-status-success-fg" />,
  error: <AlertCircle className="w-5 h-5 text-status-danger-fg" />,
  warning: <AlertTriangle className="w-5 h-5 text-status-warning-fg" />,
  info: <Info className="w-5 h-5 text-status-info-fg" />,
};

const bgColors = {
  success: 'bg-status-success-bg border-status-success-border',
  error: 'bg-status-danger-bg border-status-danger-border',
  warning: 'bg-status-warning-bg border-status-warning-border',
  info: 'bg-status-info-bg border-status-info-border',
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
            flex items-start gap-3 p-4 rounded-lg border shadow-lg
            ${bgColors[notification.type]}
          `}
        >
          <div className="flex-shrink-0 mt-0.5">
            {icons[notification.type]}
          </div>
          <div className="flex-1">
            <p className="text-sm font-medium text-foreground">
              {notification.message}
            </p>
          </div>
          <button
            onClick={() => dismissToast(notification.id)}
            className="flex-shrink-0 p-1 text-contrast-helper hover:text-contrast-muted transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      ))}
    </div>
  );
}
