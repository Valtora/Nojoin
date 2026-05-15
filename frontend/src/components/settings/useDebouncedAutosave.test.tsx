import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import useDebouncedAutosave, {
  SETTINGS_AUTOSAVE_DEBOUNCE_MS,
} from "./useDebouncedAutosave";

async function flushMicrotasks() {
  await Promise.resolve();
  await Promise.resolve();
}

describe("useDebouncedAutosave", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("debounces preference updates and reports a saved state after persistence", async () => {
    const save = vi.fn().mockResolvedValue(undefined);

    const { result, rerender } = renderHook(
      ({ value }: { value: { theme: string } }) =>
        useDebouncedAutosave({
          value,
          serialize: JSON.stringify,
          save,
          pendingMessage: "Changes pending...",
          savingMessage: "Saving changes...",
          savedMessage: "All changes saved",
        }),
      {
        initialProps: { value: { theme: "light" } },
      },
    );

    act(() => {
      result.current.markAsSaved({ theme: "light" });
    });

    act(() => {
      rerender({ value: { theme: "dark" } });
    });

    expect(result.current.autosaveState).toEqual({
      status: "pending",
      message: "Changes pending...",
    });

    await act(async () => {
      vi.advanceTimersByTime(SETTINGS_AUTOSAVE_DEBOUNCE_MS);
      await flushMicrotasks();
    });

    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith({ theme: "dark" });
    expect(result.current.autosaveState).toEqual({
      status: "saved",
      message: "All changes saved",
    });
  });

  it("queues the latest change while an earlier save is still in flight", async () => {
    let resolveFirstSave: (() => void) | null = null;
    const firstSave = new Promise<void>((resolve) => {
      resolveFirstSave = resolve;
    });
    const save = vi
      .fn<({ theme: string }) => Promise<void>>()
      .mockImplementationOnce(() => firstSave)
      .mockResolvedValueOnce(undefined);

    const { result, rerender } = renderHook(
      ({ value }: { value: { theme: string } }) =>
        useDebouncedAutosave({
          value,
          serialize: JSON.stringify,
          save,
          pendingMessage: "Changes pending...",
          savingMessage: "Saving changes...",
          savedMessage: "All changes saved",
        }),
      {
        initialProps: { value: { theme: "light" } },
      },
    );

    act(() => {
      result.current.markAsSaved({ theme: "light" });
    });

    act(() => {
      rerender({ value: { theme: "dark" } });
    });

    await act(async () => {
      vi.advanceTimersByTime(SETTINGS_AUTOSAVE_DEBOUNCE_MS);
      await flushMicrotasks();
    });

    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenLastCalledWith({ theme: "dark" });

    act(() => {
      rerender({ value: { theme: "system" } });
    });

    await act(async () => {
      vi.advanceTimersByTime(SETTINGS_AUTOSAVE_DEBOUNCE_MS);
      await flushMicrotasks();
    });

    expect(save).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirstSave?.();
      await flushMicrotasks();
    });

    expect(save).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenLastCalledWith({ theme: "system" });
    expect(result.current.autosaveState).toEqual({
      status: "saved",
      message: "All changes saved",
    });
  });

  it("surfaces validation errors without attempting a save", () => {
    const save = vi.fn().mockResolvedValue(undefined);

    const { result, rerender } = renderHook(
      ({ value }: { value: { minutes: number } }) =>
        useDebouncedAutosave({
          value,
          serialize: JSON.stringify,
          save,
          validate: (candidate) =>
            candidate.minutes > 1440 ? "Meeting length must be between 0 and 1440 minutes." : null,
        }),
      {
        initialProps: { value: { minutes: 15 } },
      },
    );

    act(() => {
      result.current.markAsSaved({ minutes: 15 });
    });

    act(() => {
      rerender({ value: { minutes: 2000 } });
    });

    expect(result.current.autosaveState).toEqual({
      status: "error",
      message: "Meeting length must be between 0 and 1440 minutes.",
    });
    expect(save).not.toHaveBeenCalled();
  });
});