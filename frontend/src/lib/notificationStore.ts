import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type NotificationType = 'success' | 'error' | 'warning' | 'info';

/** How long a transient notification stays on screen. */
export const TRANSIENT_NOTIFICATION_MS = 5000;

export interface NotificationAction {
  label: string;
  onClick: () => void;
  /** Primary for the action the notification is asking for; secondary otherwise. */
  variant?: 'primary' | 'secondary';
  /** Remove the notification once the action runs. Defaults to true. */
  dismisses?: boolean;
}

/**
 * Notifications come in two kinds:
 *
 * - Transient (the default) disappear after TRANSIENT_NOTIFICATION_MS, or
 *   sooner from their close button.
 * - Persistent (`persistent: true`) stay until dismissed. They usually carry
 *   `actions`, at least one of which dismisses them, beside the close button.
 */
export interface Notification {
  id: string;
  type: NotificationType;
  message: string;
  timestamp: number;
  persistent?: boolean;
  actions?: NotificationAction[];
  /** Runs when the close button dismisses the notification, not when an action does. */
  onClose?: () => void;
}

export type NewNotification = Omit<Notification, 'id' | 'timestamp'> & {
  /** A stable id. While a notification with this id is on screen, adding it again does nothing. */
  id?: string;
};

/** What the history keeps: the record, without the handlers that only work while it is on screen. */
export type NotificationRecord = Omit<Notification, 'actions' | 'onClose'>;

const newId = () => Math.random().toString(36).substring(7);

// History entries get their own id: a notification with a stable id can be
// shown, and so recorded, more than once.
function toRecord({ type, message, timestamp, persistent }: Notification): NotificationRecord {
  return { id: newId(), type, message, timestamp, persistent };
}

interface NotificationState {
  activeNotifications: Notification[];
  history: NotificationRecord[];

  addNotification: (notification: NewNotification) => string;
  dismissToast: (id: string) => void;
  clearHistory: () => void;
  removeActiveNotification: (id: string) => void;
}

export const useNotificationStore = create<NotificationState>()(
  persist(
    (set, get) => ({
      activeNotifications: [],
      history: [],

      addNotification: (notification) => {
        const id = notification.id ?? newId();
        if (get().activeNotifications.some((active) => active.id === id)) {
          return id;
        }

        const newNotification: Notification = {
          ...notification,
          id,
          timestamp: Date.now(),
        };

        set((state) => ({
          activeNotifications: [...state.activeNotifications, newNotification],
          history: [toRecord(newNotification), ...state.history].slice(0, 100), // Keep last 100
        }));

        if (!notification.persistent) {
          // Removes this instance only, so a reused id added again after an
          // early close keeps its own full timeout.
          setTimeout(() => {
            set((state) => ({
              activeNotifications: state.activeNotifications.filter((n) => n !== newNotification),
            }));
          }, TRANSIENT_NOTIFICATION_MS);
        }

        return id;
      },

      dismissToast: (id) => {
        set((state) => ({
          activeNotifications: state.activeNotifications.filter((n) => n.id !== id),
        }));
      },

      removeActiveNotification: (id) => {
          set((state) => ({
              activeNotifications: state.activeNotifications.filter((n) => n.id !== id),
          }));
      },

      clearHistory: () => {
        set({ history: [] });
      },
    }),
    {
      name: 'nojoin-notifications',
      partialize: (state) => ({
        history: state.history,
      }),
    }
  )
);
