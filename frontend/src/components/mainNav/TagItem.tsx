import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Plus, X } from "lucide-react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";

import { getColorByKey } from "@/lib/constants";
import { Tag } from "@/types";

import { InlineColorPicker } from "../ColorPicker";

interface TagItemProps {
  tag: Tag;
  isSelected: boolean;
  onToggle: () => void;
  onColorChange: (colorKey: string) => void;
  onDelete: () => void;
  onRename: (newName: string) => void;
  onAddChild: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
  isEditing: boolean;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  collapsed: boolean;
  level?: number;
  hasChildren: boolean;
  isExpanded: boolean;
  onToggleExpand: () => void;
}

export default function TagItem({
  tag,
  isSelected,
  onToggle,
  onColorChange,
  onDelete,
  onRename,
  onAddChild,
  onContextMenu,
  isEditing,
  onStartEdit,
  onCancelEdit,
  collapsed,
  level = 0,
  hasChildren,
  isExpanded,
  onToggleExpand,
}: TagItemProps) {
  const color = getColorByKey(tag.color);
  const [editValue, setEditValue] = useState(tag.name);

  const {
    attributes,
    listeners,
    setNodeRef: setDraggableRef,
    transform,
    isDragging,
  } = useDraggable({
    id: `tag-${tag.id}`,
    data: { tag },
  });

  const { setNodeRef: setDroppableRef, isOver } = useDroppable({
    id: `tag-${tag.id}`,
    data: { tag },
    disabled: isDragging,
  });

  const style = {
    transform: CSS.Translate.toString(transform),
  };

  // Reset edit value when editing starts
  useEffect(() => {
    if (isEditing) {
      setEditValue(tag.name);
    }
  }, [isEditing, tag.name]);

  const handleSubmit = () => {
    if (editValue.trim() && editValue.trim() !== tag.name) {
      onRename(editValue.trim());
    } else {
      onCancelEdit();
    }
  };

  if (collapsed) {
    return (
      <button
        onClick={onToggle}
        title={tag.name}
        className={`
          w-full flex justify-center py-2 rounded-lg transition-all
          ${isSelected ? "bg-gray-100 dark:bg-gray-800" : "hover:bg-gray-50 dark:hover:bg-gray-800/50"}
        `}
      >
        <span className={`w-3 h-3 rounded-full ${color.dot}`} />
      </button>
    );
  }

  const content = (
    <div
      {...attributes}
      {...listeners}
      className={`
        group flex items-center gap-2 px-3 py-1.5 rounded-lg transition-all cursor-pointer relative select-none touch-none
        ${isSelected ? "bg-gray-100 dark:bg-gray-800" : "hover:bg-gray-50 dark:hover:bg-gray-800/50"}
        ${isDragging ? "opacity-50" : ""}
        ${isOver ? "bg-orange-50 dark:bg-orange-900/10 ring-2 ring-orange-400 dark:ring-orange-600" : ""}
      `}
      style={{ paddingLeft: `${level * 12 + 12}px` }}
      onClick={() => {
        if (!isEditing) onToggle();
      }}
      onContextMenu={onContextMenu}
    >
      <InlineColorPicker
        selectedColor={tag.color || undefined}
        onColorSelect={onColorChange}
      />
      {isEditing ? (
        <input
          type="text"
          value={editValue}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
            setEditValue(e.target.value)
          }
          onKeyDown={(e: React.KeyboardEvent) => {
            if (e.key === "Enter") handleSubmit();
            if (e.key === "Escape") onCancelEdit();
          }}
          onBlur={handleSubmit}
          autoFocus
          className="flex-1 text-sm bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded px-1 py-0.5 focus:ring-1 focus:ring-orange-500 focus:outline-none select-text"
          onClick={(e: React.MouseEvent) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
        />
      ) : (
        <span
          className={`flex-1 text-sm truncate ${isSelected ? "font-medium" : ""}`}
          title={tag.name}
          onDoubleClick={(e: React.MouseEvent) => {
            e.stopPropagation();
            onStartEdit();
          }}
        >
          {tag.name}
        </span>
      )}
      {!isEditing && (
        <div className="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={(e: React.MouseEvent) => {
              e.stopPropagation();
              onAddChild();
            }}
            className="p-1 hover:text-orange-500 transition-all"
            title="Add sub-tag"
            onPointerDown={(e) => e.stopPropagation()}
          >
            <Plus className="w-3 h-3" />
          </button>

          {hasChildren && (
            <button
              onClick={(e: React.MouseEvent) => {
                e.stopPropagation();
                onToggleExpand();
              }}
              className="p-1 hover:text-gray-600 dark:hover:text-gray-300 transition-all"
              title={isExpanded ? "Collapse" : "Expand"}
              onPointerDown={(e) => e.stopPropagation()}
            >
              {isExpanded ? (
                <ChevronDown className="w-3 h-3" />
              ) : (
                <ChevronRight className="w-3 h-3" />
              )}
            </button>
          )}

          <button
            onClick={(e: React.MouseEvent) => {
              e.stopPropagation();
              onDelete();
            }}
            className="p-1 hover:text-red-500 transition-all"
            title="Delete tag"
            onPointerDown={(e) => e.stopPropagation()}
          >
            <X className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  );

  return (
    <div ref={setDroppableRef} style={style}>
      <div ref={setDraggableRef}>{content}</div>
    </div>
  );
}
