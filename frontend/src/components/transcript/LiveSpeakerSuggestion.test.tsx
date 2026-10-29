import { useState } from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SharedLiveTranscript from "@/test/SharedLiveTranscript";
import { useLiveSpeakerNaming } from "./_hooks/useLiveSpeakerNaming";

const mocks = vi.hoisted(() => ({ rename: vi.fn(), dismiss: vi.fn(), notify: vi.fn() }));
vi.mock("@/lib/api/speakers", () => ({
  nameLiveSpeaker: mocks.rename, dismissLiveSpeakerSuggestion: mocks.dismiss,
  getGlobalSpeakers: vi.fn(async () => []),
}));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: mocks.notify }) }));

const segments = [
  { id: "a", start: 0, end: 1, text: "I'm Alex", speaker: "LIVE_PROVISIONAL_1", provisional: true },
  { id: "b", start: 2, end: 3, text: "Thanks", speaker: "LIVE_PROVISIONAL_1", speaker_state: "stable" },
];
function Harness({ named = false, editable = true, lines = segments }:
  { named?: boolean; editable?: boolean; lines?: typeof segments }) {
  const [names, setNames] = useState<Record<string, string>>(named ? { LIVE_PROVISIONAL_1: "Dana" } : {});
  const [suggestions, setSuggestions] = useState<Record<string, string>>({ LIVE_PROVISIONAL_1: "Alex" });
  const naming = useLiveSpeakerNaming("r1", setNames, undefined, setSuggestions);
  return <SharedLiveTranscript hasLoaded segments={lines} liveSpeakerNames={names}
    liveSpeakerSuggestions={suggestions}
    onRenameLiveSpeaker={editable ? naming.renameLiveSpeaker : undefined}
    onDismissLiveSpeakerSuggestion={editable ? naming.dismissLiveSpeakerSuggestion : undefined} />;
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.rename.mockResolvedValue({ LIVE_PROVISIONAL_1: "Alex" });
  mocks.dismiss.mockResolvedValue({ live_speaker_names: {}, live_speaker_suggestions: {} });
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

describe("live speaker suggestions", () => {
  it("offers the name once, on the speaker's latest line, and accepts it through normal naming", async () => {
    render(<Harness />);
    const lines = screen.getAllByTestId("live-transcript-line");
    expect(screen.getAllByText("Alex?")).toHaveLength(1);
    expect(within(lines[1]).getByText("Alex?")).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Accept suggested name Alex" })));
    expect(mocks.rename).toHaveBeenCalledWith("r1", "LIVE_PROVISIONAL_1", "Alex", false);
    expect(screen.queryByText("Alex?")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("live-transcript-speaker").map((chip) => chip.textContent)).toEqual(["Alex", "Alex"]);
    expect(screen.getAllByTestId("live-transcript-speaker")[1]).toHaveFocus();
  });

  it("moves the name to the speaker's new line after someone else has spoken", () => {
    const other = { id: "c", start: 4, end: 5, text: "Hello", speaker: "LIVE_PROVISIONAL_2", speaker_state: "stable" };
    const { rerender } = render(<Harness lines={[segments[1], other]} />);
    expect(within(screen.getAllByTestId("live-transcript-line")[0]).getByText("Alex?")).toBeInTheDocument();
    rerender(<Harness lines={[segments[1], other, { ...segments[1], id: "d", start: 6, end: 7 }]} />);
    expect(screen.getAllByText("Alex?")).toHaveLength(1);
    expect(within(screen.getAllByTestId("live-transcript-line")[2]).getByText("Alex?")).toBeInTheDocument();
  });

  it("dismisses the suggestion while keeping manual renaming available", async () => {
    render(<Harness />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Dismiss suggested name Alex" })));
    expect(screen.queryByText("Alex?")).not.toBeInTheDocument();
    expect(mocks.rename).not.toHaveBeenCalled();
    expect(screen.getAllByRole("button", { name: "Rename Speaker 1" })[1]).toHaveFocus();
    fireEvent.click(screen.getAllByRole("button", { name: "Rename Speaker 1" })[0]);
    expect(screen.getByRole("combobox", { name: "Name Speaker 1" })).toHaveValue("Speaker 1");
  });

  it.each(["accept", "dismiss"])("retains a suggestion and reports a refused %s", async (decision) => {
    mocks[decision === "accept" ? "rename" : "dismiss"].mockRejectedValue({
      response: { data: { detail: "The meeting has stopped" } },
    });
    render(<Harness />);
    const label = decision === "accept" ? "Accept suggested name Alex" : "Dismiss suggested name Alex";
    await act(async () => fireEvent.click(screen.getByRole("button", { name: label })));
    expect(screen.getAllByText("Alex?")).toHaveLength(1);
    expect(screen.getByRole("button", { name: label })).toBeEnabled();
    expect(mocks.notify).toHaveBeenCalledWith({ type: "error", message: "The meeting has stopped" });
  });

  it("offers no pending name for a named label or when capture naming is unavailable", () => {
    const { unmount } = render(<Harness named />);
    expect(screen.queryByText("Alex?")).not.toBeInTheDocument();
    unmount();
    render(<Harness editable={false} />);
    expect(screen.getAllByTestId("live-transcript-speaker")[0]).toHaveTextContent("Speaker 1");
    expect(screen.queryByRole("button", { name: /suggested name/ })).not.toBeInTheDocument();
  });
});
