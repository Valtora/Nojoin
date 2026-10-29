"use client";

import { ButtonBase, InputBase } from '@/components/ui/nativeBases';
import { useEffect, useId, useRef, useState } from "react";
import { Pencil } from "lucide-react";

import { TOUCH_TARGET_HEIGHT } from "@/components/ui/touchTarget";

interface LiveSpeakerChipProps {
  /** The stored live label, `LIVE_PROVISIONAL_<n>`. */
  label: string;
  /** What the chip reads: the name the user gave, else "Speaker <n>". */
  name: string;
  /** The live lane's label has locked: shown upright, not italic. */
  locked: boolean;
  /**
   * Names the live speaker. Without it the chip is plain text, as it is for a
   * capture that cannot be named any more.
   */
  onRename?: (label: string, name: string) => Promise<void | boolean>;
  /** The People names offered while typing, read when the rename opens. */
  loadPeopleNames?: () => Promise<readonly string[]>;
}

// The finished transcript's speaker name (`TranscriptLine`): same size and
// weight, with italics left to mark a label that can still change.
const TEXT_STYLE = {
  locked: "text-base font-bold text-contrast-muted",
  provisional: "text-base font-bold italic text-contrast-helper",
};

/**
 * The live panel's speaker label, which renames the live speaker.
 *
 * A tap or click opens a text box on the chip, as a double-click does on a
 * processed transcript's speaker; Enter or leaving the box saves, Escape
 * cancels. A person's exact name from People links that person, and the
 * People names are offered as suggestions while typing. The name applies to
 * every line of the label, and reaches the processed speaker who holds the
 * speech it was given for.
 */
export default function LiveSpeakerChip({
  label,
  name,
  locked,
  onRename,
  loadPeopleNames,
}: LiveSpeakerChipProps) {
  // The label is kept from when the box opened: a provisional line can move
  // to another label while the name is typed, and the name is for this one.
  const [draft, setDraft] = useState<{
    label: string;
    opened: string;
    text: string;
  } | null>(null);
  const [peopleNames, setPeopleNames] = useState<readonly string[]>([]);
  // Enter saves and the box then blurs as it closes: one request, not two.
  const savingRef = useRef(false);
  // Escape closes the box without saving, whatever blur follows.
  const cancelledRef = useRef(false);
  // After Enter or Escape, focus goes back to the chip.
  const refocusRef = useRef(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const textStyle = locked ? TEXT_STYLE.locked : TEXT_STYLE.provisional;

  const isEditing = draft !== null;
  useEffect(() => {
    if (!isEditing || !loadPeopleNames) return;
    let cancelled = false;
    loadPeopleNames()
      .then((names) => {
        if (!cancelled) setPeopleNames(names);
      })
      .catch(() => {
        // Suggestions only: a typed name still works without them.
      });
    return () => {
      cancelled = true;
    };
  }, [isEditing, loadPeopleNames]);

  useEffect(() => {
    if (isEditing || !refocusRef.current) return;
    refocusRef.current = false;
    buttonRef.current?.focus();
  }, [isEditing]);

  if (!onRename) {
    return (
      <div
        data-testid="live-transcript-speaker"
        data-locked={locked ? "true" : "false"}
        className={`mb-1 ${textStyle}`}
      >
        {name}
      </div>
    );
  }

  const close = () => {
    setDraft(null);
  };

  const cancel = () => {
    cancelledRef.current = true;
    refocusRef.current = true;
    close();
  };

  const save = async () => {
    if (draft === null || savingRef.current || cancelledRef.current) return;
    const next = draft.text.trim();
    if (!next || next === draft.opened) {
      close();
      return;
    }
    savingRef.current = true;
    try {
      // A refused name stays in the box to retry.
      if (await onRename(draft.label, next) !== false) close();
    } catch (error) {
      console.error("Failed to rename the live speaker", error);
    } finally {
      savingRef.current = false;
    }
  };

  if (isEditing) {
    return (
      <div className="mb-1">
        <InputBase
          data-ui-fill="card"
          data-ui-border="full"
          autoFocus
          type="text"
          value={draft.text}
          list={listId}
          maxLength={255}
          aria-label={`Name ${name}`}
          enterKeyHint="done"
          autoComplete="off"
          // The shown name starts selected, so typing replaces "Speaker 3"
          // instead of running on from it ("Speaker 3Dana" passes the
          // server's placeholder check).
          onFocus={(event) => event.currentTarget.select()}
          onChange={(event) => setDraft({ ...draft, text: event.target.value })}
          onBlur={() => void save()}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              refocusRef.current = true;
              void save();
            }
            if (event.key === "Escape") cancel();
          }}
          className={`w-full max-w-[16rem] rounded border border-status-info-border bg-surface-card px-1.5 py-0.5 text-sm font-bold text-status-info-fg focus:outline-none focus:ring-2 focus:ring-status-info-border ${TOUCH_TARGET_HEIGHT}`}
        />
        <datalist id={listId}>
          {peopleNames.map((person) => (
            <option key={person} value={person} />
          ))}
        </datalist>
      </div>
    );
  }

  return (
    // The margin sits on a wrapper: on a touch screen the button is a full
    // 44 px target, and its own negative margin keeps it from pushing the
    // line down.
    <div className="mb-1">
      <ButtonBase
        ref={buttonRef}
        type="button"
        data-testid="live-transcript-speaker"
        data-locked={locked ? "true" : "false"}
        title="Rename this speaker"
        aria-label={`Rename ${name}`}
        onClick={() => {
          cancelledRef.current = false;
          setDraft({ label, opened: name, text: name });
        }}
        className={`group/chip inline-flex items-center gap-1 rounded text-left transition-colors hover:text-action-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-action-border pointer-coarse:-my-2.5 ${TOUCH_TARGET_HEIGHT} ${textStyle}`}
      >
        {name}
        <Pencil
          aria-hidden
          className="h-3 w-3 shrink-0 opacity-0 transition-opacity group-hover/chip:opacity-100 group-focus-visible/chip:opacity-100 pointer-coarse:opacity-100"
        />
      </ButtonBase>
    </div>
  );
}
