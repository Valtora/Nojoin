import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import type { BackupExport } from '@/lib/api';

export const BACKUP_STORE_KEY = 'nojoin-backup-store';

export interface BackupProgress {
    /** Human-readable phase, e.g. "Compressing audio (12 of 340)". */
    status: string;
    /** Coarse phase name, without the counts. */
    stage?: string | null;
    current?: number | null;
    total?: number | null;
}

interface BackupState {
    /** The backup build this browser is waiting for. */
    taskId: string | null;
    /** Latest reported progress, or null when no export is running. */
    progress: BackupProgress | null;
    /** A finished backup this browser asked for, until it is downloaded or dismissed. */
    readyExport: BackupExport | null;
    setTaskId: (id: string | null) => void;
    setProgress: (progress: BackupProgress | null) => void;
    setReadyExport: (readyExport: BackupExport | null) => void;
}

/**
 * The task id and the ready backup are persisted, so a long export survives a reload
 * and its notification is still waiting when the person comes back hours later. The
 * server decides when a task is lost, so no start time is kept here.
 *
 * Progress is not persisted. It is re-reported within one poll interval of a reload,
 * and a stale figure restored from storage would be worse than none.
 */
export const useBackupStore = create<BackupState>()(
    persist(
        (set) => ({
            taskId: null,
            progress: null,
            readyExport: null,
            setTaskId: (id) => set({ taskId: id, progress: null }),
            setProgress: (progress) => set({ progress }),
            setReadyExport: (readyExport) => set({ readyExport }),
        }),
        {
            name: BACKUP_STORE_KEY,
            partialize: (state) => ({
                taskId: state.taskId,
                readyExport: state.readyExport,
            }),
        }
    )
);
