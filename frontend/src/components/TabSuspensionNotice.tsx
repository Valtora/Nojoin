"use client";

import { Check, Copy, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

const DISMISSED_STORAGE_KEY = "nojoin.capture.tabSuspensionNoticeDismissed";

/**
 * Whether the notice has been dismissed on this browser.
 *
 * Deliberately per-browser rather than per-user. What it asks the reader to do
 * is add this site to a Chrome setting, so a person who has done it on their
 * desktop has not done it on their laptop, and a preference that roamed would
 * hide the notice exactly where it still applies.
 */
export const readTabSuspensionNoticeDismissed = (): boolean => {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    return window.localStorage.getItem(DISMISSED_STORAGE_KEY) === "true";
  } catch {
    // Storage can be blocked outright. Showing the notice again is a better
    // failure than suppressing it forever.
    return false;
  }
};

export const writeTabSuspensionNoticeDismissed = () => {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(DISMISSED_STORAGE_KEY, "true");
  } catch {
    // Nothing to do: the notice reappears next time, which is survivable.
  }
};

/**
 * Tells the reader to exempt Nojoin from Chrome's tab suspension, before a
 * meeting rather than after one has lost audio.
 *
 * This is guidance because it cannot be anything else. No web API lets a page
 * opt out of Memory Saver, and none lets it read whether Memory Saver is on, so
 * Nojoin can neither fix this itself nor detect that it needs fixing. Chrome
 * does exempt tabs actively capturing audio or video, which Nojoin is while
 * recording, so this is a belt-and-braces instruction rather than a certainty
 * that suspension would otherwise happen.
 */
export default function TabSuspensionNotice() {
  const [dismissed, setDismissed] = useState(true);
  const [copied, setCopied] = useState(false);
  const [origin, setOrigin] = useState("");

  // Read after mount. The server has no localStorage and no origin to show, and
  // rendering the notice during hydration would flash it at people who already
  // dismissed it.
  useEffect(() => {
    setDismissed(readTabSuspensionNoticeDismissed());
    setOrigin(window.location.host);
  }, []);

  const handleDismiss = useCallback(() => {
    writeTabSuspensionNoticeDismissed();
    setDismissed(true);
  }, []);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(origin);
      setCopied(true);
      setTimeout(() => setCopied(false), 2_000);
    } catch {
      // Clipboard access is refused in plenty of configurations; the address is
      // on screen to be typed either way.
    }
  }, [origin]);

  if (dismissed) {
    return null;
  }

  return (
    <div
      className="rounded-2xl border border-status-info-border bg-status-info-bg px-4 py-3 text-sm text-status-info-fg"
      role="note"
    >
      <div className="flex items-start gap-2">
        <div className="flex-1">
          <p className="font-medium">Keep this tab awake during meetings</p>
          <p className="mt-1 leading-5 opacity-90">
            If Chrome suspends the Nojoin tab mid-meeting, the audio for that
            stretch is not recorded and cannot be recovered. Add Nojoin to{" "}
            <span className="font-medium">
              Settings &gt; Performance &gt; Memory Saver &gt; Always keep these
              sites active
            </span>
            .
          </p>
          {origin ? (
            <div className="mt-2 flex items-center gap-2">
              <code className="rounded bg-status-info-border/40 px-2 py-1 text-xs">
                {origin}
              </code>
              <button
                type="button"
                onClick={handleCopy}
                className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-medium transition-colors hover:bg-status-info-border/40"
                aria-label="Copy the Nojoin address"
              >
                {copied ? (
                  <>
                    <Check className="h-3 w-3" /> Copied
                  </>
                ) : (
                  <>
                    <Copy className="h-3 w-3" /> Copy
                  </>
                )}
              </button>
            </div>
          ) : null}
        </div>
        <button
          type="button"
          onClick={handleDismiss}
          className="-mr-1 -mt-1 shrink-0 rounded p-1 text-status-info-fg/70 transition-colors hover:bg-status-info-border/40 hover:text-status-info-fg"
          aria-label="Dismiss the tab suspension notice"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
