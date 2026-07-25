import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import SpeakerCapField, { parseSpeakerCap } from "./SpeakerCapField";

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

    const input = screen.getByLabelText(/maximum speakers/i);
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.blur(input);

    expect(onCommit).toHaveBeenCalledWith(3);
  });

  it("commits null when the field is cleared", () => {
    const onCommit = vi.fn();
    render(<SpeakerCapField value={4} onCommit={onCommit} />);

    const input = screen.getByLabelText(/maximum speakers/i);
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);

    expect(onCommit).toHaveBeenCalledWith(null);
  });

  it("does not commit an out-of-range value and restores the previous one", () => {
    const onCommit = vi.fn();
    render(<SpeakerCapField value={2} onCommit={onCommit} />);

    const input = screen.getByLabelText(/maximum speakers/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "0" } });
    fireEvent.blur(input);

    expect(onCommit).not.toHaveBeenCalled();
    expect(input.value).toBe("2");
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("does not re-commit an unchanged value", () => {
    const onCommit = vi.fn();
    render(<SpeakerCapField value={3} onCommit={onCommit} />);

    const input = screen.getByLabelText(/maximum speakers/i);
    fireEvent.focus(input);
    fireEvent.blur(input);

    expect(onCommit).not.toHaveBeenCalled();
  });

  it("reverts the draft on Escape without committing", () => {
    const onCommit = vi.fn();
    render(<SpeakerCapField value={5} onCommit={onCommit} />);

    const input = screen.getByLabelText(/maximum speakers/i) as HTMLInputElement;
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

    const input = screen.getByLabelText(/maximum speakers/i) as HTMLInputElement;
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "4" } });

    // A background poll refreshing the recording must not yank the field.
    rerender(<SpeakerCapField value={null} onCommit={onCommit} />);

    expect(input.value).toBe("4");
  });

  it("shows the auto-detect placeholder when no cap is set", () => {
    render(<SpeakerCapField value={null} onCommit={vi.fn()} />);
    expect(screen.getByLabelText(/maximum speakers/i)).toHaveAttribute(
      "placeholder",
      "Auto-detect",
    );
  });
});
