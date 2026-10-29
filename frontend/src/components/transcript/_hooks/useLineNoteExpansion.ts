"use client";

import { useState } from "react";

/** Explicit line choices outlive overlap-group splits and pending-note changes. */
export function useLineNoteExpansion() {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const toggle = (lineId: string) => setExpanded((previous) => {
    const next = new Set(previous);
    if (next.has(lineId)) next.delete(lineId);
    else next.add(lineId);
    return next;
  });
  return { isExpanded: (lineId: string) => expanded.has(lineId), toggle };
}
