import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export interface BackupProgress {
    /** Human-readable phase, e.g. "Compressing audio (12 of 340)". */
    status: string;
    /** Coarse phase name, without the counts. */
    stage?: string | null;
    current?: number | null;
    total?: number | null;
}

interface BackupState {
    taskId: string | null;
    /** Epoch milliseconds the task was started, used to abandon a stale poll. */
    startedAt: number | null;
    /** Latest reported progress, or null when no export is running. */
    progress: BackupProgress | null;
    setTaskId: (id: string | null) => void;
    setProgress: (progress: BackupProgress | null) => void;
}

/**
 * The task id is persisted so a long export survives a page reload. That is also why it
 * needs a start time: Celery reports PENDING for an unknown task id just as it does for
 * a queued one, so a persisted id whose result has expired would otherwise be polled
 * forever with no terminal state to stop on.
 *
 * Progress is deliberately not persisted. It is re-reported within one poll interval of
 * a reload, and a stale figure restored from storage would be worse than none.
 */
export const useBackupStore = create<BackupState>()(
    persist(
        (set) => ({
            taskId: null,
            startedAt: null,
            progress: null,
            setTaskId: (id) =>
                set({
                    taskId: id,
                    startedAt: id ? Date.now() : null,
                    progress: null,
                }),
            setProgress: (progress) => set({ progress }),
        }),
        {
            name: 'nojoin-backup-store',
            partialize: (state) => ({
                taskId: state.taskId,
                startedAt: state.startedAt,
            }),
        }
    )
);
