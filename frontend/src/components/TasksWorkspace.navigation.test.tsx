import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { UserTask } from "@/types";

const api = vi.hoisted(() => ({
  getUserTasks: vi.fn(),
  getTags: vi.fn(),
  getRecordings: vi.fn(),
  createUserTask: vi.fn(),
  updateUserTask: vi.fn(),
  deleteUserTask: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("@/lib/useUserTimeZone", () => ({ useUserTimeZone: () => "UTC" }));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentPropsWithoutRef<"a">) => <a {...props}>{children}</a>,
}));
vi.mock("./Workspace", () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("./ui/TaskDeadlineModal", () => ({ default: () => null }));

import TasksWorkspace from "./TasksWorkspace";
import TasksPage from "@/app/(dashboard)/tasks/page";

const makeTask = (id: number, title: string, overrides: Partial<UserTask> = {}): UserTask => ({
  id,
  title,
  created_at: "2026-10-20T09:00:00Z",
  updated_at: "2026-10-20T09:00:00Z",
  ...overrides,
});

const tasks = [
  makeTask(1, "Unrelated open task"),
  makeTask(7, "Completed meeting follow-up", { completed_at: "2026-10-21T09:00:00Z" }),
  makeTask(8, "Archived meeting follow-up", {
    completed_at: "2026-10-21T09:00:00Z",
    archived_at: "2026-10-22T09:00:00Z",
  }),
];

const scrollIntoView = vi.fn();
const originalScrollIntoView = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");

beforeEach(() => {
  vi.clearAllMocks();
  api.getUserTasks.mockResolvedValue(tasks);
  api.getTags.mockResolvedValue([]);
  api.getRecordings.mockResolvedValue([]);
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: scrollIntoView,
  });
});

afterEach(() => {
  if (originalScrollIntoView) {
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", originalScrollIntoView);
  } else {
    Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
  }
});

describe("task links", () => {
  it.each([
    [7, "Completed meeting follow-up"],
    [8, "Archived meeting follow-up"],
  ])("reveals and focuses task %i in its status group", async (initialTaskId, title) => {
    render(<TasksWorkspace initialTaskId={initialTaskId} />);

    const article = (await screen.findByRole("heading", { name: title })).closest("article");
    await waitFor(() => expect(article).toHaveFocus());
    expect(article).toHaveAttribute("id", `task-${initialTaskId}`);
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "center" });
    expect(screen.queryByRole("heading", { name: "Unrelated open task" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "open" }));
    expect(await screen.findByRole("heading", { name: "Unrelated open task" })).toBeInTheDocument();
  });

  it("follows another task link while the workspace stays mounted", async () => {
    const { rerender } = render(<TasksWorkspace initialTaskId={7} />);
    await screen.findByRole("heading", { name: "Completed meeting follow-up" });

    rerender(<TasksWorkspace initialTaskId={8} />);

    const target = (await screen.findByRole("heading", { name: "Archived meeting follow-up" })).closest("article");
    await waitFor(() => expect(target).toHaveFocus());
    expect(screen.queryByRole("heading", { name: "Completed meeting follow-up" })).not.toBeInTheDocument();
  });

  it.each([undefined, 999])("keeps the Open view when target is %s", async (initialTaskId) => {
    render(<TasksWorkspace initialTaskId={initialTaskId} />);

    expect(await screen.findByRole("heading", { name: "Unrelated open task" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Completed meeting follow-up" })).not.toBeInTheDocument();
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("focuses the target when scrollIntoView is unavailable", async () => {
    Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
    render(<TasksWorkspace initialTaskId={7} />);

    const article = (await screen.findByRole("heading", { name: "Completed meeting follow-up" })).closest("article");
    await waitFor(() => expect(article).toHaveFocus());
  });

  it("passes a numeric task query from the server page into navigation", async () => {
    render(await TasksPage({ searchParams: Promise.resolve({ task: "8" }) }));
    expect(await screen.findByRole("heading", { name: "Archived meeting follow-up" })).toBeInTheDocument();
  });

  it.each(["bad", "-7", "7.5", "0", "9007199254740992", ["7", "8"]])(
    "ignores invalid task query %s",
    async (task) => {
      render(await TasksPage({ searchParams: Promise.resolve({ task }) }));
      expect(await screen.findByRole("heading", { name: "Unrelated open task" })).toBeInTheDocument();
    },
  );
});
