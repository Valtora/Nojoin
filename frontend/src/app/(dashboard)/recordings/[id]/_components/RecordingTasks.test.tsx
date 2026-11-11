import type { ComponentPropsWithoutRef } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useNotificationStore } from "@/lib/notificationStore";
import type { UserTask } from "@/types";

const api = vi.hoisted(() => ({ getUserTasks: vi.fn(), updateUserTask: vi.fn() }));

vi.mock("@/lib/api", () => api);
vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentPropsWithoutRef<"a">) => <a {...props}>{children}</a>,
}));

import { useRecordingTasks } from "../_hooks/useRecordingTasks";
import RecordingTasks from "./RecordingTasks";

function LinkedTasks({ recordingId }: { recordingId?: string }) {
  const { tasks, busyTaskId, toggleTask } = useRecordingTasks(recordingId);
  return <RecordingTasks tasks={tasks} busyTaskId={busyTaskId} onToggle={toggleTask} />;
}

const task = (overrides: Partial<UserTask> = {}): UserTask => ({
  id: 7,
  title: "Send the agenda",
  created_at: "2026-10-20T09:00:00Z",
  updated_at: "2026-10-20T09:00:00Z",
  completed_at: null,
  archived_at: null,
  ...overrides,
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, refuse) => { resolve = accept; reject = refuse; });
  return { promise, resolve, reject };
}

const errorMessages = () => useNotificationStore.getState().activeNotifications
  .filter((notification) => notification.type === "error")
  .map((notification) => notification.message);

