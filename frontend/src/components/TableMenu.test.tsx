import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { TableKit } from "@tiptap/extension-table";
import { Markdown } from "tiptap-markdown";

import TableMenu from "./TableMenu";
import { TableKeymap } from "@/lib/TableKeymap";

let editor: Editor;

function getMarkdown(): string {
  return (
    editor.storage as unknown as { markdown: { getMarkdown(): string } }
  ).markdown.getMarkdown();
}

beforeEach(() => {
  editor = new Editor({
    extensions: [
      StarterKit,
      TableKit.configure({ table: { resizable: true } }),
      TableKeymap,
      Markdown,
    ],
    content: "Existing notes.",
  });
});

afterEach(() => {
  editor.destroy();
});

describe("TableMenu", () => {
  it("inserts a table of the chosen size as Markdown", async () => {
    render(<TableMenu editor={editor} />);

    fireEvent.click(screen.getByLabelText("Table"));
    fireEvent.click(screen.getByLabelText("Insert 2 by 3 table"));

    await waitFor(() => expect(getMarkdown()).toContain("|"));
    const rows = getMarkdown()
      .split("\n")
      .filter((line) => line.startsWith("|"));

    // Two requested rows plus the delimiter row the header requires.
    expect(rows).toHaveLength(3);
    expect(rows[1]).toBe("| --- | --- | --- |");
  });

  it("always gives a new table a header row", async () => {
    render(<TableMenu editor={editor} />);

    fireEvent.click(screen.getByLabelText("Table"));
    fireEvent.click(screen.getByLabelText("Insert 2 by 2 table"));

    await waitFor(() => expect(editor.getHTML()).toContain("<th"));
  });

  it("reveals the editing actions once the cursor enters a table", async () => {
    // The menu stays open throughout: entering a table is an editor
    // transaction, and the actions must appear off the back of that alone.
    render(<TableMenu editor={editor} />);

    fireEvent.click(screen.getByLabelText("Table"));
    expect(screen.queryByText("Delete table")).not.toBeInTheDocument();

    editor.commands.insertTable({ rows: 2, cols: 2, withHeaderRow: true });

    await waitFor(() =>
      expect(screen.getByText("Delete table")).toBeInTheDocument(),
    );
    expect(screen.getByText("Row below")).toBeInTheDocument();
  });

  it("does not offer cell merging, which Markdown cannot represent", () => {
    editor.commands.insertTable({ rows: 2, cols: 2, withHeaderRow: true });
    render(<TableMenu editor={editor} />);

    fireEvent.click(screen.getByLabelText("Table"));

    expect(screen.queryByText(/merge/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/split/i)).not.toBeInTheDocument();
  });

  it("removes the table from the Markdown when deleted", async () => {
    editor.commands.insertTable({ rows: 2, cols: 2, withHeaderRow: true });
    render(<TableMenu editor={editor} />);

    fireEvent.click(screen.getByLabelText("Table"));
    fireEvent.click(screen.getByText("Delete table"));

    await waitFor(() => expect(getMarkdown()).not.toContain("|"));
  });
});
