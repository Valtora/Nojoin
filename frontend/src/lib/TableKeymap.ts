import { Extension } from '@tiptap/core';

/**
 * Keeps every table cell to a single block node so the table survives as a
 * Markdown table.
 *
 * Notes are stored as Markdown, and `tiptap-markdown` serialises the entire
 * table as raw HTML the moment any cell holds more than one child node. The
 * Markdown-based DOCX and PDF exporters cannot read that, so one stray Enter
 * inside a cell would silently drop the table out of every export. Enter is
 * therefore rebound to a hard break, which `tiptap-markdown` writes as `<br>`
 * while serialising a table rather than the newline it would emit elsewhere.
 */
export const TableKeymap = Extension.create({
  name: 'tableKeymap',

  // Must outrank the default paragraph-splitting Enter binding. Returning false
  // outside a cell hands the key straight back to the usual handlers.
  priority: 1000,

  addKeyboardShortcuts() {
    return {
      Enter: () => {
        if (
          !this.editor.isActive('tableCell') &&
          !this.editor.isActive('tableHeader')
        ) {
          return false;
        }
        return this.editor.commands.setHardBreak();
      },
    };
  },
});
