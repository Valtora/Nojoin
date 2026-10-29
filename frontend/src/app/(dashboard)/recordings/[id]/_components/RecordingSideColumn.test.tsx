import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useNavigationStore } from "@/lib/store";
import { matchesStyleDeclaration } from "@/test/styleMarkerContracts";

import RecordingSideColumn, { RecordingSideRail } from "./RecordingSideColumn";

vi.mock("@/components/ChatPanel", () => ({
  default: () => <div data-testid="chat-panel" />,
}));

const column = (allowChat = true, showTasks = false) =>
    <RecordingSideColumn
      allowChat={allowChat}
      speakers={<div data-testid="speaker-panel" />}
      myNotes={<div data-testid="my-notes-list" />}
      captures={<div data-testid="captures-list" />}
      meetingEdge={<div data-testid="meeting-edge" />}
      tasks={showTasks ? <div data-testid="linked-tasks" /> : undefined}
      chatPanelHeight={40}
      onChatPanelHeightChange={vi.fn()}
      onNotesUpdate={vi.fn()}
      onResizeDragging={vi.fn()}
    />;

const renderColumn = (allowChat = true) => render(column(allowChat));

// Match the page's conditional rail/column mounting, driven by the real store.
function ResponsiveSideColumn({ allowChat }: { allowChat: boolean }) {
  const collapsed = useNavigationStore((state) => state.isSpeakerPanelCollapsed);
  return collapsed ? <RecordingSideRail allowChat={allowChat} showMeetingEdge /> : column(allowChat);
}

const tablist = () => screen.getByRole("tablist", { name: "Side panels" });
const tab = (name: string) => within(tablist()).getByRole("tab", { name });

