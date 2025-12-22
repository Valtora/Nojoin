'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { Tag } from '@/types';
import { X, Plus, Check, MoreVertical } from 'lucide-react';
import { getTags, createTag, addTagToRecording, updateTag, deleteTag } from '@/lib/api';
import { getColorByKey, DEFAULT_TAG_COLORS } from '@/lib/constants';
import ContextMenu from './ContextMenu';

interface AddTagModalProps {
  isOpen: boolean;
  onClose: () => void;
  recordingId: number;
  currentTags: Tag[];
  onTagsUpdated?: () => void;
}

interface TagWithChildren extends Tag {
  children?: TagWithChildren[];
}

export default function AddTagModal({ isOpen, onClose, recordingId, currentTags, onTagsUpdated }: AddTagModalProps) {
  const [mounted, setMounted] = useState(false);
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; tag: Tag } | null>(null);
  const [editingTagId, setEditingTagId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState('');

  useEffect(() => {
    setMounted(true);
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
    if (isOpen) {
      void loadAllTags();
      setInputValue('');
    }
  }, [isOpen, loadAllTags]);

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

  const handleCreateTag = async (name: string, parentId?: number) => {
    if (!name.trim()) return;
    try {
      const existingTag = allTags.find(t => t.name.toLowerCase() === name.trim().toLowerCase());
      if (existingTag) {
        await handleAddTag(existingTag.name);
      } else {
        const randomColor = DEFAULT_TAG_COLORS[Math.floor(Math.random() * DEFAULT_TAG_COLORS.length)];
        await createTag(name.trim(), randomColor, parentId);
        if (!parentId) {
          await handleAddTag(name.trim());
        } else {
          await loadAllTags();
        }
      }
      setInputValue('');
    } catch (error) {
      console.error('Failed to create tag:', error);
    }
  };

  const handleRenameTag = async (tagId: number, newName: string) => {
    try {
      await updateTag(tagId, { name: newName });
      await loadAllTags();
      window.dispatchEvent(new CustomEvent('tags-updated'));
      if (onTagsUpdated) onTagsUpdated();
      setEditingTagId(null);
    } catch (error) {
      console.error('Failed to rename tag:', error);
    }
  };

  const handleDeleteTag = async (tagId: number) => {
    if (!confirm('Are you sure you want to delete this tag and all its children?')) return;
    try {
      await deleteTag(tagId);
      await loadAllTags();
      window.dispatchEvent(new CustomEvent('tags-updated'));
      if (onTagsUpdated) onTagsUpdated();
    } catch (error) {
      console.error('Failed to delete tag:', error);
    }
  };

  const tagTree = useMemo(() => {
    const tagMap = new Map<number, TagWithChildren>();
    const roots: TagWithChildren[] = [];

    allTags.forEach(tag => {
      tagMap.set(tag.id, { ...tag, children: [] });
    });

    allTags.forEach(tag => {
      const node = tagMap.get(tag.id)!;
      if (tag.parent_id && tagMap.has(tag.parent_id)) {
        tagMap.get(tag.parent_id)!.children!.push(node);
      } else {
        roots.push(node);
      }
    });

    return roots;
  }, [allTags]);

  const renderTagTree = (nodes: TagWithChildren[], level = 0) => {
    return nodes.map(node => {
      const isSelected = currentTags.some(t => t.id === node.id);
      const color = getColorByKey(node.color);
      const isEditing = editingTagId === node.id;

      if (inputValue && !node.name.toLowerCase().includes(inputValue.toLowerCase()) && 
          !node.children?.some(c => c.name.toLowerCase().includes(inputValue.toLowerCase()))) {
        return null;
      }

      return (
        <div key={node.id} className="w-full">
          <div 
            className="w-full flex items-center justify-between px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 group"
            style={{ paddingLeft: `${level * 12 + 12}px` }}
            onContextMenu={(e) => {
              e.preventDefault();
              setContextMenu({ x: e.clientX, y: e.clientY, tag: node });
            }}
          >
            <div className="flex items-center gap-2 flex-1">
              <span className={`w-2 h-2 rounded-full ${color.dot}`} />
              {isEditing ? (
                <input 
                  autoFocus
                  className="bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded px-1 py-0.5 text-sm w-full"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleRenameTag(node.id, editValue);
                    if (e.key === 'Escape') setEditingTagId(null);
                  }}
                  onBlur={() => setEditingTagId(null)}
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <button 
                  className="text-gray-700 dark:text-gray-200 flex-1 text-left"
                  onClick={() => isSelected ? null : handleAddTag(node.name)}
                >
                  {node.name}
                </button>
              )}
            </div>
            <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
              {isSelected && <Check className="w-3 h-3 text-orange-500 mr-2" />}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setContextMenu({ x: e.clientX, y: e.clientY, tag: node });
                }}
                className="p-1 hover:bg-gray-200 dark:hover:bg-gray-600 rounded"
              >
                <MoreVertical className="w-3 h-3 text-gray-500" />
              </button>
            </div>
          </div>
          {node.children && node.children.length > 0 && (
            <div>
              {renderTagTree(node.children, level + 1)}
            </div>
          )}
        </div>
      );
    });
  };

  if (!isOpen || !mounted) return null;

  const modalContent = (
    <div className="fixed inset-0 z-[99999] flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-md border border-gray-300 dark:border-gray-800 relative animate-in fade-in zoom-in-95 duration-200 max-h-[80vh] flex flex-col">
        <div className="flex justify-between items-center p-6 border-b border-gray-300 dark:border-gray-800">
          <h3 className="text-lg font-bold text-gray-900 dark:text-white">
            Add Tags
          </h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
            <X className="w-5 h-5" />
          </button>
        </div>
        
        <div className="p-6 flex-1 overflow-hidden flex flex-col">
          <div className="mb-4">
            <input
              autoFocus
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCreateTag(inputValue);
              }}
              placeholder="Search or create tag..."
              className="w-full px-3 py-2 text-sm bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-orange-500 focus:outline-none"
            />
          </div>
          
          <div className="flex-1 overflow-y-auto border border-gray-200 dark:border-gray-700 rounded-lg" style={{ minHeight: '200px', maxHeight: '400px' }}>
            {renderTagTree(tagTree)}
            
            {inputValue && !allTags.some(t => t.name.toLowerCase() === inputValue.toLowerCase()) && (
              <button
                onClick={() => handleCreateTag(inputValue)}
                className="w-full text-left px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 text-orange-600 dark:text-orange-400 font-medium border-t border-gray-100 dark:border-gray-700"
              >
                + Create &quot;{inputValue}&quot;
              </button>
            )}
            
            {allTags.length === 0 && !inputValue && (
              <div className="px-3 py-4 text-sm text-gray-500 text-center">
                No tags found
              </div>
            )}
          </div>
        </div>

        <div className="flex justify-end gap-3 p-6 border-t border-gray-300 dark:border-gray-800">
          <button
            onClick={onClose}
            className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg text-sm font-medium"
          >
            Done
          </button>
        </div>
      </div>

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
          items={[
            {
              label: 'Rename',
              onClick: () => {
                setEditingTagId(contextMenu.tag.id);
                setEditValue(contextMenu.tag.name);
                setContextMenu(null);
              }
            },
            {
              label: 'Add Sub-tag',
              onClick: () => {
                const name = prompt(`Create sub-tag for "${contextMenu.tag.name}":`);
                if (name) handleCreateTag(name, contextMenu.tag.id);
                setContextMenu(null);
              }
            },
            {
              label: 'Delete',
              onClick: () => {
                handleDeleteTag(contextMenu.tag.id);
                setContextMenu(null);
              },
              className: 'text-red-600 dark:text-red-400'
            }
          ]}
        />
      )}
    </div>
  );

  return createPortal(modalContent, document.body);
}
