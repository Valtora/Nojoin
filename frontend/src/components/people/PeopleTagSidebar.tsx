"use client";

import React, { useState, useMemo, useEffect, useCallback } from "react";
import {
  Tag as TagIcon,
  ChevronsDown,
  ChevronsUp,
  Plus,
  Pencil,
  ChevronDown,
  ChevronRight,
  Search,
  X,
  Check,
} from "lucide-react";
import { PeopleTag } from "@/types";
import { DEFAULT_TAG_COLORS } from "@/lib/constants";
import {
  getPeopleTags,
  createPeopleTag,
  updatePeopleTag,
  deletePeopleTag,
} from "@/lib/api";
import ConfirmationModal from "@/components/ConfirmationModal";
import { InlineColorPicker } from "../ColorPicker";

interface TagWithChildren extends PeopleTag {
  children: TagWithChildren[];
}

interface PeopleTagSidebarProps {
  selectedTagIds: number[];
  onToggleTag: (tagId: number) => void;
  onClearFilters: () => void;
  onTagsUpdated?: (tags: PeopleTag[]) => void;
  /** Mobile drawer state. On desktop the pane is always inline. */
  mobileOpen?: boolean;
  onMobileClose?: () => void;
}

// Inline pane on desktop; off-canvas drawer on mobile so the People table gets
// the full width of a phone screen.
const SIDEBAR_SHELL =
  "fixed inset-y-0 left-0 z-50 flex h-full w-[min(20rem,85vw)] shrink-0 flex-col overflow-hidden border-r border-surface-border bg-surface-card transition-transform duration-300 lg:static lg:z-auto lg:w-64 lg:translate-x-0 lg:shadow-none";

