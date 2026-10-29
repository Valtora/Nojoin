import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNotificationStore } from "@/lib/notificationStore";
import { setCachedUserTimeZone } from "@/lib/timezone";
import { withProcessTimeZone } from "@/test/processTimeZone";
import { RecordingStatus, type UserTask } from "@/types";

const api = vi.hoisted(() => ({
  getUserTasks: vi.fn(),
  getTags: vi.fn(),
  getRecordings: vi.fn(),
  createUserTask: vi.fn(),
  updateUserTask: vi.fn(),
  deleteUserTask: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentPropsWithoutRef<"a">) => <a {...props}>{children}</a>,
}));
vi.mock("./Workspace", () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
// The real deadline modal, inside a plain dialog rather than a portal.
vi.mock("@/components/ui/Modal", () => ({
  default: ({
    open,
    title,
    footer,
    children,
  }: {
    open: boolean;
    title?: ReactNode;
    footer?: ReactNode;
    children: ReactNode;
  }) =>
    open ? (
      <div role="dialog">
        <div data-testid="modal-title">{title}</div>
        {children}
        {footer}
      </div>
    ) : null,
}));

import TasksWorkspace from "./TasksWorkspace";

// 02:30 UTC on 27 October: 11:30 on the 27th in Tokyo, 22:30 on the 26th in New York.
const SAVED = "2026-10-27T02:30:00Z";

const task = (overrides: Partial<UserTask> = {}): UserTask => ({
  id: 7,
  title: "Send the agenda to Alice Example",
  created_at: "2026-10-20T09:00:00Z",
  updated_at: "2026-10-20T09:00:00Z",
  due_at: SAVED,
  completed_at: null,
  archived_at: null,
  ...overrides,
});

describe("Tasks page deadline picker in New York with the setting in Tokyo", () => {
  withProcessTimeZone("America/New_York");

  beforeEach(() => {
    vi.clearAllMocks();
    // 16:00 UTC on the 27th is 01:00 on the 28th in Tokyo, so "today" and
    // "tomorrow" differ between Tokyo, UTC and New York.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-27T16:00:00Z"));
    setCachedUserTimeZone("Asia/Tokyo");
    api.getUserTasks.mockResolvedValue([task()]);
    api.getTags.mockResolvedValue([]);
    api.getRecordings.mockResolvedValue([]);
    api.updateUserTask.mockImplementation(async (_id: number, payload: { due_at: string }) =>
      task({ due_at: payload.due_at }),
    );
    api.createUserTask.mockImplementation(async (payload: { due_at: string }) =>
      task({ id: 8, due_at: payload.due_at }),
    );
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const openEditPicker = async () => {
    render(<TasksWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: /^Edit deadline, / }));
    return screen.getByRole("dialog");
  };

  it("shows the saved deadline at the setting's wall clock and names the zone", async () => {
    const dialog = await openEditPicker();

    expect(within(dialog).getByTestId("modal-title")).toHaveTextContent(
      "Tuesday, 27 October at 11:30 AM",
    );
    expect(dialog).toHaveTextContent("Asia/Tokyo");
  });

  it("saves an untouched deadline unchanged", async () => {
    const dialog = await openEditPicker();
    expect(within(dialog).getByRole("button", { name: "Update deadline" })).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.updateUserTask).toHaveBeenCalled());
    expect(api.updateUserTask.mock.calls[0][1].due_at).toBe("2026-10-27T02:30:00.000Z");
  });

  it("saves Tomorrow as the next day in the setting, keeping 11:30 there", async () => {
    const dialog = await openEditPicker();
    fireEvent.click(within(dialog).getByRole("button", { name: "Tomorrow" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Update deadline" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.updateUserTask).toHaveBeenCalled());
    // 11:30 on 29 October in Tokyo.
    expect(api.updateUserTask.mock.calls[0][1].due_at).toBe("2026-10-29T02:30:00.000Z");
  });

  it("saves a new task's 9 AM preset as 09:00 today in the setting", async () => {
    render(<TasksWorkspace />);
    await screen.findByRole("button", { name: "Edit" });
    fireEvent.click(screen.getByRole("button", { name: "Create Task" }));
    fireEvent.change(screen.getByPlaceholderText("Task title"), {
      target: { value: "Book a room for Bob Example" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Set deadline" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "9 AM" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Set deadline" }));
    fireEvent.click(screen.getByRole("button", { name: "Add task" }));

    await waitFor(() => expect(api.createUserTask).toHaveBeenCalled());
    // 09:00 on 28 October in Tokyo, today there, is 00:00 UTC on the 28th.
    expect(api.createUserTask.mock.calls[0][0].due_at).toBe("2026-10-28T00:00:00.000Z");
  });

  it("keeps a pick when a notification arrives while the picker is open", async () => {
    useNotificationStore.setState({ activeNotifications: [], history: [] });
    const dialog = await openEditPicker();
    fireEvent.click(within(dialog).getByRole("button", { name: "Tomorrow" }));
    const title = within(dialog).getByTestId("modal-title");
    expect(title).toHaveTextContent("Thursday, 29 October at 11:30 AM");

    act(() => {
      useNotificationStore.getState().addNotification({ type: "info", message: "Backup ready" });
    });

    expect(title).toHaveTextContent("Thursday, 29 October at 11:30 AM");
  });

  it("refreshes linked recording titles and gists without replacing an unsaved task draft", async () => {
    vi.useRealTimers();
    vi.useFakeTimers();
    const linked = {
      id: "rec-1", name: "Untitled meeting", gist: null,
      status: RecordingStatus.UPLOADING, created_at: SAVED,
      is_archived: false, is_deleted: false,
    };
    api.getUserTasks.mockResolvedValue([task({ linked_recordings: [linked] })]);
    api.getRecordings.mockResolvedValue([linked]);
    const { unmount } = render(<TasksWorkspace />);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByRole("link", { name: "Untitled meeting" })).not.toHaveAttribute("title");
    fireEvent.click(screen.getByRole("button", { name: "Create Task" }));
    fireEvent.change(screen.getByPlaceholderText("Task title"), { target: { value: "My unsaved task" } });
    api.getRecordings.mockResolvedValue([{
      ...linked, name: "Launch review", gist: "Agreeing the launch date.", status: RecordingStatus.PROCESSED,
    }]);
    await act(() => vi.advanceTimersByTimeAsync(15000));
    expect(screen.getByRole("link", { name: "Launch review" })).toHaveAttribute("title", "Agreeing the launch date.");
    expect(screen.getByPlaceholderText("Task title")).toHaveValue("My unsaved task");
    expect(api.getUserTasks).toHaveBeenCalledTimes(1);
    api.getRecordings.mockRejectedValue(new Error("offline"));
    await act(() => vi.advanceTimersByTimeAsync(15000));
    expect(screen.getByRole("link", { name: "Launch review" })).toHaveAttribute("title", "Agreeing the launch date.");
    unmount();
    const reads = api.getRecordings.mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(15000));
    expect(api.getRecordings).toHaveBeenCalledTimes(reads);
  });
});
