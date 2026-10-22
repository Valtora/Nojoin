import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNotificationStore } from "@/lib/notificationStore";
import { httpError } from "@/test/httpErrors";
import type { Tag } from "@/types";

const getTags = vi.fn();
const createTag = vi.fn();

vi.mock("@/lib/api", () => ({
  getTags: (...args: unknown[]) => getTags(...args),
  createTag: (...args: unknown[]) => createTag(...args),
}));

import BatchTagModal from "./BatchTagModal";

const CLIENTS = { id: 1, name: "Clients", color: "orange" } as Tag;
const PARTNERS = { id: 2, name: "Partners", color: "blue" } as Tag;

const messages = () =>
  useNotificationStore
    .getState()
    .activeNotifications.map(({ type, message }) => `${type}: ${message}`);

async function createPartners(onApply = vi.fn()) {
  render(
    <BatchTagModal
      isOpen
      onClose={vi.fn()}
      onApply={onApply}
      count={2}
      mode="add"
    />,
  );
  await screen.findByText("Clients");
  fireEvent.change(screen.getByLabelText("Search tags"), {
    target: { value: "Partners" },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: 'Create "Partners"' }));
  });
  fireEvent.click(screen.getByRole("button", { name: "Add Tag" }));
  return onApply;
}

describe("BatchTagModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useNotificationStore.setState({ activeNotifications: [], history: [] });
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    getTags.mockResolvedValueOnce([CLIENTS]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("applies a tag it created", async () => {
    createTag.mockResolvedValue(PARTNERS);
    getTags.mockResolvedValue([CLIENTS, PARTNERS]);

    const onApply = await createPartners();

    expect(createTag).toHaveBeenCalledWith("Partners", expect.any(String));
    expect(onApply).toHaveBeenCalledWith("Partners");
    expect(messages()).toEqual([]);
  });

  it("applies a tag of that name made since the list loaded", async () => {
    createTag.mockRejectedValue(httpError(400, "Tag already exists"));
    getTags.mockResolvedValue([CLIENTS, PARTNERS]);

    const onApply = await createPartners();

    expect(onApply).toHaveBeenCalledWith("Partners");
    expect(messages()).toEqual([]);
  });

  it("names the failed action when the tag was not made", async () => {
    createTag.mockRejectedValue(httpError(500));
    getTags.mockResolvedValue([CLIENTS]);

    const onApply = await createPartners();

    expect(onApply).not.toHaveBeenCalled();
    expect(messages()).toEqual(["error: Failed to create tag."]);
  });
});
