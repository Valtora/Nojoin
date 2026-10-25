'use client';

import { ButtonBase } from '@/components/ui/nativeBases';
import { useState, useEffect, useCallback } from 'react';
import { useOnDepsChange } from '@/lib/useOnDepsChange';
import { Plus, Check } from 'lucide-react';
import { getTags, createTag } from '@/lib/api';
import { Tag } from '@/types';
import { getColorByKey, DEFAULT_TAG_COLORS } from '@/lib/constants';
import { notifyRequestError } from '@/lib/notifyRequestError';

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

  const loadTags = useCallback(
    () =>
      getTags()
        .then(setTags)
        .catch((error: unknown) => {
          console.error('Failed to load tags:', error);
        }),
    [],
  );

  // The form clears on every open; the tag list is refreshed by the effect.
  useOnDepsChange([isOpen], () => {
    if (isOpen) {
      setInputValue('');
      setSelectedTag(null);
    }
  });

  useEffect(() => {
    if (isOpen) {
      void loadTags();
    }
  }, [isOpen, loadTags]);

  const handleCreateTag = async () => {
    const name = inputValue.trim();
    if (!name) return;
    const randomColor = DEFAULT_TAG_COLORS[Math.floor(Math.random() * DEFAULT_TAG_COLORS.length)];
    try {
      await createTag(name, randomColor);
    } catch (error: unknown) {
      // A name taken since the list loaded is refused, and that tag is the
      // one to apply, as the batch add would use it.
      const latest = await getTags().catch((): Tag[] => []);
      if (!latest.some((tag) => tag.name === name)) {
        notifyRequestError(error, 'Failed to create tag.');
        return;
      }
    }
    await loadTags();
    setSelectedTag(name);
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
            <ButtonBase
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
            </ButtonBase>
          );
        })}

        {mode === 'add' && inputValue && !filteredTags.some(t => t.name.toLowerCase() === inputValue.toLowerCase()) && (
          <ButtonBase
            onClick={handleCreateTag}
            className="flex w-full items-center gap-2 border-t border-surface-divider px-3 py-2 text-left text-sm font-medium text-action-text hover:bg-surface-inset"
          >
            <Plus aria-hidden="true" className="w-4 h-4" />
            Create &quot;{inputValue}&quot;
          </ButtonBase>
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
