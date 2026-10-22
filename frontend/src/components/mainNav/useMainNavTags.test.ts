import { act, renderHook, waitFor } from "@testing-library/react";
import type { DragEndEvent } from "@dnd-kit/core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNotificationStore } from "@/lib/notificationStore";
import { httpError } from "@/test/httpErrors";
import type { Tag } from "@/types";

const getTags = vi.fn();
const updateTag = vi.fn();
const createTag = vi.fn();
const deleteTag = vi.fn();

vi.mock("@/lib/api", () => ({
  getTags: (...args: unknown[]) => getTags(...args),
  updateTag: (...args: unknown[]) => updateTag(...args),
  createTag: (...args: unknown[]) => createTag(...args),
  deleteTag: (...args: unknown[]) => deleteTag(...args),
}));

vi.mock("@/lib/store", () => ({
  useNavigationStore: () => ({
    expandedTagIds: [],
    toggleExpandedTag: vi.fn(),
    setExpandedTagIds: vi.fn(),
  }),
}));

import { useMainNavTags } from "./useMainNavTags";

const TAGS = [
  { id: 1, name: "Clients", color: "orange" },
  { id: 2, name: "Suppliers", color: "blue" },
  { id: 3, name: "Archive", color: "green", parent_id: 1 },
] as Tag[];

const messages = () =>
  useNotificationStore
    .getState()
    .activeNotifications.map(({ type, message }) => `${type}: ${message}`);

const drag = (tagId: number, overId: string) =>
  ({ active: { id: `tag-${tagId}` }, over: { id: overId } }) as DragEndEvent;

async function renderTags() {
  const hook = renderHook(() => useMainNavTags());
  await waitFor(() => expect(hook.result.current.tags).toHaveLength(3));
  return hook;
}

describe("useMainNavTags", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useNotificationStore.setState({ activeNotifications: [], history: [] });
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    getTags.mockResolvedValue(TAGS);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("says why a rename was refused and leaves the field open", async () => {
    updateTag.mockRejectedValue(
      httpError(400, "Tag with this name already exists"),
    );
    const { result } = await renderTags();
    act(() => result.current.setEditingTagId(1));

    let saved: boolean | undefined;
    await act(async () => {
      saved = await result.current.handleRenameTag(1, "Suppliers");
    });

    expect(saved).toBe(false);
    expect(messages()).toEqual(["error: Tag with this name already exists"]);
    expect(result.current.editingTagId).toBe(1);
    expect(result.current.tags.find((t) => t.id === 1)?.name).toBe("Clients");
  });

  it("closes the field once a rename is saved", async () => {
    updateTag.mockResolvedValue({ ...TAGS[0], name: "Customers" });
    const { result } = await renderTags();
    act(() => result.current.setEditingTagId(1));

    let saved: boolean | undefined;
    await act(async () => {
      saved = await result.current.handleRenameTag(1, "Customers");
    });

    expect(saved).toBe(true);
    expect(result.current.editingTagId).toBeNull();
    expect(messages()).toEqual([]);
  });

  it("puts a refused move back and says why", async () => {
    updateTag.mockRejectedValue(
      httpError(400, "Tag cannot be nested under one of its own sub-tags"),
    );
    const { result } = await renderTags();

    await act(async () => {
      await result.current.handleDragEnd(drag(2, "tag-1"));
    });

    expect(updateTag).toHaveBeenCalledWith(2, { parent_id: 1 });
    expect(result.current.tags.find((t) => t.id === 2)?.parent_id).toBeUndefined();
    expect(messages()).toEqual([
      "error: Tag cannot be nested under one of its own sub-tags",
    ]);
  });

  it("refuses a drop onto the tag's own sub-tag without asking the API", async () => {
    const { result } = await renderTags();

    await act(async () => {
      await result.current.handleDragEnd(drag(1, "tag-3"));
    });

    expect(updateTag).not.toHaveBeenCalled();
    expect(messages()).toEqual([
      "error: Tag cannot be nested under one of its own sub-tags",
    ]);
  });

  it("keeps the new tag's name when it is refused", async () => {
    createTag.mockRejectedValue(httpError(404, "Parent tag not found"));
    const { result } = await renderTags();
    act(() => {
      result.current.setIsAddingTag(true);
      result.current.setNewTagName("Partners");
    });

    await act(async () => {
      await result.current.handleAddTag();
    });

    expect(result.current.isAddingTag).toBe(true);
    expect(result.current.newTagName).toBe("Partners");
    expect(messages()).toEqual(["error: Parent tag not found"]);
  });

  it("refuses a sub-tag with a name in use and says why", async () => {
    createTag.mockRejectedValue(httpError(400, "Tag already exists"));
    const { result } = await renderTags();
    act(() => result.current.handleAddSubTag(1));

    let created: boolean | undefined;
    await act(async () => {
      created = await result.current.handleCreateTagConfirm("Suppliers");
    });

    expect(createTag).toHaveBeenCalledWith("Suppliers", expect.any(String), 1);
    expect(created).toBe(false);
    expect(result.current.tags).toHaveLength(3);
    expect(messages()).toEqual(["error: Tag already exists"]);
  });

  it("adds a new sub-tag under its parent", async () => {
    const partners = { id: 4, name: "Partners", color: "red", parent_id: 1 };
    createTag.mockResolvedValue(partners);
    const { result } = await renderTags();
    getTags.mockResolvedValue([...TAGS, partners]);
    act(() => result.current.handleAddSubTag(1));

    let created: boolean | undefined;
    await act(async () => {
      created = await result.current.handleCreateTagConfirm("Partners");
    });

    expect(created).toBe(true);
    expect(result.current.tags.map((tag) => tag.id)).toEqual([1, 2, 3, 4]);
    expect(messages()).toEqual([]);
  });

  it("names the failed action when the response gives no reason", async () => {
    updateTag.mockRejectedValue(httpError(500));
    deleteTag.mockRejectedValue(httpError(500));
    const { result } = await renderTags();

    await act(async () => {
      await result.current.handlePromoteToRoot(3);
      await result.current.handleColorChange(1, "red");
    });
    act(() => result.current.handleDeleteTag(2));
    await act(async () => {
      result.current.confirmModal.onConfirm();
    });

    await waitFor(() =>
      expect(messages()).toEqual([
        "error: Failed to move tag.",
        "error: Failed to change tag colour.",
        "error: Failed to delete tag.",
      ]),
    );
    expect(result.current.tags).toHaveLength(3);
  });
});
