"use client";

import { useEffect, useRef } from "react";

import { dismissUpdateNotice, getUpdateNotice } from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import type { UpdateNoticeAction } from "@/types";

const RELEASES_URL = "https://github.com/Valtora/Nojoin/releases/latest";

/**
 * Tells an owner or admin, once per page load, that a newer release exists.
 *
 * The server decides whether to show it, so the choices hold across tabs and
 * devices:
 *   Go to Releases -> opens the release, leaving the notification up
 *   Skip Update    -> never shown again for this release; the next one shows it
 *   Remind Me / x  -> hidden for the rest of this login, so a refresh does not
 *                     bring it back but signing in again does
 */
export default function UpdateNotice() {
  const addNotification = useNotificationStore((state) => state.addNotification);
  const checkedRef = useRef(false);

  useEffect(() => {
    if (checkedRef.current) {
      return;
    }
    checkedRef.current = true;

    void getUpdateNotice()
      .then((notice) => {
        const version = notice.latest_version;
        if (!notice.show || !version) {
          return;
        }

        const dismiss = (action: UpdateNoticeAction) => {
          void dismissUpdateNotice(action, version).catch(() => {
            addNotification({
              type: "error",
              message: "Could not save that choice, so the update notification will return.",
            });
          });
        };

        addNotification({
          id: `update-available-${version}`,
          type: "info",
          persistent: true,
          message: `Nojoin ${version} is available. This server is running ${notice.current_version}.`,
          actions: [
            {
              label: "Go to Releases",
              variant: "primary",
              dismisses: false,
              onClick: () => {
                window.open(notice.release_url ?? RELEASES_URL, "_blank", "noopener,noreferrer");
              },
            },
            { label: "Skip Update", onClick: () => dismiss("skip") },
            { label: "Remind Me", onClick: () => dismiss("remind") },
          ],
          onClose: () => dismiss("remind"),
        });
      })
      .catch(() => {
        // The check is informational: without it there is simply no notice.
      });
  }, [addNotification]);

  return null;
}
