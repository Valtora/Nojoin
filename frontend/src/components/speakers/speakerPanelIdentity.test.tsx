import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SpeakerPanel from "../SpeakerPanel";
import TranscriptView from "../TranscriptView";
import { SpeakerTurnHighlightProvider } from "./SpeakerTurnHighlight";
import { line, panelProps, person, speaker, viewProps } from "../transcript/transcriptTestFixtures";

const updateSpeaker = vi.fn();
const promote = vi.fn();
const notify = vi.fn();
vi.mock("@/lib/api", () => ({
  updateSpeaker: (...args: unknown[]) => updateSpeaker(...args),
  promoteToGlobalSpeaker: (...args: unknown[]) => promote(...args),
  getMergedRecordingSpeakers: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: notify }) }));
vi.mock("../VoiceprintModal", () => ({ default: () => null }));
vi.mock("../people/SplitPersonModal", () => ({ default: () => null }));

beforeEach(() => {
  updateSpeaker.mockReset().mockResolvedValue([]);
  promote.mockReset().mockResolvedValue({});
  notify.mockReset();
});

describe("speaker row controls", () => {
  it("highlights every grouped label without hiding other turns; second click clears; double-click renames", async () => {
    const speakers = [speaker("A", "Alex"), speaker("A2", "Alex", 3), speaker("B", "Blair", 2)];
    const segments = [line(), line({ id: "next", speaker: "A2", start: 31, end: 32 }),
      line({ id: "other", speaker: "B", start: 33, end: 34, text: "Other speaker" })];
    const { container } = render(<SpeakerTurnHighlightProvider>
      <SpeakerPanel {...panelProps()} speakers={speakers} segments={segments} />
      <TranscriptView {...viewProps(segments)} speakers={speakers} />
    </SpeakerTurnHighlightProvider>);
    const name = within(screen.getByRole("complementary")).getByRole("button", { name: "Alex" });
    fireEvent.click(name);
    expect(name).toHaveAttribute("aria-pressed", "true");
    expect(container.querySelectorAll("[data-speaker-highlighted='true']")).toHaveLength(2);
    expect(screen.getByText("Other speaker")).toBeInTheDocument();
    fireEvent.click(name);
    expect(container.querySelectorAll("[data-speaker-highlighted='true']")).toHaveLength(0);
    fireEvent.doubleClick(name);
    const input = within(screen.getByRole("complementary")).getByRole("textbox");
    fireEvent.change(input, { target: { value: "Robin" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(updateSpeaker).toHaveBeenCalledTimes(2));
    expect(updateSpeaker.mock.calls).toEqual([["r1", "A", "Robin"], ["r1", "A2", "Robin"]]);
  });

  it("uses the transcript picker with fixed whole-speaker scope for every row member", async () => {
    const props = panelProps();
    props.speakers = [speaker("A", "Alex"), speaker("A2", "Alex", 3)];
    props.globalSpeakers = [person(9, "Robin")];
    render(<SpeakerPanel {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Change identity for Alex" }));
    expect(screen.queryByRole("button", { name: "This utterance" })).toBeNull();
    expect(screen.getByText("Whole speaker in this recording")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Robin" }));
    await waitFor(() => expect(props.onRefresh).toHaveBeenCalled());
    expect(updateSpeaker.mock.calls).toEqual([["r1", "A", "Robin"], ["r1", "A2", "Robin"]]);
    expect(screen.getByRole("button", { name: "Change identity for Alex" })).toHaveFocus();
  });

  it("keeps failed identity assignment open with typed text and reports the failure", async () => {
    updateSpeaker.mockRejectedValue(new Error("conflict"));
    render(<SpeakerPanel {...panelProps()} />);
    fireEvent.click(screen.getByRole("button", { name: "Change identity for Alex" }));
    fireEvent.change(screen.getByPlaceholderText("Search or add..."), { target: { value: "Robin" } });
    fireEvent.keyDown(screen.getByPlaceholderText("Search or add..."), { key: "Enter" });
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.objectContaining({ type: "error" })));
    expect(screen.getByPlaceholderText("Search or add...")).toHaveValue("Robin");
  });

  it("shows Add to People on an unlinked row and preserves route errors for placeholder names", async () => {
    promote.mockRejectedValue({ response: { data: { detail: "Please rename them first." } } });
    render(<SpeakerPanel {...panelProps()} speakers={[speaker("A", "Speaker 1")]} />);
    fireEvent.click(screen.getByRole("button", { name: "Add to People" }));
    await waitFor(() => expect(promote).toHaveBeenCalledWith("r1", "A"));
    expect(notify).toHaveBeenCalledWith(expect.objectContaining({ type: "error", message: "Please rename them first." }));
  });

  it.each(["linked", "name-match"])("hides visible promotion for %s rows", (kind) => {
    const entry = speaker("A", "Alex");
    if (kind === "linked") entry.global_speaker_id = 7;
    render(<SpeakerPanel {...panelProps()} speakers={[entry]} globalSpeakers={[person(7, "Alex")]} />);
    expect(screen.queryByRole("button", { name: "Add to People" })).toBeNull();
  });
});
