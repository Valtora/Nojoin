"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";

const SAVE_DEBOUNCE_MS = 1800;

interface ProcessingNotesPanelProps {
  value?: string | null;
  onSave: (notes: string) => Promise<void>;
  disabled?: boolean;
  disabledMessage?: string;
}

type SaveState = "idle" | "saving" | "saved" | "error";

function ProcessingNotesPanel({
  value,
  onSave,
  disabled = false,
  disabledMessage,
}: ProcessingNotesPanelProps) {
  const normalisedValue = value ?? "";
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const disabledRef = useRef(disabled);
  const draftRef = useRef(normalisedValue);
  const lastSavedRef = useRef(normalisedValue);
  const lastPropValueRef = useRef(normalisedValue);
  const saveStateRef = useRef<SaveState>("idle");
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flushSaveRef = useRef<(valueToSave: string) => Promise<void>>(
    async () => {},
  );

  const setVisibleSaveState = useCallback((nextState: SaveState) => {
    if (saveStateRef.current === nextState) {
      return;
    }

    saveStateRef.current = nextState;
    setSaveState(nextState);
  }, []);

  const clearPendingSave = useCallback(() => {
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
  }, []);

  const flushSave = useCallback(async (valueToSave: string) => {
    clearPendingSave();

    if (valueToSave === lastSavedRef.current) {
      return;
    }

    setVisibleSaveState("saving");
    try {
      await onSave(valueToSave);
      lastSavedRef.current = valueToSave;
      lastPropValueRef.current = valueToSave;
      setVisibleSaveState(
        draftRef.current === valueToSave ? "saved" : "idle",
      );

      if (
        !disabledRef.current &&
        draftRef.current !== valueToSave &&
        draftRef.current !== lastSavedRef.current
      ) {
        saveTimeoutRef.current = setTimeout(() => {
          saveTimeoutRef.current = null;
          void flushSaveRef.current(draftRef.current);
        }, SAVE_DEBOUNCE_MS);
      }
    } catch {
      setVisibleSaveState("error");
    }
  }, [clearPendingSave, onSave, setVisibleSaveState]);

  useEffect(() => {
    flushSaveRef.current = flushSave;
  }, [flushSave]);

  useEffect(() => {
    disabledRef.current = disabled;
  }, [disabled]);

  useEffect(() => {
    if (normalisedValue === lastPropValueRef.current) {
      return;
    }

    lastPropValueRef.current = normalisedValue;

    if (draftRef.current !== lastSavedRef.current) {
      return;
    }

    draftRef.current = normalisedValue;
    lastSavedRef.current = normalisedValue;
    if (textareaRef.current && textareaRef.current.value !== normalisedValue) {
      textareaRef.current.value = normalisedValue;
    }
  }, [normalisedValue]);

  useEffect(() => {
    if (disabled) {
      return;
    }

    return () => {
      clearPendingSave();
    };
  }, [clearPendingSave, disabled]);

  useEffect(() => {
    if (!disabled) {
      return;
    }

    void flushSave(draftRef.current);
  }, [disabled, flushSave]);

  useEffect(() => {
    return () => {
      clearPendingSave();
    };
  }, [clearPendingSave]);

  const handleChange = useCallback(
    (event: React.ChangeEvent<HTMLTextAreaElement>) => {
      const nextValue = event.target.value;
      draftRef.current = nextValue;

      if (saveStateRef.current !== "idle") {
        setVisibleSaveState("idle");
      }

      if (disabledRef.current || nextValue === lastSavedRef.current) {
        clearPendingSave();
        return;
      }

      clearPendingSave();
      saveTimeoutRef.current = setTimeout(() => {
        saveTimeoutRef.current = null;
        void flushSave(draftRef.current);
      }, SAVE_DEBOUNCE_MS);
    },
    [clearPendingSave, flushSave, setVisibleSaveState],
  );

  const handleBlur = useCallback(() => {
    if (!disabledRef.current) {
      void flushSave(draftRef.current);
    }
  }, [flushSave]);

  const saveMessage =
    disabled
      ? "Locked"
      : saveState === "saving"
      ? "Saving"
      : saveState === "saved"
        ? "Saved"
        : saveState === "error"
          ? "Save failed"
          : "Autosaves while you type";

  return (
    <section className="rounded-[2rem] border border-white/60 bg-white/80 p-5 shadow-xl shadow-orange-950/10 backdrop-blur dark:border-white/10 dark:bg-gray-950/65 dark:shadow-black/20">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
            Notes
          </h3>
          <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
            Capture anything important while the meeting is being recorded or processed.
          </p>
        </div>
        <span className="text-xs font-semibold uppercase tracking-[0.2em] text-gray-500 dark:text-gray-400">
          {saveMessage}
        </span>
      </div>
      <div className="relative">
        <textarea
          ref={textareaRef}
          defaultValue={normalisedValue}
          onChange={handleChange}
          onBlur={handleBlur}
          placeholder="Type quick reminders, decisions, or action items here..."
          disabled={disabled}
          className={`min-h-[18rem] w-full resize-none rounded-[1.5rem] border border-orange-200/70 bg-white px-4 py-4 text-sm leading-6 text-gray-800 outline-none transition dark:border-orange-500/20 dark:bg-gray-900 dark:text-gray-100 ${disabled ? "cursor-not-allowed opacity-70" : "focus:border-orange-500 focus:ring-2 focus:ring-orange-500/20"}`}
        />
        {disabled ? (
          <div className="absolute inset-0 flex items-center justify-center rounded-[1.5rem] border border-white/60 bg-white/45 px-6 text-center backdrop-blur-sm dark:border-white/10 dark:bg-gray-950/55">
            <div>
              <div className="text-sm font-semibold text-gray-900 dark:text-white">
                Notes are temporarily locked
              </div>
              <p className="mt-2 text-sm leading-6 text-gray-700 dark:text-gray-200">
                {disabledMessage || "Your manual notes are being incorporated into the generated meeting notes."}
              </p>
            </div>
          </div>
        ) : null}
      </div>
      <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">
        These notes are fed into note generation and the final notes will label them as user-authored.
      </p>
    </section>
  );
}

export default memo(ProcessingNotesPanel);