import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import SentenceStartSettings from "./SentenceStartSettings";

describe("Reply starts setting", () => {
  it("is on by default and persists Off alongside the other settings", () => {
    const onPersist = vi.fn();
    render(<SentenceStartSettings settings={{ transcription_backend: "canary" }} isAdmin onPersist={onPersist} />);
    const control = screen.getByRole("switch", { name: "Reply starts" });
    expect(control).toHaveAttribute("aria-checked", "true");
    fireEvent.click(control);
    expect(onPersist).toHaveBeenLastCalledWith({ transcription_backend: "canary", sentence_start_correction: false });
  });

  it("shows a stored Off and reserves changes for administrators", () => {
    render(<SentenceStartSettings settings={{ sentence_start_correction: false }} isAdmin={false} onPersist={vi.fn()} />);
    const control = screen.getByRole("switch", { name: "Reply starts" });
    expect(control).toHaveAttribute("aria-checked", "false");
    expect(control).toBeDisabled();
  });
});
