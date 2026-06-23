"use client";

import { useEffect, useState } from "react";

import { useDragSelectionLock } from "@/lib/useDragSelectionLock";

export interface UseSidebarResizeOptions {
  minWidth: number;
  maxWidth: number;
  setWidth: (width: number) => void;
}

export interface SidebarResize {
  isResizing: boolean;
  onResizePointerDown: (e: React.PointerEvent<HTMLDivElement>) => void;
}

/**
 * Owns the draggable sidebar resize behaviour for {@link Sidebar} (FE-012):
 * the resizing flag, the pointer move/up listeners constrained to the desktop
 * layout and width bounds, and the drag-selection lock. Lifted verbatim so the
 * resize math and event wiring are unchanged.
 */
export function useSidebarResize(
  options: UseSidebarResizeOptions,
): SidebarResize {
  const { minWidth, maxWidth, setWidth } = options;
  const [isResizing, setIsResizing] = useState(false);

  useEffect(() => {
    if (!isResizing) return;

    const handlePointerMove = (e: PointerEvent) => {
      // Don't resize on mobile
      if (window.innerWidth < 1024) return;

      const sidebarElement = document.getElementById("sidebar-recordings-list");
      if (!sidebarElement) return;

      const sidebarRect = sidebarElement.getBoundingClientRect();
      // Calculate width relative to the left edge of the sidebar
      const newWidth = e.clientX - sidebarRect.left;

      if (newWidth >= minWidth && newWidth <= maxWidth) {
        setWidth(newWidth);
      }
    };

    const handlePointerUp = () => {
      setIsResizing(false);
    };

    document.addEventListener("pointermove", handlePointerMove);
    document.addEventListener("pointerup", handlePointerUp);
    document.addEventListener("pointercancel", handlePointerUp);

    return () => {
      document.removeEventListener("pointermove", handlePointerMove);
      document.removeEventListener("pointerup", handlePointerUp);
      document.removeEventListener("pointercancel", handlePointerUp);
    };
  }, [isResizing, setWidth, minWidth, maxWidth]);

  const onResizePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    window.getSelection()?.removeAllRanges();
    setIsResizing(true);
  };

  useDragSelectionLock(isResizing);

  return { isResizing, onResizePointerDown };
}
