"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { RecordingId, Tag } from "@/types";
import { ArrowLeft, Check, MoreVertical } from "lucide-react";
import {
  getTags,
  createTag,
  addTagToRecording,
  updateTag,
  deleteTag,
  removeTagFromRecording,
} from "@/lib/api";
import { getColorByKey, DEFAULT_TAG_COLORS } from "@/lib/constants";
import ContextMenu from "./ContextMenu";
import Button from "./ui/Button";
import IconButton from "./ui/IconButton";
import Input from "./ui/Input";
import Modal from "./ui/Modal";

interface AddTagModalProps {
  isOpen: boolean;
  onClose: () => void;
  recordingId: RecordingId;
  currentTags: Tag[];
  onTagsUpdated?: () => void;
}

interface TagWithChildren extends Tag {
  children?: TagWithChildren[];
}

export default function AddTagModal({
  isOpen,
  onClose,
  recordingId,
  currentTags,
  onTagsUpdated,
}: AddTagModalProps) {
  const [mounted, setMounted] = useState(false);
  const [creatingSubTagFor, setCreatingSubTagFor] = useState<Tag | null>(null);
  const [newSubTagName, setNewSubTagName] = useState("");

  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    tag: Tag;
  } | null>(null);
  const [editingTagId, setEditingTagId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");

  const [processingTags, setProcessingTags] = useState<Set<string>>(new Set());

  useEffect(() => {
    setMounted(true);
  }, []);

  const loadAllTags = useCallback(async () => {
    try {
      const data = await getTags();
      setAllTags(data);

        } catch (error: unknown) {
      console.error("Failed to load tags:", error);
    }
  }, []);
  useEffect(() => {
    if (isOpen) {
      void loadAllTags();
      setInputValue("");
      setProcessingTags(new Set());
      setCreatingSubTagFor(null);
      setNewSubTagName("");
    }
  }, [isOpen, loadAllTags]);
  const handleSubTagSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!creatingSubTagFor || !newSubTagName.trim()) return;

    await handleCreateTag(newSubTagName, creatingSubTagFor.id);
    setCreatingSubTagFor(null);
    setNewSubTagName("");
  };
  const handleAddTag = async (tagName: string) => {
    if (processingTags.has(tagName)) return;

    setProcessingTags((prev) => new Set(prev).add(tagName));
    try {
      await addTagToRecording(recordingId, tagName);
      window.dispatchEvent(new CustomEvent("tags-updated"));
      if (onTagsUpdated) onTagsUpdated();
      setInputValue("");

        } catch (error: unknown) {
      console.error("Failed to add tag:", error);
    } finally {
      setProcessingTags((prev) => {
        const next = new Set(prev);
        next.delete(tagName);
        return next;
      });
    }
  };

  const handleCreateTag = async (name: string, parentId?: number) => {
    if (!name.trim()) return;
    if (processingTags.has(name.trim())) return;

    setProcessingTags((prev) => new Set(prev).add(name.trim()));
    try {
      const existingTag = allTags.find(
        (t) => t.name.toLowerCase() === name.trim().toLowerCase(),
      );
      if (existingTag) {
        await addTagToRecording(recordingId, existingTag.name);
        window.dispatchEvent(new CustomEvent("tags-updated"));
        if (onTagsUpdated) onTagsUpdated();
      } else {
        const randomColor =
          DEFAULT_TAG_COLORS[
            Math.floor(Math.random() * DEFAULT_TAG_COLORS.length)
          ];
        await createTag(name.trim(), randomColor, parentId);
        await loadAllTags(); // Always reload to ensure list is updated
        if (!parentId) {
          // Explicitly associates the newly created root tag with the current recording.
          await addTagToRecording(recordingId, name.trim());
          window.dispatchEvent(new CustomEvent("tags-updated"));
          if (onTagsUpdated) onTagsUpdated();
        }
      }
      setInputValue("");

        } catch (error: unknown) {
      console.error("Failed to create tag:", error);
    } finally {
      setProcessingTags((prev) => {
        const next = new Set(prev);
        next.delete(name.trim());
        return next;
      });
    }
  };

  const handleRenameTag = async (tagId: number, newName: string) => {
    try {
      await updateTag(tagId, { name: newName });
      await loadAllTags();
      window.dispatchEvent(new CustomEvent("tags-updated"));
      if (onTagsUpdated) onTagsUpdated();
      setEditingTagId(null);

        } catch (error: unknown) {
      console.error("Failed to rename tag:", error);
    }
  };

  const handleDeleteTag = async (tagId: number) => {
    if (
      !confirm("Are you sure you want to delete this tag and all its children?")
    )
      return;
    try {
      await deleteTag(tagId);
      await loadAllTags();
      window.dispatchEvent(new CustomEvent("tags-updated"));
      if (onTagsUpdated) onTagsUpdated();

        } catch (error: unknown) {
      console.error("Failed to delete tag:", error);
    }
  };

  const handleRemoveTag = async (tagName: string) => {
    if (processingTags.has(tagName)) return;

    setProcessingTags((prev) => new Set(prev).add(tagName));
    try {
      await removeTagFromRecording(recordingId, tagName);
      window.dispatchEvent(new CustomEvent("tags-updated"));
      if (onTagsUpdated) onTagsUpdated();

        } catch (error: unknown) {
      console.error("Failed to remove tag:", error);
    } finally {
      setProcessingTags((prev) => {
        const next = new Set(prev);
        next.delete(tagName);
        return next;
      });
    }
  };

  const tagTree = useMemo(() => {
    const tagMap = new Map<number, TagWithChildren>();
    const roots: TagWithChildren[] = [];

    allTags.forEach((tag) => {
      tagMap.set(tag.id, { ...tag, children: [] });
    });

    allTags.forEach((tag) => {
      const node = tagMap.get(tag.id)!;
      if (tag.parent_id && tagMap.has(tag.parent_id)) {
        tagMap.get(tag.parent_id)!.children!.push(node);
      } else {
        roots.push(node);
      }
    });

    return roots;
  }, [allTags]);

  const renderTagTree = (nodes: TagWithChildren[], level = 0) => {
    return nodes.map((node) => {
      const isSelected = currentTags.some((t) => t.id === node.id);
      const isProcessing = processingTags.has(node.name);
      const color = getColorByKey(node.color);
      const isEditing = editingTagId === node.id;

      if (
        inputValue &&
        !node.name.toLowerCase().includes(inputValue.toLowerCase()) &&
        !node.children?.some((c) =>
          c.name.toLowerCase().includes(inputValue.toLowerCase()),
        )
      ) {
        return null;
      }

      return (
        <div key={node.id} className="w-full">
          <div
            className={`w-full flex items-center justify-between px-3 py-2 text-sm group transition-colors ${
              isSelected
                ? "bg-action-tint"
                : "hover:bg-surface-inset"
            } ${isProcessing ? "opacity-50 cursor-not-allowed" : ""}`}
            style={{ paddingLeft: `${level * 12 + 12}px` }}
            onContextMenu={(e) => {
              e.preventDefault();
              setContextMenu({ x: e.clientX, y: e.clientY, tag: node });
            }}
          >
            <div className="flex items-center gap-2 flex-1">
              <span className={`w-2 h-2 rounded-full ${color.dot}`} />
              {isEditing ? (
                <input
                  autoFocus
                  className="bg-control-bg text-foreground border border-control-border rounded px-1 py-0.5 text-sm w-full"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleRenameTag(node.id, editValue);
                    if (e.key === "Escape") setEditingTagId(null);
                  }}
                  onBlur={() => setEditingTagId(null)}
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <button
                  className={`flex-1 text-left ${isSelected ? "text-action-tint-fg font-medium" : "text-contrast-muted"}`}
                  onClick={() => {
                    if (isProcessing) return;
                    if (isSelected) {
                      handleRemoveTag(node.name);
                    } else {
                      handleAddTag(node.name);
                    }
                  }}
                  disabled={isProcessing}
                >
                  {node.name}
                </button>
              )}
            </div>
            <div className="flex items-center gap-2">
              {isSelected && (
                <Check aria-hidden="true" className="w-3 h-3 text-action-text" />
              )}
              {/* Revealed on hover only above the desktop breakpoint: a
                  touch device has no hover, so the row would lose its menu. */}
              <div className="flex items-center gap-1 transition-opacity lg:opacity-0 lg:group-hover:opacity-100">
                <IconButton
                  size="sm"
                  aria-label={`Tag options for ${node.name}`}
                  icon={<MoreVertical aria-hidden="true" />}
                  onClick={(e) => {
                    e.stopPropagation();
                    setContextMenu({ x: e.clientX, y: e.clientY, tag: node });
                  }}
                />
              </div>
            </div>
          </div>
          {node.children && node.children.length > 0 && (
            <div>{renderTagTree(node.children, level + 1)}</div>
          )}
        </div>
      );
    });
  };

  if (!mounted) return null;

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="md"
      title={
        creatingSubTagFor ? (
          <span className="flex items-center gap-2">
            <IconButton
              size="sm"
              aria-label="Back to tag list"
              icon={<ArrowLeft aria-hidden="true" />}
              onClick={() => setCreatingSubTagFor(null)}
            />
            New Sub-tag for &quot;{creatingSubTagFor.name}&quot;
          </span>
        ) : (
          "Add Tags"
        )
      }
      footer={
        creatingSubTagFor ? undefined : (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        )
      }
    >
      {creatingSubTagFor ? (
        <form onSubmit={handleSubTagSubmit} className="flex flex-col gap-4">
          <Input
            autoFocus
            type="text"
            label="Sub-tag Name"
            value={newSubTagName}
            onChange={(e) => setNewSubTagName(e.target.value)}
            placeholder="Enter sub-tag name..."
          />
          <div className="mt-2 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setCreatingSubTagFor(null)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={!newSubTagName.trim()}>
              Create
            </Button>
          </div>
        </form>
      ) : (
        <>
          <div className="mb-4">
            <Input
              autoFocus
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreateTag(inputValue);
              }}
              placeholder="Search or create tag..."
              aria-label="Search or create tag"
            />
          </div>

          <div
            className="overflow-y-auto rounded-lg border border-surface-border"
            style={{ minHeight: "200px", maxHeight: "400px" }}
          >
            {renderTagTree(tagTree)}

            {inputValue &&
              !allTags.some(
                (t) => t.name.toLowerCase() === inputValue.toLowerCase(),
              ) && (
                <button
                  onClick={() => handleCreateTag(inputValue)}
                  className="w-full border-t border-surface-divider px-3 py-2 text-left text-sm font-medium text-action-text hover:bg-surface-inset"
                  disabled={processingTags.has(inputValue)}
                >
                  + Create &quot;{inputValue}&quot;
                </button>
              )}

            {allTags.length === 0 && !inputValue && (
              <div className="px-3 py-4 text-center text-sm text-contrast-helper">
                No tags found
              </div>
            )}
          </div>
        </>
      )}

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
          items={[
            {
              label: "Rename",
              onClick: () => {
                setEditingTagId(contextMenu.tag.id);
                setEditValue(contextMenu.tag.name);
                setContextMenu(null);
              },
            },
            {
              label: "Add Sub-tag",
              onClick: () => {
                setCreatingSubTagFor(contextMenu.tag);
                setContextMenu(null);
              },
            },
            {
              label: "Delete",
              onClick: () => {
                handleDeleteTag(contextMenu.tag.id);
                setContextMenu(null);
              },
              className: "text-danger-text",
            },
          ]}
        />
      )}
    </Modal>
  );
}
