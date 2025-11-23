'use client';

import { useState } from 'react';
import { Tag } from '@/types';
import { X, Plus } from 'lucide-react';

interface TagsInputProps {
  tags: Tag[];
  onAddTag: (tagName: string) => void;
  onRemoveTag: (tagName: string) => void;
}

export default function TagsInput({ tags, onAddTag, onRemoveTag }: TagsInputProps) {
  const [inputValue, setInputValue] = useState('');
  const [isInputVisible, setIsInputVisible] = useState(false);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (inputValue.trim()) {
        onAddTag(inputValue.trim());
        setInputValue('');
        setIsInputVisible(false);
      }
    } else if (e.key === 'Escape') {
      setIsInputVisible(false);
      setInputValue('');
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2 mb-4">
      {tags.map((tag) => (
        <span 
          key={tag.id || tag.name} 
          className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-200 border border-orange-200 dark:border-orange-800"
        >
          {tag.name}
          <button
            onClick={() => onRemoveTag(tag.name)}
            className="ml-1.5 inline-flex items-center justify-center w-3 h-3 rounded-full text-orange-600 hover:bg-orange-200 dark:text-orange-400 dark:hover:bg-orange-800 focus:outline-none"
          >
            <X className="w-2.5 h-2.5" />
          </button>
        </span>
      ))}
      
      {isInputVisible ? (
        <input
          autoFocus
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={() => {
             if (inputValue.trim()) {
                onAddTag(inputValue.trim());
             }
             setInputValue('');
             setIsInputVisible(false);
          }}
          className="w-24 px-2 py-0.5 text-xs border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-1 focus:ring-orange-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
          placeholder="New tag..."
        />
      ) : (
        <button
          onClick={() => setIsInputVisible(true)}
          className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 border border-dashed border-gray-300 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500 transition-colors"
        >
          <Plus className="w-3 h-3 mr-1" />
          Add Tag
        </button>
      )}
    </div>
  );
}
