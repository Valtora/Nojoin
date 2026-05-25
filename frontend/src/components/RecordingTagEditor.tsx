'use client';

import { useState } from 'react';
import { RecordingId, Tag } from '@/types';
import { X, Plus } from 'lucide-react';
import { removeTagFromRecording } from '@/lib/api';
import { getColorByKey } from '@/lib/constants';
import AddTagModal from './AddTagModal';

interface RecordingTagEditorProps {
  recordingId: RecordingId;
  tags: Tag[];
  onTagsUpdated?: () => void;
  compact?: boolean;
}

export default function RecordingTagEditor({ recordingId, tags, onTagsUpdated, compact = false }: RecordingTagEditorProps) {
  const [isModalOpen, setIsModalOpen] = useState(false);

  const handleRemoveTag = async (tagName: string) => {
    try {
      await removeTagFromRecording(recordingId, tagName);
      window.dispatchEvent(new CustomEvent('tags-updated'));
      if (onTagsUpdated) onTagsUpdated();
    } catch (error) {
      console.error('Failed to remove tag:', error);
    }
  };

  const getParentName = (parentId?: number) => {
    if (!parentId) return null;
    const parent = tags.find(t => t.id === parentId);
    return parent?.name;
  };

  return (
    <>
      <div className={`flex flex-wrap items-center ${compact ? "gap-2" : "mb-4 gap-3"}`}>
        {tags.map((tag) => {
          const color = getColorByKey(tag.color);
          const parentName = getParentName(tag.parent_id);

          return (
            <span
              key={tag.id || tag.name}
              className={`inline-flex items-center rounded-full border bg-gray-100 text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 ${compact ? "px-2.5 py-1 text-xs font-medium" : "px-3 py-1 text-sm font-medium border-gray-300"}`}
              title={parentName ? `Parent: ${parentName}` : undefined}
            >
              <span className={`w-2 h-2 rounded-full mr-2 ${color.dot}`} />
              {parentName && <span className="opacity-60 mr-1">{parentName} &gt;</span>}
              {tag.name}
              <button
                onClick={() => handleRemoveTag(tag.name)}
                className="ml-2 inline-flex items-center justify-center w-4 h-4 rounded-full hover:bg-black/10 dark:hover:bg-white/10 focus:outline-none"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          );
        })}


        <button
          onClick={() => setIsModalOpen(true)}
          className={`inline-flex items-center rounded-full border border-dashed text-gray-500 transition-colors hover:border-gray-400 hover:text-gray-700 dark:border-gray-600 dark:text-gray-400 dark:hover:border-gray-500 dark:hover:text-gray-200 ${compact ? "px-2.5 py-1 text-xs font-medium" : "px-3 py-1 text-sm font-medium border-gray-300"}`}
        >
          <Plus className={`${compact ? "mr-1 h-3.5 w-3.5" : "mr-1.5 h-4 w-4"}`} />
          Add Tag
        </button>
      </div>

      <AddTagModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        recordingId={recordingId}
        currentTags={tags}
        onTagsUpdated={onTagsUpdated}
      />
    </>
  );
}
