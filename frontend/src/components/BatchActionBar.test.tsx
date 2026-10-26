import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ViewType } from "@/lib/store";

import BatchActionBar from "./BatchActionBar";

vi.mock("./BatchTagModal", () => ({
  default: ({
    isOpen,
    onApply,
  }: {
    isOpen: boolean;
    onApply: (tagName: string) => void;
  }) =>
    isOpen ? (
      <button type="button" onClick={() => onApply("Board")}>
        Apply Board
      </button>
    ) : null,
}));

const renderBar = (view: ViewType, count = 2) => {
  const props = { onClear: vi.fn(), onAction: vi.fn(), onTag: vi.fn() };
  render(<BatchActionBar view={view} count={count} {...props} />);
  return props;
};

const bar = () => screen.getByRole("group", { name: "Selected recordings" });

const actionLabels = () =>
  within(bar())
    .getAllByRole("button")
    .map((button) => button.textContent)
    .filter((label) => label !== "");

describe("BatchActionBar", () => {
  it.each<[ViewType, string[]]>([
    ["recordings", ["Tag", "Archive", "Move to Bin"]],
    ["archived", ["Tag", "Unarchive", "Move to Bin"]],
    ["deleted", ["Restore", "Delete Permanently"]],
  ])("offers the %s view's actions as labelled buttons", (view, labels) => {
    renderBar(view);

    expect(actionLabels()).toEqual(labels);
  });

  it("gives moving to the bin and deleting for good different icons", () => {
    const props = { count: 2, onClear: vi.fn(), onAction: vi.fn(), onTag: vi.fn() };
    render(
      <>
        <BatchActionBar view="recordings" {...props} />
        <BatchActionBar view="deleted" {...props} />
      </>,
    );
    const iconOf = (name: string) =>
      screen.getByRole("button", { name }).querySelector("svg")?.getAttribute("class");

    expect(iconOf("Move to Bin")).toBeTruthy();
    expect(iconOf("Delete Permanently")).not.toBe(iconOf("Move to Bin"));
  });

  it("hands the chosen action to the list", () => {
    const { onAction } = renderBar("deleted");

    fireEvent.click(screen.getByRole("button", { name: "Delete Permanently" }));

    expect(onAction).toHaveBeenCalledWith("permanentDelete");
  });

  it("asks for a tag before tagging", () => {
    const { onAction, onTag } = renderBar("archived");

    fireEvent.click(screen.getByRole("button", { name: "Tag" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply Board" }));

    expect(onTag).toHaveBeenCalledWith("Board");
    expect(onAction).not.toHaveBeenCalled();
  });

  it("clears the selection from a named button or with Escape", () => {
    const { onClear } = renderBar("recordings");

    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    fireEvent.keyDown(screen.getByRole("button", { name: "Archive" }), {
      key: "Escape",
    });

    expect(onClear).toHaveBeenCalledTimes(2);
  });

  it("announces the count politely, and stays mounted with nothing selected", () => {
    const { rerender } = render(
      <BatchActionBar
        view="recordings"
        count={0}
        onClear={vi.fn()}
        onAction={vi.fn()}
        onTag={vi.fn()}
      />,
    );
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("");
    expect(screen.queryByRole("group")).not.toBeInTheDocument();

    rerender(
      <BatchActionBar
        view="recordings"
        count={1}
        onClear={vi.fn()}
        onAction={vi.fn()}
        onTag={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toBe(status);
    expect(status).toHaveTextContent("1 recording selected");
  });
});
