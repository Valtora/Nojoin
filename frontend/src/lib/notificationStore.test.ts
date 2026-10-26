import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TRANSIENT_NOTIFICATION_MS, useNotificationStore } from "./notificationStore";

const active = () => useNotificationStore.getState().activeNotifications;
const history = () => useNotificationStore.getState().history;
const add = useNotificationStore.getState().addNotification;

describe("notificationStore", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useNotificationStore.setState({ activeNotifications: [], history: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("removes a transient notification after its timeout", () => {
    add({ type: "success", message: "Saved" });

    vi.advanceTimersByTime(TRANSIENT_NOTIFICATION_MS - 1);
    expect(active()).toHaveLength(1);

    vi.advanceTimersByTime(1);
    expect(active()).toHaveLength(0);
  });

  it("keeps a notification with its own duration for that long", () => {
    add({ type: "success", message: "Archived 2 recordings.", durationMs: 10000 });

    vi.advanceTimersByTime(9999);
    expect(active()).toHaveLength(1);

    vi.advanceTimersByTime(1);
    expect(active()).toHaveLength(0);
  });

  it("keeps a persistent notification until it is dismissed", () => {
    const id = add({ type: "info", message: "Update available", persistent: true });

    vi.advanceTimersByTime(TRANSIENT_NOTIFICATION_MS * 10);
    expect(active()).toHaveLength(1);

    useNotificationStore.getState().dismissToast(id);
    expect(active()).toHaveLength(0);
  });

  it("does not stack a notification whose id is already on screen", () => {
    const first = add({ id: "update-2.10.0", type: "info", message: "A", persistent: true });
    const second = add({ id: "update-2.10.0", type: "info", message: "A", persistent: true });

    expect(second).toBe(first);
    expect(active()).toHaveLength(1);
    expect(history()).toHaveLength(1);
  });

  it("gives a re-added transient its own full timeout", () => {
    const id = add({ id: "saved", type: "success", message: "Saved" });
    vi.advanceTimersByTime(TRANSIENT_NOTIFICATION_MS - 1000);
    useNotificationStore.getState().dismissToast(id);
    add({ id: "saved", type: "success", message: "Saved" });

    // The first instance's timer fires here and must leave the second alone.
    vi.advanceTimersByTime(1000);
    expect(active()).toHaveLength(1);

    vi.advanceTimersByTime(TRANSIENT_NOTIFICATION_MS);
    expect(active()).toHaveLength(0);
  });

  it("records history without handlers, under ids of its own", () => {
    const onClick = vi.fn();
    add({
      id: "update-2.10.0",
      type: "info",
      message: "Update available",
      persistent: true,
      actions: [{ label: "Skip Update", onClick }],
      onClose: vi.fn(),
    });
    useNotificationStore.getState().dismissToast("update-2.10.0");
    add({ id: "update-2.10.0", type: "info", message: "Update available", persistent: true });

    const [latest, earlier] = history();
    expect(latest).not.toHaveProperty("actions");
    expect(latest).not.toHaveProperty("onClose");
    expect(latest.id).not.toBe(earlier.id);
    expect(latest.message).toBe("Update available");
  });
});
