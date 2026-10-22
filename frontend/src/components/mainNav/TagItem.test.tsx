import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Tag } from "@/types";

import TagItem from "./TagItem";

const CLIENTS = { id: 1, name: "Clients", color: "orange" } as Tag;

function renderEditing(onRename: (name: string) => Promise<boolean>) {
  const onCancelEdit = vi.fn();
  render(
    <TagItem
      tag={CLIENTS}
      isSelected={false}
      onToggle={vi.fn()}
      onColorChange={vi.fn()}
      onDelete={vi.fn()}
      onRename={onRename}
      onAddChild={vi.fn()}
      onContextMenu={vi.fn()}
      isEditing
      onStartEdit={vi.fn()}
      onCancelEdit={onCancelEdit}
      collapsed={false}
      lines={[]}
      hasChildren={false}
      childCount={0}
      isExpanded={false}
      onToggleExpand={vi.fn()}
    />,
  );
  const input = screen.getByDisplayValue("Clients") as HTMLInputElement;
  return { input, onCancelEdit };
}

describe("TagItem rename", () => {
  it("keeps a refused name in the field while it has focus", async () => {
    const onRename = vi.fn().mockResolvedValue(false);
    const { input, onCancelEdit } = renderEditing(onRename);

    input.focus();
    fireEvent.change(input, { target: { value: "Suppliers" } });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });

    expect(onRename).toHaveBeenCalledWith("Suppliers");
    expect(input.value).toBe("Suppliers");
    expect(onCancelEdit).not.toHaveBeenCalled();
  });

  it("abandons a refused name on leaving the field, without sending it again", async () => {
    const onRename = vi.fn().mockResolvedValue(false);
    const { input, onCancelEdit } = renderEditing(onRename);

    input.focus();
    fireEvent.change(input, { target: { value: "Suppliers" } });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });
    act(() => input.blur());

    expect(onRename).toHaveBeenCalledTimes(1);
    expect(onCancelEdit).toHaveBeenCalledTimes(1);
  });

  it("sends a corrected name when the field is left", async () => {
    const onRename = vi.fn().mockResolvedValueOnce(false).mockResolvedValue(true);
    const { input, onCancelEdit } = renderEditing(onRename);

    input.focus();
    fireEvent.change(input, { target: { value: "Suppliers" } });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });
    fireEvent.change(input, { target: { value: "Partners" } });
    await act(async () => input.blur());

    expect(onRename).toHaveBeenLastCalledWith("Partners");
    expect(onCancelEdit).not.toHaveBeenCalled();
  });

  it("closes the field when a name sent on leaving it is refused", async () => {
    const onRename = vi.fn().mockResolvedValue(false);
    const { input, onCancelEdit } = renderEditing(onRename);

    input.focus();
    fireEvent.change(input, { target: { value: "Suppliers" } });
    await act(async () => input.blur());

    expect(onRename).toHaveBeenCalledWith("Suppliers");
    expect(onCancelEdit).toHaveBeenCalledTimes(1);
  });

  it("sends a name once while its rename is in flight", async () => {
    let answer: (saved: boolean) => void = () => undefined;
    const onRename = vi.fn(
      () => new Promise<boolean>((resolve) => (answer = resolve)),
    );
    const { input, onCancelEdit } = renderEditing(onRename);

    input.focus();
    fireEvent.change(input, { target: { value: "Suppliers" } });
    fireEvent.keyDown(input, { key: "Enter" });
    act(() => input.blur());
    await act(async () => answer(false));

    expect(onRename).toHaveBeenCalledTimes(1);
    expect(onCancelEdit).toHaveBeenCalledTimes(1);
  });
});
