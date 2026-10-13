import { create } from "zustand";

interface ImportAudioState {
  isImportAudioOpen: boolean;
  openImportAudio: () => void;
  closeImportAudio: () => void;
}

/**
 * Open state of the single Import Audio dialog. Every entry point calls
 * `openImportAudio`, and `ImportAudioHost`, mounted once in the dashboard
 * layout, renders the dialog. Not persisted: a reload starts closed.
 */
export const useImportAudioStore = create<ImportAudioState>()((set) => ({
  isImportAudioOpen: false,
  openImportAudio: () => set({ isImportAudioOpen: true }),
  closeImportAudio: () => set({ isImportAudioOpen: false }),
}));
