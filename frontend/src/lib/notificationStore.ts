import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type NotificationType = 'success' | 'error' | 'warning' | 'info';

export interface Notification {
  id: string;
  type: NotificationType;
  message: string;
  timestamp: number;
  persistent?: boolean; // If true, won't auto-dismiss from toast
}

interface NotificationState {
  activeNotifications: Notification[];
  history: Notification[];
  
  addNotification: (notification: Omit<Notification, 'id' | 'timestamp'>) => string;
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
        const id = Math.random().toString(36).substring(7);
        const newNotification: Notification = {
          ...notification,
          id,
          timestamp: Date.now(),
        };

        set((state) => ({
          activeNotifications: [...state.activeNotifications, newNotification],
          history: [newNotification, ...state.history].slice(0, 100), // Keep last 100
        }));

        // Auto-dismiss non-persistent notifications
        if (!notification.persistent) {
          setTimeout(() => {
            get().dismissToast(id);
          }, 5000);
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
