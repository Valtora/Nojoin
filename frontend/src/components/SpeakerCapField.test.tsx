import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import SpeakerCapField, {
  parseSpeakerCap,
  steppedSpeakerCap,
} from "./SpeakerCapField";

describe("parseSpeakerCap", () => {
  it("treats an empty field as auto-detect", () => {
    expect(parseSpeakerCap("")).toBeNull();
    expect(parseSpeakerCap("   ")).toBeNull();
  });

  it("accepts whole numbers within range", () => {
    expect(parseSpeakerCap("1")).toBe(1);
    expect(parseSpeakerCap("2")).toBe(2);
    expect(parseSpeakerCap("50")).toBe(50);
    expect(parseSpeakerCap(" 7 ")).toBe(7);
  });

  it("reports unusable input as undefined rather than as auto-detect", () => {
    // undefined means "do not commit"; returning null here would silently wipe
    // a cap the user had already set.
    expect(parseSpeakerCap("0")).toBeUndefined();
    expect(parseSpeakerCap("-2")).toBeUndefined();
    expect(parseSpeakerCap("51")).toBeUndefined();
    expect(parseSpeakerCap("2.5")).toBeUndefined();
    expect(parseSpeakerCap("two")).toBeUndefined();
  });
});

describe("SpeakerCapField", () => {
  it("commits a valid number on blur", () => {
    const onCommit = vi.fn();
    render(<SpeakerCapField value={null} onCommit={onCommit} />);

    const input = screen.getByLabelText(/max speakers/i);
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.blur(input);

    expect(onCommit).toHaveBeenCalledWith(3);
  });

  it("commits null when the field is cleared", () => {
    const onCommit = vi.fn();
    render(<SpeakerCapField value={4} onCommit={onCommit} />);

    const input = screen.getByLabelText(/max speakers/i);
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);

    expect(onCommit).toHaveBeenCalledWith(null);
  });

  it("does not commit an out-of-range value and restores the previous one", () => {
    const onCommit = vi.fn();
    render(<SpeakerCapField value={2} onCommit={onCommit} />);

    const input = screen.getByLabelText(/max speakers/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "0" } });
    fireEvent.blur(input);

    expect(onCommit).not.toHaveBeenCalled();
    expect(input.value).toBe("2");
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("does not re-commit an unchanged value", () => {
    const onCommit = vi.fn();
    render(<SpeakerCapField value={3} onCommit={onCommit} />);

    const input = screen.getByLabelText(/max speakers/i);
    fireEvent.focus(input);
    fireEvent.blur(input);

    expect(onCommit).not.toHaveBeenCalled();
  });

  it("reverts the draft on Escape without committing", () => {
    const onCommit = vi.fn();
    render(<SpeakerCapField value={5} onCommit={onCommit} />);

    const input = screen.getByLabelText(/max speakers/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "9" } });
    fireEvent.keyDown(input, { key: "Escape" });

    expect(input.value).toBe("5");
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("keeps the user's in-progress edit when the external value changes", () => {
    const onCommit = vi.fn();
    const { rerender } = render(
      <SpeakerCapField value={null} onCommit={onCommit} />,
    );

    const input = screen.getByLabelText(/max speakers/i) as HTMLInputElement;
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "4" } });

    // A background poll refreshing the recording must not yank the field.
    rerender(<SpeakerCapField value={null} onCommit={onCommit} />);

    expect(input.value).toBe("4");
  });

  it("shows the auto-detect placeholder when no cap is set", () => {
    render(<SpeakerCapField value={null} onCommit={vi.fn()} />);
    expect(screen.getByLabelText(/max speakers/i)).toHaveAttribute(
      "placeholder",
      "Auto-detect",
    );
  });
});

describe("steppedSpeakerCap", () => {
  it("starts at 2 rather than 1 from auto-detect", () => {
    // A cap of one means "treat the whole meeting as one speaker", which is
    // almost never what a first press of + is reaching for.
    expect(steppedSpeakerCap(null, 1)).toBe(2);
  });

  it("has nothing to decrement to from auto-detect", () => {
    expect(steppedSpeakerCap(null, -1)).toBeUndefined();
  });

  it("returns to auto-detect when stepping below the minimum", () => {
    expect(steppedSpeakerCap(1, -1)).toBeNull();
  });

  it("refuses to step past the maximum", () => {
    expect(steppedSpeakerCap(50, 1)).toBeUndefined();
    expect(steppedSpeakerCap(49, 1)).toBe(50);
  });
});

describe("SpeakerCapField stepper", () => {
  const increase = () =>
    screen.getByRole("button", { name: /increase speaker limit/i });
  const decrease = () =>
    screen.getByRole("button", { name: /decrease speaker limit/i });

  it("commits immediately on step, since there is no blur to wait for", () => {
    const onCommit = vi.fn();
    render(<SpeakerCapField value={3} onCommit={onCommit} />);

    fireEvent.click(increase());
    expect(onCommit).toHaveBeenCalledWith(4);
  });

  it("steps down to auto-detect and disables further decrements", () => {
    const onCommit = vi.fn();
    const { rerender } = render(
      <SpeakerCapField value={1} onCommit={onCommit} />,
    );

    fireEvent.click(decrease());
    expect(onCommit).toHaveBeenCalledWith(null);

    rerender(<SpeakerCapField value={null} onCommit={onCommit} />);
    expect(decrease()).toBeDisabled();
  });

  it("disables the increment at the maximum", () => {
    render(<SpeakerCapField value={50} onCommit={vi.fn()} />);
    expect(increase()).toBeDisabled();
    expect(decrease()).toBeEnabled();
  });

  it("disables both steps when the field is disabled", () => {
    render(<SpeakerCapField value={3} onCommit={vi.fn()} disabled />);
    expect(increase()).toBeDisabled();
    expect(decrease()).toBeDisabled();
  });

  it("suppresses the native number spinner", () => {
    render(<SpeakerCapField value={3} onCommit={vi.fn()} />);
    // Still a number input, so arrow keys keep working; the browser's own
    // spinner is what the flanking buttons replace.
    const input = screen.getByLabelText(/max speakers/i);
    expect(input).toHaveAttribute("type", "number");
    expect(input.className).toContain("[appearance:textfield]");
  });
});

describe("SpeakerCapField inline layout", () => {
  it("keeps the hint available without occupying a line", () => {
    render(
      <SpeakerCapField value={null} onCommit={vi.fn()} layout="inline" liveHint />,
    );

    const hint = screen.getByText(/applied when you stop/i);
    expect(hint).toHaveClass("sr-only");
    expect(screen.getByLabelText(/max speakers/i)).toBeInTheDocument();
  });

  it("surfaces the hint visibly when an entry is rejected", () => {
    render(<SpeakerCapField value={2} onCommit={vi.fn()} layout="inline" />);

    const input = screen.getByLabelText(/max speakers/i);
    fireEvent.change(input, { target: { value: "99" } });
    fireEvent.blur(input);

    const hint = screen.getByText(/enter a whole number between 1 and 50/i);
    expect(hint).not.toHaveClass("sr-only");
  });
});
