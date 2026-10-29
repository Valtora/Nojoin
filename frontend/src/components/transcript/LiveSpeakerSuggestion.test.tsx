import { useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
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
function Harness({ named = false, editable = true }: { named?: boolean; editable?: boolean }) {
  const [names, setNames] = useState<Record<string, string>>(named ? { LIVE_PROVISIONAL_1: "Dana" } : {});
  const [suggestions, setSuggestions] = useState<Record<string, string>>({ LIVE_PROVISIONAL_1: "Alex" });
  const naming = useLiveSpeakerNaming("r1", setNames, undefined, setSuggestions);
  return <SharedLiveTranscript hasLoaded segments={segments} liveSpeakerNames={names}
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
  it("accepts through normal naming without creating a person and removes pending names on every line", async () => {
    render(<Harness />);
    expect(screen.getAllByText("Alex?")).toHaveLength(2);
    await act(async () => fireEvent.click(screen.getAllByRole("button", { name: "Accept suggested name Alex" })[0]));
    expect(mocks.rename).toHaveBeenCalledWith("r1", "LIVE_PROVISIONAL_1", "Alex", false);
    expect(screen.queryByText("Alex?")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("live-transcript-speaker").map((chip) => chip.textContent)).toEqual(["Alex", "Alex"]);
    expect(screen.getAllByTestId("live-transcript-speaker")[0]).toHaveFocus();
  });

  it("dismisses across all lines while keeping manual renaming available", async () => {
    render(<Harness />);
    await act(async () => fireEvent.click(screen.getAllByRole("button", { name: "Dismiss suggested name Alex" })[0]));
    expect(screen.queryByText("Alex?")).not.toBeInTheDocument();
    expect(mocks.rename).not.toHaveBeenCalled();
    expect(screen.getAllByRole("button", { name: "Rename Speaker 1" })[0]).toHaveFocus();
    fireEvent.click(screen.getAllByRole("button", { name: "Rename Speaker 1" })[0]);
    expect(screen.getByRole("combobox", { name: "Name Speaker 1" })).toHaveValue("Speaker 1");
  });

  it.each(["accept", "dismiss"])("retains a suggestion and reports a refused %s", async (decision) => {
    mocks[decision === "accept" ? "rename" : "dismiss"].mockRejectedValue({
      response: { data: { detail: "The meeting has stopped" } },
    });
    render(<Harness />);
    const label = decision === "accept" ? "Accept suggested name Alex" : "Dismiss suggested name Alex";
    await act(async () => fireEvent.click(screen.getAllByRole("button", { name: label })[0]));
    expect(screen.getAllByText("Alex?")).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: label })[0]).toBeEnabled();
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
