import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { RecordingId } from "@/types";

export type ViewType = "recordings" | "archived" | "deleted";
export type ActivePanel =
  | "transcript"
  | "notes"
  | "documents"
  | "analytics"
  // Mobile only, where they are tabs. On desktop they live in the side
  // column's tabs (`SideTab`).
  | "my-notes"
  | "captures"
  | "speakers";

/** The tab open in the top of the recording page's desktop side column. */
export type SideTab = "speakers" | "my-notes" | "captures";

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
  recordingsSidebarWidth: number;
  setRecordingsSidebarWidth: (width: number) => void;
  isRecordingsSidebarCollapsed: boolean;
  toggleRecordingsSidebarCollapse: () => void;
  expandedTagIds: number[];
  toggleExpandedTag: (tagId: number) => void;
  setExpandedTagIds: (ids: number[]) => void;

  // Mobile Nav State
  isMobileNavOpen: boolean;
  setMobileNavOpen: (open: boolean) => void;
  toggleMobileNav: () => void;

  // Selection State: the recordings list's multi-select. The model on top of
  // it, which also keeps it to the rows on screen, is `useRecordingSelection`.
  selectedRecordingIds: RecordingId[];
  /**
   * The row a Shift-click or Shift+Space extends from. Kept by id, so a
   * refresh that moves the row keeps the range starting from it.
   */
  selectionAnchorId: RecordingId | null;
  setRecordingSelection: (ids: RecordingId[], anchorId: RecordingId | null) => void;
  /** Toggles one row and makes it the anchor. */
  toggleRecordingSelection: (id: RecordingId) => void;
  clearSelection: () => void;
  /** Drops rows that are no longer shown, and the anchor with them. */
  pruneRecordingSelection: (shownIds: readonly RecordingId[]) => void;

  // Tour State
  hasSeenTour: Record<number, boolean>;
  setHasSeenTour: (userId: number, seen: boolean) => void;
  hasSeenRecordingsTour: Record<number, boolean>;
  setHasSeenRecordingsTour: (userId: number, seen: boolean) => void;
  hasSeenTranscriptTour: Record<number, boolean>;
  setHasSeenTranscriptTour: (userId: number, seen: boolean) => void;

  // Chat Panel State
  chatPanelHeight: number;
  setChatPanelHeight: (height: number) => void;

  // Recording Detail Side-Panel Collapse State
  isSpeakerPanelCollapsed: boolean;
  setSpeakerPanelCollapsed: (collapsed: boolean) => void;
  isChatPanelCollapsed: boolean;
  setChatPanelCollapsed: (collapsed: boolean) => void;
  sideTab: SideTab;
  setSideTab: (tab: SideTab) => void;

  // Log Settings
  logShowTimestamps: boolean;
  toggleLogShowTimestamps: () => void;
  logWordWrap: boolean;
  toggleLogWordWrap: () => void;

  // Active Panel State (Transcript/Notes/Documents)
  activePanel: ActivePanel;
  setActivePanel: (panel: ActivePanel) => void;
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
      recordingsSidebarWidth: 320,
      setRecordingsSidebarWidth: (width) => set({ recordingsSidebarWidth: width }),
      isRecordingsSidebarCollapsed: false,
      toggleRecordingsSidebarCollapse: () =>
        set((state) => ({
          isRecordingsSidebarCollapsed: !state.isRecordingsSidebarCollapsed,
        })),
      expandedTagIds: [],
      toggleExpandedTag: (tagId) =>
        set((state) => ({
          expandedTagIds: state.expandedTagIds.includes(tagId)
            ? state.expandedTagIds.filter((id) => id !== tagId)
            : [...state.expandedTagIds, tagId],
        })),
      setExpandedTagIds: (ids) => set({ expandedTagIds: ids }),

      // Mobile Nav State
      isMobileNavOpen: false,
      setMobileNavOpen: (open) => set({ isMobileNavOpen: open }),
      toggleMobileNav: () =>
        set((state) => ({ isMobileNavOpen: !state.isMobileNavOpen })),

      // Selection State
      selectedRecordingIds: [],
      selectionAnchorId: null,
      setRecordingSelection: (ids, anchorId) =>
        set({ selectedRecordingIds: ids, selectionAnchorId: anchorId }),
      toggleRecordingSelection: (id) =>
        set((state) => ({
          selectedRecordingIds: state.selectedRecordingIds.includes(id)
            ? state.selectedRecordingIds.filter((rid) => rid !== id)
            : [...state.selectedRecordingIds, id],
          selectionAnchorId: id,
        })),
      clearSelection: () =>
        set({ selectedRecordingIds: [], selectionAnchorId: null }),
      pruneRecordingSelection: (shownIds) =>
        set((state) => {
          const shown = new Set(shownIds);
          const kept = state.selectedRecordingIds.filter((id) => shown.has(id));
          const anchor =
            state.selectionAnchorId !== null && shown.has(state.selectionAnchorId)
              ? state.selectionAnchorId
              : null;
          // Unchanged state is returned as is, so a prune on every list change
          // wakes no subscriber when nothing went.
          if (
            kept.length === state.selectedRecordingIds.length &&
            anchor === state.selectionAnchorId
          ) {
            return state;
          }
          return { selectedRecordingIds: kept, selectionAnchorId: anchor };
        }),

      // Tour State
      hasSeenTour: {},
      setHasSeenTour: (userId, seen) =>
        set((state) => ({
          hasSeenTour: { ...state.hasSeenTour, [userId]: seen },
        })),
      hasSeenRecordingsTour: {},
      setHasSeenRecordingsTour: (userId, seen) =>
        set((state) => ({
          hasSeenRecordingsTour: {
            ...state.hasSeenRecordingsTour,
            [userId]: seen,
          },
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

      // Recording Detail Side-Panel Collapse State
      isSpeakerPanelCollapsed: false,
      setSpeakerPanelCollapsed: (collapsed) =>
        set({ isSpeakerPanelCollapsed: collapsed }),
      isChatPanelCollapsed: false,
      setChatPanelCollapsed: (collapsed) =>
        set({ isChatPanelCollapsed: collapsed }),
      sideTab: "speakers",
      setSideTab: (tab) => set({ sideTab: tab }),

      // Log Settings
      logShowTimestamps: true,
      toggleLogShowTimestamps: () =>
        set((state) => ({ logShowTimestamps: !state.logShowTimestamps })),
      logWordWrap: true,
      toggleLogWordWrap: () =>
        set((state) => ({ logWordWrap: !state.logWordWrap })),

      // Active Panel State
      activePanel: "transcript",
      setActivePanel: (panel) => set({ activePanel: panel }),
    }),
    {
      name: "navigation-storage",
      partialize: (state) => ({
        isNavCollapsed: state.isNavCollapsed,
        navWidth: state.navWidth,
        recordingsSidebarWidth: state.recordingsSidebarWidth,
        isRecordingsSidebarCollapsed: state.isRecordingsSidebarCollapsed,
        expandedTagIds: state.expandedTagIds,
        hasSeenTour: state.hasSeenTour,
        hasSeenRecordingsTour: state.hasSeenRecordingsTour,
        hasSeenTranscriptTour: state.hasSeenTranscriptTour,
        chatPanelHeight: state.chatPanelHeight,
        isSpeakerPanelCollapsed: state.isSpeakerPanelCollapsed,
        isChatPanelCollapsed: state.isChatPanelCollapsed,
        sideTab: state.sideTab,
        logShowTimestamps: state.logShowTimestamps,
        logWordWrap: state.logWordWrap,
        activePanel: state.activePanel,
      }),
    },
  ),
);