describe("the side column's Speakers, My notes, Captures and Meeting Edge tabs", () => {
  beforeEach(() => {
    useNavigationStore.setState({
      sideTab: "speakers",
      isSpeakerPanelCollapsed: false,
      isChatPanelCollapsed: false,
    });
  });

  it.each([false, true])("matches the real splitter and grip, flattening only beside selected My notes (dark=%s)", (dark) => {
    const { container } = renderColumn();
    const handle = container.querySelector('[data-ui-resize-handle="vertical"]')!;
    const grip = handle.querySelector("[data-ui-resize-grip]")!;
    const appearance = { dark };

    expect(matchesStyleDeclaration(handle, "depth-gradient.css", "background-color",
      "var(--depth-split-fill)", appearance)).toBe(true);
    expect(matchesStyleDeclaration(grip, "depth-gradient.css", "background-color",
      "var(--control-border)", appearance)).toBe(true);
    expect(matchesStyleDeclaration(handle, "depth-gradient.css", "--depth-split-fill",
      "var(--surface-inset)", appearance)).toBe(false);

    fireEvent.click(tab("My notes"));
    expect(tab("My notes")).toHaveAttribute("data-ui-side-tab", "my-notes");
    expect(matchesStyleDeclaration(handle, "depth-gradient.css", "--depth-split-fill",
      "var(--surface-inset)", appearance)).toBe(true);

    fireEvent.click(tab("Speakers"));
    expect(matchesStyleDeclaration(handle, "depth-gradient.css", "--depth-split-fill",
      "var(--surface-inset)", appearance)).toBe(false);
  });

  it("opens on Speakers, with My notes and Captures beside it, each tied to its panel", () => {
    renderColumn();

    expect(within(tablist()).getAllByRole("tab").map((each) => each.textContent)).toEqual([
      "Speakers",
      "My notes",
      "Captures",
      "Meeting Edge",
    ]);
    expect(tab("Speakers")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("speaker-panel")).toBeVisible();
    expect(screen.getByTestId("my-notes-list")).not.toBeVisible();
    expect(screen.getByTestId("captures-list")).not.toBeVisible();
    for (const each of within(tablist()).getAllByRole("tab")) {
      const panel = document.getElementById(each.getAttribute("aria-controls") ?? "");
      expect(panel).toHaveAttribute("role", "tabpanel");
      expect(panel).toHaveAttribute("aria-labelledby", each.id);
    }
  });

  it("switches to My notes on a click, keeping Speakers mounted, and remembers it", () => {
    renderColumn();

    fireEvent.click(tab("My notes"));

    expect(tab("My notes")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("my-notes-list")).toBeVisible();
    expect(screen.getByTestId("speaker-panel")).not.toBeVisible();
    expect(useNavigationStore.getState().sideTab).toBe("my-notes");
  });

  it("switches to Captures on a click, keeping the others mounted", () => {
    renderColumn();

    fireEvent.click(tab("Captures"));

    expect(tab("Captures")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("captures-list")).toBeVisible();
    expect(screen.getByTestId("speaker-panel")).not.toBeVisible();
    expect(useNavigationStore.getState().sideTab).toBe("captures");
  });

  it.each([
    ["speakers", "ArrowRight", "My notes"],
    ["speakers", "ArrowLeft", "Meeting Edge"],
    ["my-notes", "ArrowRight", "Captures"],
    ["captures", "ArrowRight", "Meeting Edge"],
    ["meeting-edge", "ArrowRight", "Speakers"],
    ["my-notes", "Home", "Speakers"],
    ["speakers", "End", "Meeting Edge"],
  ] as const)("from %s, %s opens %s and focuses its tab", (from, key, to) => {
    useNavigationStore.setState({ sideTab: from });
    renderColumn();

    const start = within(tablist()).getAllByRole("tab").find((each) => each.tabIndex === 0)!;
    fireEvent.keyDown(start, { key });

    expect(tab(to)).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(tab(to));
    expect(within(tablist()).getAllByRole("tab").filter((each) => each.tabIndex === 0)).toEqual([tab(to)]);
  });

  // jsdom has no container queries, so this reads the rules a browser applies:
  // a label hides under 30rem to make room for the optional fifth tab, and only under 12rem on
  // the open one, where all are icons.
  it("keeps the open tab's label longest in a narrow column, the others their icons", () => {
    renderColumn();
    const label = (name: string) => tab(name).querySelector("span")!;
    const hidesBelow = (name: string) =>
      ["12rem", "30rem"].filter((width) => label(name).classList.contains(`@max-[${width}]:sr-only`));

    expect(hidesBelow("Speakers")).toEqual(["12rem"]);
    expect(hidesBelow("My notes")).toEqual(["30rem"]);
    expect(hidesBelow("Captures")).toEqual(["30rem"]);
    expect(hidesBelow("Meeting Edge")).toEqual(["30rem"]);
    fireEvent.click(tab("My notes"));
    expect(hidesBelow("Speakers")).toEqual(["30rem"]);
    expect(hidesBelow("My notes")).toEqual(["12rem"]);
    for (const name of ["Speakers", "My notes", "Captures", "Meeting Edge"]) {
      expect(tab(name)).toHaveAttribute("title", name);
      expect(tab(name).querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    }
  });

  it("includes linked Tasks in keyboard navigation and falls back when they are absent", () => {
    const { rerender } = render(column(true, true));
    fireEvent.keyDown(tab("Speakers"), { key: "End" });
    expect(tab("Tasks")).toHaveFocus();
    expect(tab("Tasks")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("linked-tasks")).toBeVisible();
    fireEvent.keyDown(tab("Tasks"), { key: "ArrowRight" });
    expect(tab("Speakers")).toHaveFocus();
    fireEvent.click(tab("Tasks"));

    rerender(column(true, false));
    expect(screen.queryByRole("tab", { name: "Tasks" })).not.toBeInTheDocument();
    expect(tab("Speakers")).toHaveAttribute("aria-selected", "true");
  });

  it("collapses both tabs together, and the bar left brings them back", () => {
    renderColumn();

    fireEvent.click(screen.getByRole("button", { name: "Collapse side panels" }));
    expect(screen.queryByRole("tablist")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Speakers, My notes, Captures and Meeting Edge" }));

    expect(tab("Speakers")).toHaveAttribute("aria-selected", "true");
  });

  it("shows the tabs over Chat's bar when Chat is collapsed", () => {
    useNavigationStore.setState({ isChatPanelCollapsed: true, sideTab: "my-notes" });
    renderColumn();

    expect(screen.getByTestId("my-notes-list")).toBeVisible();
    expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument();
  });

  it.each([
    ["Show speakers panel", "speakers"],
    ["Show my notes", "my-notes"],
    ["Show captures", "captures"],
    ["Show Meeting Edge", "meeting-edge"],
  ] as const)("opens the tab the rail's %s button names", (name, sideTab) => {
    useNavigationStore.setState({
      isSpeakerPanelCollapsed: true,
      isChatPanelCollapsed: true,
      sideTab: sideTab === "speakers" ? "my-notes" : "speakers",
    });
    render(<RecordingSideRail showMeetingEdge />);

    fireEvent.click(screen.getByRole("button", { name }));

    expect(useNavigationStore.getState()).toMatchObject({ sideTab, isSpeakerPanelCollapsed: false });
  });

  it("keeps Meeting Edge selected through collapse and reopening when Chat is unavailable", () => {
    useNavigationStore.setState({ sideTab: "meeting-edge" });
    renderColumn(false);
    expect(screen.getByTestId("meeting-edge")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Collapse side panels" }));
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByTestId("chat-panel")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Speakers, My notes, Captures and Meeting Edge" }));
    expect(tab("Meeting Edge")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("meeting-edge")).toBeVisible();
  });

  it.each([false, true])("focuses Meeting Edge after its rail opener is removed (Chat available=%s)", (allowChat) => {
    useNavigationStore.setState({ isSpeakerPanelCollapsed: true, isChatPanelCollapsed: true });
    render(<ResponsiveSideColumn allowChat={allowChat} />);
    const opener = screen.getByRole("button", { name: "Show Meeting Edge" });
    opener.focus();
    expect(opener).toHaveFocus();
    fireEvent.click(opener);

    expect(opener).not.toBeInTheDocument();
    expect(tab("Meeting Edge")).toHaveAttribute("aria-selected", "true");
    expect(tab("Meeting Edge")).toHaveFocus();
    expect(screen.getByTestId("meeting-edge")).toBeVisible();
    fireEvent.keyDown(tab("Meeting Edge"), { key: "ArrowLeft" });
    expect(tab("Captures")).toHaveFocus();
    expect(tab("Captures")).toHaveAttribute("aria-selected", "true");
  });
});
