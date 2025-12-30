"use client";

import React, { useState, useMemo, useEffect, useCallback } from "react";
import {
  Tag as TagIcon,
  ChevronsDown,
  ChevronsUp,
  Plus,
  Edit2,
  Trash2,
  FolderPlus,
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
}

export function PeopleTagSidebar({
  selectedTagIds,
  onToggleTag,
  onClearFilters,
  onTagsUpdated,
}: PeopleTagSidebarProps) {
  const [tags, setTags] = useState<PeopleTag[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [expandedTagIds, setExpandedTagIds] = useState<Set<number>>(new Set());

  const [isAddingRoot, setIsAddingRoot] = useState(false);
  const [newTagName, setNewTagName] = useState("");

  const [editingTagId, setEditingTagId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");

  const [addingSubTagTo, setAddingSubTagTo] = useState<number | null>(null);

  const [confirmDelete, setConfirmDelete] = useState<PeopleTag | null>(null);

  const fetchTags = useCallback(async () => {
    try {
      const data = await getPeopleTags();
      setTags(data);
      onTagsUpdated?.(data);
    } catch (error) {
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
    } catch (error) {
      console.error("Failed to add tag:", error);
    }
  };

  const handleUpdateTagName = async (id: number, name: string) => {
    if (!name.trim()) return;
    try {
      await updatePeopleTag(id, { name: name.trim() });
      await fetchTags();
      setEditingTagId(null);
    } catch (error) {
      console.error("Failed to update tag:", error);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deletePeopleTag(id);
      await fetchTags();
      setConfirmDelete(null);
    } catch (error) {
      console.error("Failed to delete tag:", error);
    }
  };

  const handleColorChange = async (id: number, color: string) => {
    try {
      await updatePeopleTag(id, { color });
      setTags((prev) => prev.map((t) => (t.id === id ? { ...t, color } : t)));
    } catch (error) {
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

  if (isLoading) {
    return (
      <div className="w-64 shrink-0 border-r border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 flex items-center justify-center">
        <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-orange-500"></div>
      </div>
    );
  }

  return (
    <div className="w-64 shrink-0 border-r border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 flex flex-col h-full overflow-hidden">
      <div className="p-4 border-b border-gray-200 dark:border-gray-800 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-[11px] font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider flex items-center gap-2">
            <TagIcon className="w-3.5 h-3.5" />
            People Tags
          </h2>
          <div className="flex items-center gap-1">
            <button
              onClick={() => {
                const allIds = tags.map((t) => t.id);
                setExpandedTagIds(new Set(allIds));
              }}
              className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-800 transition-colors text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
              title="Expand All"
            >
              <ChevronsDown className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setExpandedTagIds(new Set())}
              className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-800 transition-colors text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
              title="Collapse All"
            >
              <ChevronsUp className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setIsAddingRoot(true)}
              className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-800 transition-colors text-gray-500 hover:text-orange-500 dark:hover:text-orange-400"
              title="Add Tag"
            >
              <Plus className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search tags..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-2 text-sm bg-gray-50 dark:bg-gray-800 border-none rounded-lg focus:ring-2 focus:ring-orange-500 outline-none transition-all"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar p-2">
        {isAddingRoot && (
          <div className="mb-2 px-2 flex items-center gap-1">
            <input
              autoFocus
              className="flex-1 px-2 py-1 text-sm bg-white dark:bg-gray-800 border border-orange-500 rounded outline-none"
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
              className="p-1.5 text-orange-600 hover:bg-orange-50 dark:hover:bg-orange-900/30 rounded"
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
            onEditStart={(id, name) => {
              setEditingTagId(id);
              setEditName(name);
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
            <TagIcon className="w-8 h-8 text-gray-300 dark:text-gray-700 mx-auto mb-2" />
            <p className="text-xs text-gray-500 dark:text-gray-500">
              No tags found
            </p>
          </div>
        )}
      </div>

      {selectedTagIds.length > 0 && (
        <div className="p-2 border-t border-gray-200 dark:border-gray-800">
          <button
            onClick={onClearFilters}
            className="w-full px-3 py-1.5 text-xs font-medium text-orange-600 dark:text-orange-400 hover:bg-orange-50 dark:hover:bg-orange-950/30 rounded-lg flex items-center justify-center gap-2 border border-orange-200 dark:border-orange-900/50 transition-colors"
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
            ? "bg-gray-100 dark:bg-gray-800"
            : "hover:bg-gray-50 dark:hover:bg-gray-800/50"
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
              className="flex-1 bg-white dark:bg-gray-800 border border-orange-500 rounded px-1 py-0.5 text-sm"
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
          <div className="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onAddSubStart(tag.id);
              }}
              className="p-1 hover:text-orange-500 transition-all"
              title="Add sub-tag"
            >
              <Plus className="w-3 h-3" />
            </button>

            {hasChildren && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  toggleExpand(tag.id);
                }}
                className="p-1 hover:text-gray-600 dark:hover:text-gray-300 transition-all"
                title={isExpanded ? "Collapse" : "Expand"}
              >
                {isExpanded ? (
                  <ChevronDown className="w-3 h-3" />
                ) : (
                  <ChevronRight className="w-3 h-3" />
                )}
              </button>
            )}

            <button
              onClick={(e) => {
                e.stopPropagation();
                onDelete(tag);
              }}
              className="p-1 hover:text-red-500 transition-all"
              title="Delete"
            >
              <X className="w-3 h-3" />
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
            className="flex-1 px-2 py-0.5 text-xs bg-white dark:bg-gray-800 border border-orange-500 rounded outline-none"
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
            className="p-1 text-orange-600 hover:bg-orange-50 dark:hover:bg-orange-900/30 rounded"
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
