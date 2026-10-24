"use client";

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
  onRename?: (label: string, name: string) => Promise<void>;
  /** The People names offered while typing, read when the rename opens. */
  loadPeopleNames?: () => Promise<readonly string[]>;
}

const TEXT_STYLE = {
  locked: "font-semibold text-contrast-muted",
  provisional: "italic text-contrast-helper",
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
  const [draft, setDraft] = useState<string | null>(null);
  const [peopleNames, setPeopleNames] = useState<readonly string[]>([]);
  // Enter saves and the box then blurs as it closes: one request, not two.
  const savingRef = useRef(false);
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

  if (!onRename) {
    return (
      <div
        data-testid="live-transcript-speaker"
        data-locked={locked ? "true" : "false"}
        className={`mb-1 text-xs ${textStyle}`}
      >
        {name}
      </div>
    );
  }

  const close = () => {
    setDraft(null);
  };

  const save = async () => {
    if (draft === null || savingRef.current) return;
    const next = draft.trim();
    if (!next || next === name) {
      close();
      return;
    }
    savingRef.current = true;
    try {
      await onRename(label, next);
    } finally {
      savingRef.current = false;
      close();
    }
  };

  if (isEditing) {
    return (
      <div className="mb-1">
        <input
          autoFocus
          type="text"
          value={draft}
          list={listId}
          maxLength={255}
          aria-label={`Name ${name}`}
          enterKeyHint="done"
          autoComplete="off"
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => void save()}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void save();
            }
            if (event.key === "Escape") close();
          }}
          className={`w-full max-w-[16rem] rounded border border-status-info-border bg-surface-card px-1.5 py-0.5 text-sm font-semibold text-status-info-fg focus:outline-none focus:ring-2 focus:ring-status-info-border ${TOUCH_TARGET_HEIGHT}`}
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
      <button
        type="button"
        data-testid="live-transcript-speaker"
        data-locked={locked ? "true" : "false"}
        title="Rename this speaker"
        aria-label={`Rename ${name}`}
        onClick={() => setDraft(name)}
        className={`group/chip inline-flex items-center gap-1 rounded text-left text-xs transition-colors hover:text-action-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-action-border pointer-coarse:-my-2.5 ${TOUCH_TARGET_HEIGHT} ${textStyle}`}
      >
        {name}
        <Pencil
          aria-hidden
          className="h-3 w-3 shrink-0 opacity-0 transition-opacity group-hover/chip:opacity-100 group-focus-visible/chip:opacity-100 pointer-coarse:opacity-100"
        />
      </button>
    </div>
  );
}
