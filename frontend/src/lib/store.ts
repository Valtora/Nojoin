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
    }),
    {
      name: 'nojoin-navigation',
      partialize: (state) => ({ 
        isNavCollapsed: state.isNavCollapsed 
      }),
    }
  )
);
