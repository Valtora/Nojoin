import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface BackupState {
    taskId: string | null;
    setTaskId: (id: string | null) => void;
}

export const useBackupStore = create<BackupState>()(
    persist(
        (set) => ({
            taskId: null,
            setTaskId: (id) => set({ taskId: id }),
        }),
        {
            name: 'nojoin-backup-store',
        }
    )
);
