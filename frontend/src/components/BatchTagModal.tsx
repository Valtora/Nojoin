'use client';

import { useState, useEffect, useCallback } from 'react';
import { X, Plus, Check } from 'lucide-react';
import { getTags, createTag } from '@/lib/api';
import { Tag } from '@/types';
import { getColorByKey, DEFAULT_TAG_COLORS } from '@/lib/constants';

interface BatchTagModalProps {
  isOpen: boolean;
  onClose: () => void;
  onApply: (tagName: string) => void;
  count: number;
  mode: 'add' | 'remove';
}

export default function BatchTagModal({ isOpen, onClose, onApply, count, mode }: BatchTagModalProps) {
  const [tags, setTags] = useState<Tag[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [selectedTag, setSelectedTag] = useState<string | null>(null);

  const loadTags = useCallback(async () => {
    try {
      const data = await getTags();
      setTags(data);
    } catch (error) {
      console.error('Failed to load tags:', error);
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      void loadTags();
      setInputValue('');
      setSelectedTag(null);
    }
  }, [isOpen, loadTags]);

  const handleCreateTag = async () => {
    if (!inputValue.trim()) return;
    try {
      const randomColor = DEFAULT_TAG_COLORS[Math.floor(Math.random() * DEFAULT_TAG_COLORS.length)];
      await createTag(inputValue.trim(), randomColor);
      await loadTags();
      setSelectedTag(inputValue.trim());
    } catch (error) {
      console.error('Failed to create tag:', error);
    }
  };

  const filteredTags = tags.filter(tag => 
    tag.name.toLowerCase().includes(inputValue.toLowerCase())
  );

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-md border border-gray-300 dark:border-gray-800 p-6 relative animate-in fade-in zoom-in-95 duration-200">
        <div className="flex justify-between items-center mb-4">
          <h3 className="text-lg font-bold text-gray-900 dark:text-white">
            {mode === 'add' ? 'Add Tag to' : 'Remove Tag from'} {count} Recordings
          </h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
            <X className="w-5 h-5" />
          </button>
        </div>
        
        <div className="mb-4">
          <input
            autoFocus
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder="Search tags..."
            className="w-full px-3 py-2 text-sm bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-orange-500 focus:outline-none mb-2"
          />
          
          <div className="max-h-60 overflow-y-auto border border-gray-200 dark:border-gray-700 rounded-lg">
            {filteredTags.map(tag => {
              const color = getColorByKey(tag.color);
              return (
                <button
                  key={tag.id}
                  onClick={() => setSelectedTag(tag.name)}
                  className={`w-full text-left px-3 py-2 text-sm flex items-center justify-between group transition-colors ${
                    selectedTag === tag.name 
                      ? 'bg-orange-50 dark:bg-orange-900/20' 
                      : 'hover:bg-gray-50 dark:hover:bg-gray-800'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${color.dot}`} />
                    <span className="text-gray-700 dark:text-gray-200">{tag.name}</span>
                  </div>
                  {selectedTag === tag.name && <Check className="w-4 h-4 text-orange-500" />}
                </button>
              );
            })}
            
            {mode === 'add' && inputValue && !filteredTags.some(t => t.name.toLowerCase() === inputValue.toLowerCase()) && (
              <button
                onClick={handleCreateTag}
                className="w-full text-left px-3 py-2 text-sm hover:bg-gray-50 dark:hover:bg-gray-800 text-orange-600 dark:text-orange-400 font-medium border-t border-gray-100 dark:border-gray-700 flex items-center gap-2"
              >
                <Plus className="w-4 h-4" />
                Create &quot;{inputValue}&quot;
              </button>
            )}
            
            {filteredTags.length === 0 && !inputValue && (
              <div className="px-3 py-4 text-sm text-gray-500 text-center">
                No tags found
              </div>
            )}
          </div>
        </div>

        <div className="flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg text-sm font-medium"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              if (selectedTag) {
                onApply(selectedTag);
                onClose();
              }
            }}
            disabled={!selectedTag}
            className={`px-4 py-2 text-white rounded-lg text-sm font-medium transition-opacity ${
              selectedTag 
                ? 'bg-orange-600 hover:bg-orange-700' 
                : 'bg-orange-400 cursor-not-allowed opacity-50'
            }`}
          >
            {mode === 'add' ? 'Add Tag' : 'Remove Tag'}
          </button>
        </div>
      </div>
    </div>
  );
}
