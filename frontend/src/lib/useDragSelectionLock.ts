"use client";

import { useEffect } from "react";

const RESIZE_DRAG_CLASS = "resize-dragging";

const clearDocumentSelection = () => {
  window.getSelection()?.removeAllRanges();
};

export function useDragSelectionLock(active: boolean) {
  useEffect(() => {
    if (!active) {
      return;
    }

    const preventSelection = (event: Event) => {
      event.preventDefault();
    };

    clearDocumentSelection();
    document.body.classList.add(RESIZE_DRAG_CLASS);
    document.addEventListener("selectstart", preventSelection);
    document.addEventListener("dragstart", preventSelection);

    return () => {
      document.body.classList.remove(RESIZE_DRAG_CLASS);
      document.removeEventListener("selectstart", preventSelection);
      document.removeEventListener("dragstart", preventSelection);
      clearDocumentSelection();
    };
  }, [active]);
}
