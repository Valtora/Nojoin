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
    // Regression guard for silent data loss. NotesView autosaves every change
    // the editor emits, and the editor once mangled tables on mount, so the
    // autosave overwrote the stored notes without any user input. Opening
    // notes now emits nothing; anything that is emitted must still round trip.
    const onChange = vi.fn();
    render(<RichTextEditor content={TABLE_NOTES} onChange={onChange} />);

    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    for (const [emitted] of onChange.mock.calls) {
      expect(emitted).toBe(TABLE_NOTES);
    }
  });

  it("emits nothing when notes open, even if their Markdown is re-serialised", async () => {
    // "*" bullets come back as "-", so the stored notes and the editor's
    // Markdown differ before anyone types. The resync must not count as an edit.
    const onChange = vi.fn();
    let ready: Editor | null = null;
    render(
      <RichTextEditor
        content={"## Summary\n\n* first point\n* second point"}
        onChange={onChange}
        onEditorReady={(editor) => {
          ready = editor as unknown as Editor;
        }}
      />,
    );

    await waitFor(() => expect(screen.getByText("first point")).toBeInTheDocument());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(onChange).not.toHaveBeenCalled();

    // An edit still reaches the autosave.
    ready!.commands.insertContentAt(ready!.state.doc.content.size, "<p>Added.</p>");
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    expect(onChange.mock.calls[0][0]).toContain("Added.");
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

describe("RichTextEditor resync after a save (issue #1074)", () => {
  // Notes that end with a table: the editor's Markdown for them ends in a
  // newline, and NotesView hands saved notes back trimmed.
  const ACTION_ITEMS = `## Action items

| Owner | Task | Due |
| --- | --- | --- |
| Alice Example | Draft the release note | Thursday |
| Bob Sample | Check the onboarding emails | Friday |`;

  function caretRow(editor: Editor): number | null {
    const { $from } = editor.state.selection;
    for (let depth = $from.depth; depth > 0; depth--) {
      if ($from.node(depth).type.name === "tableRow") {
        return $from.index(depth - 1);
      }
    }
    return null;
  }

  /** Puts the caret at the start of the given row's first cell. */
  function caretIntoRow(editor: Editor, row: number) {
    let seen = 0;
    let target = -1;
    editor.state.doc.descendants((node, pos) => {
      if (node.type.name === "tableRow") {
        if (seen === row) {
          // Row, then cell, then the cell's paragraph.
          target = pos + 3;
        }
        seen++;
      }
      return target < 0;
    });
    editor.commands.setTextSelection(target);
  }

  async function mountEditor(content: string) {
    const onChange = vi.fn();
    let ready: Editor | null = null;
    const props = {
      onChange,
      onEditorReady: (editor: unknown) => {
        ready = editor as Editor;
      },
    };
    const view = render(<RichTextEditor content={content} {...props} />);
    await waitFor(() => expect(ready).not.toBeNull());
    return {
      editor: ready! as Editor,
      onChange,
      show: (next: string) => view.rerender(<RichTextEditor content={next} {...props} />),
    };
  }

  it("emits nothing when notes that end with a table open", async () => {
    // The stored notes lack the newline the editor puts after a table. That
    // no longer replaces the document on mount, so the update setEditable
    // emits on mount must not report the loaded notes as an edit (#463).
    const { onChange } = await mountEditor(ACTION_ITEMS);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("keeps the caret in a row just added when the saved notes come back", async () => {
    const { editor, onChange, show } = await mountEditor(ACTION_ITEMS);

    // Row below from Alice Example's row, then click into the new row.
    caretIntoRow(editor, 1);
    editor.commands.addRowAfter();
    caretIntoRow(editor, 2);
    const saved = onChange.mock.lastCall![0] as string;
    expect(saved.endsWith("\n")).toBe(true);
    show(saved);
    const doc = editor.state.doc;
    const caret = editor.state.selection.from;

    // The autosave lands and NotesView passes the saved notes back trimmed.
    show(saved.trim());

    expect(caretRow(editor)).toBe(2);
    expect(editor.state.selection.from).toBe(caret);
    expect(editor.state.doc).toBe(doc);
    editor.commands.insertContent("Carol Example");
    const rows = [...document.querySelectorAll("tr")].map((row) => row.textContent);
    expect(rows[2]).toBe("Carol Example");
    expect(rows[3]).toBe("Bob SampleCheck the onboarding emailsFriday");
  });

  it("keeps a blank first line that the saved notes come back without", async () => {
    const { editor, onChange, show } = await mountEditor("Book the room.");

    // Enter at the start of the notes, then back up onto the blank line.
    editor.chain().setTextSelection(1).splitBlock().setTextSelection(1).run();
    const saved = onChange.mock.lastCall![0] as string;
    expect(saved).toBe("\nBook the room.");
    show(saved);
    const doc = editor.state.doc;

    show(saved.trim());

    expect(editor.state.selection.from).toBe(1);
    expect(editor.state.doc).toBe(doc);
  });

  it("still loads notes that changed elsewhere, without reporting them as an edit", async () => {
    const { editor, onChange, show } = await mountEditor(ACTION_ITEMS);

    show(`${ACTION_ITEMS}\n\nBob Sample moved the review to Monday.`);

    await waitFor(() => expect(screen.getByText("Bob Sample moved the review to Monday.")).toBeInTheDocument());
    expect(getMarkdown(editor)).toContain("Bob Sample moved the review to Monday.");
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("MarkdownBubble table support", () => {
  it("renders a table in a chat answer as a table", async () => {
    render(<MarkdownBubble content={TABLE_NOTES} />);

    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.getByText("DEC-001")).toBeInTheDocument();
  });
});

describe("Tiptap extensions", () => {
  const LINKED = "Alice Example shared [the plan](https://example.com/plan).";
  type EditorRoot = HTMLElement & { editor?: Editor };

  // StarterKit 3 already includes Link and Underline. Registering either again
  // ran two copies of its autolink, click and paste plugins, and Tiptap warned
  // on every editor (#621).
  it.each([
    ["MarkdownBubble", () => <MarkdownBubble content={LINKED} />],
    ["RichTextEditor", () => <RichTextEditor content={LINKED} onChange={vi.fn()} />],
  ])("%s registers each extension once and keeps its link attributes", async (_, ui) => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { container } = render(ui());

    const link = await screen.findByRole("link", { name: "the plan" });
    const root = container.querySelector(".ProseMirror") as EditorRoot | null;
    const names = root?.editor?.extensionManager.extensions.map((extension) => extension.name) ?? [];
    expect(names).toContain("link");
    expect(names).toContain("underline");
    expect(names.filter((name, index) => names.indexOf(name) !== index)).toEqual([]);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link.className).toContain("text-status-info-fg");
    expect(warn).not.toHaveBeenCalledWith(expect.stringContaining("Duplicate extension names"));
    warn.mockRestore();
  });
});

describe("RichTextEditor accessible name (issue #856)", () => {
  it("names the editor Meeting notes and says when it takes no input", async () => {
    const view = render(<RichTextEditor content="Book the room." editable={false} onChange={vi.fn()} />);

    const editor = await screen.findByRole("textbox", { name: "Meeting notes" });
    expect(editor).toHaveAttribute("aria-readonly", "true");

    view.rerender(<RichTextEditor content="Book the room." editable onChange={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "Meeting notes" })).toHaveAttribute("aria-readonly", "false"),
    );
  });
});
