import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RecordingFilters } from "@/lib/api";
import { setCachedUserTimeZone } from "@/lib/timezone";
import { RecordingStatus, type Recording, type UserTask } from "@/types";

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({ attentionRecordings: [] }),
}));

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
// TasksWorkspace.deadline.test.tsx covers the deadline picker.
vi.mock("./ui/TaskDeadlineModal", () => ({ default: () => null }));

import TasksWorkspace from "./TasksWorkspace";

/** A made-up library of 300 meetings, newest first, a day apart. */
const library: Recording[] = Array.from({ length: 300 }, (_, n) => ({
  id: `rec-${n}`,
  name: n === 299 ? "Budget review with Alice Example" : `Weekly sync ${n}`,
  created_at: new Date(Date.UTC(2026, 10, 2, 12) - n * 86_400_000).toISOString().replace("Z", ""),
  status: RecordingStatus.PROCESSED,
  is_archived: false,
  is_deleted: false,
})) as Recording[];
const oldest = library[299];

const task = (overrides: Partial<UserTask> = {}): UserTask => ({
  id: 7,
  title: "Send the figures to Bob Example",
  created_at: "2026-10-20T09:00:00Z",
  updated_at: "2026-10-20T09:00:00Z",
  due_at: null,
  completed_at: null,
  archived_at: null,
  tags: [],
  linked_recordings: [],
  ...overrides,
});

const pickerSearch = () => screen.getByRole("combobox", { name: "Search recordings to link" });

describe("linking recordings to a task (issue #906)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setCachedUserTimeZone("UTC");
    api.getTags.mockResolvedValue([]);
    api.getRecordings.mockImplementation(async (filters: RecordingFilters = {}) => {
      const q = filters.q?.toLowerCase();
      const skip = filters.skip ?? 0;
      return library
        .filter((recording) => !q || recording.name.toLowerCase().includes(q))
        .slice(skip, skip + (filters.limit ?? 100));
    });
  });

  it("asks for no recordings until a picker opens", async () => {
    api.getUserTasks.mockResolvedValue([task()]);
    render(<TasksWorkspace />);

    await screen.findByText("Send the figures to Bob Example");
    expect(api.getRecordings).not.toHaveBeenCalled();
  });

  it("links a new task to the oldest recording, found by search", async () => {
    api.getUserTasks.mockResolvedValue([]);
    api.createUserTask.mockImplementation(async (payload: { title: string }) =>
      task({ id: 8, title: payload.title }),
    );
    render(<TasksWorkspace />);

    fireEvent.click(await screen.findByRole("button", { name: "Create Task" }));
    fireEvent.change(screen.getByPlaceholderText("Task title"), {
      target: { value: "Follow up on the budget" },
    });
    await screen.findByRole("option", { name: /Weekly sync 0/ });
    expect(screen.queryByRole("option", { name: /Budget review/ })).not.toBeInTheDocument();

    fireEvent.change(pickerSearch(), { target: { value: "budget review" } });
    fireEvent.click(await screen.findByRole("option", { name: /Budget review with Alice Example/ }));
    expect(screen.getByRole("list", { name: "Linked recordings" })).toHaveTextContent(oldest.name);

    fireEvent.click(screen.getByRole("button", { name: "Add task" }));

    await waitFor(() =>
      expect(api.createUserTask).toHaveBeenCalledWith(
        expect.objectContaining({ title: "Follow up on the budget", recording_ids: [oldest.id] }),
      ),
    );
    expect(api.getRecordings).toHaveBeenCalledWith(
      expect.objectContaining({ include_archived: true, q: "budget review" }),
    );
  });

  it("shows a linked recording the picker has not loaded, and saves without it once unlinked", async () => {
    const linked = {
      id: oldest.id,
      name: oldest.name,
      created_at: oldest.created_at,
      status: RecordingStatus.PROCESSED,
      is_archived: false,
      is_deleted: false,
    };
    api.getUserTasks.mockResolvedValue([task({ linked_recordings: [linked] })]);
    api.updateUserTask.mockImplementation(async () => task());
    render(<TasksWorkspace />);

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await screen.findByRole("option", { name: /Weekly sync 0/ });

    expect(screen.getByRole("list", { name: "Linked recordings" })).toHaveTextContent(oldest.name);
    expect(
      within(screen.getByRole("listbox")).queryByRole("option", { name: /Budget review/ }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: `Unlink ${oldest.name}` }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(api.updateUserTask).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ recording_ids: [] }),
      ),
    );
  });

  it("keeps a linked recording on save when it was never in the results", async () => {
    const linked = {
      id: oldest.id,
      name: oldest.name,
      created_at: oldest.created_at,
      status: RecordingStatus.PROCESSED,
      is_archived: false,
      is_deleted: false,
    };
    api.getUserTasks.mockResolvedValue([task({ linked_recordings: [linked] })]);
    api.updateUserTask.mockImplementation(async () => task({ linked_recordings: [linked] }));
    render(<TasksWorkspace />);

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.click(await screen.findByRole("option", { name: /^Weekly sync 3,/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(api.updateUserTask).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ recording_ids: [oldest.id, "rec-3"] }),
      ),
    );
  });
});
