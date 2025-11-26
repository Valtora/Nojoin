import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type ViewType = 'recordings' | 'archived' | 'deleted';

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

  // Selection State
  selectionMode: boolean;
  selectedRecordingIds: number[];
  setSelectionMode: (enabled: boolean) => void;
  toggleSelectionMode: () => void;
  toggleRecordingSelection: (id: number) => void;
  selectAllRecordings: (ids: number[]) => void;
  clearSelection: () => void;
}

export const useNavigationStore = create<NavigationState>()(
  persist(
    (set) => ({
      // View State
      currentView: 'recordings',
      setCurrentView: (view) => set({ currentView: view, selectedTagIds: [] }),
      
      // Tag Filters
      selectedTagIds: [],
      toggleTagFilter: (tagId) => set((state) => ({
        selectedTagIds: state.selectedTagIds.includes(tagId)
          ? state.selectedTagIds.filter(id => id !== tagId)
          : [...state.selectedTagIds, tagId]
      })),
      clearTagFilters: () => set({ selectedTagIds: [] }),
      
      // MainNav Collapse State
      isNavCollapsed: false,
      toggleNavCollapse: () => set((state) => ({ isNavCollapsed: !state.isNavCollapsed })),
      setNavCollapsed: (collapsed) => set({ isNavCollapsed: collapsed }),

      // Selection State
      selectionMode: false,
      selectedRecordingIds: [],
      setSelectionMode: (enabled) => set({ selectionMode: enabled }),
      toggleSelectionMode: () => set((state) => ({ 
        selectionMode: !state.selectionMode,
        selectedRecordingIds: [] // Clear selection when toggling mode
      })),
      toggleRecordingSelection: (id) => set((state) => {
        const newSelectedIds = state.selectedRecordingIds.includes(id)
          ? state.selectedRecordingIds.filter(rid => rid !== id)
          : [...state.selectedRecordingIds, id];
        
        return {
          selectedRecordingIds: newSelectedIds,
          selectionMode: newSelectedIds.length > 0
        };
      }),
      selectAllRecordings: (ids) => set({ selectedRecordingIds: ids, selectionMode: ids.length > 0 }),
      clearSelection: () => set({ selectedRecordingIds: [], selectionMode: false }),
    }),
    {
      name: 'nojoin-navigation',
      partialize: (state) => ({ 
        isNavCollapsed: state.isNavCollapsed 
      }),
    }
  )
);
