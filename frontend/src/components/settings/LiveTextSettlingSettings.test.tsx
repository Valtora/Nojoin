import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import LiveTextSettlingSettings from "./LiveTextSettlingSettings";

describe("Live word settling settings", () => {
  it("persists each policy setting while retaining the engine selection", () => {
    const onPersist = vi.fn();
    render(<LiveTextSettlingSettings settings={{ transcription_backend: "canary" }} isAdmin onPersist={onPersist} />);
    fireEvent.change(screen.getByRole("combobox", { name: "Live word settling" }), { target: { value: "auto" } });
    expect(onPersist).toHaveBeenLastCalledWith({ transcription_backend: "canary", live_text_settling: "auto" });
    fireEvent.change(screen.getByRole("combobox", { name: "Unchanged comparisons" }), { target: { value: "5" } });
    expect(onPersist).toHaveBeenLastCalledWith({ transcription_backend: "canary", live_text_settling_k: 5 });
    fireEvent.change(screen.getByRole("combobox", { name: "Settling window" }), { target: { value: "15" } });
    expect(onPersist).toHaveBeenLastCalledWith({ transcription_backend: "canary", live_text_settling_window_s: 15 });
  });

  it("shows the shipped defaults and reserves changes for administrators", () => {
    render(<LiveTextSettlingSettings settings={{}} isAdmin={false} onPersist={vi.fn()} />);
    const controls = screen.getAllByRole("combobox") as HTMLSelectElement[];
    expect(controls.map((control) => control.value)).toEqual(["off", "3", "10"]);
    expect(controls.every((control) => control.disabled)).toBe(true);
  });
});
