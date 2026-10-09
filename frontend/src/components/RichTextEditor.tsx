'use client';

import { useEditor, EditorContent, Editor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Underline from '@tiptap/extension-underline';
import Link from '@tiptap/extension-link';
import { TableKit } from '@tiptap/extension-table';
import { Markdown } from 'tiptap-markdown';
import { useEffect } from 'react';

import { SearchExtension } from '@/lib/SearchExtension';
import { SpellCheckExtension } from '@/lib/SpellCheckExtension';
import { TableKeymap } from '@/lib/TableKeymap';

interface RichTextEditorProps {
  content: string;
  onChange: (content: string) => void;
  editable?: boolean;
  onEditorReady?: (editor: Editor) => void;
}

type MarkdownStorageEditor = Editor & {
  storage: {
    markdown: {
      getMarkdown(): string;
    };
  };
};

function getMarkdown(editor: Editor): string {
  return (editor as MarkdownStorageEditor).storage.markdown.getMarkdown();
}

export default function RichTextEditor({ content, onChange, editable = true, onEditorReady }: RichTextEditorProps) {
  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit,
      Underline,
      Link.configure({
        openOnClick: true,
        autolink: true,
        linkOnPaste: true,
        HTMLAttributes: {
          class: 'text-status-info-fg underline hover:text-status-info-fg cursor-pointer',
          target: '_blank',
          rel: 'noopener noreferrer',
        },
      }),
      // Registering the table nodes is what makes Markdown tables survive a
      // load: without them ProseMirror discards the table structure and
      // flattens every cell into one run-on paragraph, which the notes
      // autosave then writes back over the original.
      TableKit.configure({ table: { resizable: true } }),
      TableKeymap,
      Markdown,
      SearchExtension,
      SpellCheckExtension,
    ],
    content: content,
    editable: editable,
    onUpdate: ({ editor }) => {
      const markdown = getMarkdown(editor);
      onChange(markdown);
    },
    onCreate: ({ editor }) => {
      onEditorReady?.(editor);
    },
    editorProps: {
      attributes: {
        class: 'prose prose-gray dark:prose-invert max-w-none focus:outline-none min-h-full p-6',
        spellcheck: 'false',
      },
    },
  });

  // Update content if it changes externally (e.g. reset)
  useEffect(() => {
    if (editor && content !== getMarkdown(editor)) {
      editor.commands.setContent(content);
    }
  }, [content, editor]);

  // Update editable state
  useEffect(() => {
    if (editor) {
      editor.setEditable(editable);
    }
  }, [editable, editor]);

  if (!editor) {
    return null;
  }

  return (
    // The editor is the notes tab's scroller. It is a column so the content
    // fills it when short (the ProseMirror root is min-h-full, so a click
    // anywhere still lands in the editor) and grows past it when long, and
    // the clearance padding is room after long notes, never extra height
    // for short ones.
    <div className="flex h-full w-full flex-col overflow-y-auto bg-surface-card pb-clear-floating-0">
      <EditorContent editor={editor} className="flex-1" />
    </div>
  );
}
