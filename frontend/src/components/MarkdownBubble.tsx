'use client';

import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from 'tiptap-markdown';
import Link from '@tiptap/extension-link';
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
          class: 'text-blue-600 dark:text-blue-400 underline hover:text-blue-800 dark:hover:text-blue-300 cursor-pointer',
          target: '_blank',
          rel: 'noopener noreferrer',
        },
      }),
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
        // Only update if content changed significantly to avoid cursor jumps (though it's read-only)
        // Tiptap's markdown extension might not perfectly round-trip, so be careful.
        // For chat streaming, we want to update.
        editor.commands.setContent(content);
    }
  }, [content, editor]);

  if (!editor) {
    return null;
  }

  return <EditorContent editor={editor} />;
}

