import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useNavigationStore } from "@/lib/store";
import type { RecordingId } from "@/types";

import { useRecordingSelection } from "./useRecordingSelection";

const stored = () => {
  const { selectedRecordingIds, selectionAnchorId } = useNavigationStore.getState();
  return { ids: selectedRecordingIds, anchor: selectionAnchorId };
};

const renderSelection = (shown: RecordingId[]) =>
  renderHook(({ ids }) => useRecordingSelection(ids), {
    initialProps: { ids: shown },
  });

describe("useRecordingSelection", () => {
  beforeEach(() => {
    useNavigationStore.setState({ selectedRecordingIds: [], selectionAnchorId: null });
  });

  it("drops rows, and the anchor, that are no longer shown", () => {
    const { result, rerender } = renderSelection(["a", "b", "c"]);
    act(() => result.current.toggle("a"));
    act(() => result.current.toggle("c"));

    rerender({ ids: ["a", "b"] });

    expect(result.current.selectedIds).toEqual(["a"]);
    expect(stored()).toEqual({ ids: ["a"], anchor: null });
  });

  it("reads only shown rows, so nothing acts on a row that has gone", () => {
    useNavigationStore.setState({ selectedRecordingIds: ["a", "b"], selectionAnchorId: "b" });
    const { result } = renderSelection(["a"]);

    expect(result.current.selectedIds).toEqual(["a"]);
    expect(result.current.anchorId).toBeNull();
  });

  it("extends a range from the anchor in list order, or toggles with no anchor", () => {
    const { result } = renderSelection(["a", "b", "c", "d"]);

    act(() => result.current.extendTo("b"));
    expect(result.current.selectedIds).toEqual(["b"]);

    act(() => result.current.extendTo("d"));
    expect(result.current.selectedIds).toEqual(["b", "c", "d"]);
    expect(result.current.anchorId).toBe("b");
  });

  it("reports the header state and selects or clears every shown row", () => {
    const { result } = renderSelection(["a", "b"]);
    expect(result.current.headerState).toBe("none");

    act(() => result.current.toggle("a"));
    expect(result.current.headerState).toBe("some");

    act(() => result.current.toggleAll());
    expect(result.current.headerState).toBe("all");
    expect(result.current.isSelecting).toBe(true);

    act(() => result.current.toggleAll());
    expect(result.current.headerState).toBe("none");
    expect(result.current.isSelecting).toBe(false);
  });
});
