import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNotificationStore } from "@/lib/notificationStore";
import { httpError } from "@/test/httpErrors";
import type { RecordingId, Tag } from "@/types";

const getTags = vi.fn();
const createTag = vi.fn();
const addTagToRecording = vi.fn();
const updateTag = vi.fn();
const deleteTag = vi.fn();
const removeTagFromRecording = vi.fn();

vi.mock("@/lib/api", () => ({
  getTags: (...args: unknown[]) => getTags(...args),
  createTag: (...args: unknown[]) => createTag(...args),
  addTagToRecording: (...args: unknown[]) => addTagToRecording(...args),
  updateTag: (...args: unknown[]) => updateTag(...args),
  deleteTag: (...args: unknown[]) => deleteTag(...args),
  removeTagFromRecording: (...args: unknown[]) =>
    removeTagFromRecording(...args),
}));

import AddTagModal from "./AddTagModal";

const TAGS = [
  { id: 1, name: "Clients", color: "orange" },
  { id: 2, name: "Suppliers", color: "blue" },
] as Tag[];

const messages = () =>
  useNotificationStore
    .getState()
    .activeNotifications.map(({ type, message }) => `${type}: ${message}`);

async function openMenuItem(tagName: string, item: string) {
  render(
    <AddTagModal
      isOpen
      onClose={vi.fn()}
      recordingId={"rec-1" as RecordingId}
      currentTags={[]}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: `Tag options for ${tagName}` }),
  );
  fireEvent.click(screen.getByText(item));
}

describe("AddTagModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useNotificationStore.setState({ activeNotifications: [], history: [] });
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    getTags.mockResolvedValue(TAGS);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("says why a rename was refused and keeps the name typed", async () => {
    updateTag.mockRejectedValue(
      httpError(400, "Tag with this name already exists"),
    );
    await openMenuItem("Clients", "Rename");

    const field = screen.getByDisplayValue("Clients");
    fireEvent.change(field, { target: { value: "Suppliers" } });
    await act(async () => {
      fireEvent.keyDown(field, { key: "Enter" });
    });

    expect(updateTag).toHaveBeenCalledWith(1, { name: "Suppliers" });
    expect(messages()).toEqual(["error: Tag with this name already exists"]);
    expect(screen.getByDisplayValue("Suppliers")).toBe(field);
  });

  it("keeps the sub-tag form and its name when the sub-tag is refused", async () => {
    createTag.mockRejectedValue(httpError(404, "Parent tag not found"));
    await openMenuItem("Clients", "Add Sub-tag");

    fireEvent.change(screen.getByLabelText("Sub-tag Name"), {
      target: { value: "Partners" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Create" }));
    });

    expect(createTag).toHaveBeenCalledWith("Partners", expect.any(String), 1);
    expect(messages()).toEqual(["error: Parent tag not found"]);
    expect(screen.getByLabelText("Sub-tag Name")).toHaveValue("Partners");
  });

  it("says why a tag could not be added to the meeting", async () => {
    addTagToRecording.mockRejectedValue(httpError(404, "Recording not found"));
    render(
      <AddTagModal
        isOpen
        onClose={vi.fn()}
        recordingId={"rec-1" as RecordingId}
        currentTags={[]}
      />,
    );

    const clients = await screen.findByRole("button", { name: "Clients" });
    await act(async () => {
      fireEvent.click(clients);
    });

    expect(addTagToRecording).toHaveBeenCalledWith("rec-1", "Clients");
    expect(messages()).toEqual(["error: Recording not found"]);
  });
});
