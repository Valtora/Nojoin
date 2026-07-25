import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface BackupState {
    taskId: string | null;
    /** Epoch milliseconds the task was started, used to abandon a stale poll. */
    startedAt: number | null;
    setTaskId: (id: string | null) => void;
}

/**
 * The task id is persisted so a long export survives a page reload. That is also why it
 * needs a start time: Celery reports PENDING for an unknown task id just as it does for
 * a queued one, so a persisted id whose result has expired would otherwise be polled
 * forever with no terminal state to stop on.
 */
export const useBackupStore = create<BackupState>()(
    persist(
        (set) => ({
            taskId: null,
            startedAt: null,
            setTaskId: (id) => set({ taskId: id, startedAt: id ? Date.now() : null }),
        }),
        {
            name: 'nojoin-backup-store',
        }
    )
);
