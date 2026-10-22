import { cleanup, fireEvent, render, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import TranscriptView from "@/components/TranscriptView";
import type { TimelineNote, TranscriptSegment } from "@/types";

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

// "Revised" means updated in the last 15 s, so the clock is held still.
const NOW = Date.parse("2026-10-22T12:00:00Z");
const originalScrollIntoView = Element.prototype.scrollIntoView;
beforeAll(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  Element.prototype.scrollIntoView = vi.fn();
});
afterAll(() => {
  vi.useRealTimers();
  Element.prototype.scrollIntoView = originalScrollIntoView;
});
afterEach(cleanup);

type Props = ComponentProps<typeof TranscriptView>;

const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "SPEAKER_0", start: 0, end: 2, text: "alpha opens", revision: 1 },
  { id: "u1", speaker: "SPEAKER_1", start: 3, end: 5, text: "beta answers", revision: 1 },
  { id: "u2", speaker: "SPEAKER_0", start: 6, end: 9, text: "alpha and", revision: 1, overlapping_speakers: ["SPEAKER_0", "SPEAKER_1"] },
  { id: "u3", speaker: "SPEAKER_1", start: 7, end: 10, text: "beta at once", revision: 1, overlapping_speakers: ["SPEAKER_0", "SPEAKER_1"] },
  { id: "u4", speaker: "SPEAKER_0", start: 11, end: 13, text: "gamma alpha", revision: 1 },
  { id: "u5", speaker: "SPEAKER_1", start: 14, end: 16, text: "delta", revision: 1 },
  { id: "u6", speaker: "SPEAKER_0", start: 17, end: 19, text: "still forming", revision: 1, provisional: true },
  { id: "u7", speaker: "SPEAKER_1", start: 20, end: 22, text: "alpha closes", revision: 1 },
];

const START: Props = {
  recordingId: "rec-1",
  segments: SEGMENTS,
  currentTime: -1,
  onPlaySegment: () => {},
  isPlaying: false,
  onPause: () => {},
  onResume: () => {},
  speakerMap: { SPEAKER_0: "Alex", SPEAKER_1: "Blair" },
  speakers: [],
  globalSpeakers: [],
  onRenameSpeaker: () => {},
  onUpdateSegmentSpeaker: () => {},
  onUpdateSegmentText: () => {},
  onFindAndReplace: () => {},
  speakerColors: { SPEAKER_0: "orange", SPEAKER_1: "blue" },
  onUndo: () => {},
  onRedo: () => {},
  canUndo: false,
  canRedo: false,
  onExport: () => {},
  pendingRemoteUtteranceIds: [],
  timelineNotes: [],
};

const note: TimelineNote = {
  id: "n1",
  recording_id: "rec-1",
  kind: "point",
  body: "Check the overlap",
  anchor_ms: 8_000,
  revision: 1,
  created_at: "2026-10-22T00:00:00Z",
  updated_at: "2026-10-22T00:00:00Z",
};

const withLine = (props: Props, id: string, change: Partial<TranscriptSegment>): Props => ({
  ...props,
  segments: props.segments.map((segment) => (segment.id === id ? { ...segment, ...change } : segment)),
});

/** A step as the page would take it, and whether the page should look different after. */
interface Step {
  name: string;
  apply: (props: Props) => Props;
  shows: boolean;
}