describe("recording linked tasks", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    useNotificationStore.setState({ activeNotifications: [], history: [] });
    api.getUserTasks.mockResolvedValue([task()]);
  });

  it("completes and reopens tasks from the saved response, keeping both in the list", async () => {
    api.getUserTasks.mockResolvedValue([
      task(),
      task({ id: 8, title: "Book a room", completed_at: "2026-10-21T09:00:00Z" }),
    ]);
    api.updateUserTask.mockResolvedValueOnce(task({ completed_at: "2026-10-22T09:00:00Z" }))
      .mockResolvedValueOnce(task({ id: 8, title: "Book a room" }));
    render(<LinkedTasks recordingId="rec-planning" />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Mark Send the agenda complete" }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Mark Send the agenda incomplete" }))
      .toBeChecked());
    expect(api.getUserTasks).toHaveBeenCalledWith("all", { recordingId: "rec-planning" });
    expect(api.updateUserTask).toHaveBeenCalledWith(7, { completed: true });

    const completed = screen.getByRole("checkbox", { name: "Mark Book a room incomplete" });
    expect(completed).toBeChecked();
    fireEvent.click(completed);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Mark Book a room complete" }))
      .not.toBeChecked());
    expect(api.updateUserTask).toHaveBeenCalledWith(8, { completed: false });
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("keeps a failed toggle unchecked, reports it, and allows retry", async () => {
    api.updateUserTask.mockRejectedValueOnce({ response: { data: { detail: "Could not save task" } } })
      .mockResolvedValueOnce(task({ completed_at: "2026-10-22T09:00:00Z" }));
    render(<LinkedTasks recordingId="rec-planning" />);
    const checkbox = await screen.findByRole("checkbox", { name: "Mark Send the agenda complete" });
    fireEvent.click(checkbox);

    await waitFor(() => expect(errorMessages()).toEqual(["Could not save task"]));
    expect(checkbox).not.toBeChecked();
    expect(checkbox).toBeEnabled();
    fireEvent.click(checkbox);

    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Mark Send the agenda incomplete" }))
      .toBeChecked());
    expect(api.updateUserTask).toHaveBeenCalledTimes(2);
  });

  it("holds the other checkboxes while a save is pending and keeps focus on the one being saved", async () => {
    const save = deferred<UserTask>();
    api.getUserTasks.mockResolvedValue([task(), task({ id: 8, title: "Book a room" })]);
    api.updateUserTask.mockReturnValue(save.promise);
    render(<LinkedTasks recordingId="rec-planning" />);
    const first = await screen.findByRole("checkbox", { name: "Mark Send the agenda complete" });
    const other = screen.getByRole("checkbox", { name: "Mark Book a room complete" });
    first.focus();
    fireEvent.click(first);

    expect(other).toBeDisabled();
    // A disabled control loses keyboard focus, so the one being saved stays enabled.
    expect(first).toBeEnabled();
    expect(first).toHaveAttribute("aria-busy", "true");
    expect(first).toHaveFocus();
    expect(first).not.toBeChecked();
    fireEvent.click(first);
    expect(api.updateUserTask).toHaveBeenCalledTimes(1);
    await act(async () => save.resolve(task({ completed_at: "2026-10-22T09:00:00Z" })));

    for (const checkbox of screen.getAllByRole("checkbox")) expect(checkbox).toBeEnabled();
    const saved = screen.getByRole("checkbox", { name: "Mark Send the agenda incomplete" });
    expect(saved).toBeChecked();
    expect(saved).toHaveFocus();
    expect(saved).toHaveAttribute("aria-busy", "false");
  });

  it("labels archived tasks and links each title to that task in Tasks", async () => {
    api.getUserTasks.mockResolvedValue([task({ archived_at: "2026-10-22T09:00:00Z" })]);
    render(<LinkedTasks recordingId="rec-planning" />);
    const link = await screen.findByRole("link", { name: "Send the agenda" });

    expect(link).toHaveAttribute("href", "/tasks?task=7");
    expect(link).toHaveAttribute("title", "Open in Tasks");
    expect(within(screen.getByRole("listitem")).getByText("Archived")).toBeInTheDocument();
  });

  it("disables an archived task's checkbox, keeps its state, and describes it as archived", async () => {
    api.getUserTasks.mockResolvedValue([
      task(),
      task({ id: 8, title: "Book a room", completed_at: "2026-10-21T09:00:00Z" }),
      task({ id: 9, title: "Order lunch", completed_at: "2026-10-21T10:00:00Z", archived_at: "2026-10-22T09:00:00Z" }),
      task({ id: 10, title: "Chase the minutes", archived_at: "2026-10-22T09:00:00Z" }),
    ]);
    api.updateUserTask.mockResolvedValueOnce(task({ completed_at: "2026-10-22T09:00:00Z" }));
    render(<LinkedTasks recordingId="rec-planning" />);

    const archivedDone = await screen.findByRole("checkbox", { name: "Mark Order lunch incomplete" });
    const archivedOpen = screen.getByRole("checkbox", { name: "Mark Chase the minutes complete" });
    expect(archivedDone).toBeDisabled();
    expect(archivedDone).toBeChecked();
    expect(archivedDone).toHaveAccessibleDescription("Archived");
    expect(archivedOpen).toBeDisabled();
    expect(archivedOpen).not.toBeChecked();
    expect(archivedOpen).toHaveAccessibleDescription("Archived");
    expect(archivedDone.getAttribute("aria-describedby")).not.toBe(archivedOpen.getAttribute("aria-describedby"));

    fireEvent.click(archivedDone);
    fireEvent.click(archivedOpen);
    // A disabled box cannot take focus, so Space never reaches it.
    archivedOpen.focus();
    expect(archivedOpen).not.toHaveFocus();
    expect(api.updateUserTask).not.toHaveBeenCalled();
    expect(archivedDone).toBeChecked();
    expect(archivedOpen).not.toBeChecked();

    const done = screen.getByRole("checkbox", { name: "Mark Book a room incomplete" });
    expect(done).toBeEnabled();
    expect(done).not.toHaveAttribute("aria-describedby");
    const open = screen.getByRole("checkbox", { name: "Mark Send the agenda complete" });
    expect(open).toBeEnabled();
    expect(open).not.toHaveAttribute("aria-describedby");
    fireEvent.click(open);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Mark Send the agenda incomplete" }))
      .toBeChecked());
    expect(api.updateUserTask).toHaveBeenCalledExactlyOnceWith(7, { completed: true });
    expect(archivedDone).toBeDisabled();
    expect(archivedOpen).toBeDisabled();
  });

  it("never passes an archived task to the toggle", () => {
    const onToggle = vi.fn().mockResolvedValue(undefined);
    render(<RecordingTasks
      tasks={[task(), task({ id: 9, title: "Order lunch", archived_at: "2026-10-22T09:00:00Z" })]}
      busyTaskId={null} onToggle={onToggle} />);

    fireEvent.click(screen.getByRole("checkbox", { name: "Mark Order lunch complete" }));
    expect(onToggle).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Mark Send the agenda complete" }));
    expect(onToggle).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ id: 7 }));
  });

  it("ignores a read that finishes after navigation to another recording", async () => {
    const previous = deferred<UserTask[]>();
    api.getUserTasks.mockReturnValueOnce(previous.promise)
      .mockResolvedValueOnce([task({ id: 8, title: "Book a room" })]);
    const { rerender } = render(<LinkedTasks recordingId="rec-planning" />);
    rerender(<LinkedTasks recordingId="rec-retro" />);
    await screen.findByRole("link", { name: "Book a room" });
    await act(async () => previous.resolve([task()]));

    expect(screen.queryByRole("link", { name: "Send the agenda" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Book a room" })).toBeInTheDocument();
  });

  it("reports a failed read and leaves the recording without unrelated rows", async () => {
    api.getUserTasks.mockRejectedValue(new Error("Tasks temporarily unavailable"));
    render(<LinkedTasks recordingId="rec-planning" />);

    await waitFor(() => expect(errorMessages()).toEqual(["Tasks temporarily unavailable"]));
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });
});
