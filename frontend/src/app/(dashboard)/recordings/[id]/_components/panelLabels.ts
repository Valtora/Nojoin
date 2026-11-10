import type { ActivePanel, SideTab } from "@/lib/store";

/**
 * Each recording panel's name, shared by the main tab row and the desktop
 * side column so a panel reads the same wherever it opens. The generated
 * notes are "Meeting notes" and the user's own are "Your notes", so neither
 * is taken for the other (#960). The panel ids stay as they were, so a
 * remembered selection still opens.
 */
export const PANEL_LABELS: Record<ActivePanel, string> = {
  transcript: "Transcript",
  notes: "Meeting notes",
  documents: "Documents",
  analytics: "Analytics",
  speakers: "Speakers",
  "my-notes": "Your notes",
  captures: "Captures",
  "meeting-edge": "Meeting Edge",
  tasks: "Tasks",
};

/** The side panels' names as one phrase, as in "Speakers, Your notes and Captures". */
export function sidePanelsPhrase(tabs: readonly SideTab[]): string {
  const labels = tabs.map((tab) => PANEL_LABELS[tab]);
  return labels.length < 2
    ? labels.join("")
    : `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
}
