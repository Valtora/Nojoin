import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useNavigationStore } from "@/lib/store";

import RecordingSideColumn, { RecordingSideRail } from "./RecordingSideColumn";

vi.mock("@/components/ChatPanel", () => ({
  default: () => <div data-testid="chat-panel" />,
}));

const renderColumn = () =>
  render(
    <RecordingSideColumn
      speakers={<div data-testid="speaker-panel" />}
      myNotes={<div data-testid="my-notes-list" />}
      chatPanelHeight={40}
      onChatPanelHeightChange={vi.fn()}
      onNotesUpdate={vi.fn()}
      onResizeDragging={vi.fn()}
    />,
  );

const tablist = () => screen.getByRole("tablist", { name: "Side panels" });
const tab = (name: string) => within(tablist()).getByRole("tab", { name });

describe("the side column's Speakers and My notes tabs", () => {
  beforeEach(() => {
    useNavigationStore.setState({
      sideTab: "speakers",
      isSpeakerPanelCollapsed: false,
      isChatPanelCollapsed: false,
    });
  });

  it("opens on Speakers, with My notes a tab beside it, each tied to its panel", () => {
    renderColumn();

    expect(within(tablist()).getAllByRole("tab").map((each) => each.textContent)).toEqual([
      "Speakers",
      "My notes",
    ]);
    expect(tab("Speakers")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("speaker-panel")).toBeVisible();
    expect(screen.getByTestId("my-notes-list")).not.toBeVisible();
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

  it.each([
    ["speakers", "ArrowRight", "My notes"],
    ["speakers", "ArrowLeft", "My notes"],
    ["my-notes", "ArrowRight", "Speakers"],
    ["my-notes", "Home", "Speakers"],
    ["speakers", "End", "My notes"],
  ] as const)("from %s, %s opens %s and focuses its tab", (from, key, to) => {
    useNavigationStore.setState({ sideTab: from });
    renderColumn();

    const start = within(tablist()).getAllByRole("tab").find((each) => each.tabIndex === 0)!;
    fireEvent.keyDown(start, { key });

    expect(tab(to)).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(tab(to));
    expect(within(tablist()).getAllByRole("tab").filter((each) => each.tabIndex === 0)).toEqual([tab(to)]);
  });

  it("collapses both tabs together, and the bar left brings them back", () => {
    renderColumn();

    fireEvent.click(screen.getByRole("button", { name: "Collapse speakers and notes panel" }));
    expect(screen.queryByRole("tablist")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Speakers and My notes" }));

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
  ] as const)("opens the tab the rail's %s button names", (name, sideTab) => {
    useNavigationStore.setState({
      isSpeakerPanelCollapsed: true,
      isChatPanelCollapsed: true,
      sideTab: sideTab === "speakers" ? "my-notes" : "speakers",
    });
    render(<RecordingSideRail />);

    fireEvent.click(screen.getByRole("button", { name }));

    expect(useNavigationStore.getState()).toMatchObject({ sideTab, isSpeakerPanelCollapsed: false });
  });
});
