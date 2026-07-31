'use client';

import { Trash2, Bell } from 'lucide-react';
import { useNotificationStore } from '@/lib/notificationStore';
import { format } from 'date-fns';

import { Badge, type BadgeTone } from './ui/Badge';
import Button from './ui/Button';
import Modal from './ui/Modal';

interface NotificationHistoryModalProps {
  isOpen: boolean;
  onClose: () => void;
}

/** Notification kinds map onto the same five tones as everything else. */
const TONES: Record<string, BadgeTone> = {
  success: 'success',
  error: 'danger',
  warning: 'warning',
  info: 'info',
};

export default function NotificationHistoryModal({ isOpen, onClose }: NotificationHistoryModalProps) {
  const { history, clearHistory } = useNotificationStore();

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="lg"
      title={
        <span className="flex items-center gap-2">
          <Bell aria-hidden="true" className="w-5 h-5 text-action-text" />
          Notification History
        </span>
      }
      className="max-h-[80dvh]"
    >
      <div className="-mx-5 -mt-4 mb-4 flex items-center justify-between border-b border-surface-divider bg-surface-inset px-5 py-2">
        <span className="text-xs text-contrast-helper">
          {history.length} notifications
        </span>
        <Button
          size="sm"
          variant="danger"
          onClick={clearHistory}
          iconLeft={<Trash2 aria-hidden="true" className="w-3.5 h-3.5" />}
        >
          Clear history
        </Button>
      </div>

      <div className="space-y-3">
        {history.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-contrast-helper">
            <Bell aria-hidden="true" className="w-12 h-12 mb-3 opacity-20" />
            <p>No notifications yet</p>
          </div>
        ) : (
          history.map((notification) => (
            <div
              key={notification.id}
              className="rounded-lg border border-surface-border bg-surface-card p-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1">
                  <div className="mb-1 flex items-center gap-2">
                    <Badge size="sm" tone={TONES[notification.type] ?? 'neutral'} className="uppercase tracking-wider">
                      {notification.type}
                    </Badge>
                    <span className="text-xs text-contrast-helper">
                      {format(notification.timestamp, 'MMM d, h:mm a')}
                    </span>
                  </div>
                  <p className="text-sm text-foreground">
                    {notification.message}
                  </p>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </Modal>
  );
}
