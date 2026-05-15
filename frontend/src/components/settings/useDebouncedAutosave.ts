import { useCallback, useEffect, useRef, useState } from "react";

import type { SettingsAutosaveStatus } from "./SettingsAutosaveState";

export const SETTINGS_AUTOSAVE_DEBOUNCE_MS = 1000;

export interface SettingsAutosaveSnapshot {
  status: Exclude<SettingsAutosaveStatus, "blocked">;
  message?: string;
}

interface UseDebouncedAutosaveOptions<T> {
  value: T;
  enabled?: boolean;
  debounceMs?: number;
  serialize: (value: T) => string;
  validate?: (value: T) => string | null;
  save: (value: T) => Promise<void>;
  pendingMessage?: string;
  savingMessage?: string;
  savedMessage?: string;
  fallbackErrorMessage?: string;
  onStatusChange?: (snapshot: SettingsAutosaveSnapshot) => void;
}

const DEFAULT_SNAPSHOT: SettingsAutosaveSnapshot = {
  status: "saved",
};

function getAutosaveErrorMessage(
  error: unknown,
  fallbackMessage: string,
): string {
  if (typeof error === "object" && error !== null) {
    const maybeResponse = error as {
      response?: { data?: { detail?: unknown } };
    };
    const detail = maybeResponse.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
  }

  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }

  return fallbackMessage;
}

export default function useDebouncedAutosave<T>({
  value,
  enabled = true,
  debounceMs = SETTINGS_AUTOSAVE_DEBOUNCE_MS,
  serialize,
  validate,
  save,
  pendingMessage,
  savingMessage,
  savedMessage,
  fallbackErrorMessage = "Failed to save changes",
  onStatusChange,
}: UseDebouncedAutosaveOptions<T>) {
  const [autosaveState, setAutosaveState] =
    useState<SettingsAutosaveSnapshot>(DEFAULT_SNAPSHOT);

  const optionsRef = useRef({
    serialize,
    validate,
    save,
    pendingMessage,
    savingMessage,
    savedMessage,
    fallbackErrorMessage,
    onStatusChange,
  });
  const snapshotRef = useRef<SettingsAutosaveSnapshot>(DEFAULT_SNAPSHOT);
  const lastSavedSerializedRef = useRef<string | null>(null);
  const latestValueRef = useRef(value);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentSavePromiseRef = useRef<Promise<void> | null>(null);
  const inFlightRef = useRef(false);
  const queuedSaveRef = useRef(false);

  optionsRef.current = {
    serialize,
    validate,
    save,
    pendingMessage,
    savingMessage,
    savedMessage,
    fallbackErrorMessage,
    onStatusChange,
  };

  const updateAutosaveState = useCallback((nextState: SettingsAutosaveSnapshot) => {
    if (
      snapshotRef.current.status === nextState.status &&
      snapshotRef.current.message === nextState.message
    ) {
      return;
    }

    snapshotRef.current = nextState;
    setAutosaveState(nextState);
    optionsRef.current.onStatusChange?.(nextState);
  }, []);

  const clearPendingSave = useCallback(() => {
    if (debounceTimerRef.current !== null) {
      clearTimeout(debounceTimerRef.current);
      debounceTimerRef.current = null;
    }
  }, []);

  const executeSave = useCallback(
    async (valueToSave: T) => {
      clearPendingSave();
      latestValueRef.current = valueToSave;

      const serialized = optionsRef.current.serialize(valueToSave);
      const validationError = optionsRef.current.validate?.(valueToSave) ?? null;

      if (validationError) {
        updateAutosaveState({
          status: "error",
          message: validationError,
        });
        throw new Error(validationError);
      }

      if (inFlightRef.current) {
        queuedSaveRef.current = true;
        return currentSavePromiseRef.current ?? Promise.resolve();
      }

      inFlightRef.current = true;
      updateAutosaveState({
        status: "saving",
        message: optionsRef.current.savingMessage,
      });

      const savePromise = (async () => {
        try {
          await optionsRef.current.save(valueToSave);
          lastSavedSerializedRef.current = serialized;
          updateAutosaveState({
            status: "saved",
            message: optionsRef.current.savedMessage,
          });
        } catch (error) {
          updateAutosaveState({
            status: "error",
            message: getAutosaveErrorMessage(
              error,
              optionsRef.current.fallbackErrorMessage,
            ),
          });
          throw error;
        } finally {
          inFlightRef.current = false;
          currentSavePromiseRef.current = null;

          if (queuedSaveRef.current) {
            queuedSaveRef.current = false;
            const latestValue = latestValueRef.current;
            const latestSerialized = optionsRef.current.serialize(latestValue);

            if (latestSerialized !== lastSavedSerializedRef.current) {
              const latestValidationError =
                optionsRef.current.validate?.(latestValue) ?? null;

              if (latestValidationError) {
                updateAutosaveState({
                  status: "error",
                  message: latestValidationError,
                });
              } else {
                void executeSave(latestValue);
              }
            }
          }
        }
      })();

      currentSavePromiseRef.current = savePromise;
      return savePromise;
    },
    [clearPendingSave, updateAutosaveState],
  );

  const markAsSaved = useCallback(
    (savedValue: T) => {
      clearPendingSave();
      latestValueRef.current = savedValue;
      queuedSaveRef.current = false;
      lastSavedSerializedRef.current = optionsRef.current.serialize(savedValue);
      updateAutosaveState({
        status: "saved",
        message: optionsRef.current.savedMessage,
      });
    },
    [clearPendingSave, updateAutosaveState],
  );

  const saveNow = useCallback(
    async (nextValue?: T) => {
      clearPendingSave();

      if (nextValue !== undefined) {
        latestValueRef.current = nextValue;
      }

      const candidate = nextValue ?? latestValueRef.current;

      if (currentSavePromiseRef.current) {
        try {
          await currentSavePromiseRef.current;
        } catch {
          // The latest explicit save should still retry with current state.
        }
      }

      const serialized = optionsRef.current.serialize(candidate);
      if (serialized === lastSavedSerializedRef.current) {
        updateAutosaveState({
          status: "saved",
          message: optionsRef.current.savedMessage,
        });
        return;
      }

      await executeSave(candidate);
    },
    [clearPendingSave, executeSave, updateAutosaveState],
  );

  useEffect(() => {
    latestValueRef.current = value;

    if (!enabled) {
      clearPendingSave();
      return;
    }

    if (lastSavedSerializedRef.current === null) {
      return;
    }

    const serialized = optionsRef.current.serialize(value);
    if (serialized === lastSavedSerializedRef.current) {
      clearPendingSave();
      if (!inFlightRef.current) {
        updateAutosaveState({
          status: "saved",
          message: optionsRef.current.savedMessage,
        });
      }
      return;
    }

    const validationError = optionsRef.current.validate?.(value) ?? null;
    clearPendingSave();

    if (validationError) {
      updateAutosaveState({
        status: "error",
        message: validationError,
      });
      return;
    }

    if (!inFlightRef.current) {
      updateAutosaveState({
        status: "pending",
        message: optionsRef.current.pendingMessage,
      });
    }

    debounceTimerRef.current = setTimeout(() => {
      void executeSave(latestValueRef.current);
    }, debounceMs);

    return clearPendingSave;
  }, [value, enabled, debounceMs, clearPendingSave, executeSave, updateAutosaveState]);

  useEffect(() => {
    return clearPendingSave;
  }, [clearPendingSave]);

  return {
    autosaveState,
    markAsSaved,
    saveNow,
  };
}