import { renderHook } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { useOnDepsChange } from "./useOnDepsChange";

describe("useOnDepsChange", () => {
  it("does not fire on the first render", () => {
    const onChange = vi.fn();

    renderHook(() => useOnDepsChange([1, "a"], onChange));

    expect(onChange).not.toHaveBeenCalled();
  });

  it("fires once when a dependency changes and not when they stay the same", () => {
    const onChange = vi.fn();
    const { rerender } = renderHook(
      ({ open }: { open: boolean }) => useOnDepsChange([open], onChange),
      { initialProps: { open: false } },
    );

    rerender({ open: false });
    expect(onChange).not.toHaveBeenCalled();

    rerender({ open: true });
    expect(onChange).toHaveBeenCalledTimes(1);

    rerender({ open: true });
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("lets the component reset its own state in the same render", () => {
    const { result, rerender } = renderHook(
      ({ open }: { open: boolean }) => {
        const [draft, setDraft] = useState("typed");
        useOnDepsChange([open], () => {
          if (open) setDraft("");
        });
        return draft;
      },
      { initialProps: { open: false } },
    );

    expect(result.current).toBe("typed");

    rerender({ open: true });
    expect(result.current).toBe("");
  });
});
