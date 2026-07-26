import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { TableKit } from "@tiptap/extension-table";
import { Markdown } from "tiptap-markdown";

import RichTextEditor from "./RichTextEditor";
import MarkdownBubble from "./MarkdownBubble";
import { TableKeymap } from "@/lib/TableKeymap";

const TABLE_NOTES = `## Key Decisions

| ID | Decision | Owner |
| --- | --- | --- |
| DEC-001 | Use **PostgreSQL** as the persistence layer | Peter |
| DEC-002 | Keep Memgraph as a projection | Anna |

Trailing paragraph.`;

/** Mirrors the extension set RichTextEditor registers, for direct serialisation
 * assertions that do not need a mounted React tree. */
function buildEditor(content: string) {
  return new Editor({
    extensions: [
      StarterKit,
      TableKit.configure({ table: { resizable: true } }),
      TableKeymap,
      Markdown,
    ],
    content,
  });
}

function getMarkdown(editor: Editor): string {
  return (
    editor.storage as unknown as { markdown: { getMarkdown(): string } }
  ).markdown.getMarkdown();
}

describe("RichTextEditor table support", () => {
  it("round trips a Markdown table without altering a byte", () => {
    const editor = buildEditor(TABLE_NOTES);
    expect(getMarkdown(editor).trim()).toBe(TABLE_NOTES.trim());
  });

  it("parses a Markdown table into real table nodes rather than a paragraph", () => {
    const editor = buildEditor(TABLE_NOTES);
    const html = editor.getHTML();

    expect(html).toContain("<table");
    expect(html).toContain("<th");
    // The pre-table-support failure mode: every cell flattened into one run of
    // text with the boundaries gone and no way to recover them.
    expect(html).not.toContain("IDDecisionOwner");
  });

  it("preserves the table in the content it emits when notes are opened", async () => {
    // Regression guard for silent data loss. Opening notes emits one change
    // even when nothing was typed, and NotesView autosaves it a second later.
    // That was harmless for plain notes and fatal for tables: the editor
    // mangled the table on mount, so the autosave overwrote the stored notes
    // with the wreckage without any user input. What matters is therefore not
    // that the emission stops, but that whatever it emits still round trips.
    const onChange = vi.fn();
    render(<RichTextEditor content={TABLE_NOTES} onChange={onChange} />);

    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    for (const [emitted] of onChange.mock.calls) {
      expect(emitted).toBe(TABLE_NOTES);
    }
  });

  it("keeps a hard break inside a cell on a single Markdown row", () => {
    // tiptap-markdown serialises a hard break as <br> inside a table and as a
    // newline everywhere else. A newline here would split the row and destroy
    // the table, which is why TableKeymap rebinds Enter within a cell.
    const withBreak = `| A | B |
| --- | --- |
| one<br>two | three |`;
    const roundTripped = getMarkdown(buildEditor(withBreak));

    expect(roundTripped).toContain("<br>");
    expect(roundTripped.trim().split("\n")).toHaveLength(3);
  });

  it("serialises a multi-row table body without an HTML fallback", () => {
    const editor = buildEditor(TABLE_NOTES);
    const markdown = getMarkdown(editor);

    // The HTML fallback path triggers on merged or multi-block cells and would
    // take the table out of reach of the Markdown-based DOCX and PDF exporters.
    expect(markdown).not.toContain("<table");
    expect(markdown).toContain("| DEC-001 |");
  });
});

describe("MarkdownBubble table support", () => {
  it("renders a table in a chat answer as a table", async () => {
    render(<MarkdownBubble content={TABLE_NOTES} />);

    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.getByText("DEC-001")).toBeInTheDocument();
  });
});
