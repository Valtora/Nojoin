import { useCallback, useEffect, useRef, useState } from "react";

import type { ActivePanel } from "@/lib/store";
import { isTourPanel, TOUR_RESTORE_PANEL_EVENT, TOUR_SWITCH_PANEL_EVENT } from "@/lib/tour-config";
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

  useTourPanels(activePanel, setActivePanel);
  return { activePanel, setActivePanel };
}

/**
 * Opens each panel the transcript tour asks for, and puts back the panel the
 * tour found once it ends, or when the page goes while it runs.
 */
function useTourPanels(activePanel: ActivePanel, setActivePanel: (panel: ActivePanel) => void) {
  const activePanelRef = useRef(activePanel);
  useEffect(() => {
    activePanelRef.current = activePanel;
  }, [activePanel]);

  useEffect(() => {
    let found: ActivePanel | null = null;
    const restore = () => {
      if (found === null) return;
      const panel = found;
      found = null;
      setActivePanel(panel);
    };
    const show = (event: Event) => {
      const panel = (event as CustomEvent<unknown>).detail;
      if (!isTourPanel(panel)) return;
      found ??= activePanelRef.current;
      setActivePanel(panel);
    };
    window.addEventListener(TOUR_SWITCH_PANEL_EVENT, show);
    window.addEventListener(TOUR_RESTORE_PANEL_EVENT, restore);
    return () => {
      window.removeEventListener(TOUR_SWITCH_PANEL_EVENT, show);
      window.removeEventListener(TOUR_RESTORE_PANEL_EVENT, restore);
      // The page can go before the tour's own end reaches it.
      restore();
    };
  }, [setActivePanel]);
}
