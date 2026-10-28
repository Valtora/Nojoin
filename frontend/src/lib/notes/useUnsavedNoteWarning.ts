"use client";

import { useEffect } from "react";

import { holdPendingNoteWrite } from "./noteOutboxes";

/**
 * The app's own ways off a page: its links, the main navigation, Settings and
 * any other control marked `data-leaves-page`, such as a page's Back button.
 */
const LEAVING_CONTROLS = "a[href], nav button, #nav-settings, [data-leaves-page]";

export const LEAVE_WITH_UNSAVED_NOTE = "This note has changes that are not saved. Leave without saving them?";

const leavesThisPage = (control: Element) =>
  !(control instanceof HTMLAnchorElement) ||
  (control.target !== "_blank" && control.origin === window.location.origin &&
    control.pathname !== window.location.pathname);

/**
 * While a note editor holds text not saved yet, the page asks before it is
 * closed or reloaded and keeps an ended session in place (`holdPendingNoteWrite`),
 * and it asks before an in-app link or the main navigation takes the user away.
 * The browser's own Back and Forward are not covered: they cannot be held.
 */
export function useUnsavedNoteWarning(unsaved: boolean): void {
  useEffect(() => {
    if (!unsaved) return;
    const release = holdPendingNoteWrite();
    const confirmLeaving = (event: MouseEvent) => {
      // A modified click opens a link elsewhere, or selects, and stays here.
      if (event.ctrlKey || event.metaKey || event.shiftKey) return;
      const control = event.target instanceof Element ? event.target.closest(LEAVING_CONTROLS) : null;
      if (!control || !leavesThisPage(control) || window.confirm(LEAVE_WITH_UNSAVED_NOTE)) return;
      event.preventDefault();
      event.stopPropagation();
    };
    document.addEventListener("click", confirmLeaving, true);
    return () => {
      document.removeEventListener("click", confirmLeaving, true);
      release();
    };
  }, [unsaved]);
}
