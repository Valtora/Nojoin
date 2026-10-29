"use client";

import { useId, useState } from "react";
import { Users } from "lucide-react";
import type { GlobalSpeaker, RecordingSpeaker } from "@/types";
import { liveSpeakerDisplayName, segmentSpeakerDisplayName } from "@/lib/recordingSpeakerUtils";
import { ButtonBase } from "@/components/ui/nativeBases";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import SpeakerAssignmentPopover from "@/components/SpeakerAssignmentPopover";
import { useSpeakerTurnHighlight } from "./SpeakerTurnHighlight";
import { TOUCH_TARGET_HEIGHT } from "@/components/ui/touchTarget";

export type RenameLiveSpeaker = (label: string, name: string, createPerson?: boolean) => Promise<boolean>;

// Live names are taken only while the capture runs or is paused; the final
// speakers can be named once processing finishes (routes_live_names.py).
const NAMING_CLOSED = "Speakers can be named here while recording or paused, and again once processing finishes.";
const NEEDS_NAME = "Choose a name with Change identity before adding to People.";

interface Props {
  labels: string[];
  names: Record<string, string>;
  speakers: RecordingSpeaker[];
  globalSpeakers: GlobalSpeaker[];
  speakerColors: Record<string, string>;
  onRename?: RenameLiveSpeaker;
  readOnly: boolean;
}

/** Live identities are source labels, never invented recording-speaker records. */
export default function LiveSpeakerRows({ labels, names, speakers, globalSpeakers,
  speakerColors, onRename, readOnly }: Props) {
  const highlight = useSpeakerTurnHighlight();
  const hintPrefix = useId();
  const [picker, setPicker] = useState<{ label: string; anchor: HTMLElement } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // As the transcript's live header chip: no line needs to have locked.
  const canName = (label: string) => !readOnly && !!onRename && liveSpeakerDisplayName(label) !== null;
  const displayName = (label: string) => segmentSpeakerDisplayName(names, label);
  const assign = async (label: string, name: string, createPerson: boolean) => {
    if (submitting || !canName(label) || !onRename) return false;
    setSubmitting(true);
    try { return await onRename(label, name, createPerson); }
    finally { setSubmitting(false); }
  };

  return <>{labels.map((label) => {
    const name = displayName(label);
    const disabled = submitting || !canName(label);
    const needsName = !names[label]?.trim() || /^(speaker[\s_]*\d+|unknown)$/i.test(name.trim());
    const offersPeople = !globalSpeakers.some((person) => person.name === name);
    // Shown as text, not only a tooltip, which touch and keyboard users never see.
    const hint = !canName(label) ? NAMING_CLOSED : offersPeople && needsName ? NEEDS_NAME : null;
    const hintId = `${hintPrefix}-${label}`;
    return <SurfaceBase key={label} kind="card" data-ui-fill="card" data-ui-border="full"
      className="rounded-lg border border-control-border bg-surface-card p-3 shadow-card">
      <ButtonBase type="button" aria-pressed={highlight.labels.includes(label)}
        onClick={() => highlight.toggle([label])}
        className={`max-w-full truncate text-left text-sm font-medium text-foreground ${TOUCH_TARGET_HEIGHT}`}>
        {name}
      </ButtonBase>
      <div className="mt-2 flex flex-wrap gap-2">
        <ButtonBase type="button" aria-label={`Change identity for ${name}`} disabled={disabled}
          aria-describedby={hint === NAMING_CLOSED ? hintId : undefined}
          onClick={(event) => setPicker({ label, anchor: event.currentTarget })}
          className={`inline-flex items-center gap-1 rounded px-2 text-xs text-action-text hover:bg-action-tint disabled:opacity-50 ${TOUCH_TARGET_HEIGHT}`}>
          <Users className="h-3.5 w-3.5" aria-hidden="true" /> Change identity
        </ButtonBase>
        {offersPeople &&
          <ButtonBase type="button" disabled={disabled || needsName}
            aria-describedby={hint ? hintId : undefined}
            onClick={() => { if (!needsName) void assign(label, name, true); }}
            className={`rounded px-2 text-xs text-action-text hover:bg-action-tint disabled:opacity-50 ${TOUCH_TARGET_HEIGHT}`}>
            Add to People
          </ButtonBase>}
      </div>
      {hint && <p id={hintId} className="mt-1 text-xs text-contrast-helper">{hint}</p>}
    </SurfaceBase>;
  })}
    {picker && !readOnly && onRename && <SpeakerAssignmentPopover availableSpeakers={speakers} globalSpeakers={globalSpeakers}
      currentSpeakerLabel={picker.label} speakerColors={speakerColors} targetElement={picker.anchor}
      fixedScope="speaker_everywhere_in_recording"
      onSelect={async (assignment) => {
        if (await assign(picker.label, assignment.name, false)) {
          setPicker(null); picker.anchor.focus();
        }
      }}
      onClose={() => { setPicker(null); picker.anchor.focus(); }} />}
  </>;
}
