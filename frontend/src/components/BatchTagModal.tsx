'use client';

import { useState, useEffect, useCallback } from 'react';
import { Plus, Check } from 'lucide-react';
import { getTags, createTag } from '@/lib/api';
import { Tag } from '@/types';
import { getColorByKey, DEFAULT_TAG_COLORS } from '@/lib/constants';

import Button from './ui/Button';
import Input from './ui/Input';
import Modal from './ui/Modal';

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

        } catch (error: unknown) {
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

        } catch (error: unknown) {
      console.error('Failed to create tag:', error);
    }
  };

  const filteredTags = tags.filter(tag =>
    tag.name.toLowerCase().includes(inputValue.toLowerCase())
  );

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="sm"
      title={`${mode === 'add' ? 'Add Tag to' : 'Remove Tag from'} ${count} Recordings`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!selectedTag}
            onClick={() => {
              if (selectedTag) {
                onApply(selectedTag);
                onClose();
              }
            }}
          >
            {mode === 'add' ? 'Add Tag' : 'Remove Tag'}
          </Button>
        </>
      }
    >
      <div className="mb-2">
        <Input
          autoFocus
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder="Search tags..."
          aria-label="Search tags"
        />
      </div>

      <div className="max-h-60 overflow-y-auto rounded-lg border border-surface-border">
        {filteredTags.map(tag => {
          const color = getColorByKey(tag.color);
          return (
            <button
              key={tag.id}
              onClick={() => setSelectedTag(tag.name)}
              className={`group flex w-full items-center justify-between px-3 py-2 text-left text-sm transition-colors ${
                selectedTag === tag.name
                  ? 'bg-action-tint'
                  : 'hover:bg-surface-inset'
              }`}
            >
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${color.dot}`} />
                <span className="text-contrast-muted">{tag.name}</span>
              </div>
              {selectedTag === tag.name && (
                <Check aria-hidden="true" className="w-4 h-4 text-action-text" />
              )}
            </button>
          );
        })}

        {mode === 'add' && inputValue && !filteredTags.some(t => t.name.toLowerCase() === inputValue.toLowerCase()) && (
          <button
            onClick={handleCreateTag}
            className="flex w-full items-center gap-2 border-t border-surface-divider px-3 py-2 text-left text-sm font-medium text-action-text hover:bg-surface-inset"
          >
            <Plus aria-hidden="true" className="w-4 h-4" />
            Create &quot;{inputValue}&quot;
          </button>
        )}

        {filteredTags.length === 0 && !inputValue && (
          <div className="px-3 py-4 text-center text-sm text-contrast-helper">
            No tags found
          </div>
        )}
      </div>
    </Modal>
  );
}
