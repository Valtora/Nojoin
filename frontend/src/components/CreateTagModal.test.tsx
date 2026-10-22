import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import CreateTagModal from "./CreateTagModal";

async function submit(onConfirm: (name: string) => Promise<boolean>) {
  const onClose = vi.fn();
  render(
    <CreateTagModal
      isOpen
      onClose={onClose}
      onConfirm={onConfirm}
      title="Add Sub-tag"
    />,
  );
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: " Partners " },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
  });
  return onClose;
}

describe("CreateTagModal", () => {
  it("closes once the tag is created", async () => {
    const onConfirm = vi.fn().mockResolvedValue(true);

    const onClose = await submit(onConfirm);

    expect(onConfirm).toHaveBeenCalledWith("Partners");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("stays open with the name typed when the tag is refused", async () => {
    const onConfirm = vi.fn().mockResolvedValue(false);

    const onClose = await submit(onConfirm);

    expect(onConfirm).toHaveBeenCalledWith("Partners");
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue(" Partners ");
    expect(screen.getByRole("button", { name: "Create" })).toBeEnabled();
  });
});
