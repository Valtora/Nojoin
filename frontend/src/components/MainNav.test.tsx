import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fireEvent,
  renderWithProviders,
  screen,
  waitFor,
  within,
} from "@/test/renderWithProviders";
import type { Tag } from "@/types";
import { selectorFor } from "@/test/depthSelectors";
import { styleRules } from "@/test/styleRules";
import { activeTransitions, animatesWidth } from "@/test/widthTransition";

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
let isNavCollapsed = false;
const setNavWidth = vi.fn();
const toggleNavCollapse = vi.fn(() => {
  isNavCollapsed = !isNavCollapsed;
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
  usePathname: () => pathname,
}));

vi.mock("next/image", () => ({
  default: ({ alt }: { alt: string }) => <span data-testid="logo">{alt}</span>,
}));

vi.mock("@/lib/store", () => ({
  useNavigationStore: () => ({
    currentView: "recordings",
    setCurrentView: vi.fn(),
    selectedTagIds: [],
    toggleTagFilter,
    isNavCollapsed,
    toggleNavCollapse,
    navWidth: 256,
    setNavWidth,
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
    isNavCollapsed = false;
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

  it("resolves its rail fill independently of its pane or float role", async () => {
    const { container } = renderWithProviders(<MainNav />);
    await waitFor(() => expect(getTags).toHaveBeenCalled());
    const rail = container.querySelector('[data-ui-fill="rail"]')!;
    expect(rail).not.toBeNull();
    const oldStyle = document.documentElement.getAttribute("data-style");
    const oldDark = document.documentElement.classList.contains("dark");
    document.documentElement.dataset.style = "depth";
    document.documentElement.classList.add("dark");
    try {
      const fill = styleRules("depth-dark", "--depth-fill-base", "var(--rail-bg)")[0];
      expect(rail.matches(selectorFor(fill))).toBe(true);
    } finally {
      if (oldStyle === null) document.documentElement.removeAttribute("data-style");
      else document.documentElement.setAttribute("data-style", oldStyle);
      document.documentElement.classList.toggle("dark", oldDark);
    }
  });

  it("follows the pointer while resizing and animates only collapse and expand (#747)", async () => {
    const { rerender } = renderWithProviders(<MainNav />);
    await waitFor(() => expect(getTags).toHaveBeenCalled());
    const rail = document.getElementById("main-nav")!;
    const handle = rail.querySelector<HTMLElement>(".cursor-col-resize")!;
    const expandedWidth = rail.style.width;
    expect(expandedWidth).toMatch(/^\d+px$/);
    expect(animatesWidth(rail)).toBe(true);

    fireEvent.pointerDown(handle, { button: 0 });
    expect(activeTransitions(rail)).toEqual([]);
    fireEvent.pointerMove(document, { clientX: 300 });
    expect(setNavWidth).toHaveBeenCalledWith(300);
    expect(activeTransitions(rail)).toEqual([]);

    fireEvent.pointerUp(document);
    expect(animatesWidth(rail)).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Collapse navigation" }));
    rerender(<MainNav />);
    expect(rail.style.width).toMatch(/^\d+px$/);
    expect(rail.style.width).not.toBe(expandedWidth);
    expect(animatesWidth(rail)).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Expand navigation" }));
    rerender(<MainNav />);
    expect(rail.style.width).toBe(expandedWidth);
    expect(animatesWidth(rail)).toBe(true);
  });

  it("leaves Import Audio to the recordings list", async () => {
    renderWithProviders(<MainNav />);

    await waitFor(() => {
      expect(getTags).toHaveBeenCalled();
    });
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /import audio/i }),
    ).not.toBeInTheDocument();
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
