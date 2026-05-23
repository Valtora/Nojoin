"use client";

import {
  Brain,
  Lightbulb,
  Loader2,
  MessageSquareQuote,
  Target,
} from "lucide-react";
import type { ChangeEvent } from "react";
import { memo, useCallback, useEffect, useRef, useState } from "react";

import { MeetingEdgePayload } from "@/types";

const SAVE_DEBOUNCE_MS = 1200;

type SaveState = "idle" | "saving" | "saved" | "error";

interface MeetingEdgePanelProps {
  payload?: MeetingEdgePayload | null;
  focusText?: string | null;
  status?: string | null;
  errorMessage?: string | null;
  onSaveFocus: (focus: string) => Promise<void>;
}

function MeetingEdgePanel({
  payload,
  focusText,
  status,
  errorMessage,
  onSaveFocus,
}: MeetingEdgePanelProps) {
  const normalisedFocus = focusText ?? "";
  const [draftFocus, setDraftFocus] = useState(normalisedFocus);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftFocusRef = useRef(normalisedFocus);
  const lastSavedRef = useRef(normalisedFocus);
  const lastPropValueRef = useRef(normalisedFocus);
  const saveStateRef = useRef<SaveState>("idle");
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

  const flushSave = useCallback(
    async (valueToSave: string) => {
      clearPendingSave();

      if (valueToSave === lastSavedRef.current) {
        return;
      }

      setVisibleSaveState("saving");
      try {
        await onSaveFocus(valueToSave);
        lastSavedRef.current = valueToSave;
        lastPropValueRef.current = valueToSave;
        setVisibleSaveState(
          draftFocusRef.current === valueToSave ? "saved" : "idle",
        );
      } catch {
        setVisibleSaveState("error");
      }
    },
    [clearPendingSave, onSaveFocus, setVisibleSaveState],
  );

  useEffect(() => {
    flushSaveRef.current = flushSave;
  }, [flushSave]);

  useEffect(() => {
    if (normalisedFocus === lastPropValueRef.current) {
      return;
    }

    lastPropValueRef.current = normalisedFocus;

    if (draftFocusRef.current !== lastSavedRef.current) {
      return;
    }

    setDraftFocus(normalisedFocus);
    draftFocusRef.current = normalisedFocus;
    lastSavedRef.current = normalisedFocus;
  }, [normalisedFocus]);

  useEffect(() => {
    return () => {
      clearPendingSave();
    };
  }, [clearPendingSave]);

  const handleChange = useCallback(
    (event: ChangeEvent<HTMLTextAreaElement>) => {
      const nextValue = event.target.value;
      draftFocusRef.current = nextValue;
      setDraftFocus(nextValue);
      if (saveStateRef.current !== "idle") {
        setVisibleSaveState("idle");
      }

      clearPendingSave();
      if (nextValue === lastSavedRef.current) {
        return;
      }

      saveTimeoutRef.current = setTimeout(() => {
        saveTimeoutRef.current = null;
        void flushSaveRef.current(draftFocusRef.current);
      }, SAVE_DEBOUNCE_MS);
    },
    [clearPendingSave, setVisibleSaveState],
  );

  const handleBlur = useCallback(() => {
    void flushSaveRef.current(draftFocusRef.current);
  }, []);

  const questions = payload?.questions ?? [];
  const points = payload?.points ?? [];
  const concepts = payload?.concepts ?? [];
  const conceptHistory =
    payload?.concept_history && payload.concept_history.length > 0
      ? payload.concept_history
      : concepts;
  const hasPayload = Boolean(
    payload?.summary || questions.length || points.length || conceptHistory.length,
  );

  const saveMessage =
    saveState === "saving"
      ? "Saving"
      : saveState === "saved"
        ? "Saved"
        : saveState === "error"
          ? "Save failed"
          : "Autosaves";

  return (
    <section className="rounded-[2rem] border border-white/60 bg-white/84 p-5 shadow-xl shadow-orange-950/10 backdrop-blur dark:border-white/10 dark:bg-gray-950/68 dark:shadow-black/20">
      <div className="flex items-center justify-between gap-3">
        <div className="inline-flex items-center gap-2 rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
          {status === "updating" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Brain className="h-3.5 w-3.5" />
          )}
          Meeting Edge
        </div>
        {status === "error" && !hasPayload ? (
          <span className="text-xs font-semibold uppercase tracking-[0.2em] text-rose-600 dark:text-rose-300">
            Unavailable
          </span>
        ) : null}
      </div>

      {status === "error" && errorMessage ? (
        <div className="mt-4 rounded-[1.25rem] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-200">
          {errorMessage}
        </div>
      ) : null}

      {hasPayload ? (
        <div className="mt-5 space-y-4">
          {payload?.summary ? (
            <div className="rounded-[1.5rem] border border-white/70 bg-white/80 p-4 dark:border-white/10 dark:bg-gray-900/70">
              <div className="text-xs font-semibold uppercase tracking-[0.2em] text-gray-500 dark:text-gray-400">
                Current read
              </div>
              <p className="mt-2 text-sm leading-6 text-gray-700 dark:text-gray-200">
                {payload.summary}
              </p>
            </div>
          ) : null}

          <div className="grid gap-4 xl:grid-cols-2">
            <div className="rounded-[1.5rem] border border-white/70 bg-white/80 p-4 dark:border-white/10 dark:bg-gray-900/70">
              <div className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
                <MessageSquareQuote className="h-4 w-4 text-orange-600 dark:text-orange-300" />
                Questions to ask
              </div>
              <ul className="mt-3 space-y-2 text-sm leading-6 text-gray-700 dark:text-gray-200">
                {questions.length > 0 ? (
                  questions.map((question, index) => (
                    <li key={`${question}-${index}`} className="rounded-xl bg-orange-50/80 px-3 py-2 dark:bg-orange-500/10">
                      {question}
                    </li>
                  ))
                ) : (
                  <li className="text-gray-500 dark:text-gray-400">
                    Meeting Edge is still gathering enough context to suggest questions.
                  </li>
                )}
              </ul>
            </div>

            <div className="rounded-[1.5rem] border border-white/70 bg-white/80 p-4 dark:border-white/10 dark:bg-gray-900/70">
              <div className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
                <Lightbulb className="h-4 w-4 text-orange-600 dark:text-orange-300" />
                Points to raise
              </div>
              <ul className="mt-3 space-y-2 text-sm leading-6 text-gray-700 dark:text-gray-200">
                {points.length > 0 ? (
                  points.map((point, index) => (
                    <li key={`${point}-${index}`} className="rounded-xl bg-amber-50/80 px-3 py-2 dark:bg-amber-500/10">
                      {point}
                    </li>
                  ))
                ) : (
                  <li className="text-gray-500 dark:text-gray-400">
                    No overlooked points identified yet.
                  </li>
                )}
              </ul>
            </div>
          </div>

          {conceptHistory.length > 0 ? (
            <div className="rounded-[1.5rem] border border-white/70 bg-white/80 p-4 dark:border-white/10 dark:bg-gray-900/70">
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-semibold text-gray-900 dark:text-white">
                  Quick context
                </div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-gray-500 dark:text-gray-400">
                  {conceptHistory.length} term{conceptHistory.length === 1 ? "" : "s"} tracked
                </div>
              </div>
              <div className="mt-3 max-h-[22rem] overflow-y-auto pr-1">
                <div className="grid gap-3 md:grid-cols-2">
                  {conceptHistory.map((concept, index) => (
                    <div
                      key={`${concept.term}-${index}`}
                      className="rounded-xl border border-orange-100 bg-orange-50/60 px-3 py-3 dark:border-orange-500/10 dark:bg-orange-500/5"
                    >
                      <div className="text-sm font-semibold text-gray-900 dark:text-white">
                        {concept.term}
                      </div>
                      <p className="mt-1 text-sm leading-6 text-gray-600 dark:text-gray-300">
                        {concept.explanation}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="mt-5 rounded-[1.5rem] border border-dashed border-orange-200/80 bg-white/65 px-4 py-5 text-sm leading-6 text-gray-600 dark:border-orange-500/20 dark:bg-gray-900/60 dark:text-gray-300">
          {status === "updating"
            ? "Meeting Edge is building the first guidance pass from the live transcript."
            : "Meeting Edge will start suggesting questions and overlooked points once the transcript has enough signal."}
        </div>
      )}

      <div className="mt-5 rounded-[1.5rem] border border-orange-200/70 bg-orange-50/75 p-4 dark:border-orange-500/20 dark:bg-orange-500/10">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
            <Target className="h-4 w-4 text-orange-600 dark:text-orange-300" />
            Guide Meeting Edge
          </div>
          <span className="text-[11px] font-semibold uppercase tracking-[0.2em] text-gray-500 dark:text-gray-400">
            {saveMessage}
          </span>
        </div>
        <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
          Add a short goal, concern, or angle you want this guidance to optimize for.
        </p>
        <textarea
          value={draftFocus}
          onChange={handleChange}
          onBlur={handleBlur}
          placeholder="Example: Help me ask sharper timeline questions and flag hidden risks or missing owners."
          className="mt-3 min-h-[6rem] w-full resize-none rounded-[1.25rem] border border-white/80 bg-white px-4 py-3 text-sm leading-6 text-gray-800 outline-none transition focus:border-orange-500 focus:ring-2 focus:ring-orange-500/20 dark:border-white/10 dark:bg-gray-900 dark:text-gray-100"
        />
      </div>
    </section>
  );
}

export default memo(MeetingEdgePanel);
