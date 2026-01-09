import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ViewType = "recordings" | "archived" | "deleted";

interface NavigationState {
  // View State
  currentView: ViewType;
  setCurrentView: (view: ViewType) => void;

  // Tag Filters
  selectedTagIds: number[];
  toggleTagFilter: (tagId: number) => void;
  clearTagFilters: () => void;

  // MainNav Collapse State
  isNavCollapsed: boolean;
  toggleNavCollapse: () => void;
  setNavCollapsed: (collapsed: boolean) => void;
  navWidth: number;
  setNavWidth: (width: number) => void;
  expandedTagIds: number[];
  toggleExpandedTag: (tagId: number) => void;
  setExpandedTagIds: (ids: number[]) => void;

  // Selection State
  selectionMode: boolean;
  selectedRecordingIds: number[];
  setSelectionMode: (enabled: boolean) => void;
  toggleSelectionMode: () => void;
  toggleRecordingSelection: (id: number) => void;
  selectAllRecordings: (ids: number[]) => void;
  clearSelection: () => void;

  // Tour State
  hasSeenTour: Record<number, boolean>;
  setHasSeenTour: (userId: number, seen: boolean) => void;
  hasSeenTranscriptTour: Record<number, boolean>;
  setHasSeenTranscriptTour: (userId: number, seen: boolean) => void;

  // Chat Panel State
  chatPanelHeight: number;
  setChatPanelHeight: (height: number) => void;

  // Log Settings
  logShowTimestamps: boolean;
  toggleLogShowTimestamps: () => void;
  logWordWrap: boolean;
  toggleLogWordWrap: () => void;
}

export const useNavigationStore = create<NavigationState>()(
  persist(
    (set) => ({
      // View State
      currentView: "recordings",
      setCurrentView: (view) => set({ currentView: view, selectedTagIds: [] }),

      // Tag Filters
      selectedTagIds: [],
      toggleTagFilter: (tagId) =>
        set((state) => ({
          selectedTagIds: state.selectedTagIds.includes(tagId)
            ? state.selectedTagIds.filter((id) => id !== tagId)
            : [...state.selectedTagIds, tagId],
        })),
      clearTagFilters: () => set({ selectedTagIds: [] }),

      // MainNav Collapse State
      isNavCollapsed: false,
      toggleNavCollapse: () =>
        set((state) => ({ isNavCollapsed: !state.isNavCollapsed })),
      setNavCollapsed: (collapsed) => set({ isNavCollapsed: collapsed }),
      navWidth: 224,
      setNavWidth: (width) => set({ navWidth: width }),
      expandedTagIds: [],
      toggleExpandedTag: (tagId) =>
        set((state) => ({
          expandedTagIds: state.expandedTagIds.includes(tagId)
            ? state.expandedTagIds.filter((id) => id !== tagId)
            : [...state.expandedTagIds, tagId],
        })),
      setExpandedTagIds: (ids) => set({ expandedTagIds: ids }),

      // Selection State
      selectionMode: false,
      selectedRecordingIds: [],
      setSelectionMode: (enabled) =>
        set({
          selectionMode: enabled,
          selectedRecordingIds: enabled ? [] : [],
        }),
      toggleSelectionMode: () =>
        set((state) => ({
          selectionMode: !state.selectionMode,
          selectedRecordingIds: !state.selectionMode ? [] : [],
        })),
      toggleRecordingSelection: (id) =>
        set((state) => ({
          selectedRecordingIds: state.selectedRecordingIds.includes(id)
            ? state.selectedRecordingIds.filter((rid) => rid !== id)
            : [...state.selectedRecordingIds, id],
        })),
      selectAllRecordings: (ids) => set({ selectedRecordingIds: ids }),
      clearSelection: () => set({ selectedRecordingIds: [] }),

      // Tour State
      hasSeenTour: {},
      setHasSeenTour: (userId, seen) =>
        set((state) => ({
          hasSeenTour: { ...state.hasSeenTour, [userId]: seen },
        })),
      hasSeenTranscriptTour: {},
      setHasSeenTranscriptTour: (userId, seen) =>
        set((state) => ({
          hasSeenTranscriptTour: {
            ...state.hasSeenTranscriptTour,
            [userId]: seen,
          },
        })),

      // Chat Panel State
      chatPanelHeight: 50,
      setChatPanelHeight: (height) => set({ chatPanelHeight: height }),

      // Log Settings
      logShowTimestamps: true,
      toggleLogShowTimestamps: () =>
        set((state) => ({ logShowTimestamps: !state.logShowTimestamps })),
      logWordWrap: true,
      toggleLogWordWrap: () =>
        set((state) => ({ logWordWrap: !state.logWordWrap })),
    }),
    {
      name: "navigation-storage",
      partialize: (state) => ({
        isNavCollapsed: state.isNavCollapsed,
        navWidth: state.navWidth,
        expandedTagIds: state.expandedTagIds,
        hasSeenTour: state.hasSeenTour,
        hasSeenTranscriptTour: state.hasSeenTranscriptTour,
        chatPanelHeight: state.chatPanelHeight,
        logShowTimestamps: state.logShowTimestamps,
        logWordWrap: state.logWordWrap,
      }),
    },
  ),
);
