'use client';

import { X, Trash2, Bell } from 'lucide-react';
import { useNotificationStore } from '@/lib/notificationStore';
import { format } from 'date-fns';

interface NotificationHistoryModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function NotificationHistoryModal({ isOpen, onClose }: NotificationHistoryModalProps) {
  const { history, clearHistory } = useNotificationStore();

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-2xl max-h-[80vh] flex flex-col border border-gray-200 dark:border-gray-800 m-4">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-800">
          <div className="flex items-center gap-2">
            <Bell className="w-5 h-5 text-orange-600" />
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              Notification History
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 rounded-lg transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Toolbar */}
        <div className="flex items-center justify-between px-4 py-2 bg-gray-50 dark:bg-gray-800/50 border-b border-gray-200 dark:border-gray-800">
          <span className="text-xs text-gray-500 dark:text-gray-400">
            {history.length} notifications
          </span>
          <div className="flex gap-2">
            <button
              onClick={clearHistory}
              className="flex items-center gap-1.5 px-2 py-1 text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Clear history
            </button>
          </div>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {history.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-gray-500 dark:text-gray-400">
              <Bell className="w-12 h-12 mb-3 opacity-20" />
              <p>No notifications yet</p>
            </div>
          ) : (
            history.map((notification) => (
              <div
                key={notification.id}
                className="p-3 rounded-lg border transition-colors bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-800"
              >
                <div className="flex justify-between items-start gap-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`
                        text-xs font-medium px-1.5 py-0.5 rounded uppercase tracking-wider
                        ${notification.type === 'success' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : ''}
                        ${notification.type === 'error' ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' : ''}
                        ${notification.type === 'warning' ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400' : ''}
                        ${notification.type === 'info' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' : ''}
                      `}>
                        {notification.type}
                      </span>
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        {format(notification.timestamp, 'MMM d, h:mm a')}
                      </span>
                    </div>
                    <p className="text-sm text-gray-900 dark:text-gray-100">
                      {notification.message}
                    </p>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
