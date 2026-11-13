import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { settingsCategoryHref } from "@/components/settings/settingsCategories";
import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import { useNotificationStore, type NewNotification } from "@/lib/notificationStore";
import type { LiveAiNotice, Recording } from "@/types";

/** Where a recording's shown notice ids are kept, so a reload does not repeat them. */
const shownKey = (recordingId: string) => `nojoin:live-ai-notices:${recordingId}`;

/** The toast's own id, so a notice on screen is not added twice. */
export const liveAiToastId = (notice: LiveAiNotice) => `live-ai-${notice.id}`;

function readShown(recordingId: string): Set<string> {
  try {
    const raw = window.sessionStorage.getItem(shownKey(recordingId));
    const ids: unknown = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(ids) ? ids.filter((id) => typeof id === "string") : []);
  } catch {
    return new Set();
  }
}

function writeShown(recordingId: string, shown: Set<string>) {
  try {
    window.sessionStorage.setItem(shownKey(recordingId), JSON.stringify([...shown]));
  } catch {
    // Storage blocked: the page still shows each notice once while it is open.
  }
}

/** The toast for a notice: transient while retrying, persistent once stopped. */
export function liveAiNotification(
  notice: LiveAiNotice,
  openSettings: (href: string) => void,
): NewNotification {
  const stopped = notice.action === "stopped";
  const settings = notice.fixable_by === "you" ? notice.settings : null;
  return {
    id: liveAiToastId(notice),
    type: stopped ? "error" : "warning",
    message: notice.message,
    persistent: stopped,
    actions:
      stopped && settings
        ? [
            {
              label: "Open Settings",
              variant: "primary",
              onClick: () => openSettings(settingsCategoryHref(settings)),
            },
          ]
        : undefined,
  };
}

/**
 * One toast per live AI notice per recording (#994).
 *
 * A notice keeps its id while the same provider fails the same way, so it is
 * shown once, across reloads of the tab. One that leaves the payload (its
 * provider answered again, or the settings changed) takes its toast with it.
 */
export function useLiveAiNotices(recording: Recording | null | undefined) {
  const router = useRouter();
  const { addNotification, removeActiveNotification } = useNotificationStore();
  const live = isLiveCaptureInProgress(recording);
  const recordingId = recording?.id ?? null;
  const notices = recording?.live_ai_notices;

  useEffect(() => {
    if (!recordingId || !live) return;
    const current = notices ?? [];
    const shown = readShown(recordingId);
    let changed = false;
    for (const notice of current) {
      if (shown.has(notice.id)) continue;
      shown.add(notice.id);
      changed = true;
      addNotification(liveAiNotification(notice, (href) => router.push(href)));
    }
    if (changed) writeShown(recordingId, shown);
    // Each poll brings a new array; a pass over the same ids changes nothing.
    const present = new Set(current.map((notice) => notice.id));
    for (const id of shown) {
      if (!present.has(id)) removeActiveNotification(`live-ai-${id}`);
    }
  }, [addNotification, live, notices, recordingId, removeActiveNotification, router]);
}
