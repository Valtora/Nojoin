import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fireEvent,
  act,
  renderWithProviders,
  screen,
  waitFor,
} from "@/test/renderWithProviders";
import type { UserTask } from "@/types";

const addNotification = vi.fn();

const getUserTasks = vi.fn();
const createUserTask = vi.fn();
const updateUserTask = vi.fn();
const deleteUserTask = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

vi.mock("@/lib/timezone", async () => {
  const actual = await vi.importActual<typeof import("@/lib/timezone")>(
    "@/lib/timezone",
  );
  return {
    ...actual,
    getUserTimeZone: () => Promise.resolve("UTC"),
  };
});

vi.mock("@/lib/api", () => ({
  getUserTasks: (...args: unknown[]) => getUserTasks(...args),
  createUserTask: (...args: unknown[]) => createUserTask(...args),
  updateUserTask: (...args: unknown[]) => updateUserTask(...args),
  deleteUserTask: (...args: unknown[]) => deleteUserTask(...args),
}));

// The deadline modal renders its own portal/dialog; stub it so the panel's
// own orchestration (which tasks render, open/completed counts, composer) is
// what the tests pin rather than the modal internals.
vi.mock("./ui/TaskDeadlineModal", () => ({
  default: ({ isOpen, taskTitle }: { isOpen: boolean; taskTitle: string }) =>
    isOpen ? <div data-testid="deadline-modal">{taskTitle}</div> : null,
}));

import DashboardTasksPanel from "./DashboardTasksPanel";

function makeTask(overrides: Partial<UserTask> = {}): UserTask {
  return {
    id: 1,
    title: "Task one",
    created_at: "2026-06-01T00:00:00.000Z",
    completed_at: null,
    archived_at: null,
    due_at: null,
    ...overrides,
  } as UserTask;
}

describe("DashboardTasksPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getUserTasks.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows a loading indicator then the open-task count", async () => {
    getUserTasks.mockResolvedValue([
      makeTask({ id: 1, title: "Alpha" }),
      makeTask({ id: 2, title: "Beta" }),
    ]);

    renderWithProviders(<DashboardTasksPanel />);

    expect(screen.getByText("Loading your tasks...")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("2 open")).toBeInTheDocument();
    });
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
  });

  it("renders open and completed tasks in separate sections with counts", async () => {
    getUserTasks.mockResolvedValue([
      makeTask({ id: 1, title: "Open task" }),
      makeTask({
        id: 2,
        title: "Done task",
        completed_at: "2026-06-02T00:00:00.000Z",
      }),
    ]);

    renderWithProviders(<DashboardTasksPanel />);

    await waitFor(() => {
      expect(screen.getByText("1 open")).toBeInTheDocument();
    });
    expect(screen.getByText("1 completed")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("Done task")).toBeInTheDocument();
  });

  it("orders tasks by due date, with overdue tasks first", async () => {
    getUserTasks.mockResolvedValue([
      makeTask({ id: 1, title: "Later", due_at: "2999-01-01T00:00:00.000Z" }),
      makeTask({ id: 2, title: "Overdue", due_at: "2000-01-01T00:00:00.000Z" }),
    ]);

    renderWithProviders(<DashboardTasksPanel />);

    await waitFor(() => {
      expect(screen.getByText("Overdue")).toBeInTheDocument();
    });

    const titles = screen
      .getAllByTitle("Double-click to edit")
      .map((node) => node.textContent);
    expect(titles).toEqual(["Overdue", "Later"]);
  });

  it.each([null, "2026-06-02T00:00:00Z"])(
    "caps task tags in the open and completed sections (completed_at=%s)",
    async (completed_at) => {
      const tags = ["Planning", "Product", "Research", "Review"].map((name, id) => ({
        id, name, created_at: "2026-06-01T00:00:00Z", updated_at: "2026-06-01T00:00:00Z",
      }));
      getUserTasks.mockResolvedValue([makeTask({ tags, completed_at })]);

      renderWithProviders(<DashboardTasksPanel />);

      expect(await screen.findByText("Planning")).toBeInTheDocument();
      expect(screen.getByText("Product")).toBeInTheDocument();
      expect(screen.queryByText("Research")).not.toBeInTheDocument();
      expect(screen.queryByText("Review")).not.toBeInTheDocument();
      expect(screen.getByLabelText("2 more tags: Research, Review")).toHaveTextContent("+2");
    },
  );

  it("shows all tags at the two-chip boundary without an overflow count", async () => {
    getUserTasks.mockResolvedValue([makeTask({
      tags: ["Planning", "Product"].map((name, id) => ({
        id, name, created_at: "2026-06-01T00:00:00Z", updated_at: "2026-06-01T00:00:00Z",
      })),
    })]);

    renderWithProviders(<DashboardTasksPanel />);

    expect(await screen.findByText("Planning")).toBeInTheDocument();
    expect(screen.getByText("Product")).toBeInTheDocument();
    expect(screen.queryByLabelText(/more tags:/)).not.toBeInTheDocument();
  });

  it("refreshes task chips after a tag is renamed or deleted", async () => {
    const tag = { id: 1, name: "Planning", created_at: "2026-06-01T00:00:00Z", updated_at: "2026-06-01T00:00:00Z" };
    getUserTasks.mockResolvedValue([makeTask({ tags: [tag] })]);
    renderWithProviders(<DashboardTasksPanel />);
    await screen.findByText("Planning");

    getUserTasks.mockResolvedValue([makeTask({ tags: [{ ...tag, name: "Research" }] })]);
    act(() => { window.dispatchEvent(new Event("tags-updated")); });
    expect(await screen.findByText("Research")).toBeInTheDocument();
    expect(screen.queryByText("Planning")).not.toBeInTheDocument();

    getUserTasks.mockResolvedValue([makeTask({ tags: [] })]);
    act(() => { window.dispatchEvent(new Event("tags-updated")); });
    await waitFor(() => expect(screen.queryByText("Research")).not.toBeInTheDocument());
  });

  it("creates a task through the composer and notifies success", async () => {
    getUserTasks.mockResolvedValue([]);
    createUserTask.mockResolvedValue(makeTask({ id: 5, title: "Fresh task" }));

    renderWithProviders(<DashboardTasksPanel />);

    await waitFor(() => {
      expect(screen.getByText("0 open")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Add a task..."));

    const input = await screen.findByPlaceholderText(
      "Add a task and press Enter",
    );
    fireEvent.change(input, { target: { value: "Fresh task" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() => {
      expect(createUserTask).toHaveBeenCalledWith({ title: "Fresh task" });
    });
    await waitFor(() => {
      expect(screen.getByText("Fresh task")).toBeInTheDocument();
    });
    expect(addNotification).toHaveBeenCalledWith({
      message: "Task added.",
      type: "success",
    });
  });

  it("toggles a task complete via the API and moves it to completed", async () => {
    getUserTasks.mockResolvedValue([makeTask({ id: 1, title: "Toggle me" })]);
    updateUserTask.mockResolvedValue(
      makeTask({
        id: 1,
        title: "Toggle me",
        completed_at: "2026-06-05T00:00:00.000Z",
      }),
    );

    renderWithProviders(<DashboardTasksPanel />);

    await waitFor(() => {
      expect(screen.getByText("Toggle me")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText("Mark Toggle me complete"));

    await waitFor(() => {
      expect(updateUserTask).toHaveBeenCalledWith(1, { completed: true });
    });
    await waitFor(() => {
      expect(screen.getByText("1 completed")).toBeInTheDocument();
    });
  });
});
