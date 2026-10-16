import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { NewNotification } from "@/lib/notificationStore";
import type { UpdateNotice as UpdateNoticeData } from "@/types";

import UpdateNotice from "./UpdateNotice";

const addNotification = vi.fn();
const getUpdateNotice = vi.fn();
const dismissUpdateNotice = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: (selector: (state: unknown) => unknown) =>
    selector({ addNotification }),
}));

vi.mock("@/lib/api", () => ({
  getUpdateNotice: (...args: unknown[]) => getUpdateNotice(...args),
  dismissUpdateNotice: (...args: unknown[]) => dismissUpdateNotice(...args),
}));

const notice = (overrides: Partial<UpdateNoticeData> = {}): UpdateNoticeData => ({
  show: true,
  current_version: "2.9.0",
  latest_version: "2.10.0",
  release_url: "https://github.com/Valtora/Nojoin/releases/tag/v2.10.0",
  ...overrides,
});

async function shownNotification(): Promise<NewNotification> {
  render(<UpdateNotice />);
  await waitFor(() => expect(addNotification).toHaveBeenCalled());
  return addNotification.mock.calls[0][0] as NewNotification;
}

const action = (notification: NewNotification, label: string) => {
  const found = notification.actions?.find((candidate) => candidate.label === label);
  if (!found) throw new Error(`No ${label} action`);
  return found;
};

describe("UpdateNotice", () => {
  beforeEach(() => {
    addNotification.mockReset();
    getUpdateNotice.mockReset();
    dismissUpdateNotice.mockReset().mockResolvedValue(undefined);
  });

  it("shows a persistent notification with the three choices", async () => {
    getUpdateNotice.mockResolvedValue(notice());

    const notification = await shownNotification();

    expect(notification).toMatchObject({
      id: "update-available-2.10.0",
      type: "info",
      persistent: true,
    });
    expect(notification.message).toContain("2.10.0");
    expect(notification.message).toContain("2.9.0");
    expect(notification.actions?.map((candidate) => candidate.label)).toEqual([
      "Go to Releases",
      "Skip Update",
      "Remind Me",
    ]);
  });

  it("opens the latest release and leaves the notification up", async () => {
    getUpdateNotice.mockResolvedValue(notice());
    const open = vi.spyOn(window, "open").mockReturnValue(null);

    const releases = action(await shownNotification(), "Go to Releases");
    releases.onClick();

    expect(releases.dismisses).toBe(false);
    expect(open).toHaveBeenCalledWith(
      "https://github.com/Valtora/Nojoin/releases/tag/v2.10.0",
      "_blank",
      "noopener,noreferrer",
    );
    expect(dismissUpdateNotice).not.toHaveBeenCalled();
    open.mockRestore();
  });

  it("skips this release", async () => {
    getUpdateNotice.mockResolvedValue(notice());

    action(await shownNotification(), "Skip Update").onClick();

    expect(dismissUpdateNotice).toHaveBeenCalledWith("skip", "2.10.0");
  });

  it("treats Remind Me and the close button alike", async () => {
    getUpdateNotice.mockResolvedValue(notice());
    const notification = await shownNotification();

    action(notification, "Remind Me").onClick();
    notification.onClose?.();

    expect(dismissUpdateNotice).toHaveBeenNthCalledWith(1, "remind", "2.10.0");
    expect(dismissUpdateNotice).toHaveBeenNthCalledWith(2, "remind", "2.10.0");
  });

  it("reports a choice the server did not save", async () => {
    getUpdateNotice.mockResolvedValue(notice());
    dismissUpdateNotice.mockRejectedValue(new Error("offline"));

    action(await shownNotification(), "Skip Update").onClick();

    await waitFor(() =>
      expect(addNotification).toHaveBeenLastCalledWith(
        expect.objectContaining({ type: "error" }),
      ),
    );
  });

  it("shows nothing when the server says not to", async () => {
    getUpdateNotice.mockResolvedValue(notice({ show: false }));

    render(<UpdateNotice />);

    await waitFor(() => expect(getUpdateNotice).toHaveBeenCalled());
    await Promise.resolve();
    expect(addNotification).not.toHaveBeenCalled();
  });
});
