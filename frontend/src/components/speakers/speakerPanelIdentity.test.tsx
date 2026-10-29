import { act, createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SpeakerPanel from "../SpeakerPanel";
import TranscriptView from "../TranscriptView";
import { SpeakerTurnHighlightProvider } from "./SpeakerTurnHighlight";
import { line, panelProps, person, speaker, viewProps } from "../transcript/transcriptTestFixtures";

const updateSpeaker = vi.fn();
const merge = vi.fn();
const promote = vi.fn();
const notify = vi.fn();
vi.mock("@/lib/api", () => ({
  updateSpeaker: (...args: unknown[]) => updateSpeaker(...args),
  mergeRecordingSpeakers: (...args: unknown[]) => merge(...args),
  promoteToGlobalSpeaker: (...args: unknown[]) => promote(...args),
  getMergedRecordingSpeakers: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: notify }) }));
vi.mock("../VoiceprintModal", () => ({ default: () => null }));
vi.mock("../people/SplitPersonModal", () => ({ default: () => null }));

beforeEach(() => {
  updateSpeaker.mockReset().mockResolvedValue([]);
  merge.mockReset().mockResolvedValue({});
  promote.mockReset().mockResolvedValue({});
  notify.mockReset();
});

afterEach(() => vi.useRealTimers());

/** Opens a speaker card's ⋯ menu and returns one of its items. */
const menuItem = (speakerName: string, item: string) => {
  fireEvent.click(screen.getByRole("button", { name: `Actions for ${speakerName}` }));
  return within(screen.getByRole("menu", { name: `Actions for ${speakerName}` }))
    .getByRole("menuitem", { name: item });
};

