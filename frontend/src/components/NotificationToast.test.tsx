import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useNotificationStore } from "@/lib/notificationStore";

import NotificationToast from "./NotificationToast";

const add = useNotificationStore.getState().addNotification;
const active = () => useNotificationStore.getState().activeNotifications;

describe("NotificationToast", () => {
  beforeEach(() => {
    useNotificationStore.setState({ activeNotifications: [], history: [] });
  });

  it("draws each toast on the opaque float surface", () => {
    add({ type: "info", message: "Opaque", persistent: true });
    render(<NotificationToast />);

    const toast = screen.getByText("Opaque").closest(".shadow-float");
    expect(toast).toHaveClass("bg-surface-float");
  });

  it("runs an action and then dismisses the notification", () => {
    const onClick = vi.fn();
    add({
      type: "info",
      message: "Update available",
      persistent: true,
      actions: [{ label: "Skip Update", onClick }],
    });
    render(<NotificationToast />);

    fireEvent.click(screen.getByRole("button", { name: "Skip Update" }));

    expect(onClick).toHaveBeenCalledOnce();
    expect(active()).toHaveLength(0);
  });

  it("keeps the notification up for an action that does not dismiss", () => {
    add({
      type: "info",
      message: "Update available",
      persistent: true,
      actions: [{ label: "Go to Releases", onClick: vi.fn(), dismisses: false }],
    });
    render(<NotificationToast />);

    fireEvent.click(screen.getByRole("button", { name: "Go to Releases" }));

    expect(active()).toHaveLength(1);
  });

  it("calls onClose when the close button dismisses it", () => {
    const onClose = vi.fn();
    const onClick = vi.fn();
    add({
      type: "info",
      message: "Update available",
      persistent: true,
      actions: [{ label: "Remind Me", onClick }],
      onClose,
    });
    render(<NotificationToast />);

    fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));

    expect(onClose).toHaveBeenCalledOnce();
    expect(onClick).not.toHaveBeenCalled();
    expect(active()).toHaveLength(0);
  });

  it("does not call onClose when an action dismisses it", () => {
    const onClose = vi.fn();
    add({
      type: "info",
      message: "Update available",
      persistent: true,
      actions: [{ label: "Skip Update", onClick: vi.fn() }],
      onClose,
    });
    render(<NotificationToast />);

    fireEvent.click(screen.getByRole("button", { name: "Skip Update" }));

    expect(onClose).not.toHaveBeenCalled();
  });
});
