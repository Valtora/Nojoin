"use client";

import { useState } from "react";
import {
  Table as TableIcon,
  Plus,
  Minus,
  Trash2,
  Heading,
} from "lucide-react";
import { useEditorState, type Editor } from "@tiptap/react";

interface TableMenuProps {
  editor: Editor;
}

const GRID_ROWS = 6;
const GRID_COLS = 6;

/**
 * Table controls for the notes formatting toolbar.
 *
 * Deliberately omits cell merging and splitting. Notes are stored as Markdown,
 * which has no representation for a merged cell, so `tiptap-markdown` falls
 * back to emitting the whole table as raw HTML as soon as one exists. The
 * Markdown-based DOCX and PDF exporters cannot read that, so a merge would
 * quietly drop the table from every export.
 */
export default function TableMenu({ editor }: TableMenuProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [hovered, setHovered] = useState<{ rows: number; cols: number } | null>(
    null,
  );

  // Subscribed rather than read once: moving the cursor into a table is an
  // editor transaction that React knows nothing about, so a plain isActive call
  // would leave the row and column actions hidden until an unrelated re-render.
  const isInTable = useEditorState({
    editor,
    selector: ({ editor: instance }) => instance.isActive("table"),
  });

  const run = (action: () => void) => {
    action();
    setIsOpen(false);
  };

  const insertTable = (rows: number, cols: number) =>
    run(() =>
      editor
        .chain()
        .focus()
        // The header row is what makes the first Markdown row a header
        // delimiter, so it is always on for a newly inserted table.
        .insertTable({ rows, cols, withHeaderRow: true })
        .run(),
    );

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`p-2 rounded hover:bg-surface-inset transition-colors ${
          isInTable || isOpen
            ? "bg-surface-inset text-action-text"
            : "text-contrast-helper"
        }`}
        title="Table"
        aria-label="Table"
        aria-expanded={isOpen}
      >
        <TableIcon className="w-4 h-4" />
      </button>

      {isOpen && (
        <>
          <div className="fixed inset-0 z-[var(--z-dropdown)]" onClick={() => setIsOpen(false)} />
          <div className="absolute left-0 top-full mt-2 w-56 bg-surface-card rounded-lg shadow-float border border-surface-border p-2 z-50 flex flex-col gap-1">
            <div className="text-xs font-semibold text-contrast-icon-muted px-2 py-1 border-b border-surface-border">
              {hovered
                ? `Insert ${hovered.rows} x ${hovered.cols} table`
                : "Insert Table"}
            </div>

            {/* Track width is pinned to the cell size rather than left as 1fr:
                a fractional track stretches to the panel width, leaving each
                fixed-width cell parked at the start of a much wider column and
                the horizontal gaps visibly larger than the vertical ones. */}
            <div
              className="grid grid-cols-[repeat(6,1.5rem)] auto-rows-[1.5rem] gap-1.5 justify-center p-2"
              onMouseLeave={() => setHovered(null)}
            >
              {Array.from({ length: GRID_ROWS * GRID_COLS }, (_, index) => {
                const row = Math.floor(index / GRID_COLS) + 1;
                const col = (index % GRID_COLS) + 1;
                const active =
                  hovered !== null && row <= hovered.rows && col <= hovered.cols;
                return (
                  <button
                    key={index}
                    onMouseEnter={() => setHovered({ rows: row, cols: col })}
                    onClick={() => insertTable(row, col)}
                    aria-label={`Insert ${row} by ${col} table`}
                    className={`h-full w-full rounded-sm border transition-colors ${
                      active
                        ? "bg-action border-action"
                        : "bg-surface-inset border-control-border"
                    }`}
                  />
                );
              })}
            </div>

            {isInTable && (
              <>
                <div className="text-xs font-semibold text-contrast-icon-muted px-2 py-1 border-t border-surface-border">
                  Edit Table
                </div>

                <TableAction
                  icon={<Plus className="w-3.5 h-3.5" />}
                  label="Row below"
                  onClick={() =>
                    run(() => editor.chain().focus().addRowAfter().run())
                  }
                />
                <TableAction
                  icon={<Minus className="w-3.5 h-3.5" />}
                  label="Delete row"
                  onClick={() =>
                    run(() => editor.chain().focus().deleteRow().run())
                  }
                />
                <TableAction
                  icon={<Plus className="w-3.5 h-3.5" />}
                  label="Column right"
                  onClick={() =>
                    run(() => editor.chain().focus().addColumnAfter().run())
                  }
                />
                <TableAction
                  icon={<Minus className="w-3.5 h-3.5" />}
                  label="Delete column"
                  onClick={() =>
                    run(() => editor.chain().focus().deleteColumn().run())
                  }
                />
                <TableAction
                  icon={<Heading className="w-3.5 h-3.5" />}
                  label="Toggle header row"
                  onClick={() =>
                    run(() => editor.chain().focus().toggleHeaderRow().run())
                  }
                />
                <TableAction
                  icon={<Trash2 className="w-3.5 h-3.5" />}
                  label="Delete table"
                  destructive
                  onClick={() =>
                    run(() => editor.chain().focus().deleteTable().run())
                  }
                />
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function TableAction({
  icon,
  label,
  onClick,
  destructive = false,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-2 py-1.5 rounded text-sm text-left hover:bg-surface-inset transition-colors ${
        destructive
          ? "text-status-danger-fg"
          : "text-contrast-muted"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}
