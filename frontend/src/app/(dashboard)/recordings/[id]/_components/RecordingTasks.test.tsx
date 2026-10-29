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
    expect(api.getUserTasks).toHaveBeenCalledWith("all", "rec-planning");
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

  it("disables every checkbox while a save is pending and restores them afterwards", async () => {
    const save = deferred<UserTask>();
    api.getUserTasks.mockResolvedValue([task(), task({ id: 8, title: "Book a room" })]);
    api.updateUserTask.mockReturnValue(save.promise);
    render(<LinkedTasks recordingId="rec-planning" />);
    const first = await screen.findByRole("checkbox", { name: "Mark Send the agenda complete" });
    fireEvent.click(first);

    for (const checkbox of screen.getAllByRole("checkbox")) expect(checkbox).toBeDisabled();
    expect(first).not.toBeChecked();
    await act(async () => save.resolve(task({ completed_at: "2026-10-22T09:00:00Z" })));

    for (const checkbox of screen.getAllByRole("checkbox")) expect(checkbox).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "Mark Send the agenda incomplete" })).toBeChecked();
  });

  it("labels archived tasks and links each title to that task in Tasks", async () => {
    api.getUserTasks.mockResolvedValue([task({ archived_at: "2026-10-22T09:00:00Z" })]);
    render(<LinkedTasks recordingId="rec-planning" />);
    const link = await screen.findByRole("link", { name: "Send the agenda" });

    expect(link).toHaveAttribute("href", "/tasks?task=7");
    expect(link).toHaveAttribute("title", "Open in Tasks");
    expect(within(screen.getByRole("listitem")).getByText("Archived")).toBeInTheDocument();
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
