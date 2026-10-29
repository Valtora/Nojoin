"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

const SpeakerTurnHighlight = createContext<{
  labels: readonly string[];
  toggle: (labels: readonly string[]) => void;
}>({ labels: [], toggle: () => {} });

/** One recording's panel and transcript share a transient speaker selection. */
export function SpeakerTurnHighlightProvider({ children }: { children: ReactNode }) {
  const [labels, setLabels] = useState<readonly string[]>([]);
  const toggle = useCallback((next: readonly string[]) => setLabels((current) =>
    current.length === next.length && next.every((label) => current.includes(label)) ? [] : next), []);
  const value = useMemo(() => ({ labels, toggle }), [labels, toggle]);
  return <SpeakerTurnHighlight.Provider value={value}>{children}</SpeakerTurnHighlight.Provider>;
}

export const useSpeakerTurnHighlight = () => useContext(SpeakerTurnHighlight);
