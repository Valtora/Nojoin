import type { ChangeEvent } from "react";
import { useCallback, useLayoutEffect, useRef, useState } from "react";

const SAVE_DEBOUNCE_MS = 1200;
type SaveState = "idle" | "saving" | "saved" | "error";

/** Owns the focus draft and fences autosaves when guidance becomes read-only. */
export function useMeetingEdgeFocus(
  focusText: string | null | undefined,
  readOnly: boolean,
  onSaveFocus: (focus: string) => Promise<void>,
) {
  const normalisedFocus = focusText ?? "";
  const [state, setState] = useState({
    draftFocus: normalisedFocus,
    lastSaved: normalisedFocus,
    observedProp: normalisedFocus,
    readOnly,
    focusSaveState: "idle" as SaveState,
  });
  if (state.observedProp !== normalisedFocus || state.readOnly !== readOnly) {
    const propChanged = state.observedProp !== normalisedFocus;
    // Read-only cancels the pending save, so a draft typed in the last moments
    // is never sent: show what is saved rather than leave it on screen.
    const saved = propChanged ? normalisedFocus : state.lastSaved;
    const drop = readOnly && !state.readOnly;
    const acceptProp = propChanged && state.draftFocus === state.lastSaved;
    setState({
      ...state,
      observedProp: normalisedFocus,
      readOnly,
      draftFocus: drop ? saved : acceptProp ? normalisedFocus : state.draftFocus,
      lastSaved: drop ? saved : acceptProp ? normalisedFocus : state.lastSaved,
      focusSaveState: state.readOnly !== readOnly ? "idle" : state.focusSaveState,
    });
  }
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftFocusRef = useRef(normalisedFocus);
  const lastSavedRef = useRef(normalisedFocus);
  const generationRef = useRef(0);
  const flushSaveRef = useRef<(valueToSave: string) => Promise<void>>(async () => {});

  const clearPendingSave = useCallback(() => {
    if (saveTimeoutRef.current !== null) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
  }, []);

  const flushSave = useCallback(async (valueToSave: string) => {
    clearPendingSave();
    if (readOnly || valueToSave === lastSavedRef.current) return;

    const generation = generationRef.current;
    setState((current) => ({ ...current, focusSaveState: "saving" }));
    try {
      await onSaveFocus(valueToSave);
      if (generation !== generationRef.current) return;
      lastSavedRef.current = valueToSave;
      setState((current) => ({
        ...current,
        lastSaved: valueToSave,
        focusSaveState: draftFocusRef.current === valueToSave ? "saved" : "idle",
      }));
    } catch {
      if (generation === generationRef.current) {
        setState((current) => ({ ...current, focusSaveState: "error" }));
      }
    }
  }, [clearPendingSave, onSaveFocus, readOnly]);

  // Blur can fire during the commit that changes the recording's state.
  useLayoutEffect(() => {
    flushSaveRef.current = flushSave;
    draftFocusRef.current = state.draftFocus;
    lastSavedRef.current = state.lastSaved;
  }, [flushSave, state.draftFocus, state.lastSaved]);

  useLayoutEffect(() => {
    if (readOnly) clearPendingSave();
    return () => {
      generationRef.current += 1;
      clearPendingSave();
    };
  }, [clearPendingSave, readOnly]);

  const handleChange = useCallback((event: ChangeEvent<HTMLTextAreaElement>) => {
    if (readOnly) return;
    const nextValue = event.target.value;
    draftFocusRef.current = nextValue;
    setState((current) => ({ ...current, draftFocus: nextValue, focusSaveState: "idle" }));
    clearPendingSave();
    if (nextValue === lastSavedRef.current) return;

    saveTimeoutRef.current = setTimeout(() => {
      saveTimeoutRef.current = null;
      void flushSaveRef.current(draftFocusRef.current);
    }, SAVE_DEBOUNCE_MS);
  }, [clearPendingSave, readOnly]);

  const handleBlur = useCallback(() => {
    void flushSaveRef.current(draftFocusRef.current);
  }, []);

  return { draftFocus: state.draftFocus, focusSaveState: state.focusSaveState, handleChange, handleBlur };
}
