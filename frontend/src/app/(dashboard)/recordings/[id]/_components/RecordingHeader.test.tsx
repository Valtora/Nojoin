import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test/renderWithProviders";
import { RecordingStatus, type Recording } from "@/types";

vi.mock("@/lib/api", () => ({ getRecording: vi.fn() }));
vi.mock("@/components/AudioPlayer", () => ({ default: () => null }));
vi.mock("@/components/RecordingTagEditor", () => ({ default: () => null }));
vi.mock("@/components/LinkedEventPanel", () => ({ default: () => null }));

import RecordingHeader from "./RecordingHeader";

const recording = {
  id: "rec-1",
  name: "Weekly sync",
  status: RecordingStatus.PROCESSED,
  tags: [],
} as unknown as Recording;

const renderHeader = (
  props: Partial<React.ComponentProps<typeof RecordingHeader>> = {},
) =>
  renderWithProviders(
    <RecordingHeader
      recording={recording}
      isMobile
      isEditingTitle={false}
      titleValue="Weekly sync"
      isMobileHeaderActionsOpen
      currentTime={0}
      audioRef={{ current: null }}
      setRecording={vi.fn()}
      setTitleValue={vi.fn()}
      setIsEditingTitle={vi.fn()}
      setIsMobileHeaderActionsOpen={vi.fn()}
      onBack={vi.fn()}
      onTitleSubmit={vi.fn()}
      onTimeUpdate={vi.fn()}
      onPlay={vi.fn()}
      onPause={vi.fn()}
      {...props}
    />,
  );

describe("RecordingHeader mobile actions", () => {
  it("offers Speakers in the actions menu, since it has no tab at this width", () => {
    const onShowSpeakers = vi.fn();

    renderHeader({ onShowSpeakers });
    screen.getByRole("button", { name: "Speakers" }).click();

    expect(onShowSpeakers).toHaveBeenCalledOnce();
  });

  it("omits Speakers where the panel is reachable another way", () => {
    // Desktop passes no handler: the speaker panel lives in the side column
    // there, so an entry in this menu would be a second route to one surface.
    renderHeader({ onShowSpeakers: undefined });

    expect(
      screen.queryByRole("button", { name: "Speakers" }),
    ).not.toBeInTheDocument();
  });
});
