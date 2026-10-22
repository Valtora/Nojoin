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

function renderModal() {
  render(
    <AddTagModal
      isOpen
      onClose={vi.fn()}
      recordingId={"rec-1" as RecordingId}
      currentTags={[]}
    />,
  );
}

async function openMenuItem(tagName: string, item: string) {
  renderModal();
  fireEvent.click(
    await screen.findByRole("button", { name: `Tag options for ${tagName}` }),
  );
  fireEvent.click(screen.getByText(item));
}

async function submitSubTag(name: string) {
  fireEvent.change(screen.getByLabelText("Sub-tag Name"), {
    target: { value: name },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
  });
}

async function enterInSearch(name: string) {
  await screen.findByRole("button", { name: "Clients" });
  const search = screen.getByLabelText("Search or create tag");
  fireEvent.change(search, { target: { value: name } });
  await act(async () => {
    fireEvent.keyDown(search, { key: "Enter" });
  });
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

    await submitSubTag("Partners");

    expect(createTag).toHaveBeenCalledWith("Partners", expect.any(String), 1);
    expect(messages()).toEqual(["error: Parent tag not found"]);
    expect(screen.getByLabelText("Sub-tag Name")).toHaveValue("Partners");
  });

  it("asks for a sub-tag with a name in use and says why it was refused", async () => {
    createTag.mockRejectedValue(httpError(400, "Tag already exists"));
    await openMenuItem("Clients", "Add Sub-tag");

    await submitSubTag("suppliers");

    expect(createTag).toHaveBeenCalledWith("suppliers", expect.any(String), 1);
    expect(addTagToRecording).not.toHaveBeenCalled();
    expect(messages()).toEqual(["error: Tag already exists"]);
    expect(screen.getByLabelText("Sub-tag Name")).toHaveValue("suppliers");
  });

  it("creates a sub-tag without adding it to the meeting", async () => {
    createTag.mockResolvedValue({ id: 3, name: "Partners", parent_id: 1 });
    await openMenuItem("Clients", "Add Sub-tag");

    await submitSubTag("Partners");

    expect(createTag).toHaveBeenCalledWith("Partners", expect.any(String), 1);
    expect(addTagToRecording).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Sub-tag Name")).toBeNull();
    expect(messages()).toEqual([]);
  });

  it("adds a new name to the meeting in one request", async () => {
    renderModal();

    await enterInSearch("Partners");

    expect(createTag).not.toHaveBeenCalled();
    expect(addTagToRecording).toHaveBeenCalledWith(
      "rec-1",
      "Partners",
      expect.any(String),
    );
    expect(getTags).toHaveBeenCalledTimes(2);
  });

  it("adds the tag whose name matches ignoring case", async () => {
    renderModal();

    await enterInSearch("clients");

    expect(createTag).not.toHaveBeenCalled();
    expect(addTagToRecording).toHaveBeenCalledWith("rec-1", "Clients");
  });

  it("says why a tag could not be added to the meeting", async () => {
    addTagToRecording.mockRejectedValue(httpError(404, "Recording not found"));
    renderModal();

    const clients = await screen.findByRole("button", { name: "Clients" });
    await act(async () => {
      fireEvent.click(clients);
    });

    expect(addTagToRecording).toHaveBeenCalledWith("rec-1", "Clients");
    expect(messages()).toEqual(["error: Recording not found"]);
  });
});
