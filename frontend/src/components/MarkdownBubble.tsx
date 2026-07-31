'use client';

import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from 'tiptap-markdown';
import Link from '@tiptap/extension-link';
import { TableKit } from '@tiptap/extension-table';
import { useEffect } from 'react';

interface MarkdownBubbleProps {
  content: string;
}

export default function MarkdownBubble({ content }: MarkdownBubbleProps) {
  const editor = useEditor({
    editable: false,
    extensions: [
      StarterKit,
      Link.configure({
        openOnClick: true,
        autolink: true,
        HTMLAttributes: {
          class: 'text-status-info-fg underline hover:text-status-info-fg cursor-pointer',
          target: '_blank',
          rel: 'noopener noreferrer',
        },
      }),
      // Read-only surface, so column resizing would only get in the way; the
      // nodes are here purely so a table in a chat answer renders as a table.
      TableKit.configure({ table: { resizable: false } }),
      Markdown,
    ],
    content: content,
    editorProps: {
      attributes: {
        class: 'prose prose-sm dark:prose-invert max-w-none focus:outline-none',
      },
    },
    immediatelyRender: false, // Fix for SSR hydration mismatch
  });

  useEffect(() => {
    if (editor) {
        // Updates editor content on each change to support streaming responses.
        editor.commands.setContent(content);
    }
  }, [content, editor]);

  if (!editor) {
    return null;
  }

  return <EditorContent editor={editor} />;
}

