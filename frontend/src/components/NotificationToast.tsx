'use client';

import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { useHydrated } from '@/lib/useHydrated';
import { X, CheckCircle, AlertTriangle, Info, AlertCircle } from 'lucide-react';
import {
  useNotificationStore,
  type Notification,
  type NotificationAction,
} from '@/lib/notificationStore';

import Button from './ui/Button';
import IconButton from './ui/IconButton';

const icons = {
  success: <CheckCircle className="w-5 h-5 text-status-success-fg" />,
  error: <AlertCircle className="w-5 h-5 text-status-danger-fg" />,
  warning: <AlertTriangle className="w-5 h-5 text-status-warning-fg" />,
  info: <Info className="w-5 h-5 text-status-info-fg" />,
};

const borders = {
  success: 'border-status-success-border',
  error: 'border-status-danger-border',
  warning: 'border-status-warning-border',
  info: 'border-status-info-border',
};

const tints = {
  success: 'bg-status-success-bg',
  error: 'bg-status-danger-bg',
  warning: 'bg-status-warning-bg',
  info: 'bg-status-info-bg',
};

export default function NotificationToast() {
  const { activeNotifications, dismissToast } = useNotificationStore();
  const mounted = useHydrated();

  if (!mounted) return null;

  const runAction = (notification: Notification, action: NotificationAction) => {
    action.onClick();
    if (action.dismisses !== false) {
      dismissToast(notification.id);
    }
  };

  const close = (notification: Notification) => {
    notification.onClose?.();
    dismissToast(notification.id);
  };

  return (
    <div
      aria-live="polite"
      className="fixed bottom-4 left-4 right-4 z-[var(--z-toast)] flex flex-col gap-2 pointer-events-none sm:left-auto sm:w-full sm:max-w-md"
    >
      {activeNotifications.map((notification) => (
        // The status tints are translucent in dark mode, so the tint sits on
        // an opaque float surface rather than being the fill: a toast must
        // stay readable over whatever it covers.
        <SurfaceBase kind="float"
          key={notification.id}
          role={notification.type === 'error' ? 'alert' : undefined}
          className={`pointer-events-auto overflow-hidden rounded-lg border bg-surface-float shadow-float ${borders[notification.type]}`}
        >
          <div className={`flex items-start gap-3 p-4 ${tints[notification.type]}`}>
            <div className="flex-shrink-0 mt-0.5">
              {icons[notification.type]}
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground">
                {notification.message}
              </p>
              {notification.actions && notification.actions.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {notification.actions.map((action) => (
                    <Button
                      key={action.label}
                      size="sm"
                      variant={action.variant ?? 'secondary'}
                      onClick={() => runAction(notification, action)}
                    >
                      {action.label}
                    </Button>
                  ))}
                </div>
              )}
            </div>
            <IconButton
              aria-label="Dismiss notification"
              size="sm"
              icon={<X />}
              onClick={() => close(notification)}
              className="-mr-2 -mt-2 flex-shrink-0"
            />
          </div>
        </SurfaceBase>
      ))}
    </div>
  );
}
