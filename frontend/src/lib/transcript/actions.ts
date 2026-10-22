import { Pencil, Play, StickyNote, UserRound, type LucideIcon } from "lucide-react";

/** What a transcript line's ⋯ menu can offer. */
export type LineActionId = "note" | "editText" | "changeSpeaker" | "play";

/** What a line and its view allow. */
export interface LineActionContext {
  view: "live" | "finished";
  /** A note can be added here, and the line has text and a span to put it on. */
  canNote: boolean;
  /** The line's text and speaker can be changed. */
  canEdit: boolean;
  /** The recording's audio can be played from the line. */
  canPlay: boolean;
}

export interface LineAction {
  id: LineActionId;
  label: string;
  icon: LucideIcon;
  when: (context: LineActionContext) => boolean;
}

/**
 * Every line action, in menu order. The live view has only notes: it has no
 * editing and no playback. The finished view's other actions repeat the
 * line's own gestures (click the text, click the speaker, the play button),
 * so each also has a keyboard path from one place.
 */
export const LINE_ACTIONS: readonly LineAction[] = [
  {
    id: "note",
    label: "Note on this line",
    icon: StickyNote,
    when: ({ canNote }) => canNote,
  },
  {
    id: "editText",
    label: "Edit text",
    icon: Pencil,
    when: ({ view, canEdit }) => view === "finished" && canEdit,
  },
  {
    id: "changeSpeaker",
    label: "Change speaker",
    icon: UserRound,
    when: ({ view, canEdit }) => view === "finished" && canEdit,
  },
  {
    id: "play",
    label: "Play from here",
    icon: Play,
    when: ({ view, canPlay }) => view === "finished" && canPlay,
  },
];

/** The actions a line offers, in menu order; empty when it offers none. */
export const lineActionsFor = (context: LineActionContext): LineAction[] =>
  LINE_ACTIONS.filter((action) => action.when(context));
