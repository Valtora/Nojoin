'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { Tag } from '@/types';
import { X, Plus, Check } from 'lucide-react';
import { getTags, createTag, addTagToRecording, removeTagFromRecording } from '@/lib/api';
import { getColorByKey, DEFAULT_TAG_COLORS } from '@/lib/constants';

interface RecordingTagEditorProps {
  recordingId: number;
  tags: Tag[];
  onTagsUpdated?: () => void;
}

export default function RecordingTagEditor({ recordingId, tags, onTagsUpdated }: RecordingTagEditorProps) {
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [inputValue, setInputValue] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const loadAllTags = useCallback(async () => {
    try {
      const data = await getTags();
      setAllTags(data);
    } catch (error) {
      console.error('Failed to load tags:', error);
    }
  }, []);

  useEffect(() => {
    if (isDropdownOpen) {
      void loadAllTags();
    }
  }, [isDropdownOpen, loadAllTags]);

  const handleAddTag = async (tagName: string) => {
    try {
      await addTagToRecording(recordingId, tagName);
      window.dispatchEvent(new CustomEvent('tags-updated'));
      if (onTagsUpdated) onTagsUpdated();
      setInputValue('');
    } catch (error) {
      console.error('Failed to add tag:', error);
    }
  };

  const handleCreateTag = async () => {
    if (!inputValue.trim()) return;
    try {
      // Check if tag already exists
      const existingTag = allTags.find(t => t.name.toLowerCase() === inputValue.trim().toLowerCase());
      if (existingTag) {
        await handleAddTag(existingTag.name);
      } else {
        // Create new tag with random color
        const randomColor = DEFAULT_TAG_COLORS[Math.floor(Math.random() * DEFAULT_TAG_COLORS.length)];
        await createTag(inputValue.trim(), randomColor);
        await handleAddTag(inputValue.trim());
      }
      setIsDropdownOpen(false);
    } catch (error) {
      console.error('Failed to create tag:', error);
    }
  };

  const handleRemoveTag = async (tagName: string) => {
    try {
      await removeTagFromRecording(recordingId, tagName);
      window.dispatchEvent(new CustomEvent('tags-updated'));
      if (onTagsUpdated) onTagsUpdated();
    } catch (error) {
      console.error('Failed to remove tag:', error);
    }
  };

  const filteredTags = allTags.filter(tag => 
    tag.name.toLowerCase().includes(inputValue.toLowerCase())
  );

  return (
    <div className="flex flex-wrap items-center gap-3 mb-4 relative">
      {tags.map((tag) => {
        const color = getColorByKey(tag.color);
        return (
          <span 
            key={tag.id || tag.name} 
            className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium border ${color.bg} ${color.text} ${color.border}`}
          >
            {tag.name}
            <button
              onClick={() => handleRemoveTag(tag.name)}
              className={`ml-2 inline-flex items-center justify-center w-4 h-4 rounded-full hover:bg-black/10 dark:hover:bg-white/10 focus:outline-none`}
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        );
      })}
      
      <div className="relative" ref={dropdownRef}>
        <button
          onClick={() => setIsDropdownOpen(!isDropdownOpen)}
          className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 border border-dashed border-gray-300 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500 transition-colors"
        >
          <Plus className="w-4 h-4 mr-1.5" />
          Add Tag
        </button>

        {isDropdownOpen && (
          <div className="absolute top-full left-0 mt-1 w-56 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 z-50 overflow-hidden">
            <div className="p-2 border-b border-gray-200 dark:border-gray-700">
              <input
                autoFocus
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleCreateTag();
                }}
                placeholder="Search or create tag..."
                className="w-full px-2 py-1 text-sm bg-gray-50 dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded focus:ring-2 focus:ring-orange-500 focus:outline-none"
              />
            </div>
            
            <div className="max-h-48 overflow-y-auto">
              {filteredTags.map(tag => {
                const isSelected = tags.some(t => t.id === tag.id);
                const color = getColorByKey(tag.color);
                return (
                  <button
                    key={tag.id}
                    onClick={() => isSelected ? handleRemoveTag(tag.name) : handleAddTag(tag.name)}
                    className="w-full text-left px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center justify-between group"
                  >
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${color.dot}`} />
                      <span className="text-gray-700 dark:text-gray-200">{tag.name}</span>
                    </div>
                    {isSelected && <Check className="w-3 h-3 text-orange-500" />}
                  </button>
                );
              })}
              
              {inputValue && !filteredTags.some(t => t.name.toLowerCase() === inputValue.toLowerCase()) && (
                <button
                  onClick={handleCreateTag}
                  className="w-full text-left px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 text-orange-600 dark:text-orange-400 font-medium border-t border-gray-100 dark:border-gray-700"
                >
                  + Create &quot;{inputValue}&quot;
                </button>
              )}
              
              {filteredTags.length === 0 && !inputValue && (
                <div className="px-3 py-2 text-xs text-gray-500 text-center">
                  No tags found
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
