"use client";

import { useState } from "react";
import { Merge, UserPlus, Users } from "lucide-react";
import type { GlobalSpeaker, RecordingSpeaker } from "@/types";
import {
  isPlaceholderSpeakerName, liveSpeakerDisplayName, segmentSpeakerDisplayName,
} from "@/lib/recordingSpeakerUtils";
import { ButtonBase } from "@/components/ui/nativeBases";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import SpeakerAssignmentPopover from "@/components/SpeakerAssignmentPopover";
import TranscriptActionsMenu, { type TranscriptMenuItem } from "@/components/transcript/TranscriptActionsMenu";
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
}

/** Live identities are source labels, never invented recording-speaker records. */
export default function LiveSpeakerRows({ labels, names, speakers, globalSpeakers,
  speakerColors, onRename, onMerge, readOnly }: Props) {
  const highlight = useSpeakerTurnHighlight();
  // Change identity offers names, People and the other live speakers; Merge
  // into offers the other live speakers alone.
  const [picker, setPicker] = useState<{ label: string; anchor: HTMLElement; merge: boolean } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // As the transcript's live header chip: no line needs to have locked.
  const canName = (label: string) => !readOnly && !!onRename && liveSpeakerDisplayName(label) !== null;
  const displayName = (label: string) => segmentSpeakerDisplayName(names, label);
  const run = async (label: string, command: () => Promise<boolean>) => {
    if (submitting || !canName(label)) return false;
    setSubmitting(true);
    try { return await command(); }
    finally { setSubmitting(false); }
  };
  const assign = (label: string, name: string, createPerson: boolean) =>
    onRename ? run(label, () => onRename(label, name, createPerson)) : Promise.resolve(false);
  // Another live speaker picked for the whole row merges this one into it.
  const merge = (label: string, into: string) =>
    onMerge ? run(label, () => onMerge(label, into)) : Promise.resolve(false);
  const liveLabels = Object.fromEntries(labels.map((label) => [label, displayName(label)]));

  return <>{labels.map((label) => {
    const name = displayName(label);
    const needsName = !names[label]?.trim() || isPlaceholderSpeakerName(name);
    const offersPeople = !globalSpeakers.some((person) => person.name === name);
    // The menu says why an action is held back, under its items.
    const closed = !canName(label) ? NAMING_CLOSED : submitting ? SPEAKER_SAVING : undefined;
    const items: TranscriptMenuItem[] = [
      {
        id: "change-identity",
        label: "Change identity",
        icon: Users,
        disabledReason: closed,
        onSelect: (trigger) => setPicker({ label, anchor: trigger, merge: false }),
      },
      ...(onMerge && labels.length > 1 ? [{
        id: "merge-into",
        label: "Merge into...",
        icon: Merge,
        disabledReason: closed,
        onSelect: (trigger: HTMLButtonElement) => setPicker({ label, anchor: trigger, merge: true }),
      }] : []),
      ...(offersPeople ? [{
        id: "add-to-people",
        label: "Add to People",
        icon: UserPlus,
        disabledReason: closed ?? (needsName ? NEEDS_NAME : undefined),
        onSelect: () => { void assign(label, name, true); },
      }] : []),
    ];
    return <SurfaceBase key={label} kind="card" data-ui-fill="card" data-ui-border="full"
      className="flex items-center gap-2 rounded-lg border border-control-border bg-surface-card p-2.5 shadow-card @min-[18rem]:p-3">
      <ButtonBase type="button" aria-pressed={highlight.labels.includes(label)}
        onClick={() => highlight.toggle([label])}
        className={`min-w-0 flex-1 truncate text-left text-sm font-medium text-foreground ${TOUCH_TARGET_HEIGHT}`}>
        {name}
      </ButtonBase>
      <TranscriptActionsMenu label={`Actions for ${name}`} title="Speaker actions" reveal="always"
        items={items} active={picker?.label === label} />
    </SurfaceBase>;
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