export function PeopleTagSidebar({
  selectedTagIds,
  onToggleTag,
  onClearFilters,
  onTagsUpdated,
  mobileOpen = false,
  onMobileClose,
}: PeopleTagSidebarProps) {
  const [tags, setTags] = useState<PeopleTag[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [expandedTagIds, setExpandedTagIds] = useState<Set<number>>(new Set());

  const [isAddingRoot, setIsAddingRoot] = useState(false);
  const [newTagName, setNewTagName] = useState("");

  const [editingTagId, setEditingTagId] = useState<number | null>(null);

  const [addingSubTagTo, setAddingSubTagTo] = useState<number | null>(null);

  const [confirmDelete, setConfirmDelete] = useState<PeopleTag | null>(null);

  const fetchTags = useCallback(async () => {
    try {
      const data = await getPeopleTags();
      setTags(data);
      onTagsUpdated?.(data);
    } catch (error: unknown) {
      console.error("Failed to fetch tags:", error);
    } finally {
      setIsLoading(false);
    }
  }, [onTagsUpdated]);

  useEffect(() => {
    fetchTags();
  }, [fetchTags]);

  // Build tree
  const tagTree = useMemo(() => {
    const tagMap = new Map<number, TagWithChildren>();
    const roots: TagWithChildren[] = [];

    tags.forEach((tag) => {
      tagMap.set(tag.id, { ...tag, children: [] });
    });

    tags.forEach((tag) => {
      const node = tagMap.get(tag.id)!;
      if (tag.parent_id && tagMap.has(tag.parent_id)) {
        tagMap.get(tag.parent_id)!.children.push(node);
      } else {
        roots.push(node);
      }
    });

    return roots;
  }, [tags]);

  const toggleExpand = (tagId: number) => {
    const newSet = new Set(expandedTagIds);
    if (newSet.has(tagId)) {
      newSet.delete(tagId);
    } else {
      newSet.add(tagId);
    }
    setExpandedTagIds(newSet);
  };

  const handleAddTag = async (name: string, parentId?: number) => {
    if (!name.trim()) return;
    try {
      const randomColor =
        DEFAULT_TAG_COLORS[
          Math.floor(Math.random() * DEFAULT_TAG_COLORS.length)
        ];
      await createPeopleTag(name.trim(), randomColor, parentId);
      await fetchTags();
      setNewTagName("");
      setIsAddingRoot(false);
      setAddingSubTagTo(null);
      if (parentId) {
        setExpandedTagIds((prev) => new Set(prev).add(parentId));
      }
    } catch (error: unknown) {
      console.error("Failed to add tag:", error);
    }
  };

  const handleUpdateTagName = async (id: number, name: string) => {
    if (!name.trim()) return;
    try {
      await updatePeopleTag(id, { name: name.trim() });
      await fetchTags();
      setEditingTagId(null);
    } catch (error: unknown) {
      console.error("Failed to update tag:", error);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deletePeopleTag(id);
      await fetchTags();
      setConfirmDelete(null);
    } catch (error: unknown) {
      console.error("Failed to delete tag:", error);
    }
  };

  const handleColorChange = async (id: number, color: string) => {
    try {
      await updatePeopleTag(id, { color });
      setTags((prev) => prev.map((t) => (t.id === id ? { ...t, color } : t)));
    } catch (error: unknown) {
      console.error("Failed to update tag color:", error);
    }
  };

  const filteredTree = useMemo(() => {
    if (!searchQuery.trim()) return tagTree;

    const filter = (nodes: TagWithChildren[]): TagWithChildren[] => {
      return nodes
        .map((node) => ({
          ...node,
          children: filter(node.children),
        }))
        .filter(
          (node) =>
            node.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
            node.children.length > 0,
        );
    };

    return filter(tagTree);
  }, [tagTree, searchQuery]);

  const mobileTransform = mobileOpen
    ? "translate-x-0 shadow-2xl lg:shadow-none"
    : "-translate-x-full";

  if (isLoading) {
    return (
      <>
        {mobileOpen && (
          <div
            className="fixed inset-0 z-[var(--z-dropdown)] bg-scrim lg:hidden"
            onClick={onMobileClose}
          />
        )}
        <div
          className={`${SIDEBAR_SHELL} ${mobileTransform} items-center justify-center`}
        >
          <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-action"></div>
        </div>
      </>
    );
  }

  return (
    <>
      {mobileOpen && (
        <div
          className="fixed inset-0 z-[var(--z-dropdown)] bg-scrim lg:hidden"
          onClick={onMobileClose}
        />
      )}
      <div className={`${SIDEBAR_SHELL} ${mobileTransform}`}>
        <div className="p-4 border-b border-surface-border space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-[11px] font-bold text-contrast-helper uppercase tracking-wider flex items-center gap-2">
              <TagIcon className="w-3.5 h-3.5" />
              People Tags
            </h2>
            <div className="flex items-center gap-1">
              <button
                onClick={onMobileClose}
                className="lg:hidden p-1 rounded hover:bg-surface-inset transition-colors text-contrast-helper hover:text-contrast-muted"
                title="Close"
                aria-label="Close tag filters"
              >
                <X className="w-4 h-4" />
              </button>
              <button
                onClick={() => {
                  const allIds = tags.map((t) => t.id);
                  setExpandedTagIds(new Set(allIds));
                }}
                className="p-1 rounded hover:bg-surface-inset transition-colors text-contrast-helper hover:text-contrast-muted"
                title="Expand All"
              >
                <ChevronsDown className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => setExpandedTagIds(new Set())}
                className="p-1 rounded hover:bg-surface-inset transition-colors text-contrast-helper hover:text-contrast-muted"
                title="Collapse All"
              >
                <ChevronsUp className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => setIsAddingRoot(true)}
                className="p-1 rounded hover:bg-surface-inset transition-colors text-contrast-helper hover:text-action-text"
                title="Add Tag"
              >
                <Plus className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-contrast-icon-muted" />
            <input
              type="text"
              placeholder="Search tags..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-3 py-2 text-sm bg-surface-inset border-none rounded-lg focus:ring-2 focus:ring-action outline-none transition-all"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto custom-scrollbar p-2">
          {isAddingRoot && (
            <div className="mb-2 px-2 flex items-center gap-1">
              <input
                autoFocus
                className="flex-1 px-2 py-1 text-sm bg-surface-card border border-action rounded outline-none"
                placeholder="Root tag name..."
                value={newTagName}
                onChange={(e) => setNewTagName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleAddTag(newTagName);
                  else if (e.key === "Escape") setIsAddingRoot(false);
                }}
              />
              <button
                onClick={() => handleAddTag(newTagName)}
                className="p-1.5 text-action-text hover:bg-action-tint rounded"
              >
                <Check className="w-4 h-4" />
              </button>
            </div>
          )}

          {filteredTree.map((tag) => (
            <TagNode
              key={tag.id}
              tag={tag}
              level={0}
              expandedIds={expandedTagIds}
              toggleExpand={toggleExpand}
              selectedTagIds={selectedTagIds}
              onToggleTag={onToggleTag}
              editingTagId={editingTagId}
              onEditStart={(id) => {
                setEditingTagId(id);
              }}
              onEditCancel={() => setEditingTagId(null)}
              onEditSave={handleUpdateTagName}
              addingSubTagTo={addingSubTagTo}
              onAddSubStart={setAddingSubTagTo}
              onAddSubSave={handleAddTag}
              onDelete={setConfirmDelete}
              onColorChange={handleColorChange}
            />
          ))}

          {filteredTree.length === 0 && !isAddingRoot && (
            <div className="text-center py-8">
              <TagIcon className="w-8 h-8 text-contrast-icon-muted mx-auto mb-2" />
              <p className="text-xs text-contrast-helper">
                No tags found
              </p>
            </div>
          )}
        </div>

        {selectedTagIds.length > 0 && (
          <div className="p-2 border-t border-surface-border">
            <button
              onClick={onClearFilters}
              className="w-full px-3 py-1.5 text-xs font-medium text-action-text hover:bg-action-tint rounded-lg flex items-center justify-center gap-2 border border-action-border transition-colors"
            >
              <X className="w-3.5 h-3.5" /> Clear active filters (
              {selectedTagIds.length})
            </button>
          </div>
        )}

        <ConfirmationModal
          isOpen={!!confirmDelete}
          onClose={() => setConfirmDelete(null)}
          onConfirm={() => confirmDelete && handleDelete(confirmDelete.id)}
          title="Delete People Tag"
          message={`Are you sure you want to delete "${confirmDelete?.name}"? This will remove it from all people.`}
          isDangerous
        />
      </div>
    </>
  );
}

