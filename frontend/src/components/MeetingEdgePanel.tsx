"use client";

import {
  Brain,
  ChevronDown,
  Lightbulb,
  Loader2,
  MessageSquareQuote,
  Target,
} from "lucide-react";
import type { ChangeEvent, ReactNode } from "react";
import { memo, useCallback, useEffect, useRef, useState } from "react";

import {
  clampMeetingEdgeContextLevel,
  DEFAULT_MEETING_EDGE_CONTEXT_LEVEL,
  MEETING_EDGE_CONTEXT_OPTIONS,
} from "@/lib/meetingEdgeContext";
import { MeetingEdgePayload } from "@/types";

const SAVE_DEBOUNCE_MS = 1200;

type SaveState = "idle" | "saving" | "saved" | "error";

interface MeetingEdgePanelProps {
  payload?: MeetingEdgePayload | null;
  focusText?: string | null;
  status?: string | null;
  onSaveFocus: (focus: string) => Promise<void>;
  contextLevel?: number;
  onSaveContextLevel?: (level: number) => Promise<void>;
}

/**
 * A section of the guidance panel that can be folded away.
 *
 * Meeting Edge accumulates: questions, points and tracked terms all grow as the
 * meeting runs, and a setting nobody touches sat between them and the page. The
 * header is the control, so there is no separate affordance to find, and the
 * body is unmounted rather than hidden so a collapsed section costs no height.
 */
function EdgeSection({
  title,
  icon,
  meta,
  tone = "inset",
  defaultOpen = true,
  open,
  onToggle,
  children,
}: {
  title: string;
  icon?: ReactNode;
  meta?: ReactNode;
  tone?: "inset" | "tint";
  defaultOpen?: boolean;
  /** Controlled state, for sections that fold together. */
  open?: boolean;
  onToggle?: () => void;
  children: ReactNode;
}) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const isOpen = open ?? uncontrolledOpen;
  const setIsOpen = onToggle
    ? () => onToggle()
    : () => setUncontrolledOpen((value) => !value);

  return (
    <div
      className={`density-surface-panel ${
        tone === "tint" ? "border border-action-border bg-action-tint" : "bg-surface-inset"
      }`}
    >
      <button
        type="button"
        onClick={setIsOpen}
        aria-expanded={isOpen}
        className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-semibold text-foreground transition-colors hover:text-action-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
      >
        {icon}
        <span className="min-w-0 flex-1">{title}</span>
        {meta}
        <ChevronDown
          aria-hidden="true"
          className={`h-4 w-4 shrink-0 text-contrast-icon-muted transition-transform duration-150 ${
            isOpen ? "" : "-rotate-90"
          }`}
        />
      </button>
      {isOpen ? <div className="px-4 pb-4">{children}</div> : null}
    </div>
  );
}

