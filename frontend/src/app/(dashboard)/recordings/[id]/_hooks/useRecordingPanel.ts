import { useCallback, useEffect, useRef, useState } from "react";

import { useNavigationStore, type ActivePanel, type SideTab } from "@/lib/store";
import { isSideTab, isTourPanel, TOUR_RESTORE_PANEL_EVENT, TOUR_SWITCH_PANEL_EVENT } from "@/lib/tour-config";
import { useOnDepsChange } from "@/lib/useOnDepsChange";

/**
 * The panel the recording page shows. It is the one last chosen, which every
 * recording opens on, except that a live capture opens on its transcript: its
 * notes come only after processing (#834). That holds for the capture until a
 * panel is chosen on it, so the panel last chosen is still the one a finished
 * meeting opens on.
 */
export function useRecordingPanel(
  recordingId: string | null,
  capturing: boolean,
  rememberedPanel: ActivePanel,
  rememberPanel: (panel: ActivePanel) => void,
  isMobile = false,
) {
  const [transcriptFirstId, setTranscriptFirstId] = useState(capturing ? recordingId : null);
  useOnDepsChange([recordingId, capturing], () => {
    if (capturing) setTranscriptFirstId(recordingId);
  });
  const activePanel: ActivePanel =
    recordingId !== null && transcriptFirstId === recordingId ? "transcript" : rememberedPanel;
  const setActivePanel = useCallback((panel: ActivePanel) => {
    setTranscriptFirstId(null);
    rememberPanel(panel);
  }, [rememberPanel]);

  useTourPanels(activePanel, setActivePanel, isMobile);
  return { activePanel, setActivePanel };
}

/**
 * Opens each panel the transcript tour asks for, and puts back the panels the
 * tour found once it ends, or when the page goes while it runs. On desktop
 * Speakers, My notes, Captures and Meeting Edge open in the side column, so
 * its tab is put back as well as the main one.
 */
function useTourPanels(
  activePanel: ActivePanel,
  setActivePanel: (panel: ActivePanel) => void,
  isMobile: boolean,
) {
  const setSideTab = useNavigationStore((state) => state.setSideTab);
  const latestRef = useRef({ activePanel, isMobile });
  useEffect(() => {
    latestRef.current = { activePanel, isMobile };
  }, [activePanel, isMobile]);

  useEffect(() => {
    let found: { panel: ActivePanel; sideTab: SideTab } | null = null;
    const restore = () => {
      if (found === null) return;
      const { panel, sideTab } = found;
      found = null;
      setActivePanel(panel);
      setSideTab(sideTab);
    };
    const show = (event: Event) => {
      const panel = (event as CustomEvent<unknown>).detail;
      if (!isTourPanel(panel)) return;
      found ??= { panel: latestRef.current.activePanel, sideTab: useNavigationStore.getState().sideTab };
      if (isSideTab(panel) && !latestRef.current.isMobile) setSideTab(panel);
      else setActivePanel(panel);
    };
    window.addEventListener(TOUR_SWITCH_PANEL_EVENT, show);
    window.addEventListener(TOUR_RESTORE_PANEL_EVENT, restore);
    return () => {
      window.removeEventListener(TOUR_SWITCH_PANEL_EVENT, show);
      window.removeEventListener(TOUR_RESTORE_PANEL_EVENT, restore);
      // The page can go before the tour's own end reaches it.
      restore();
    };
  }, [setActivePanel, setSideTab]);
}
