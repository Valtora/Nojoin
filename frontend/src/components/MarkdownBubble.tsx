'use client';

import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from 'tiptap-markdown';
import Link from '@tiptap/extension-link';
import { TableKit } from '@tiptap/extension-table';
import { useEffect } from 'react';

interface MarkdownBubbleProps {
  content: string;
  /** Replaces the default prose classes on the rendered document. */
  className?: string;
  /** Replaces the default classes on links. */
  linkClassName?: string;
  /** Whether raw HTML in the Markdown is parsed. Off shows it as text. */
  html?: boolean;
}

const DEFAULT_CLASS = 'prose prose-sm dark:prose-invert max-w-none focus:outline-none';
const DEFAULT_LINK_CLASS = 'text-status-info-fg underline hover:text-status-info-fg cursor-pointer';

export default function MarkdownBubble({
  content,
  className = DEFAULT_CLASS,
  linkClassName = DEFAULT_LINK_CLASS,
  html = true,
}: MarkdownBubbleProps) {
  const editor = useEditor({
    // The CSP refuses the style tag Tiptap would inject; the base rules are in
    // globals.css (#501).
    injectCSS: false,
    editable: false,
    extensions: [
      StarterKit,
      Link.configure({
        openOnClick: true,
        autolink: true,
        HTMLAttributes: {
          class: linkClassName,
          target: '_blank',
          rel: 'noopener noreferrer',
        },
      }),
      // Read-only surface, so column resizing would only get in the way; the
      // nodes are here purely so a table in a chat answer renders as a table.
      TableKit.configure({ table: { resizable: false } }),
      Markdown.configure({ html }),
    ],
    content: content,
    editorProps: {
      attributes: {
        class: className,
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

