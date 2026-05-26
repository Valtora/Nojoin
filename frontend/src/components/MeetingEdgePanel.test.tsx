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

    const slider = screen.getByLabelText(
      "Meeting Edge Technical Context sensitivity",
    );

    expect(slider).toBeInTheDocument();
    expect(slider).toHaveValue("2");
    expect(screen.getAllByText("Focused")).toHaveLength(1);
    expect(
      screen.queryByText(
        /Explain domain-specific or advanced terms, but skip common software and workplace language\./i,
      ),
    ).not.toBeInTheDocument();

    fireEvent.change(slider, { target: { value: "5" } });

    await waitFor(() => {
      expect(onSaveContextLevel).toHaveBeenCalledWith(5);
    });

    await waitFor(() => {
      expect(slider).toHaveValue("5");
    });
  });
});