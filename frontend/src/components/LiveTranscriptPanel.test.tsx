import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "./LiveTranscriptPanel";
import { CaptureSourceChannel, TranscriptSegment } from "@/types";

const buildSegment = (
  overrides: Partial<TranscriptSegment> & { id: string },
): TranscriptSegment => ({
  start: 0,
  end: 1,
  text: "placeholder",
  speaker: "UNKNOWN",
  provisional: true,
  segment_source: "live",
  ...overrides,
});

const buildLine = (
  id: string,
  start: number,
  text: string,
  sourceChannel?: CaptureSourceChannel | null,
) =>
  buildSegment({
    id,
    start,
    end: start + 1,
    text,
    source_channel: sourceChannel,
  });

const renderPanel = (segments: TranscriptSegment[], hasLoaded = true) =>
  render(<LiveTranscriptPanel segments={segments} hasLoaded={hasLoaded} />);

/**
 * jsdom performs no layout, so scroll geometry is always zero. Fake it so the
 * pin logic can be exercised at all.
 */
const setScrollGeometry = (
  element: HTMLElement,
  geometry: { scrollTop: number; scrollHeight: number; clientHeight: number },
) => {
  Object.defineProperty(element, "scrollHeight", {
    configurable: true,
    value: geometry.scrollHeight,
  });
  Object.defineProperty(element, "clientHeight", {
    configurable: true,
    value: geometry.clientHeight,
  });
  element.scrollTop = geometry.scrollTop;
};