const STEPS: Step[] = [
  { name: "rename a speaker", apply: (p) => ({ ...p, speakerMap: { ...p.speakerMap, SPEAKER_1: "Bea" } }), shows: true },
  { name: "recolour a speaker", apply: (p) => ({ ...p, speakerColors: { ...p.speakerColors, SPEAKER_0: "green" } }), shows: true },
  { name: "mark an update pending", apply: (p) => ({ ...p, pendingRemoteUtteranceIds: ["u5"] }), shows: true },
  { name: "go read-only", apply: (p) => ({ ...p, readOnly: true }), shows: true },
  { name: "leave read-only", apply: (p) => ({ ...p, readOnly: false }), shows: true },
  { name: "allow provisional edits", apply: (p) => ({ ...p, allowProvisionalEdits: true }), shows: true },
  { name: "make a line provisional", apply: (p) => withLine(p, "u1", { provisional: true }), shows: true },
  { name: "lower a line's confidence", apply: (p) => withLine(p, "u4", { speaker_confidence: 0.3 }), shows: true },
  { name: "add a note", apply: (p) => ({ ...p, timelineNotes: [note] }), shows: true },
  { name: "seek into a line", apply: (p) => ({ ...p, currentTime: 11.5 }), shows: true },
  { name: "tick inside it", apply: (p) => ({ ...p, currentTime: 11.75 }), shows: false },
  { name: "start playing", apply: (p) => ({ ...p, isPlaying: true }), shows: true },
  { name: "revise a line", apply: (p) => withLine(p, "u7", { updated_at: new Date(NOW - 1_000).toISOString() }), shows: true },
  { name: "change a line's text", apply: (p) => withLine(p, "u0", { text: "alpha opens again" }), shows: true },
  { name: "reassign a line", apply: (p) => withLine(p, "u5", { speaker: "SPEAKER_0" }), shows: true },
  // Blair now speaks first, so the overlap's lanes swap; its own lines don't change.
  { name: "reassign the first line", apply: (p) => withLine(p, "u0", { speaker: "SPEAKER_1" }), shows: true },
  { name: "poll equal lines", apply: (p) => ({ ...p, segments: p.segments.map((segment) => ({ ...segment })) }), shows: false },
  { name: "stop playing", apply: (p) => ({ ...p, isPlaying: false }), shows: true },
  { name: "seek between lines", apply: (p) => ({ ...p, currentTime: 2.5 }), shows: true },
  { name: "turn off segment playback", apply: (p) => ({ ...p, disableSegmentPlayback: true }), shows: true },
];

// Attributes sorted, so the order React wrote them in doesn't count.
const canon = (node: Node): string => {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
  if (!(node instanceof Element)) return "";
  const attributes = Array.from(node.attributes)
    .map(({ name, value }) => `${name}="${value}"`)
    .sort()
    .join(" ");
  const tag = node.tagName.toLowerCase();
  return `<${tag} ${attributes}>${Array.from(node.childNodes).map(canon).join("")}</${tag}>`;
};

const openSearch = (container: HTMLElement, findText: string) => {
  fireEvent.click(within(container).getByRole("button", { name: "Search" }));
  fireEvent.change(within(container).getByPlaceholderText("Find..."), {
    target: { value: findText },
  });
};

/** The page as a fresh mount of `props` draws it. */
const freshPage = (props: Props, findText: string | null) => {
  const fresh = render(<TranscriptView {...props} />);
  if (findText) openSearch(fresh.container, findText);
  const page = canon(fresh.container);
  fresh.unmount();
  return page;
};

describe("transcript lines after a change", () => {
  // Lines and groups are memoised: one that misses a change to its props keeps
  // drawing the old value. After each step, the page updated in place must
  // match a fresh mount of the same props, and a step that should show must
  // change the page.
  it.each([
    ["without a search", null],
    ["with a search open", "alpha"],
  ])("match a fresh mount at every step, %s", (_, findText) => {
    let props = START;
    const page = render(<TranscriptView {...props} />);
    if (findText) openSearch(page.container, findText);
    let before = canon(page.container);

    for (const step of STEPS) {
      props = step.apply(props);
      page.rerender(<TranscriptView {...props} />);
      const after = canon(page.container);

      expect(after, step.name).toBe(freshPage(props, findText));
      if (step.shows) expect(after, `${step.name} shows`).not.toBe(before);
      else expect(after, `${step.name} shows nothing`).toBe(before);
      before = after;
    }
  });
});
