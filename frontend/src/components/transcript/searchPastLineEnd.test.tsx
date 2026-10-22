import { fireEvent, render, screen } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import TranscriptView from "@/components/TranscriptView";

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

const originalScrollIntoView = Element.prototype.scrollIntoView;
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterAll(() => {
  Element.prototype.scrollIntoView = originalScrollIntoView;
});

describe("search past a line's end", () => {
  // "İ" lower-cases to two characters, so a case-insensitive match found
  // after it lies past the line's end. Search cuts it to the end; without
  // that, the text's renderer drops a decoration with no part on the text.
  it("keeps a match that lower-casing pushed past the end, as a mark at the end", () => {
    render(
      <TranscriptView
        recordingId="rec-1"
        segments={[{ id: "u1", speaker: "S0", start: 0, end: 1, text: "İİİ a", revision: 1 }]}
        currentTime={-1}
        onPlaySegment={() => {}}
        isPlaying={false}
        onPause={() => {}}
        onResume={() => {}}
        speakerMap={{}}
        speakers={[]}
        globalSpeakers={[]}
        onRenameSpeaker={() => {}}
        onUpdateSegmentSpeaker={() => {}}
        onUpdateSegmentText={() => {}}
        onFindAndReplace={() => {}}
        speakerColors={{}}
        onUndo={() => {}}
        onRedo={() => {}}
        canUndo={false}
        canRedo={false}
        onExport={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.change(screen.getByPlaceholderText("Find..."), { target: { value: "a" } });

    const text = document.querySelector("[data-order-index] p")!;
    expect(text.textContent).toBe("İİİ a");
    const marks = text.querySelectorAll("mark");
    expect(marks).toHaveLength(1);
    expect(text.lastChild).toBe(marks[0]);
  });
});
