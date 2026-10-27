import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useNotificationStore } from "@/lib/notificationStore";
import { setCachedUserTimeZone } from "@/lib/timezone";
import { withProcessTimeZone } from "@/test/processTimeZone";
import type { UserTask } from "@/types";

const api = vi.hoisted(() => ({
  getUserTasks: vi.fn(),
  getTags: vi.fn(),
  getRecordings: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ...api,
  createUserTask: vi.fn(),
  deleteUserTask: vi.fn(),
  updateUserTask: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentPropsWithoutRef<"a">) => <a {...props}>{children}</a>,
}));
vi.mock("./Workspace", () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("./ui/Modal", () => ({
  default: ({ open, children }: { open: boolean; children: ReactNode }) =>
    open ? <div role="dialog">{children}</div> : null,
}));
// TasksWorkspace.deadline.test.tsx covers the deadline picker itself.
vi.mock("./ui/TaskDeadlineModal", () => ({ default: () => null }));

import NotificationHistoryModal from "./NotificationHistoryModal";
import TasksWorkspace from "./TasksWorkspace";

// 02:30 UTC is the evening before in New York and late morning in Tokyo.
const INSTANT = "2026-10-27T02:30:00+00:00";

const task = (overrides: Partial<UserTask> = {}): UserTask => ({
  id: 1,
  title: "Send the agenda to Alice Example",
  created_at: "2026-10-20T09:00:00+00:00",
  updated_at: "2026-10-20T09:00:00+00:00",
  due_at: INSTANT,
  completed_at: null,
  archived_at: null,
  ...overrides,
});

describe.each([
  ["America/New_York", "Asia/Tokyo"],
  ["Asia/Tokyo", "America/New_York"],
])("dates in %s with the time-zone setting in %s", (processZone, settingZone) => {
  withProcessTimeZone(processZone);

  beforeEach(() => {
    vi.clearAllMocks();
    setCachedUserTimeZone(settingZone);
    api.getTags.mockResolvedValue([]);
    api.getRecordings.mockResolvedValue([]);
    useNotificationStore.setState({ activeNotifications: [], history: [] });
  });

  it("shows a task's deadline on the Tasks page in the setting's zone", async () => {
    api.getUserTasks.mockResolvedValue([task()]);
    render(<TasksWorkspace />);

    const expected = new Date(INSTANT).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: settingZone,
    });
    expect(await screen.findByText(expected)).toBeVisible();
  });

  it("dates the notification history in the setting's zone", () => {
    useNotificationStore.setState({
      history: [{ id: "n1", type: "info", message: "Backup ready", timestamp: Date.parse(INSTANT) }],
    });
    render(<NotificationHistoryModal isOpen onClose={vi.fn()} />);

    const expected = settingZone === "Asia/Tokyo" ? "Oct 27, 11:30 AM" : "Oct 26, 10:30 PM";
    expect(screen.getByText(expected)).toBeVisible();
  });

  it("moves an open view to a newly saved setting", async () => {
    api.getUserTasks.mockResolvedValue([task()]);
    render(<TasksWorkspace />);
    const inZone = (timeZone: string) =>
      new Date(INSTANT).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone,
      });
    await screen.findByText(inZone(settingZone));

    act(() => {
      setCachedUserTimeZone("Europe/London");
    });

    expect(screen.getByText(inZone("Europe/London"))).toBeVisible();
  });
});
