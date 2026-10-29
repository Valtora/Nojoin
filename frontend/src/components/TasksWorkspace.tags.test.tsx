import type { ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useNavigationStore } from "@/lib/store";
import { setCachedUserTimeZone } from "@/lib/timezone";
import type { Tag, UserTask } from "@/types";

const api = vi.hoisted(() => ({
  getUserTasks: vi.fn(), getTags: vi.fn(), getRecordings: vi.fn(),
  createUserTask: vi.fn(), updateUserTask: vi.fn(), deleteUserTask: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("./Workspace", () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

import TasksWorkspace from "./TasksWorkspace";

const tags: Tag[] = ["Planning", "Product", "Unused"].map((name, id) => ({
  id: id + 1, name, created_at: "2026-06-01T00:00:00Z", updated_at: "2026-06-01T00:00:00Z",
}));
const task = (id: number, title: string, taskTags: Tag[], extra: Partial<UserTask> = {}): UserTask => ({
  id, title, tags: taskTags,
  created_at: "2026-06-01T00:00:00Z", updated_at: "2026-06-01T00:00:00Z",
  ...extra,
});
const tasks = [
  task(1, "Plan release", [tags[0]]), task(2, "Review product", [tags[1]]),
  task(3, "Unlabelled task", []),
  task(4, "Finished plan", [tags[0]], { completed_at: "2026-06-02T00:00:00Z" }),
  task(5, "Archived plan", [tags[0]], { archived_at: "2026-06-02T00:00:00Z" }),
];

describe("Tasks tag filters", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useNavigationStore.setState({ selectedTagIds: [] });
    setCachedUserTimeZone("UTC");
    api.getTags.mockResolvedValue(tags);
    api.getRecordings.mockResolvedValue([]);
    // Stand in for the server's any-of tag filter.
    api.getUserTasks.mockImplementation(async (_status: string, tagIds: number[] = []) =>
      tagIds.length === 0 ? tasks : tasks.filter((item) => item.tags?.some((tag) => tagIds.includes(tag.id))),
    );
  });

  it("uses navigation selections, matches any tag, and removes or clears filters", async () => {
    render(<TasksWorkspace />);
    await screen.findByRole("heading", { name: "Unlabelled task" });

    act(() => useNavigationStore.getState().toggleTagFilter(1));
    await screen.findByRole("heading", { name: "Plan release" });
    expect(api.getUserTasks).toHaveBeenLastCalledWith("all", [1]);
    expect(screen.queryByRole("heading", { name: "Review product" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Unlabelled task" })).not.toBeInTheDocument();

    act(() => useNavigationStore.getState().toggleTagFilter(2));
    await screen.findByRole("heading", { name: "Review product" });
    expect(screen.getByRole("heading", { name: "Plan release" })).toBeInTheDocument();
    expect(api.getUserTasks).toHaveBeenLastCalledWith("all", [1, 2]);

    fireEvent.click(screen.getByTitle("Remove Planning filter"));
    await screen.findByRole("heading", { name: "Review product" });
    expect(screen.queryByRole("heading", { name: "Plan release" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear All" }));
    await screen.findByRole("heading", { name: "Unlabelled task" });
    expect(useNavigationStore.getState().selectedTagIds).toEqual([]);
    expect(api.getRecordings).toHaveBeenCalledTimes(1);
  });

  it("retains the filter across status views and explains empty results", async () => {
    useNavigationStore.setState({ selectedTagIds: [1] });
    render(<TasksWorkspace />);
    await screen.findByRole("heading", { name: "Plan release" });

    fireEvent.click(screen.getByRole("button", { name: "completed" }));
    expect(screen.getByRole("heading", { name: "Finished plan" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "archived" }));
    expect(screen.getByRole("heading", { name: "Archived plan" })).toBeInTheDocument();

    act(() => useNavigationStore.setState({ selectedTagIds: [3] }));
    expect(await screen.findByText("No archived tasks match the selected tags.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear All" }));
    expect(await screen.findByRole("heading", { name: "Archived plan" })).toBeInTheDocument();
  });

  it("keeps a task created under an active filter until the next fetch", async () => {
    useNavigationStore.setState({ selectedTagIds: [1] });
    api.createUserTask.mockResolvedValue(task(6, "Untagged follow-up", []));
    render(<TasksWorkspace />);
    await screen.findByRole("heading", { name: "Plan release" });
    fireEvent.click(screen.getByRole("button", { name: "Create Task" }));
    fireEvent.change(screen.getByPlaceholderText("Task title"), { target: { value: "Untagged follow-up" } });
    fireEvent.click(screen.getByRole("button", { name: "Add task" }));

    expect(await screen.findByRole("heading", { name: "Untagged follow-up" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Plan release" })).toBeInTheDocument();
  });

  it("refreshes results after a selected tag is deleted and can clear the filter", async () => {
    useNavigationStore.setState({ selectedTagIds: [1] });
    api.getUserTasks.mockResolvedValueOnce([tasks[0]]).mockResolvedValue([]);
    render(<TasksWorkspace />);
    await screen.findByRole("heading", { name: "Plan release" });

    api.getTags.mockResolvedValue(tags.slice(1));
    act(() => { window.dispatchEvent(new Event("tags-updated")); });

    expect(await screen.findByText("No open tasks match the selected tags.")).toBeInTheDocument();
    expect(api.getUserTasks).toHaveBeenCalledTimes(2);
    api.getUserTasks.mockResolvedValue([task(1, "Plan release", [])]);
    fireEvent.click(screen.getByRole("button", { name: "Clear All" }));
    expect(await screen.findByRole("heading", { name: "Plan release" })).toBeInTheDocument();
  });

  it("keeps the list when a reload after a tag edit fails", async () => {
    render(<TasksWorkspace />);
    await screen.findByRole("heading", { name: "Plan release" });

    api.getUserTasks.mockRejectedValue(new Error("offline"));
    act(() => { window.dispatchEvent(new Event("tags-updated")); });

    await waitFor(() => expect(api.getUserTasks).toHaveBeenCalledTimes(2));
    await act(async () => {});
    expect(screen.getByRole("heading", { name: "Plan release" })).toBeInTheDocument();
    expect(screen.queryByText("Loading tasks...")).not.toBeInTheDocument();
  });

  it("ignores an older response after the selected tags change", async () => {
    const older = Promise.withResolvers<UserTask[]>();
    const newer = Promise.withResolvers<UserTask[]>();
    api.getUserTasks.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    useNavigationStore.setState({ selectedTagIds: [1] });
    render(<TasksWorkspace />);

    act(() => useNavigationStore.setState({ selectedTagIds: [2] }));
    await act(async () => { newer.resolve([tasks[1]]); });
    expect(screen.getByRole("heading", { name: "Review product" })).toBeInTheDocument();

    await act(async () => { older.resolve([tasks[0]]); });
    expect(screen.getByRole("heading", { name: "Review product" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Plan release" })).not.toBeInTheDocument();
  });
});
