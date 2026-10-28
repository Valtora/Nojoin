import { Pencil, Play, Scissors, StickyNote, UserRound, type LucideIcon } from "lucide-react";

/** What a transcript line's ⋯ menu can offer. */
export type LineActionId = "note" | "editText" | "changeSpeaker" | "split" | "play";

/** What a line and its view allow. */
export interface LineActionContext {
  view: "live" | "finished";
  /** A note can be added here, and the line has text and a span to put it on. */
  canNote: boolean;
  /** The line's text and speaker can be changed. */
  canEdit: boolean;
  /** A provisional legacy line has no text editor; the settling policy preserves accepted live edits. */
  provisional: boolean;
  /** The recording's audio can be played from the line. */
  canPlay: boolean;
  /** The recording uses word settling, whose live lines take corrections (which freeze them). */
  settlingRequired?: boolean;
  /** Why "Note on this line" is unavailable: a line note quotes the line, so it waits for the lock. */
  noteDisabledReason?: string;
  canSplit?: boolean;
}

export interface LineAction {
  id: LineActionId;
  label: string;
  icon: LucideIcon;
  when: (context: LineActionContext) => boolean;
  disabledReason?: string;
}

/**
 * Every line action, in menu order. Legacy live views offer notes; marked
 * live views also offer corrections, speaker assignment and genuine-word splits,
 * on open lines too. In the finished view, Edit text is the only way
 * into a line's editor (a click on the text selects it), and Change speaker
 * and Play from here repeat the speaker button and the play button, so each
 * also has a keyboard path from one place.
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
    when: ({ view, canEdit, provisional, settlingRequired }) =>
      canEdit && ((view === "finished" && !provisional) || settlingRequired === true),
  },
  {
    id: "changeSpeaker",
    label: "Change speaker",
    icon: UserRound,
    when: ({ view, canEdit, settlingRequired }) => canEdit && (view === "finished" || settlingRequired === true),
  },
  {
    id: "split",
    label: "Split line",
    icon: Scissors,
    when: ({ canSplit }) => canSplit === true,
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
  LINE_ACTIONS.filter((action) => action.when(context)).map((action) =>
    action.id === "note" && context.noteDisabledReason
      ? { ...action, disabledReason: context.noteDisabledReason } : action,
  );
