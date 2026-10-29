import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import LiveTextSettlingSettings from "./LiveTextSettlingSettings";

describe("Live word settling settings", () => {
  it("persists each policy setting while retaining the engine selection", () => {
    const onPersist = vi.fn();
    render(<LiveTextSettlingSettings settings={{ transcription_backend: "canary" }} isAdmin onPersist={onPersist} />);
    fireEvent.change(screen.getByRole("combobox", { name: "Live word settling" }), { target: { value: "off" } });
    expect(onPersist).toHaveBeenLastCalledWith({ transcription_backend: "canary", live_text_settling: "off" });
    fireEvent.change(screen.getByRole("combobox", { name: "Unchanged comparisons" }), { target: { value: "5" } });
    expect(onPersist).toHaveBeenLastCalledWith({ transcription_backend: "canary", live_text_settling_k: 5 });
    fireEvent.change(screen.getByRole("combobox", { name: "Settling window" }), { target: { value: "15" } });
    expect(onPersist).toHaveBeenLastCalledWith({ transcription_backend: "canary", live_text_settling_window_s: 15 });
  });

  it("shows the shipped defaults and reserves changes for administrators", () => {
    render(<LiveTextSettlingSettings settings={{}} isAdmin={false} onPersist={vi.fn()} />);
    const controls = screen.getAllByRole("combobox") as HTMLSelectElement[];
    expect(controls.map((control) => control.value)).toEqual(["auto", "3", "10"]);
    expect(controls.every((control) => control.disabled)).toBe(true);
  });

  it("shows an explicit Off choice instead of the shipped default", () => {
    const onPersist = vi.fn();
    render(<LiveTextSettlingSettings settings={{ live_text_settling: "off" }} isAdmin onPersist={onPersist} />);
    expect((screen.getByRole("combobox", { name: "Live word settling" }) as HTMLSelectElement).value).toBe("off");
    fireEvent.change(screen.getByRole("combobox", { name: "Live word settling" }), { target: { value: "auto" } });
    expect(onPersist).toHaveBeenLastCalledWith({ live_text_settling: "auto" });
  });

  it("says what the selected number of comparisons asks of a word", () => {
    const { rerender } = render(<LiveTextSettlingSettings settings={{}} isAdmin onPersist={vi.fn()} />);
    const comparisons = screen.getByRole("combobox", { name: "Unchanged comparisons" });
    expect(comparisons).toHaveAccessibleDescription(/^With 3, a word locks once it comes out the same in 4 transcriptions in a row\./);
    rerender(<LiveTextSettlingSettings settings={{ live_text_settling_k: 1 }} isAdmin onPersist={vi.fn()} />);
    expect(comparisons).toHaveAccessibleDescription(/^With 1, a word locks once it comes out the same in 2 transcriptions in a row\./);
  });

  it("says Auto settles on a GPU server and keeps whole lines on a CPU-only one", () => {
    render(<LiveTextSettlingSettings settings={{}} isAdmin onPersist={vi.fn()} />);
    expect(screen.getByRole("combobox", { name: "Live word settling" })).toHaveAccessibleDescription(
      /Auto settles live text on a GPU server and shows ordinary whole lines on a CPU-only one\./,
    );
  });

  it("offers settling on CPU too, and says what it costs", () => {
    const onPersist = vi.fn();
    render(<LiveTextSettlingSettings settings={{}} isAdmin onPersist={onPersist} />);
    const mode = screen.getByRole("combobox", { name: "Live word settling" });
    expect(mode).toHaveAccessibleDescription(/needs a lot of CPU and RAM/);
    fireEvent.change(mode, { target: { value: "always" } });
    expect(onPersist).toHaveBeenLastCalledWith({ live_text_settling: "always" });
  });
});
