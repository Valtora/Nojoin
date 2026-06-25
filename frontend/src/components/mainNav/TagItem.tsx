import { useEffect, useState } from "react";
import { ChevronRight, Plus, X } from "lucide-react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";

import { getColorByKey } from "@/lib/constants";
import { Tag } from "@/types";

import { InlineColorPicker } from "../ColorPicker";

// Horizontal space (px) reserved for each level of tag nesting. Each level draws
// one guide column of this width, so the indent and the rails stay in lock-step.
const INDENT = 18;

const RAIL_CLASS = "bg-gray-300 dark:bg-gray-700";

/**
 * Renders the tree guide lines for a tag row.
 *
 * `lines[i]` answers "does the node that owns column i have a sibling below it?".
 * The last entry is this node's own connector: when true the elbow continues
 * downward to the next sibling (a "tee"), when false it stops at the row's
 * midpoint (an "ell"), so a branch's rail ends at its last child. Root rows pass
 * an empty array and therefore draw no guides.
 */
function TagGuides({ lines }: { lines: boolean[] }) {
  if (lines.length === 0) return null;
  const lastIndex = lines.length - 1;

  return (
    <div
      className="flex shrink-0 self-stretch pointer-events-none"
      aria-hidden="true"
    >
      {lines.map((hasSiblingBelow, i) => {
        const isElbow = i === lastIndex;
        return (
          <div key={i} className="relative" style={{ width: INDENT }}>
            {/* Ancestor rail: a continuous vertical line when this ancestor has
                more siblings below the current branch. */}
            {!isElbow && hasSiblingBelow && (
              <span className={`absolute top-0 bottom-0 left-1/2 w-px ${RAIL_CLASS}`} />
            )}
            {isElbow && (
              <>
                {/* Down-stem from the parent into this row (top -> midpoint). */}
                <span className={`absolute top-0 left-1/2 h-1/2 w-px ${RAIL_CLASS}`} />
                {/* Continue the rail past this row only if a sibling follows. */}
                {hasSiblingBelow && (
                  <span className={`absolute top-1/2 bottom-0 left-1/2 w-px ${RAIL_CLASS}`} />
                )}
                {/* Elbow arm reaching toward the disclosure/colour slot. */}
                <span className={`absolute top-1/2 left-1/2 right-0 h-px ${RAIL_CLASS}`} />
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

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
  /** Tree guide flags from root to this node; see {@link TagGuides}. */
  lines: boolean[];
  hasChildren: boolean;
  /** Number of direct children, shown as a badge when the sidebar is folded. */
  childCount: number;
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
  lines,
  hasChildren,
  childCount,
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
        title={hasChildren ? `${tag.name} (${childCount})` : tag.name}
        className={`
          w-full flex justify-center py-2 rounded-lg transition-all
          ${isSelected ? "bg-gray-100 dark:bg-gray-800" : "hover:bg-gray-50 dark:hover:bg-gray-800/50"}
        `}
      >
        <span className="relative inline-flex">
          <span className={`w-3 h-3 rounded-full ${color.dot}`} />
          {hasChildren && (
            <span
              className="absolute -top-1.5 -right-2 flex h-3.5 min-w-[14px] items-center justify-center rounded-full bg-gray-200 px-1 text-[9px] font-medium leading-none text-gray-600 dark:bg-gray-700 dark:text-gray-300"
              title={`${childCount} sub-tag${childCount === 1 ? "" : "s"}`}
            >
              {childCount}
            </span>
          )}
        </span>
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
      onClick={() => {
        if (!isEditing) onToggle();
      }}
      onContextMenu={onContextMenu}
    >
      <TagGuides lines={lines} />
      {/* Reserved disclosure slot keeps colour dots aligned at every depth:
          parents get a persistent chevron, leaves get an empty spacer. */}
      {hasChildren ? (
        <button
          onClick={(e: React.MouseEvent) => {
            e.stopPropagation();
            onToggleExpand();
          }}
          className="flex h-4 w-4 shrink-0 items-center justify-center text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
          title={isExpanded ? "Collapse" : "Expand"}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <ChevronRight
            className={`w-3 h-3 transition-transform ${isExpanded ? "rotate-90" : ""}`}
          />
        </button>
      ) : (
        <span className="h-4 w-4 shrink-0" aria-hidden="true" />
      )}
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
