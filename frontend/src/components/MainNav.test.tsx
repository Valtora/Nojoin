import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fireEvent,
  renderWithProviders,
  screen,
  waitFor,
  within,
} from "@/test/renderWithProviders";
import type { Tag } from "@/types";

const routerPush = vi.fn();
const toggleTagFilter = vi.fn();
const toggleExpandedTag = vi.fn();
const setExpandedTagIds = vi.fn();

const getTags = vi.fn();
const updateTag = vi.fn();
const deleteTag = vi.fn();
const createTag = vi.fn();
const logout = vi.fn();

let pathname = "/";
let expandedTagIdsArray: number[] = [];

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
  usePathname: () => pathname,
}));

vi.mock("next/image", () => ({
  default: ({ alt }: { alt: string }) => <span data-testid="logo">{alt}</span>,
}));

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({ pausedRecording: null, runtimeActive: false }),
}));

vi.mock("@/lib/store", () => ({
  useNavigationStore: () => ({
    currentView: "recordings",
    setCurrentView: vi.fn(),
    selectedTagIds: [],
    toggleTagFilter,
    isNavCollapsed: false,
    toggleNavCollapse: vi.fn(),
    navWidth: 256,
    setNavWidth: vi.fn(),
    expandedTagIds: expandedTagIdsArray,
    toggleExpandedTag,
    setExpandedTagIds,
    isMobileNavOpen: false,
    setMobileNavOpen: vi.fn(),
  }),
}));

vi.mock("@/lib/api", () => ({
  getTags: (...args: unknown[]) => getTags(...args),
  updateTag: (...args: unknown[]) => updateTag(...args),
  deleteTag: (...args: unknown[]) => deleteTag(...args),
  createTag: (...args: unknown[]) => createTag(...args),
  logout: (...args: unknown[]) => logout(...args),
}));

// Heavy modals/menus are stubbed so the tests pin the nav's own
// orchestration (which items render, routing, tag tree) rather than the
// modal/menu internals.
vi.mock("./ImportAudioModal", () => ({ default: () => null }));
vi.mock("./ConfirmationModal", () => ({
  default: ({ isOpen, title }: { isOpen: boolean; title: string }) =>
    isOpen ? <div data-testid="confirm-modal">{title}</div> : null,
}));
vi.mock("./CreateTagModal", () => ({ default: () => null }));
vi.mock("./NotificationHistoryModal", () => ({ default: () => null }));
vi.mock("./ContextMenu", () => ({ default: () => null }));

import MainNav from "./MainNav";

function makeTag(overrides: Partial<Tag> = {}): Tag {
  return {
    id: 1,
    name: "Tag",
    color: "orange",
    parent_id: undefined,
    ...overrides,
  } as Tag;
}

describe("MainNav", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    pathname = "/";
    expandedTagIdsArray = [];
    getTags.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the primary navigation items", async () => {
    renderWithProviders(<MainNav />);

    await waitFor(() => {
      expect(getTags).toHaveBeenCalled();
    });
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    expect(screen.getByText("Tasks")).toBeInTheDocument();
    expect(screen.getByText("People")).toBeInTheDocument();
    expect(screen.getByText("Recordings")).toBeInTheDocument();
    expect(screen.getByText("Archived")).toBeInTheDocument();
    expect(screen.getByText("Deleted")).toBeInTheDocument();
  });

  it("routes to /tasks when the Tasks item is clicked from the dashboard", async () => {
    renderWithProviders(<MainNav />);

    await waitFor(() => {
      expect(getTags).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByText("Tasks"));

    expect(routerPush).toHaveBeenCalledWith("/tasks");
  });

  it("does not re-navigate when already on the active route", async () => {
    pathname = "/tasks";
    renderWithProviders(<MainNav />);

    await waitFor(() => {
      expect(getTags).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByText("Tasks"));

    expect(routerPush).not.toHaveBeenCalled();
  });

  it("renders nested tags only when the parent is expanded", async () => {
    getTags.mockResolvedValue([
      makeTag({ id: 1, name: "Parent" }),
      makeTag({ id: 2, name: "Child", parent_id: 1 }),
    ]);

    renderWithProviders(<MainNav />);

    await waitFor(() => {
      expect(screen.getByText("Parent")).toBeInTheDocument();
    });
    expect(screen.queryByText("Child")).not.toBeInTheDocument();

    // Expand the parent: re-render with expanded state.
    expandedTagIdsArray = [1];
    renderWithProviders(<MainNav />);
    await waitFor(() => {
      expect(screen.getAllByText("Child").length).toBeGreaterThan(0);
    });
  });

  it("toggles a tag filter when a tag is clicked", async () => {
    getTags.mockResolvedValue([makeTag({ id: 7, name: "Work" })]);

    renderWithProviders(<MainNav />);

    await waitFor(() => {
      expect(screen.getByText("Work")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Work"));

    expect(toggleTagFilter).toHaveBeenCalledWith(7);
  });

  it("opens a delete confirmation modal for a tag", async () => {
    getTags.mockResolvedValue([makeTag({ id: 3, name: "Removable" })]);

    const { container } = renderWithProviders(<MainNav />);

    await waitFor(() => {
      expect(screen.getByText("Removable")).toBeInTheDocument();
    });

    const tagRow = screen.getByText("Removable").closest("div")!;
    const deleteButton = within(tagRow).getByTitle("Delete tag");
    fireEvent.click(deleteButton);

    expect(within(container).getByTestId("confirm-modal")).toHaveTextContent(
      "Delete Tag",
    );
  });

  it("logs out when the Log Out item is clicked", async () => {
    renderWithProviders(<MainNav />);

    await waitFor(() => {
      expect(getTags).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByText("Log Out"));

    expect(logout).toHaveBeenCalled();
  });
});