interface TagNodeProps {
  tag: TagWithChildren;
  level: number;
  expandedIds: Set<number>;
  toggleExpand: (id: number) => void;
  selectedTagIds: number[];
  onToggleTag: (id: number) => void;
  editingTagId: number | null;
  onEditStart: (id: number, name: string) => void;
  onEditCancel: () => void;
  onEditSave: (id: number, name: string) => void;
  addingSubTagTo: number | null;
  onAddSubStart: (id: number | null) => void;
  onAddSubSave: (name: string, parentId: number) => void;
  onDelete: (tag: PeopleTag) => void;
  onColorChange: (id: number, color: string) => void;
}

function TagNode({
  tag,
  level,
  expandedIds,
  toggleExpand,
  selectedTagIds,
  onToggleTag,
  editingTagId,
  onEditStart,
  onEditCancel,
  onEditSave,
  addingSubTagTo,
  onAddSubStart,
  onAddSubSave,
  onDelete,
  onColorChange,
}: TagNodeProps) {
  const isExpanded = expandedIds.has(tag.id);
  const isSelected = selectedTagIds.includes(tag.id);
  const isEditing = editingTagId === tag.id;
  const isAddingSub = addingSubTagTo === tag.id;
  const hasChildren = tag.children.length > 0;

  const [editValue, setEditValue] = useState(tag.name);
  const [subValue, setSubValue] = useState("");

  return (
    <div>
      <div
        className={`group flex items-center gap-2 px-3 py-1.5 rounded-lg border border-transparent transition-all cursor-pointer relative select-none ${
          isSelected
            ? "bg-surface-inset"
            : "hover:bg-surface-inset"
        }`}
        style={{ marginLeft: `${level * 12}px` }}
      >
        <div
          className="flex-1 flex items-center gap-2 min-w-0"
          onClick={() => !isEditing && onToggleTag(tag.id)}
        >
          <div onClick={(e) => e.stopPropagation()}>
            <InlineColorPicker
              selectedColor={tag.color || undefined}
              onColorSelect={(color) => onColorChange(tag.id, color)}
            />
          </div>
          {isEditing ? (
            <input
              autoFocus
              className="flex-1 bg-surface-card border border-action rounded px-1 py-0.5 text-sm"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                if (e.key === "Enter") onEditSave(tag.id, editValue);
                else if (e.key === "Escape") onEditCancel();
              }}
            />
          ) : (
            <span
              className="text-sm truncate font-medium"
              title="Double-click to rename"
              onDoubleClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onEditStart(tag.id, tag.name);
              }}
            >
              {tag.name}
            </span>
          )}
        </div>

        {!isEditing && (
          // Controls stay visible on touch (no hover) but keep the clean
          // hover-reveal on desktop where a pointer is available.
          <div className="flex items-center gap-0.5 opacity-100 transition-opacity lg:opacity-0 lg:group-hover:opacity-100">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onEditStart(tag.id, tag.name);
              }}
              className="p-1.5 hover:text-action-text transition-all"
              title="Rename"
              aria-label={`Rename ${tag.name}`}
            >
              <Pencil className="w-3.5 h-3.5" />
            </button>

            <button
              onClick={(e) => {
                e.stopPropagation();
                onAddSubStart(tag.id);
              }}
              className="p-1.5 hover:text-action-text transition-all"
              title="Add sub-tag"
              aria-label={`Add sub-tag to ${tag.name}`}
            >
              <Plus className="w-3.5 h-3.5" />
            </button>

            {hasChildren && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  toggleExpand(tag.id);
                }}
                className="p-1.5 hover:text-contrast-helper transition-all"
                title={isExpanded ? "Collapse" : "Expand"}
                aria-label={
                  isExpanded ? `Collapse ${tag.name}` : `Expand ${tag.name}`
                }
              >
                {isExpanded ? (
                  <ChevronDown className="w-3.5 h-3.5" />
                ) : (
                  <ChevronRight className="w-3.5 h-3.5" />
                )}
              </button>
            )}

            <button
              onClick={(e) => {
                e.stopPropagation();
                onDelete(tag);
              }}
              className="p-1.5 hover:text-status-danger-fg transition-all"
              title="Delete"
              aria-label={`Delete ${tag.name}`}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>

      {isAddingSub && (
        <div
          className="mt-1 flex items-center gap-1"
          style={{ paddingLeft: `${(level + 1) * 12 + 20}px` }}
        >
          <input
            autoFocus
            className="flex-1 px-2 py-0.5 text-xs bg-surface-card border border-action rounded outline-none"
            placeholder="New sub-tag..."
            value={subValue}
            onChange={(e) => setSubValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                onAddSubSave(subValue, tag.id);
                setSubValue("");
              } else if (e.key === "Escape") onAddSubStart(null);
            }}
          />
          <button
            onClick={() => {
              onAddSubSave(subValue, tag.id);
              setSubValue("");
            }}
            className="p-1 text-action-text hover:bg-action-tint rounded"
          >
            <Check className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {hasChildren && isExpanded && (
        <div className="mt-0.5">
          {tag.children.map((child) => (
            <TagNode
              key={child.id}
              tag={child}
              level={level + 1}
              expandedIds={expandedIds}
              toggleExpand={toggleExpand}
              selectedTagIds={selectedTagIds}
              onToggleTag={onToggleTag}
              editingTagId={editingTagId}
              onEditStart={onEditStart}
              onEditCancel={onEditCancel}
              onEditSave={onEditSave}
              addingSubTagTo={addingSubTagTo}
              onAddSubStart={onAddSubStart}
              onAddSubSave={onAddSubSave}
              onDelete={onDelete}
              onColorChange={onColorChange}
            />
          ))}
        </div>
      )}
    </div>
  );
}
