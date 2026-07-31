import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import MeetingEdgePanel from "./MeetingEdgePanel";

describe("MeetingEdgePanel", () => {
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

  it("folds a guidance section away without losing its content", () => {
    render(
      <MeetingEdgePanel
        onSaveFocus={vi.fn().mockResolvedValue(undefined)}
        payload={{
          summary: "A summary.",
          questions: ["Which index is being used?"],
          points: [],
          concepts: [],
        }}
      />,
    );

    expect(screen.getByText("Which index is being used?")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Questions to Ask/ }));
    expect(
      screen.queryByText("Which index is being used?"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Questions to Ask/ }));
    expect(screen.getByText("Which index is being used?")).toBeInTheDocument();
  });
});
