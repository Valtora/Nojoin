import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SpeakerPanel from "@/components/SpeakerPanel";
import { panelProps } from "@/components/transcript/transcriptTestFixtures";
import { useNavigationStore, type SideTab } from "@/lib/store";

import RecordingSideColumn, { RecordingSideRail } from "./RecordingSideColumn";

vi.mock("@/components/ChatPanel", () => ({ default: () => <div data-testid="chat-panel" /> }));
vi.mock("@/lib/api", () => ({
  getMergedRecordingSpeakers: vi.fn(async () => []),
  promoteToGlobalSpeaker: vi.fn(),
}));
vi.mock("@/components/VoiceprintModal", () => ({ default: () => null }));
vi.mock("@/components/people/SplitPersonModal", () => ({ default: () => null }));

// The page's mounting, on the real store: the rail while collapsed, the column
// with the real speaker panel otherwise.
function SideColumn() {
  const collapsed = useNavigationStore((state) => state.isSpeakerPanelCollapsed);
  if (collapsed) return <RecordingSideRail allowChat showMeetingEdge />;
  return (
    <RecordingSideColumn
      allowChat
      speakers={<SpeakerPanel {...panelProps()} />}
      myNotes={<div data-testid="my-notes-list" />}
      captures={<div data-testid="captures-list" />}
      meetingEdge={<div data-testid="meeting-edge" />}
      chatPanelHeight={40}
      onChatPanelHeightChange={vi.fn()}
      onNotesUpdate={vi.fn()}
      onResizeDragging={vi.fn()}
    />
  );
}

// The calls Analytics' Review speakers makes on desktop, which page.test.tsx
// pins on RecordingMainContent; this follows them through to the real panel.
const reviewSpeakers = () => {
  const state = useNavigationStore.getState();
  state.setSideTab("speakers");
  state.setSpeakerPanelCollapsed(false);
  state.setSpeakerReviewPending(true);
};

describe("Review speakers reaching the side column", () => {
  beforeEach(() => {
    useNavigationStore.setState({
      sideTab: "speakers",
      isSpeakerPanelCollapsed: false,
      isChatPanelCollapsed: false,
      speakerReviewPending: false,
    });
  });

  it.each<[string, SideTab, boolean]>([
    ["Speakers, already open", "speakers", false],
    ["My notes", "my-notes", false],
    ["the collapsed rail", "my-notes", true],
  ])("focuses the speaker list with its ring from %s", (_, sideTab, collapsed) => {
    useNavigationStore.setState({ sideTab, isSpeakerPanelCollapsed: collapsed });
    render(<SideColumn />);

    act(reviewSpeakers);

    const panel = screen.getByRole("complementary", { name: "Speakers" });
    expect(screen.getByRole("tab", { name: "Speakers" })).toHaveAttribute("aria-selected", "true");
    expect(panel).toHaveFocus();
    expect(panel).toHaveAttribute("data-reviewing", "true");
    expect(useNavigationStore.getState().speakerReviewPending).toBe(false);
  });
});
