import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CAPTURE_SEGMENT_SECONDS } from "@/lib/capture/recorder";

import LiveTextSettlingSettings, { SETTLING_STEP_S, settlingCanAgree } from "./LiveTextSettlingSettings";

const MODE = "Refine live words";
const K = "How soon a word is fixed";
const WINDOW = "Audio checked again";

const openFineTune = () => fireEvent.click(screen.getByRole("button", { name: "Fine-tune" }));

afterEach(() => {
  window.location.hash = "";
});

describe("Refine live words", () => {
  it("names each mode by what it does, storing the same keys as before", () => {
    const onPersist = vi.fn();
    render(<LiveTextSettlingSettings settings={{ transcription_backend: "canary" }} isAdmin onPersist={onPersist} />);
    const mode = screen.getByRole("combobox", { name: MODE }) as HTMLSelectElement;

    expect([...mode.options].map((option) => [option.value, option.text])).toEqual([
      ["off", "Off"],
      ["auto", "On with a GPU"],
      ["always", "On, also without a GPU (heavy)"],
    ]);
    fireEvent.change(mode, { target: { value: "off" } });
    expect(onPersist).toHaveBeenLastCalledWith({ transcription_backend: "canary", live_text_settling: "off" });
    fireEvent.change(mode, { target: { value: "always" } });
    expect(onPersist).toHaveBeenLastCalledWith({ transcription_backend: "canary", live_text_settling: "always" });
  });

  it("defaults to On with a GPU and says what each choice shows", () => {
    render(<LiveTextSettlingSettings settings={{}} isAdmin onPersist={vi.fn()} />);
    const mode = screen.getByRole("combobox", { name: MODE }) as HTMLSelectElement;

    expect(mode.value).toBe("auto");
    expect(mode).toHaveAccessibleDescription(/corrects the newest ones as more speech arrives/);
    expect(mode).toHaveAccessibleDescription(/Off shows the live transcript a line at a time/);
    expect(mode).toHaveAccessibleDescription(/high CPU and RAM cost/);
  });

  it("keeps the timing collapsed under Fine-tune until asked", () => {
    const onPersist = vi.fn();
    render(<LiveTextSettlingSettings settings={{}} isAdmin onPersist={onPersist} />);
    const toggle = screen.getByRole("button", { name: "Fine-tune" });

    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("combobox", { name: K })).not.toBeInTheDocument();

    openFineTune();

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const k = screen.getByRole("combobox", { name: K }) as HTMLSelectElement;
    const windowSelect = screen.getByRole("combobox", { name: WINDOW }) as HTMLSelectElement;
    expect([k.value, windowSelect.value]).toEqual(["3", "10"]);
    expect(k.options[0].text).toBe("After 1 matching check");
    expect(k.options[2].text).toBe("After 3 matching checks");
    expect(k).toHaveAccessibleDescription(/^A word is fixed once it comes out the same 3 more times in a row\./);
    expect(windowSelect).toHaveAccessibleDescription(/Some combinations fix every word before it can be checked again\./);

    fireEvent.change(k, { target: { value: "2" } });
    expect(onPersist).toHaveBeenLastCalledWith({ live_text_settling_k: 2 });
    fireEvent.change(windowSelect, { target: { value: "15" } });
    expect(onPersist).toHaveBeenLastCalledWith({ live_text_settling_window_s: 15 });
  });

  it("opens Fine-tune when a search result points inside it", () => {
    window.location.hash = "#transcription-live-settling-window";
    render(<LiveTextSettlingSettings settings={{}} isAdmin onPersist={vi.fn()} />);

    expect(screen.getByRole("combobox", { name: WINDOW })).toBeInTheDocument();
  });

  it("warns when the audio checked again is too short for the matching checks", () => {
    const { rerender } = render(
      <LiveTextSettlingSettings settings={{ live_text_settling_k: 5, live_text_settling_window_s: 10 }} isAdmin onPersist={vi.fn()} />,
    );

    // Visible while collapsed, so a hand-edited config.json does not hide it.
    expect(screen.getByText(/The current timing can fix words as heard/)).toBeInTheDocument();
    openFineTune();
    expect(screen.getByRole("combobox", { name: WINDOW })).toHaveAccessibleDescription(
      /Too short for 5 matching checks: words can be fixed as heard before they are checked again\. Use more than 12 seconds, or fewer checks\./,
    );

    rerender(<LiveTextSettlingSettings settings={{ live_text_settling_k: 5, live_text_settling_window_s: 15 }} isAdmin onPersist={vi.fn()} />);
    expect(screen.queryByText(/Too short for/)).not.toBeInTheDocument();
    expect(screen.queryByText(/The current timing can fix words as heard/)).not.toBeInTheDocument();
  });

  it("reserves every control for administrators", () => {
    render(<LiveTextSettlingSettings settings={{}} isAdmin={false} onPersist={vi.fn()} />);
    openFineTune();

    const controls = screen.getAllByRole("combobox") as HTMLSelectElement[];
    expect(controls.map((control) => control.value)).toEqual(["auto", "3", "10"]);
    expect(controls.every((control) => control.disabled)).toBe(true);
  });
});

describe("settlingCanAgree (#730: a 10 s window never locked by agreement at K=5)", () => {
  it("steps once per capture segment", () => {
    expect(SETTLING_STEP_S).toBe(CAPTURE_SEGMENT_SECONDS);
  });

  it.each([
    [3, 10, true],
    [4, 10, false],
    [5, 10, false],
    [5, 15, true],
    [1, 5, true],
    [1, 2, false],
    [10, 30, true],
  ])("K=%i with a %i s window: %s", (k, windowS, expected) => {
    expect(settlingCanAgree(k, windowS)).toBe(expected);
  });
});
