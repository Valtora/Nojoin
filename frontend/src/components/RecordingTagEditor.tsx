'use client';

import { useState } from 'react';
import { Tag } from '@/types';
import { X, Plus } from 'lucide-react';
import { removeTagFromRecording } from '@/lib/api';
import { getColorByKey } from '@/lib/constants';
import AddTagModal from './AddTagModal';

interface RecordingTagEditorProps {
  recordingId: number;
  tags: Tag[];
  onTagsUpdated?: () => void;
}

export default function RecordingTagEditor({ recordingId, tags, onTagsUpdated }: RecordingTagEditorProps) {
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
      <div className="flex flex-wrap items-center gap-3 mb-4">
        {tags.map((tag) => {
          const color = getColorByKey(tag.color);
          const parentName = getParentName(tag.parent_id);
          
          return (
            <span 
              key={tag.id || tag.name} 
              className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium border ${color.bg} ${color.text} ${color.border}`}
              title={parentName ? `Parent: ${parentName}` : undefined}
            >
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
          className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 border border-dashed border-gray-300 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500 transition-colors"
        >
          <Plus className="w-4 h-4 mr-1.5" />
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
