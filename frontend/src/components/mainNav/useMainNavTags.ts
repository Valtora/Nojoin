import { useCallback, useEffect, useMemo, useState } from "react";
import { DragEndEvent, useDroppable } from "@dnd-kit/core";

import { createTag, deleteTag, getTags, updateTag } from "@/lib/api";
import { DEFAULT_TAG_COLORS } from "@/lib/constants";
import { useNavigationStore } from "@/lib/store";
import { Tag } from "@/types";

export interface TagWithChildren extends Tag {
  children: TagWithChildren[];
}

interface ConfirmModalState {
  isOpen: boolean;
  title: string;
  message: string;
  onConfirm: () => void;
  isDangerous?: boolean;
}

interface ContextMenuState {
  x: number;
  y: number;
  tagId: number;
}

export function useMainNavTags() {
  const {
    expandedTagIds: expandedTagIdsArray,
    toggleExpandedTag,
    setExpandedTagIds,
  } = useNavigationStore();

  const [tags, setTags] = useState<Tag[]>([]);
  const [isAddingTag, setIsAddingTag] = useState(false);
  const [createTagModal, setCreateTagModal] = useState<{
    isOpen: boolean;
    parentId?: number;
  }>({ isOpen: false });

  // Convert array to Set for easier lookup
  const expandedTagIds = useMemo(
    () => new Set(expandedTagIdsArray),
    [expandedTagIdsArray],
  );

  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);

  // Close context menu on click outside
  useEffect(() => {
    const handleClick = () => setContextMenu(null);
    window.addEventListener("click", handleClick);
    return () => window.removeEventListener("click", handleClick);
  }, []);

  const [newTagName, setNewTagName] = useState("");
  const [editingTagId, setEditingTagId] = useState<number | null>(null);

  const [confirmModal, setConfirmModal] = useState<ConfirmModalState>({
    isOpen: false,
    title: "",
    message: "",
    onConfirm: () => {},
  });

  const loadTags = useCallback(async () => {
    try {
      const data = await getTags();
      setTags(data);

        } catch (error: unknown) {
      console.error("Failed to load tags:", error);
    }
  }, []);

  useEffect(() => {
    void loadTags();

    // Listen for global tag updates
    const handleTagsUpdated = () => {
      void loadTags();
    };
    window.addEventListener("tags-updated", handleTagsUpdated);
    return () => window.removeEventListener("tags-updated", handleTagsUpdated);
  }, [loadTags]);

  const tagTree = useMemo(() => {
    const tagMap = new Map<number, TagWithChildren>();
    const roots: TagWithChildren[] = [];

    // First pass: create nodes
    tags.forEach((tag: Tag) => {
      tagMap.set(tag.id, { ...tag, children: [] });
    });

    // Second pass: build tree
    tags.forEach((tag: Tag) => {
      const node = tagMap.get(tag.id)!;
      if (tag.parent_id && tagMap.has(tag.parent_id)) {
        tagMap.get(tag.parent_id)!.children.push(node);
      } else {
        roots.push(node);
      }
    });

    return roots;
  }, [tags]);

  const handleColorChange = async (tagId: number, colorKey: string) => {
    try {
      await updateTag(tagId, { color: colorKey });
      setTags((prev: Tag[]) =>
        prev.map((t: Tag) => (t.id === tagId ? { ...t, color: colorKey } : t)),
      );
      window.dispatchEvent(new CustomEvent("tags-updated"));

        } catch (error: unknown) {
      console.error("Failed to update tag color:", error);
    }
  };

  const handleRenameTag = async (tagId: number, newName: string) => {
    try {
      await updateTag(tagId, { name: newName });
      setTags((prev: Tag[]) =>
        prev.map((t: Tag) => (t.id === tagId ? { ...t, name: newName } : t)),
      );
      setEditingTagId(null);
      window.dispatchEvent(new CustomEvent("tags-updated"));

        } catch (error: unknown) {
      console.error("Failed to rename tag:", error);
    }
  };

  const handleDeleteTag = (tagId: number) => {
    const tag = tags.find((t: Tag) => t.id === tagId);
    if (!tag) return;

    setConfirmModal({
      isOpen: true,
      title: "Delete Tag",
      message: `Are you sure you want to delete the tag "${tag.name}"? This will remove it from all recordings.`,
      isDangerous: true,
      onConfirm: async () => {
        try {
          await deleteTag(tag.id);
          setTags((prev: Tag[]) => prev.filter((t: Tag) => t.id !== tag.id));
          window.dispatchEvent(new CustomEvent("tags-updated"));

                } catch (error: unknown) {
          console.error("Failed to delete tag:", error);
        }
      },
    });
  };

  const handleAddTag = async (parentId?: number) => {
    if (!newTagName.trim()) return;

    try {
      const randomColor =
        DEFAULT_TAG_COLORS[
          Math.floor(Math.random() * DEFAULT_TAG_COLORS.length)
        ];
      const newTag = await createTag(newTagName.trim(), randomColor, parentId);
      setTags((prev: Tag[]) => [...prev, newTag]);
      setNewTagName("");
      setIsAddingTag(false);
      window.dispatchEvent(new CustomEvent("tags-updated"));

        } catch (error: unknown) {
      console.error("Failed to create tag:", error);
    }
  };

  const handleAddSubTag = (parentId: number) => {
    setCreateTagModal({ isOpen: true, parentId });
  };

  const handleCreateTagConfirm = async (name: string) => {
    try {
      const parentId = createTagModal.parentId;
      const randomColor =
        DEFAULT_TAG_COLORS[
          Math.floor(Math.random() * DEFAULT_TAG_COLORS.length)
        ];
      const newTag = await createTag(name, randomColor, parentId);
      setTags((prev: Tag[]) => [...prev, newTag]);

      // If adding a sub-tag, ensure parent is expanded
      if (parentId && !expandedTagIds.has(parentId)) {
        toggleExpandedTag(parentId);
      }

      window.dispatchEvent(new CustomEvent("tags-updated"));

        } catch (error: unknown) {
      console.error("Failed to create tag:", error);
    }
  };

  const handleContextMenu = (e: React.MouseEvent, tagId: number) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({
      x: e.clientX,
      y: e.clientY,
      tagId,
    });
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;

    if (!over) return;

    // Extract tag IDs
    const activeIdString = String(active.id);
    const overIdString = String(over.id);

    const activeTagId = parseInt(activeIdString.replace("tag-", ""));
    let newParentId: number | null = null;

    if (overIdString === "root-tags-drop-zone") {
      newParentId = null;
    } else if (overIdString.startsWith("tag-")) {
      const overTagId = parseInt(overIdString.replace("tag-", ""));
      // Don't do anything if dropped on itself
      if (activeTagId === overTagId) return;

      // Check for cycles: ensure overTagId is not a descendant of activeTagId
      const isDescendant = (parentId: number, childId: number): boolean => {
        if (parentId === childId) return true;
        const child = tags.find((t) => t.id === childId);
        if (!child || !child.parent_id) return false;
        return isDescendant(parentId, child.parent_id);
      };

      if (isDescendant(activeTagId, overTagId)) {
        console.warn("Cannot move parent into child - cycle detected");
        return;
      }

      newParentId = overTagId;
    } else {
      return; // Dropped somewhere else
    }

    // Find the active tag object
    const activeTag = tags.find((t) => t.id === activeTagId);
    if (!activeTag) return;

    // Don't update if parent hasn't changed
    if (activeTag.parent_id === newParentId) return;

    // Optimistic update - local state uses undefined for no parent
    setTags((prev) =>
      prev.map((t) =>
        t.id === activeTagId
          ? { ...t, parent_id: newParentId === null ? undefined : newParentId }
          : t,
      ),
    );

    try {
      // API Call - MUST send null to explicitly unset parent
      await updateTag(activeTagId, { parent_id: newParentId });
      window.dispatchEvent(new CustomEvent("tags-updated"));

      // If moved to a new parent, expand that parent
      if (newParentId && !expandedTagIds.has(newParentId)) {
        toggleExpandedTag(newParentId);
      }

        } catch (e: unknown) {
      console.error("Failed to move tag", e);
      // Revert optimistic update
      setTags((prev) =>
        prev.map((t) =>
          t.id === activeTagId ? { ...t, parent_id: activeTag.parent_id } : t,
        ),
      );
    }
  };

  const { isOver: isOverRoot, setNodeRef: setRootNodeRef } = useDroppable({
    id: "root-tags-drop-zone",
  });

  const handlePromoteToRoot = async (tagId: number) => {
    try {
      await updateTag(tagId, { parent_id: null }); // Send null to API
      // Optimistic update (local state undefined)
      setTags((prev) =>
        prev.map((t) =>
          t.id === tagId ? { ...t, parent_id: undefined } : t,
        ),
      );
      window.dispatchEvent(new CustomEvent("tags-updated"));

        } catch (e: unknown) {
      console.error(e);
    }
    setContextMenu(null);
  };

  const handlePromoteOneLevel = async (tagId: number) => {
    const tag = tags.find((t) => t.id === tagId);
    if (tag?.parent_id) {
      const parent = tags.find((t) => t.id === tag.parent_id);
      if (parent?.parent_id) {
        try {
          await updateTag(tag.id, {
            parent_id: parent.parent_id,
          });
          // Optimistic update
          setTags((prev) =>
            prev.map((t) =>
              t.id === tag.id
                ? {
                    ...t,
                    parent_id: parent.parent_id,
                  }
                : t,
            ),
          );
          window.dispatchEvent(new CustomEvent("tags-updated"));

                } catch (e: unknown) {
          console.error(e);
        }
      }
    }
    setContextMenu(null);
  };

  const handleExpandAllTags = () => {
    const parentIds = tags
      .filter((t) => tags.some((child) => child.parent_id === t.id))
      .map((t) => t.id);
    setExpandedTagIds(parentIds);
  };

  return {
    tags,
    tagTree,
    expandedTagIds,
    toggleExpandedTag,
    setExpandedTagIds,
    isAddingTag,
    setIsAddingTag,
    newTagName,
    setNewTagName,
    editingTagId,
    setEditingTagId,
    createTagModal,
    setCreateTagModal,
    contextMenu,
    setContextMenu,
    confirmModal,
    setConfirmModal,
    isOverRoot,
    setRootNodeRef,
    handleColorChange,
    handleRenameTag,
    handleDeleteTag,
    handleAddTag,
    handleAddSubTag,
    handleCreateTagConfirm,
    handleContextMenu,
    handleDragEnd,
    handlePromoteToRoot,
    handlePromoteOneLevel,
    handleExpandAllTags,
  };
}