describe("LiveTranscriptPanel", () => {
  it("states the expected latency while waiting for the first utterance", () => {
    renderPanel([]);

    expect(screen.getByText("Listening.")).toBeInTheDocument();
    expect(
      screen.getByText(/a few seconds behind the conversation/i),
    ).toBeInTheDocument();
  });

  it("distinguishes a paused capture from an active one", () => {
    render(<LiveTranscriptPanel segments={[]} hasLoaded isPaused />);

    expect(screen.getByText("Recording is paused.")).toBeInTheDocument();
  });

  it("says it is still loading before the first fetch resolves", () => {
    renderPanel([], false);

    expect(screen.getByText(/loading the transcript so far/i)).toBeInTheDocument();
  });

  it("renders each utterance with its elapsed timestamp", () => {
    renderPanel([
      buildLine("a", 12, "so the migration ran clean"),
      buildLine("b", 3671, "and the rollback path was tested"),
    ]);

    expect(screen.getByText("00:12")).toBeInTheDocument();
    expect(screen.getByText("1:01:11")).toBeInTheDocument();
    expect(screen.getAllByTestId("live-transcript-line")).toHaveLength(2);
  });

  it("labels the capture channel only where it changes", () => {
    renderPanel([
      buildLine("a", 0, "first shared line", "system"),
      buildLine("b", 5, "second shared line", "system"),
      buildLine("c", 10, "a microphone line", "microphone"),
      buildLine("d", 15, "back to shared", "system"),
    ]);

    // Two consecutive shared-audio lines share one label; the run that follows
    // gets its own, so a switch of source reads as an event rather than noise.
    expect(screen.getAllByText("Shared audio")).toHaveLength(2);
    expect(screen.getAllByText("Microphone")).toHaveLength(1);
  });

  it("shows no channel label when the source was not attributable", () => {
    // Overlapping speech and regions with no dominant channel serialize as
    // null. Absence reads as "not sure"; a guess would read as fact.
    renderPanel([
      buildLine("a", 0, "clear microphone line", "microphone"),
      buildLine("b", 5, "people talking over each other", null),
      buildLine("c", 10, "still ambiguous", undefined),
    ]);

    expect(screen.getAllByText("Microphone")).toHaveLength(1);
    expect(screen.queryByText("Shared audio")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("live-transcript-line")).toHaveLength(3);
  });

  it("names a live speaker label only where the speaker changes", () => {
    renderPanel([
      buildSegment({ id: "a", start: 0, text: "opening", speaker: "LIVE_PROVISIONAL_1" }),
      buildSegment({ id: "b", start: 2, text: "still me", speaker: "LIVE_PROVISIONAL_1" }),
      buildSegment({ id: "c", start: 4, text: "a reply", speaker: "LIVE_PROVISIONAL_12" }),
      buildSegment({ id: "d", start: 6, text: "who said that", speaker: "UNKNOWN" }),
      buildSegment({ id: "e", start: 8, text: "me again", speaker: "LIVE_PROVISIONAL_1" }),
    ]);

    // An unlabelled line in between shows no label and breaks the run, so the
    // returning speaker is named again.
    expect(
      screen.getAllByTestId("live-transcript-speaker").map((node) => node.textContent),
    ).toEqual(["Speaker 1", "Speaker 12", "Speaker 1"]);
  });

  it("styles a provisional label apart from a locked one without relying on colour", () => {
    renderPanel([
      buildSegment({
        id: "a",
        start: 0,
        text: "settled",
        speaker: "LIVE_PROVISIONAL_2",
        speaker_state: "stable",
      }),
      buildSegment({
        id: "b",
        start: 2,
        text: "still settling",
        speaker: "LIVE_PROVISIONAL_3",
        speaker_state: "provisional",
      }),
    ]);

    const [locked, provisional] = screen.getAllByTestId("live-transcript-speaker");
    expect(locked).toHaveAttribute("data-locked", "true");
    expect(locked.className).not.toContain("italic");
    expect(provisional).toHaveAttribute("data-locked", "false");
    expect(provisional.className).toContain("italic");
  });

  it("shows marked speaker headers only when the speaker changes, including assigned speakers", () => {
    const marked = (id: string, speaker: string, start: number) => buildSegment({
      id, speaker, start, settling_required: true, locked: true,
    });
    renderPanel([
      marked("a", "LIVE_PROVISIONAL_1", 0),
      marked("b", "LIVE_PROVISIONAL_1", 3),
      marked("c", "SPEAKER_0", 6),
      marked("d", "SPEAKER_0", 9),
      marked("e", "UNKNOWN", 12),
      marked("f", "SPEAKER_0", 15),
      marked("g", "LIVE_PROVISIONAL_1", 18),
    ]);

    expect(screen.getAllByTestId("live-transcript-speaker").map((node) => node.textContent))
      .toEqual(["Speaker 1", "SPEAKER_0", "SPEAKER_0", "Speaker 1"]);
  });

  it("separates marked same-speaker lines after a pause without repeating their header", () => {
    renderPanel([
      buildSegment({ id: "a", start: 0, end: 1, speaker: "LIVE_PROVISIONAL_1", settling_required: true }),
      buildSegment({ id: "b", start: 3, end: 4, speaker: "LIVE_PROVISIONAL_1", settling_required: true }),
    ]);

    const lines = screen.getAllByTestId("live-transcript-line");
    expect(lines).toHaveLength(2);
    expect(lines[0]).not.toHaveClass("pt-2");
    expect(lines[1]).toHaveClass("pt-2");
    expect(screen.getAllByTestId("live-transcript-speaker")).toHaveLength(1);
  });

  it("keeps marked label italics tied to the line lock rather than the speaker state", () => {
    const segment = buildSegment({ id: "a", speaker: "LIVE_PROVISIONAL_1",
      settling_required: true, locked: false, speaker_state: "stable" });
    const { rerender } = renderPanel([segment]);
    const chip = screen.getByTestId("live-transcript-speaker");
    expect(chip).toHaveAttribute("data-locked", "false");
    expect(chip).toHaveClass("italic");

    rerender(<LiveTranscriptPanel segments={[{ ...segment, locked: true, speaker_state: "provisional" }]} hasLoaded />);
    expect(chip).toHaveAttribute("data-locked", "true");
    expect(chip).not.toHaveClass("italic");
  });

  it("describes settling on the line without a visible caption and removes the description when locked", () => {
    const segment = buildSegment({ id: "a", text: "Words still changing", settling_required: true, locked: false });
    const { rerender } = renderPanel([segment]);
    const line = screen.getByTestId("live-transcript-line");
    expect(line).toHaveAccessibleDescription("This line is still settling.");
    expect(screen.queryByText("Settling…")).not.toBeInTheDocument();
    expect(screen.getByText("Lines settle as the conversation continues. Your corrections are kept."))
      .toBeInTheDocument();
    expect(line.querySelector("p[data-segment-id]")?.textContent).toBe("Words still changing");

    rerender(<LiveTranscriptPanel segments={[{ ...segment, locked: true }]} hasLoaded />);
    expect(line).not.toHaveAttribute("aria-describedby");
  });

  it("keeps unmarked lines free of marked spacing and settling descriptions", () => {
    renderPanel([
      buildSegment({ id: "a", speaker: "LIVE_PROVISIONAL_1", locked: false }),
      buildSegment({ id: "b", start: 3, speaker: "LIVE_PROVISIONAL_1", locked: false }),
    ]);

    for (const line of screen.getAllByTestId("live-transcript-line")) {
      expect(line.className).toBe("group/line");
      expect(line).not.toHaveAttribute("aria-describedby");
    }
    expect(screen.getAllByTestId("live-transcript-speaker")).toHaveLength(1);
    expect(screen.getByText("Provisional. Corrected when the recording is processed.")).toBeInTheDocument();
  });

  it("shows no speaker for lines the live lane left unlabelled", () => {
    renderPanel([
      buildLine("a", 0, "no label yet", "microphone"),
      buildSegment({ id: "b", start: 2, text: "final-pass label", speaker: "SPEAKER_00" }),
    ]);

    expect(screen.queryByTestId("live-transcript-speaker")).not.toBeInTheDocument();
  });

  it("never presents the microphone channel as the viewer", () => {
    // A capture with no shared tab audio puts the whole room on the microphone
    // channel, so identity wording here would misattribute every other speaker.
    renderPanel([buildLine("a", 0, "someone else in the room", "microphone")]);

    expect(screen.getByText("Microphone")).toBeInTheDocument();
    expect(screen.queryByText(/^You$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Others$/)).not.toBeInTheDocument();
  });

  it("offers a jump back to the latest line once scrolled away", () => {
    renderPanel([
      buildLine("a", 0, "first"),
      buildLine("b", 5, "second"),
    ]);

    const container = screen.getByTestId("live-transcript-scroll");
    expect(
      screen.queryByRole("button", { name: /jump to latest/i }),
    ).not.toBeInTheDocument();

    setScrollGeometry(container, {
      scrollTop: 0,
      scrollHeight: 1000,
      clientHeight: 300,
    });
    fireEvent.scroll(container);

    const jumpButton = screen.getByRole("button", { name: /jump to latest/i });
    expect(jumpButton).toHaveAttribute("data-ui-chrome", "action");

    setScrollGeometry(container, {
      scrollTop: 700,
      scrollHeight: 1000,
      clientHeight: 300,
    });
    fireEvent.click(jumpButton);

    expect(
      screen.queryByRole("button", { name: /jump to latest/i }),
    ).not.toBeInTheDocument();
  });

  it("keeps following new lines while pinned to the bottom", () => {
    const { rerender } = renderPanel([buildLine("a", 0, "first")]);
    const container = screen.getByTestId("live-transcript-scroll");

    setScrollGeometry(container, {
      scrollTop: 0,
      scrollHeight: 500,
      clientHeight: 300,
    });

    rerender(
      <LiveTranscriptPanel
        segments={[buildLine("a", 0, "first"), buildLine("b", 5, "second")]}
        hasLoaded
      />,
    );

    expect(container.scrollTop).toBe(500);
  });

  it("follows a provisional line that grows without adding a line", () => {
    const { rerender } = renderPanel([
      buildLine("a", 0, "first"),
      buildLine("b", 5, "second"),
    ]);
    const container = screen.getByTestId("live-transcript-scroll");

    setScrollGeometry(container, {
      scrollTop: 0,
      scrollHeight: 500,
      clientHeight: 300,
    });

    // An utterance is rewritten in place as it finalises, so the text wraps to
    // more rows while the count stays put. Keying the follow on the line count
    // missed this and let the tail drift below the fold.
    rerender(
      <LiveTranscriptPanel
        segments={[
          buildLine("a", 0, "first"),
          buildLine("b", 5, "second, and then a good deal more of it"),
        ]}
        hasLoaded
      />,
    );

    expect(container.scrollTop).toBe(500);
  });

  it("lets the scroll window shrink to the card it is given", () => {
    renderPanel([buildLine("a", 0, "first")]);
    const container = screen.getByTestId("live-transcript-scroll");

    // From 54rem this module absorbs whatever the notes and documents cards
    // leave, which on a short window is under 14rem. A rigid floor there made
    // the window taller than its own card: the frame stayed at the card's
    // height and the overspill painted across the notes card below. The floor
    // is kept for the stacked layout, where the page scrolls instead.
    expect(container.className).toContain("min-h-[14rem]");
    expect(container.className).toContain("@min-[54rem]:min-h-0");
  });

  it("keeps every line inside the window, and clips the card only beside the other column", () => {
    const { container } = renderPanel([buildLine("a", 0, "first")]);
    const card = container.querySelector("section");
    const transcriptWindow = screen.getByTestId("live-transcript-scroll").parentElement;

    expect(transcriptWindow?.className.split(" ")).toContain("overflow-hidden");
    const cardClasses = card?.className.split(" ") ?? [];
    // Two columns: the card has a definite height, and the note box must not
    // spill over the Notes card below it.
    expect(cardClasses).toContain("@min-[54rem]:overflow-hidden");
    // Stacked: a clipping card is a scroll container, which a sticky note box
    // inside it would stick to instead of the page.
    expect(cardClasses).not.toContain("overflow-hidden");
  });

  describe("naming a live speaker", () => {
    const LIVE_LINES = [
      buildSegment({ id: "a", start: 0, text: "opening", speaker: "LIVE_PROVISIONAL_1" }),
      buildSegment({ id: "b", start: 2, text: "a reply", speaker: "LIVE_PROVISIONAL_2" }),
      buildSegment({ id: "c", start: 4, text: "me again", speaker: "LIVE_PROVISIONAL_1" }),
    ];

    const renderNaming = ({
      names = {},
      onRename = vi.fn(async () => {}),
      loadPeopleNames = vi.fn(async () => ["Dana Whitfield", "Erin"]),
    }: {
      names?: Record<string, string>;
      onRename?: (label: string, name: string) => Promise<void>;
      loadPeopleNames?: () => Promise<readonly string[]>;
    } = {}) => {
      render(
        <LiveTranscriptPanel
          segments={LIVE_LINES}
          hasLoaded
          liveSpeakerNames={names}
          onRenameLiveSpeaker={onRename}
          loadPeopleNames={loadPeopleNames}
        />,
      );
      return { onRename, loadPeopleNames };
    };

    const chips = () => screen.getAllByTestId("live-transcript-speaker");
    const openRename = async (index: number) => {
      fireEvent.click(chips()[index]);
      await act(async () => {});
      return screen.getByRole("combobox", { name: /^Name / });
    };

    it("shows the given name on every run of that label, and Speaker N on the others", () => {
      renderNaming({ names: { LIVE_PROVISIONAL_1: "Alex" } });

      expect(chips().map((chip) => chip.textContent)).toEqual([
        "Alex",
        "Speaker 2",
        "Alex",
      ]);
    });

    it("renames from the chip with one request, though the box blurs as it saves", async () => {
      let finish = () => {};
      const onRename = vi.fn(
        () => new Promise<void>((resolve) => (finish = resolve)),
      );
      renderNaming({ onRename });

      const input = await openRename(2);
      expect(input).toHaveValue("Speaker 1");
      expect(document.activeElement).toBe(input);

      fireEvent.change(input, { target: { value: "  Alex " } });
      fireEvent.keyDown(input, { key: "Enter" });
      fireEvent.blur(input);

      expect(onRename).toHaveBeenCalledTimes(1);
      expect(onRename).toHaveBeenCalledWith("LIVE_PROVISIONAL_1", "Alex");
      await act(async () => finish());
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    });

    it("opens with the shown name selected, so typing replaces it", async () => {
      renderNaming();

      const input = (await openRename(1)) as HTMLInputElement;

      expect(input).toHaveValue("Speaker 2");
      expect([input.selectionStart, input.selectionEnd]).toEqual([0, "Speaker 2".length]);
    });

    it("sends nothing for Escape, a blank name or the name it already has", async () => {
      const { onRename } = renderNaming({ names: { LIVE_PROVISIONAL_2: "Blair" } });

      let input = await openRename(1);
      fireEvent.change(input, { target: { value: "Someone" } });
      fireEvent.keyDown(input, { key: "Escape" });
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();

      input = await openRename(1);
      fireEvent.change(input, { target: { value: "   " } });
      fireEvent.keyDown(input, { key: "Enter" });

      input = await openRename(1);
      fireEvent.blur(input);

      expect(onRename).not.toHaveBeenCalled();
    });

    it("saves to the label it opened on, though the line moves to another meanwhile", async () => {
      const onRename = vi.fn(async () => {});
      const panel = (segments: typeof LIVE_LINES) => (
        <LiveTranscriptPanel
          segments={segments}
          hasLoaded
          liveSpeakerNames={{}}
          onRenameLiveSpeaker={onRename}
          loadPeopleNames={vi.fn(async () => [])}
        />
      );
      const { rerender } = render(panel(LIVE_LINES));

      const input = await openRename(0);
      fireEvent.change(input, { target: { value: "Alex" } });
      // The next read relabels the provisional first line.
      rerender(
        panel([{ ...LIVE_LINES[0], speaker: "LIVE_PROVISIONAL_3" }, ...LIVE_LINES.slice(1)]),
      );
      fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });

      expect(onRename).toHaveBeenCalledTimes(1);
      expect(onRename).toHaveBeenCalledWith("LIVE_PROVISIONAL_1", "Alex");
    });

    it("gives focus back to the chip after Enter", async () => {
      renderNaming();

      const input = await openRename(1);
      fireEvent.change(input, { target: { value: "Blair" } });
      await act(async () => {
        fireEvent.keyDown(input, { key: "Enter" });
      });

      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
      expect(document.activeElement).toBe(chips()[1]);
    });

    it("cancels on Escape without saving, though the box then blurs, and gives focus back", async () => {
      const { onRename } = renderNaming();

      const input = await openRename(1);
      fireEvent.change(input, { target: { value: "Someone" } });
      // A blur that lands before the box closes, as a browser may send one.
      await act(async () => {
        fireEvent.keyDown(input, { key: "Escape" });
        fireEvent.blur(input);
      });

      expect(onRename).not.toHaveBeenCalled();
      expect(document.activeElement).toBe(chips()[1]);
    });

    it("offers People's names while typing, read only once a rename opens", async () => {
      const { loadPeopleNames } = renderNaming();
      expect(loadPeopleNames).not.toHaveBeenCalled();

      const input = await openRename(0);

      expect(loadPeopleNames).toHaveBeenCalledTimes(1);
      const listId = input.getAttribute("list");
      const options = document.getElementById(listId!)?.querySelectorAll("option");
      expect(Array.from(options ?? []).map((option) => option.value)).toEqual([
        "Dana Whitfield",
        "Erin",
      ]);
    });

    it("is a full touch target on a phone", () => {
      renderNaming();

      expect(chips()[0].tagName).toBe("BUTTON");
      expect(chips()[0]).toHaveAccessibleName("Rename Speaker 1");
      expect(chips()[0].className).toContain("pointer-coarse:h-[44px]");
    });

    it("is plain text where the speaker cannot be named", () => {
      renderPanel(LIVE_LINES);

      expect(chips()[0].tagName).toBe("DIV");
      expect(screen.queryByRole("button", { name: /^Rename / })).not.toBeInTheDocument();
    });
  });
});
