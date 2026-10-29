import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useLayoutEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import MeetingEdgePanel from "./MeetingEdgePanel";

describe("MeetingEdgePanel", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("cancels a pending focus debounce on read-only transition while retaining guidance and history", async () => {
    vi.useFakeTimers();
    const onSaveFocus = vi.fn().mockResolvedValue(undefined);
    const props = {
      onSaveFocus,
      focusText: "Original focus",
      payload: {
        summary: "Retained summary",
        questions: ["Who owns delivery?"],
        points: ["Check the launch date."],
        concepts: [],
        concept_history: [{ term: "SLO", explanation: "Service level objective" }],
      },
    };
    const { rerender } = render(<MeetingEdgePanel {...props} />);
    fireEvent.change(screen.getByDisplayValue("Original focus"), { target: { value: "Pending focus" } });
    rerender(<MeetingEdgePanel {...props} readOnly />);
    // The cancelled draft was never saved, so it does not stay beside "Read-only".
    expect(screen.queryByDisplayValue("Pending focus")).toBeNull();
    expect(screen.getByDisplayValue("Original focus")).toHaveAttribute("readonly");
    expect(screen.getByText("Read-only")).toBeVisible();
    expect(screen.getByText("Retained summary")).toBeVisible();
    expect(screen.getByText("Who owns delivery?")).toBeVisible();
    expect(screen.getByText("Check the launch date.")).toBeVisible();
    expect(screen.getByText("Service level objective")).toBeVisible();
    // Reopening must not resurrect the timer cancelled by the settled state.
    rerender(<MeetingEdgePanel {...props} />);
    await act(async () => vi.advanceTimersByTime(1200));
    expect(onSaveFocus).not.toHaveBeenCalled();
    fireEvent.change(screen.getByDisplayValue("Original focus"), { target: { value: "Deliberate new focus" } });
    await act(async () => vi.advanceTimersByTime(1200));
    expect(onSaveFocus).toHaveBeenCalledExactlyOnceWith("Deliberate new focus");
    expect(screen.getByText("Saved")).toBeVisible();
  });

  it("blocks blur saves during the read-only commit before passive effects run", async () => {
    const onSaveFocus = vi.fn().mockResolvedValue(undefined);
    function Transition({ readOnly }: { readOnly: boolean }) {
      useLayoutEffect(() => {
        if (readOnly) fireEvent.blur(screen.getByRole("textbox"));
      }, [readOnly]);
      return <MeetingEdgePanel readOnly={readOnly} onSaveFocus={onSaveFocus} focusText="Saved focus" />;
    }
    const { rerender } = render(<Transition readOnly={false} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Pending focus" } });
    await act(async () => rerender(<Transition readOnly />));
    expect(onSaveFocus).not.toHaveBeenCalled();
  });

  it("shows the focus saved before the read-only transition, not a draft dropped by it", async () => {
    vi.useFakeTimers();
    const onSaveFocus = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(<MeetingEdgePanel onSaveFocus={onSaveFocus} focusText="Original focus" />);
    const box = screen.getByRole("textbox");
    fireEvent.change(box, { target: { value: "Saved focus" } });
    await act(async () => vi.advanceTimersByTime(1200));
    expect(onSaveFocus).toHaveBeenCalledExactlyOnceWith("Saved focus");
    // Typed within the debounce before Stop: never sent.
    fireEvent.change(box, { target: { value: "Saved focus and more" } });
    rerender(<MeetingEdgePanel onSaveFocus={onSaveFocus} focusText="Original focus" readOnly />);
    expect(screen.getByRole("textbox")).toHaveValue("Saved focus");
    await act(async () => vi.advanceTimersByTime(1200));
    expect(onSaveFocus).toHaveBeenCalledOnce();
  });

  it("ignores a failed focus save from before the read-only transition", async () => {
    let rejectSave!: (reason: Error) => void;
    const onSaveFocus = vi.fn(() => new Promise<void>((_, reject) => { rejectSave = reject; }));
    const { rerender } = render(<MeetingEdgePanel onSaveFocus={onSaveFocus} focusText="Saved focus" />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "New focus" } });
    fireEvent.blur(screen.getByRole("textbox"));
    expect(onSaveFocus).toHaveBeenCalledOnce();
    rerender(<MeetingEdgePanel onSaveFocus={onSaveFocus} focusText="Saved focus" readOnly />);
    await act(async () => rejectSave(new Error("Late failure")));
    rerender(<MeetingEdgePanel onSaveFocus={onSaveFocus} focusText="Saved focus" />);
    expect(screen.queryByText("Save failed")).not.toBeInTheDocument();
  });

  it("does not restore an obsolete context level after a read-only transition", async () => {
    let rejectSave!: (reason: Error) => void;
    const onSaveContextLevel = vi.fn(() => new Promise<void>((_, reject) => { rejectSave = reject; }));
    const props = { onSaveFocus: vi.fn().mockResolvedValue(undefined), onSaveContextLevel };
    const { rerender } = render(<MeetingEdgePanel {...props} contextLevel={2} />);
    fireEvent.click(screen.getByRole("button", { name: /Meeting Edge Technical Context/ }));
    fireEvent.change(screen.getByRole("slider"), { target: { value: "5" } });
    expect(onSaveContextLevel).toHaveBeenCalledWith(5);
    rerender(<MeetingEdgePanel {...props} contextLevel={4} readOnly />);
    await act(async () => rejectSave(new Error("Late failure")));
    expect(screen.getByRole("slider")).toBeDisabled();
    expect(screen.getByRole("slider")).toHaveValue("4");
  });

  it("keeps retained guidance readable and blocks focus/context saves in read-only mode", async () => {
    const onSaveFocus = vi.fn().mockResolvedValue(undefined);
    const onSaveContextLevel = vi.fn().mockResolvedValue(undefined);
    render(<MeetingEdgePanel readOnly focusText="Previous focus" onSaveFocus={onSaveFocus}
      onSaveContextLevel={onSaveContextLevel} payload={{ summary: "Previous guidance", questions: [], points: [], concepts: [] }} />);
    const focus = screen.getByDisplayValue("Previous focus");
    expect(focus).toHaveAttribute("readonly");
    fireEvent.change(focus, { target: { value: "A new focus" } });
    fireEvent.blur(focus);
    fireEvent.click(screen.getByRole("button", { name: /Meeting Edge Technical Context/ }));
    const slider = screen.getByLabelText("Meeting Edge Technical Context sensitivity");
    expect(slider).toBeDisabled();
    fireEvent.change(slider, { target: { value: "5" } });
    await Promise.resolve();
    expect(onSaveFocus).not.toHaveBeenCalled();
    expect(onSaveContextLevel).not.toHaveBeenCalled();
    expect(screen.getByText("Previous guidance")).toBeInTheDocument();
  });

  it("shows the live-page Technical Context slider and persists changes", async () => {
    const onSaveContextLevel = vi.fn().mockResolvedValue(undefined);

    render(
      <MeetingEdgePanel
        onSaveFocus={vi.fn().mockResolvedValue(undefined)}
        contextLevel={2}
        onSaveContextLevel={onSaveContextLevel}
      />,
    );

    // The section is collapsed by default: it is a setting rather than
    // guidance, and a collapsed section is unmounted rather than hidden.
    expect(
      screen.queryByLabelText("Meeting Edge Technical Context sensitivity"),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /Meeting Edge Technical Context/ }),
    );

    const slider = screen.getByLabelText(
      "Meeting Edge Technical Context sensitivity",
    );

    expect(slider).toBeInTheDocument();
    expect(slider).toHaveValue("2");
    expect(screen.getAllByText("Most Complex")).toHaveLength(1);
    expect(screen.getAllByText("Least Complex")).toHaveLength(1);

    fireEvent.change(slider, { target: { value: "5" } });

    await waitFor(() => {
      expect(onSaveContextLevel).toHaveBeenCalledWith(5);
    });

    await waitFor(() => {
      expect(slider).toHaveValue("5");
    });
  });

  it("folds questions and points together, from either header", () => {
    render(
      <MeetingEdgePanel
        onSaveFocus={vi.fn().mockResolvedValue(undefined)}
        payload={{
          summary: "A summary.",
          questions: ["Which index is being used?"],
          points: ["The comparison is price-action based."],
          concepts: [],
        }}
      />,
    );

    expect(screen.getByText("Which index is being used?")).toBeInTheDocument();

    // They are grid siblings: collapsing one alone would leave its cell hollow
    // while the other still set the row height, so the space would move rather
    // than be recovered.
    fireEvent.click(screen.getByRole("button", { name: /Questions to Ask/ }));
    expect(
      screen.queryByText("Which index is being used?"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("The comparison is price-action based."),
    ).not.toBeInTheDocument();

    // And either header brings both back.
    fireEvent.click(screen.getByRole("button", { name: /Points to Raise/ }));
    expect(screen.getByText("Which index is being used?")).toBeInTheDocument();
    expect(
      screen.getByText("The comparison is price-action based."),
    ).toBeInTheDocument();
  });
});
