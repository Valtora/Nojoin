"use client";

import { memo, useCallback, useLayoutEffect, useRef, useState } from "react";
import { Merge, UserPlus, Users } from "lucide-react";
import type { GlobalSpeaker, RecordingSpeaker, SpeakerNameSource } from "@/types";
import {
  isPlaceholderSpeakerName, liveSpeakerDisplayName, segmentSpeakerDisplayName,
} from "@/lib/recordingSpeakerUtils";
import { ButtonBase } from "@/components/ui/nativeBases";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import SpeakerAssignmentPopover from "@/components/SpeakerAssignmentPopover";
import TranscriptActionsMenu, { type TranscriptMenuItem } from "@/components/transcript/TranscriptActionsMenu";
import LiveSpeakerSuggestion from "@/components/transcript/LiveSpeakerSuggestion";
import { scrollTranscriptToLiveSuggestion } from "@/components/notes/transcriptJump";
import { useSpeakerTurnHighlight } from "./SpeakerTurnHighlight";
import { TOUCH_TARGET_HEIGHT } from "@/components/ui/touchTarget";

export type RenameLiveSpeaker = (label: string, name: string, createPerson?: boolean) => Promise<boolean>;
/** Merges live label `label` into live label `intoLabel`. */
export type MergeLiveSpeaker = (label: string, intoLabel: string) => Promise<boolean>;

// Said in a card's menu, under the actions they hold back. Live names are
// taken only while the capture runs or is paused; the final speakers can be
// named once processing finishes (routes_live_names.py).
const NAMING_CLOSED = "Speakers can be named here while recording or paused, and again once processing finishes.";
const NEEDS_NAME = "Choose a name with Change identity before adding to People.";
export const SPEAKER_SAVING = "Wait for the last change to save.";
const NO_PEOPLE: GlobalSpeaker[] = [];
const NO_SPEAKERS: RecordingSpeaker[] = [];

interface Props {
  labels: string[];
  names: Record<string, string>;
  speakers: RecordingSpeaker[];
  globalSpeakers: GlobalSpeaker[];
  speakerColors: Record<string, string>;
  onRename?: RenameLiveSpeaker;
  onMerge?: MergeLiveSpeaker;
  readOnly: boolean;
  /** Pending names by label, as the live transcript offers them. */
  suggestions?: Readonly<Record<string, string>>;
  suggestionSources?: Readonly<Record<string, SpeakerNameSource>>;
  onDismissSuggestion?: (label: string) => Promise<boolean>;
  /** Shows the transcript where it is a tab of its own; the jump then scrolls it. */
  onShowTranscript?: () => void;
}

/** Live identities are source labels, never invented recording-speaker records. */
export default function LiveSpeakerRows({ labels, names, speakers, globalSpeakers,
  speakerColors, onRename, onMerge, readOnly, suggestions, suggestionSources,
  onDismissSuggestion, onShowTranscript }: Props) {
  const highlight = useSpeakerTurnHighlight();
  // Change identity offers names, People and the other live speakers; Merge
  // into offers the other live speakers alone.
  const [picker, setPicker] = useState<{ label: string; anchor: HTMLElement; merge: boolean } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // As the transcript's live header chip: no line needs to have locked.
  const canName = useCallback((label: string) => !readOnly && !!onRename && liveSpeakerDisplayName(label) !== null,
    [readOnly, onRename]);
  const displayName = (label: string) => segmentSpeakerDisplayName(names, label);
  const run = useCallback(async (label: string, command: () => Promise<boolean>) => {
    if (submitting || !canName(label)) return false;
    setSubmitting(true);
    try { return await command(); }
    finally { setSubmitting(false); }
  }, [submitting, canName]);
  const assign = useCallback((label: string, name: string, createPerson: boolean) =>
    onRename ? run(label, () => onRename(label, name, createPerson)) : Promise.resolve(false), [onRename, run]);
  // Another live speaker picked for the whole row merges this one into it.
  const merge = (label: string, into: string) =>
    onMerge ? run(label, () => onMerge(label, into)) : Promise.resolve(false);
  const pick = useCallback((label: string, anchor: HTMLElement, merging: boolean) =>
    setPicker({ label, anchor, merge: merging }), []);
  // The page hands this over anew on each render; the rows keep one callback.
  const showTranscript = useRef(onShowTranscript);
  useLayoutEffect(() => { showTranscript.current = onShowTranscript; });
  const showLine = useCallback((label: string) => {
    showTranscript.current?.();
    scrollTranscriptToLiveSuggestion(label);
  }, []);
  const liveLabels = Object.fromEntries(labels.map((label) => [label, displayName(label)]));

  return <>{labels.map((label) => {
    const name = displayName(label);
    // The menu says why an action is held back, under its items.
    const closed = !canName(label) ? NAMING_CLOSED : submitting ? SPEAKER_SAVING : undefined;
    const suggestion = canName(label) && name === liveSpeakerDisplayName(label) ? suggestions?.[label] : undefined;
    return <LiveSpeakerRow key={label} label={label} name={name}
      needsName={!names[label]?.trim() || isPlaceholderSpeakerName(name)}
      offersPeople={!globalSpeakers.some((person) => person.name === name)}
      closed={closed} mergeable={!!onMerge && labels.length > 1}
      highlighted={highlight.labels.includes(label)} onToggleHighlight={highlight.toggle}
      menuActive={picker?.label === label} onPick={pick} onAssign={assign}
      suggestion={suggestion} suggestionSource={suggestion ? suggestionSources?.[label] : undefined}
      onDismissSuggestion={onDismissSuggestion} onShowLine={showLine} />;
  })}
    {picker && !readOnly && onRename && <SpeakerAssignmentPopover availableSpeakers={picker.merge ? NO_SPEAKERS : speakers}
      globalSpeakers={picker.merge ? NO_PEOPLE : globalSpeakers} offerNames={!picker.merge}
      liveSpeakerLabels={liveLabels} liveTargets="speaker"
      currentSpeakerLabel={picker.label} currentSpeakerName={displayName(picker.label)}
      speakerColors={speakerColors} targetElement={picker.anchor}
      fixedScope="speaker_everywhere_in_recording"
      onSelect={async (assignment) => {
        const into = assignment.diarizationLabel;
        const done = into && liveSpeakerDisplayName(into) !== null
          ? await merge(picker.label, into)
          : await assign(picker.label, assignment.name, false);
        if (done) {
          setPicker(null); picker.anchor.focus();
        }
      }}
      onClose={() => { setPicker(null); picker.anchor.focus(); }} />}
  </>;
}