describe("speaker row controls", () => {
  const renderHighlightPanel = () => {
    const props = panelProps();
    const result = render(<SpeakerTurnHighlightProvider>
      <SpeakerPanel {...props} />
      <TranscriptView {...viewProps(props.segments)} speakers={props.speakers} />
    </SpeakerTurnHighlightProvider>);
    const name = within(screen.getByRole("complementary")).getByRole("button", { name: "Alex" });
    const highlighted = () => result.container.querySelectorAll("[data-speaker-highlighted='true']").length;
    return { ...result, name, highlighted };
  };

  it.each([false, true])("opens rename without changing highlights (initially selected: %s)", (selected) => {
    vi.useFakeTimers();
    const { name, highlighted } = renderHighlightPanel();
    if (selected) fireEvent.click(name, { detail: 0 });
    const initialCount = selected ? 1 : 0;
    expect(highlighted()).toBe(initialCount);

    // Browsers send both clicks before dblclick; checking after the delay
    // also catches a pending toggle that rename failed to cancel.
    fireEvent.click(name, { detail: 1 });
    expect(highlighted()).toBe(initialCount);
    act(() => vi.advanceTimersByTime(100));
    fireEvent.click(name, { detail: 2 });
    fireEvent.doubleClick(name, { detail: 2 });
    expect(within(screen.getByRole("complementary")).getByRole("textbox")).toHaveValue("Alex");
    act(() => vi.advanceTimersByTime(500));
    expect(highlighted()).toBe(initialCount);
  });

  it("highlights a mouse single click only after 250 ms and clears on the next single click", () => {
    vi.useFakeTimers();
    const { name, highlighted } = renderHighlightPanel();
    fireEvent.click(name, { detail: 1 });
    act(() => vi.advanceTimersByTime(249));
    expect(name).toHaveAttribute("aria-pressed", "false");
    expect(highlighted()).toBe(0);
    act(() => vi.advanceTimersByTime(1));
    expect(name).toHaveAttribute("aria-pressed", "true");
    expect(highlighted()).toBe(1);
    fireEvent.click(name, { detail: 1 });
    expect(highlighted()).toBe(1);
    act(() => vi.advanceTimersByTime(250));
    expect(highlighted()).toBe(0);
  });

  it.each(["keyboard", "touch"])("keeps %s activation immediate without a delayed toggle", (input) => {
    vi.useFakeTimers();
    const { name, highlighted } = renderHighlightPanel();
    if (input === "touch") {
      const down = createEvent.pointerDown(name);
      Object.defineProperty(down, "pointerType", { value: "touch" });
      fireEvent(name, down);
    }
    fireEvent.click(name, { detail: input === "keyboard" ? 0 : 1 });
    expect(name).toHaveAttribute("aria-pressed", "true");
    expect(highlighted()).toBe(1);
    act(() => vi.advanceTimersByTime(500));
    expect(highlighted()).toBe(1);
  });

  it("cancels a pending mouse highlight when the panel unmounts", () => {
    vi.useFakeTimers();
    const { name, unmount } = renderHighlightPanel();
    const timersBefore = vi.getTimerCount();
    fireEvent.click(name, { detail: 1 });
    expect(vi.getTimerCount()).toBe(timersBefore + 1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

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
    fireEvent.click(menuItem("Alex", "Change identity"));
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.queryByRole("button", { name: "This utterance" })).toBeNull();
    expect(screen.getByText("Whole speaker in this recording")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Robin" }));
    await waitFor(() => expect(props.onRefresh).toHaveBeenCalled());
    expect(updateSpeaker.mock.calls).toEqual([["r1", "A", "Robin"], ["r1", "A2", "Robin"]]);
    // Back on the card's menu button, which the picker sat against.
    expect(screen.getByRole("button", { name: "Actions for Alex" })).toHaveFocus();
  });

  it("moves every row member onto a speaker picked from this recording", async () => {
    const props = panelProps();
    const renamed = vi.fn();
    props.speakers = [speaker("A", "Alex"), speaker("A2", "Alex", 3), speaker("B", "Blair", 2)];
    render(<SpeakerPanel {...props} onSpeakerRenamed={renamed} />);
    fireEvent.click(menuItem("Alex", "Change identity"));
    fireEvent.click(screen.getByRole("button", { name: "Blair Recording only" }));
    await waitFor(() => expect(props.onRefresh).toHaveBeenCalled());
    expect(merge.mock.calls).toEqual([["r1", "B", "A"], ["r1", "B", "A2"]]);
    expect(updateSpeaker).not.toHaveBeenCalled();
    // Generated notes follow the move, as they follow a rename.
    expect(renamed).toHaveBeenCalledWith("Alex", "Blair");
  });

  it("keeps failed identity assignment open with typed text and reports the failure", async () => {
    updateSpeaker.mockRejectedValue(new Error("conflict"));
    render(<SpeakerPanel {...panelProps()} />);
    fireEvent.click(menuItem("Alex", "Change identity"));
    fireEvent.change(screen.getByPlaceholderText("Search or add..."), { target: { value: "Robin" } });
    fireEvent.keyDown(screen.getByPlaceholderText("Search or add..."), { key: "Enter" });
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.objectContaining({ type: "error" })));
    expect(screen.getByPlaceholderText("Search or add...")).toHaveValue("Robin");
  });

  it("offers Add to People on an unlinked row and preserves route errors for placeholder names", async () => {
    promote.mockRejectedValue({ response: { data: { detail: "Please rename them first." } } });
    render(<SpeakerPanel {...panelProps()} speakers={[speaker("A", "Speaker 1")]} />);
    fireEvent.click(menuItem("Speaker 1", "Add to People"));
    await waitFor(() => expect(promote).toHaveBeenCalledWith("r1", "A"));
    expect(notify).toHaveBeenCalledWith(expect.objectContaining({ type: "error", message: "Please rename them first." }));
  });

  it.each(["linked", "name-match"])("leaves promotion out of the menu for %s rows", (kind) => {
    const entry = speaker("A", "Alex");
    if (kind === "linked") entry.global_speaker_id = 7;
    render(<SpeakerPanel {...panelProps()} speakers={[entry]} globalSpeakers={[person(7, "Alex")]} />);
    menuItem("Alex", "Change identity");
    expect(screen.queryByRole("menuitem", { name: "Add to People" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add to People" })).toBeNull();
  });

  it("keeps a card to one row and explains in its menu why actions wait", () => {
    render(<SpeakerPanel {...panelProps()} speakers={[speaker("A", "Speaker 1")]} disableEditing />);
    expect(screen.queryByRole("button", { name: /Change identity|Add to People/ })).toBeNull();
    const trigger = screen.getByRole("button", { name: "Actions for Speaker 1" });
    expect(trigger).toHaveAttribute("title", "Speaker actions");
    const change = menuItem("Speaker 1", "Change identity");
    const add = screen.getByRole("menuitem", { name: "Add to People" });
    const reason = "Speakers can be changed once processing finishes.";
    expect(screen.getAllByText(reason)).toHaveLength(1);
    for (const item of [change, add]) {
      expect(item).toHaveAttribute("aria-disabled", "true");
      expect(item).toHaveAccessibleDescription(reason);
    }
    fireEvent.click(add);
    fireEvent.click(change);
    expect(promote).not.toHaveBeenCalled();
    expect(screen.queryByPlaceholderText("Search or add...")).toBeNull();
  });

  it("opens the card's menu from the keyboard and closes it on Escape", () => {
    render(<SpeakerPanel {...panelProps()} />);
    const trigger = screen.getByRole("button", { name: "Actions for Alex" });
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const [change] = within(screen.getByRole("menu", { name: "Actions for Alex" })).getAllByRole("menuitem");
    expect(change).toHaveFocus();
    fireEvent.keyDown(change, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(trigger).toHaveFocus();
  });
});
