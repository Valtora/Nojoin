import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setCachedUserTimeZone } from "@/lib/timezone";
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
vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentPropsWithoutRef<"a">) => <a {...props}>{children}</a>,
}));
vi.mock("./Workspace", () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

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

beforeEach(() => {
  vi.clearAllMocks();
  setCachedUserTimeZone("UTC");
  api.getUserTasks.mockResolvedValue(tasks);
  api.getTags.mockResolvedValue([]);
  api.getRecordings.mockResolvedValue([]);
});

describe("task links", () => {
  it.each([
    [7, "Completed meeting follow-up"],
    [8, "Archived meeting follow-up"],
  ])("opens task %i in its status group", async (initialTaskId, title) => {
    render(<TasksWorkspace initialTaskId={initialTaskId} />);

    expect(await screen.findByRole("heading", { name: title })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Unrelated open task" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "open" }));
    expect(await screen.findByRole("heading", { name: "Unrelated open task" })).toBeInTheDocument();
  });

  it.each([undefined, 999])("keeps the Open view when target is %s", async (initialTaskId) => {
    render(<TasksWorkspace initialTaskId={initialTaskId} />);

    expect(await screen.findByRole("heading", { name: "Unrelated open task" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Completed meeting follow-up" })).not.toBeInTheDocument();
  });

  it("passes a numeric task query from the server page into navigation", async () => {
    render(await TasksPage({ searchParams: Promise.resolve({ task: "8" }) }));
    expect(await screen.findByRole("heading", { name: "Archived meeting follow-up" })).toBeInTheDocument();
  });

  it.each(["bad", "0"])("ignores invalid task query %s", async (task) => {
    render(await TasksPage({ searchParams: Promise.resolve({ task }) }));
    expect(await screen.findByRole("heading", { name: "Unrelated open task" })).toBeInTheDocument();
  });
});