interface RowProps {
  label: string;
  name: string;
  needsName: boolean;
  offersPeople: boolean;
  /** Why naming is held back, if it is. */
  closed?: string;
  mergeable: boolean;
  highlighted: boolean;
  onToggleHighlight: (labels: readonly string[]) => void;
  menuActive: boolean;
  onPick: (label: string, anchor: HTMLElement, merge: boolean) => void;
  onAssign: (label: string, name: string, createPerson: boolean) => Promise<boolean>;
  suggestion?: string;
  suggestionSource?: SpeakerNameSource;
  onDismissSuggestion?: (label: string) => Promise<boolean>;
  onShowLine: (label: string) => void;
}

/**
 * One live speaker's card. Memoised with stable props, so a poll that changes
 * one speaker's pending name redraws that card alone.
 */
const LiveSpeakerRow = memo(function LiveSpeakerRow({ label, name, needsName, offersPeople, closed, mergeable,
  highlighted, onToggleHighlight, menuActive, onPick, onAssign, suggestion, suggestionSource,
  onDismissSuggestion, onShowLine }: RowProps) {
  const nameRef = useRef<HTMLButtonElement>(null);
  const items: TranscriptMenuItem[] = [
    {
      id: "change-identity",
      label: "Change identity",
      icon: Users,
      disabledReason: closed,
      onSelect: (trigger) => onPick(label, trigger, false),
    },
    ...(mergeable ? [{
      id: "merge-into",
      label: "Merge into...",
      icon: Merge,
      disabledReason: closed,
      onSelect: (trigger: HTMLButtonElement) => onPick(label, trigger, true),
    }] : []),
    ...(offersPeople ? [{
      id: "add-to-people",
      label: "Add to People",
      icon: UserPlus,
      disabledReason: closed ?? (needsName ? NEEDS_NAME : undefined),
      onSelect: () => { void onAssign(label, name, true); },
    }] : []),
  ];
  return <SurfaceBase kind="card" data-ui-fill="card" data-ui-border="full"
    className="flex flex-wrap items-center gap-2 rounded-lg border border-control-border bg-surface-card p-2.5 shadow-card @min-[18rem]:p-3">
    <ButtonBase ref={nameRef} type="button" aria-pressed={highlighted}
      onClick={() => onToggleHighlight([label])}
      className={`min-w-0 flex-1 truncate text-left text-sm font-medium text-foreground ${TOUCH_TARGET_HEIGHT}`}>
      {name}
    </ButtonBase>
    <TranscriptActionsMenu label={`Actions for ${name}`} title="Speaker actions" reveal="always"
      items={items} active={menuActive} />
    {/* A pending name wraps under the name and its menu, with its actions
        on a row of their own beneath it. */}
    {suggestion && onDismissSuggestion && <div className="basis-full">
      <LiveSpeakerSuggestion label={label} name={suggestion} source={suggestionSource} layout="below"
        onAccept={(speaker, suggested) => onAssign(speaker, suggested, false)} onDismiss={onDismissSuggestion}
        focusAfterDecision={() => nameRef.current?.focus()}>
        <ButtonBase type="button" onClick={() => onShowLine(label)}
          className={`rounded px-2 text-xs text-action-text hover:bg-action-tint ${TOUCH_TARGET_HEIGHT}`}
          aria-label={`Go to the line where ${suggestion} was heard`}>
          Go to line
        </ButtonBase>
      </LiveSpeakerSuggestion>
    </div>}
  </SurfaceBase>;
});