function MeetingEdgePanel({
  payload,
  focusText,
  status,
  onSaveFocus,
  contextLevel,
  onSaveContextLevel,
}: MeetingEdgePanelProps) {
  const normalisedFocus = focusText ?? "";
  const resolvedContextLevel = clampMeetingEdgeContextLevel(
    contextLevel ?? payload?.context_level ?? DEFAULT_MEETING_EDGE_CONTEXT_LEVEL,
  );
  const contextStepCount = MEETING_EDGE_CONTEXT_OPTIONS.length - 1;
  const [draftFocus, setDraftFocus] = useState(normalisedFocus);
  const [focusSaveState, setFocusSaveState] = useState<SaveState>("idle");
  const [draftContextLevel, setDraftContextLevel] = useState(resolvedContextLevel);
  const [isGuidanceOpen, setIsGuidanceOpen] = useState(true);
  const toggleGuidance = useCallback(
    () => setIsGuidanceOpen((open) => !open),
    [],
  );
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftFocusRef = useRef(normalisedFocus);
  const lastSavedRef = useRef(normalisedFocus);
  const lastPropValueRef = useRef(normalisedFocus);
  const focusSaveStateRef = useRef<SaveState>("idle");
  const flushSaveRef = useRef<(valueToSave: string) => Promise<void>>(
    async () => {},
  );

  const setVisibleFocusSaveState = useCallback((nextState: SaveState) => {
    if (focusSaveStateRef.current === nextState) {
      return;
    }

    focusSaveStateRef.current = nextState;
    setFocusSaveState(nextState);
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

      setVisibleFocusSaveState("saving");
      try {
        await onSaveFocus(valueToSave);
        lastSavedRef.current = valueToSave;
        lastPropValueRef.current = valueToSave;
        setVisibleFocusSaveState(
          draftFocusRef.current === valueToSave ? "saved" : "idle",
        );
      } catch {
        setVisibleFocusSaveState("error");
      }
    },
    [clearPendingSave, onSaveFocus, setVisibleFocusSaveState],
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

  useEffect(() => {
    setDraftContextLevel(resolvedContextLevel);
  }, [resolvedContextLevel]);

  const handleChange = useCallback(
    (event: ChangeEvent<HTMLTextAreaElement>) => {
      const nextValue = event.target.value;
      draftFocusRef.current = nextValue;
      setDraftFocus(nextValue);
      if (focusSaveStateRef.current !== "idle") {
        setVisibleFocusSaveState("idle");
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
    [clearPendingSave, setVisibleFocusSaveState],
  );

  const handleBlur = useCallback(() => {
    void flushSaveRef.current(draftFocusRef.current);
  }, []);

  const handleContextLevelChange = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      if (!onSaveContextLevel) {
        return;
      }

      const nextLevel = clampMeetingEdgeContextLevel(Number(event.target.value));
      setDraftContextLevel(nextLevel);

      if (nextLevel === resolvedContextLevel) {
        return;
      }

      try {
        await onSaveContextLevel(nextLevel);
      } catch {
        setDraftContextLevel(resolvedContextLevel);
      }
    },
    [onSaveContextLevel, resolvedContextLevel],
  );

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
    focusSaveState === "saving"
      ? "Saving"
      : focusSaveState === "saved"
        ? "Saved"
        : focusSaveState === "error"
          ? "Save failed"
          : "Autosaves";

  return (
    <section className="@container density-surface flex min-h-0 flex-col border border-surface-border bg-surface-card shadow-card">
      <div className="flex items-center justify-between gap-3">
        <div className="inline-flex items-center gap-2 rounded-full border border-action-border bg-action-tint px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-action-text">
          {status === "updating" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Brain className="h-3.5 w-3.5" />
          )}
          Meeting Edge
        </div>
        {status === "error" && !hasPayload ? (
          <span className="text-xs font-semibold uppercase tracking-[0.2em] text-status-danger-fg">
            Unavailable
          </span>
        ) : null}
      </div>

      {hasPayload ? (
        <div className="mt-5 space-y-4">
          {payload?.summary ? (
            <div className="density-surface-panel bg-surface-inset p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.2em] text-contrast-helper">
                Current Read
              </div>
              <p className="mt-2 text-sm leading-6 text-contrast-muted">
                {payload.summary}
              </p>
            </div>
          ) : null}

          {/* Side by side only when this panel is wide enough to carry two
              columns of prose, which is a question about the panel and not
              about the window. On `xl:` these split at a 1280px viewport even
              when the panel itself was 400px, which is what wrapped these
              lists to three words a line. */}
          {/* These two fold together. They are grid siblings, so collapsing one
              alone leaves its cell hollow while the other still sets the row
              height: the space is not recovered, it just moves. They are also
              one thought, read across rather than down. */}
          <div className="grid gap-4 @min-[34rem]:grid-cols-2">
            <EdgeSection
              title="Questions to Ask"
              icon={<MessageSquareQuote className="h-4 w-4 shrink-0 text-action-text" />}
              open={isGuidanceOpen}
              onToggle={toggleGuidance}
            >
              <ul className="space-y-2 text-sm leading-6 text-contrast-muted">
                {questions.length > 0 ? (
                  questions.map((question, index) => (
                    <li key={`${question}-${index}`} className="rounded-xl bg-action-tint px-3 py-2">
                      {question}
                    </li>
                  ))
                ) : (
                  <li className="text-contrast-helper">
                    Meeting Edge is still gathering enough context to suggest questions.
                  </li>
                )}
              </ul>
            </EdgeSection>

            <EdgeSection
              title="Points to Raise"
              icon={<Lightbulb className="h-4 w-4 shrink-0 text-action-text" />}
              open={isGuidanceOpen}
              onToggle={toggleGuidance}
            >
              <ul className="space-y-2 text-sm leading-6 text-contrast-muted">
                {points.length > 0 ? (
                  points.map((point, index) => (
                    <li key={`${point}-${index}`} className="rounded-xl bg-status-warning-bg px-3 py-2">
                      {point}
                    </li>
                  ))
                ) : (
                  <li className="text-contrast-helper">
                    No overlooked points identified yet.
                  </li>
                )}
              </ul>
            </EdgeSection>
          </div>

          {conceptHistory.length > 0 ? (
            <EdgeSection
              title="Technical Context"
              meta={
                <span className="text-[11px] font-semibold uppercase tracking-[0.2em] text-contrast-helper">
                  {conceptHistory.length} term{conceptHistory.length === 1 ? "" : "s"} tracked
                </span>
              }
            >
              <div className="max-h-[22rem] overflow-y-auto pr-1">
                <div className="grid gap-3 @min-[34rem]:grid-cols-2">
                  {conceptHistory.map((concept, index) => (
                    <div
                      key={`${concept.term}-${index}`}
                      className="rounded-xl border border-action-border bg-action-tint px-3 py-3"
                    >
                      <div className="text-sm font-semibold text-foreground">
                        {concept.term}
                      </div>
                      <p className="mt-1 text-sm leading-6 text-contrast-helper">
                        {concept.explanation}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            </EdgeSection>
          ) : null}
        </div>
      ) : (
        <div className="density-surface-panel mt-5 border border-dashed border-action-border bg-surface-inset px-4 py-5 text-sm leading-6 text-contrast-helper">
          {status === "updating"
            ? "Meeting Edge is building the first guidance pass from the live meeting."
            : "Meeting Edge will start suggesting questions and overlooked points once the meeting has enough signal."}
        </div>
      )}

      <div className="mt-5 rounded-[1.5rem] border border-action-border bg-action-tint p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Target className="h-4 w-4 text-action-text" />
            Guide Meeting Edge
          </div>
          <span className="text-[11px] font-semibold uppercase tracking-[0.2em] text-contrast-helper">
            {saveMessage}
          </span>
        </div>
        <p className="mt-2 text-sm leading-6 text-contrast-helper">
          Add a short goal, concern, or angle you want this guidance to optimize for.
        </p>
        <textarea
          value={draftFocus}
          onChange={handleChange}
          onBlur={handleBlur}
          placeholder="Example: Help me ask sharper timeline questions and flag hidden risks or missing owners."
          className="mt-3 min-h-[6rem] w-full resize-none rounded-[1.25rem] border border-surface-border bg-surface-card px-4 py-3 text-sm leading-6 text-foreground outline-none transition focus:border-action focus:ring-2 focus:ring-action"
        />
      </div>

      {/* Last, and closed. It is a setting rather than guidance: once it is set
          for a recording it is rarely touched again, and it was sitting between
          the guidance and the box you type into. */}
      {onSaveContextLevel ? (
        <div className="mt-5">
          <EdgeSection
            title="Meeting Edge Technical Context"
            tone="tint"
            defaultOpen={false}
          >
            <p className="text-xs leading-5 text-contrast-helper">
              Adjust how readily live guidance explains technical language on this recording page.
            </p>

            <input
            type="range"
            min={1}
            max={5}
            step={1}
            value={draftContextLevel}
            onChange={(event) => {
              void handleContextLevelChange(event);
            }}
              aria-label="Meeting Edge Technical Context sensitivity"
              className="mt-5 w-full accent-action"
            />

            <div className="relative mt-5 h-4 text-[11px] font-medium text-contrast-helper">
              {MEETING_EDGE_CONTEXT_OPTIONS.map((option, index) => {
                const position = `${(index / contextStepCount) * 100}%`;
                const alignmentClass =
                  index === 0
                    ? "-translate-x-0 text-left"
                    : index === contextStepCount
                      ? "-translate-x-full text-right"
                      : "-translate-x-1/2 text-center";

                return (
                  <span
                    key={option.value}
                    className={`absolute top-0 whitespace-nowrap ${alignmentClass}`}
                    style={{ left: position }}
                  >
                    {option.label}
                  </span>
                );
              })}
            </div>
          </EdgeSection>
        </div>
      ) : null}
    </section>
  );
}

export default memo(MeetingEdgePanel);
