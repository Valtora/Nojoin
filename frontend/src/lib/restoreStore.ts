import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import type { RestoreSkipSummary } from '@/lib/api';

export const RESTORE_STORE_KEY = 'nojoin-restore-store';

export type RestoreOutcomeKind = 'completed' | 'warnings' | 'failed' | 'unknown';

export interface RestoreOutcome {
    kind: RestoreOutcomeKind;
    message: string;
    /** Rows the restore could not bring across, when it finished with warnings. */
    skipped: RestoreSkipSummary | null;
}

interface RestoreState {
    /** True while this tab is sending the archive. Lost with the tab, so not persisted. */
    uploading: boolean;
    uploadPercent: number;
    /** The restore the server is running for this browser. */
    jobId: string | null;
    status: string;
    /** How the last restore ended, kept until it is dismissed. */
    outcome: RestoreOutcome | null;
    beginUpload: () => void;
    setUploadProgress: (percent: number) => void;
    setStatus: (status: string) => void;
    startJob: (jobId: string) => void;
    finish: (outcome: RestoreOutcome) => void;
    dismissOutcome: () => void;
}

/**
 * Restore state lives here rather than in the settings panel, so a restore keeps being
 * followed, and its result reported, after the person leaves the page. The upload runs
 * in the page's JavaScript, which keeps going across in-app navigation; once the server
 * has the archive, the global RestorePoller follows the job.
 */
export const useRestoreStore = create<RestoreState>()(
    persist(
        (set) => ({
            uploading: false,
            uploadPercent: 0,
            jobId: null,
            status: '',
            outcome: null,
            beginUpload: () =>
                set({ uploading: true, uploadPercent: 0, status: '', outcome: null }),
            setUploadProgress: (uploadPercent) => set({ uploadPercent }),
            setStatus: (status) => set({ status }),
            startJob: (jobId) =>
                set({
                    uploading: false,
                    uploadPercent: 100,
                    jobId,
                    status: 'Processing on server...',
                }),
            finish: (outcome) =>
                set({
                    uploading: false,
                    uploadPercent: 0,
                    jobId: null,
                    status: '',
                    outcome,
                }),
            dismissOutcome: () => set({ outcome: null }),
        }),
        {
            name: RESTORE_STORE_KEY,
            partialize: (state) => ({ jobId: state.jobId, outcome: state.outcome }),
        }
    )
);
