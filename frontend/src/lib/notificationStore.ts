import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type NotificationType = 'success' | 'error' | 'warning' | 'info';

export interface Notification {
  id: string;
  type: NotificationType;
  message: string;
  timestamp: number;
  read: boolean;
  persistent?: boolean; // If true, won't auto-dismiss from toast
}

interface NotificationState {
  activeNotifications: Notification[];
  history: Notification[];
  unreadCount: number;
  
  addNotification: (notification: Omit<Notification, 'id' | 'timestamp' | 'read'>) => string;
  dismissToast: (id: string) => void;
  markAsRead: (id: string) => void;
  markAllAsRead: () => void;
  clearHistory: () => void;
  removeActiveNotification: (id: string) => void;
}

export const useNotificationStore = create<NotificationState>()(
  persist(
    (set, get) => ({
      activeNotifications: [],
      history: [],
      unreadCount: 0,

      addNotification: (notification) => {
        const id = Math.random().toString(36).substring(7);
        const newNotification: Notification = {
          ...notification,
          id,
          timestamp: Date.now(),
          read: false,
        };

        set((state) => ({
          activeNotifications: [...state.activeNotifications, newNotification],
          history: [newNotification, ...state.history].slice(0, 100), // Keep last 100
          unreadCount: state.unreadCount + 1,
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

      markAsRead: (id) => {
        set((state) => {
          const notification = state.history.find((n) => n.id === id);
          if (notification && !notification.read) {
            return {
              history: state.history.map((n) =>
                n.id === id ? { ...n, read: true } : n
              ),
              unreadCount: Math.max(0, state.unreadCount - 1),
            };
          }
          return state;
        });
      },

      markAllAsRead: () => {
        set((state) => ({
          history: state.history.map((n) => ({ ...n, read: true })),
          unreadCount: 0,
        }));
      },

      clearHistory: () => {
        set({ history: [], unreadCount: 0 });
      },
    }),
    {
      name: 'nojoin-notifications',
      partialize: (state) => ({
        history: state.history,
        unreadCount: state.unreadCount,
      }),
    }
  )
);
