'use client';

import { X } from 'lucide-react';
import React, { useEffect, useState, useRef } from 'react';
import { createPortal } from 'react-dom';

interface CreateTagModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (tagName: string) => void;
  title?: string;
  placeholder?: string;
  confirmText?: string;
  cancelText?: string;
}

export default function CreateTagModal({
  isOpen,
  onClose,
  onConfirm,
  title = "Create Tag",
  placeholder = "Tag name...",
  confirmText = "Create",
  cancelText = "Cancel",
}: CreateTagModalProps) {
  const [mounted, setMounted] = useState(false);
  const [tagName, setTagName] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  useEffect(() => {
    if (isOpen) {
      setTagName('');
      // Focus input after a short delay to allow modal animation
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [isOpen]);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (tagName.trim()) {
      onConfirm(tagName.trim());
      onClose();
    }
  };

  if (!isOpen || !mounted) return null;

  return createPortal(
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-md border border-gray-300 dark:border-gray-800 p-6 relative animate-in fade-in zoom-in-95 duration-200">
        <div className="flex justify-between items-center mb-4">
          <h3 className="text-lg font-bold text-gray-900 dark:text-white">{title}</h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
            <X className="w-5 h-5" />
          </button>
        </div>
        
        <form onSubmit={handleSubmit}>
          <div className="mb-6">
            <input
              ref={inputRef}
              type="text"
              value={tagName}
              onChange={(e) => setTagName(e.target.value)}
              placeholder={placeholder}
              className="w-full px-3 py-2 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-orange-500 focus:outline-none text-gray-900 dark:text-white"
            />
          </div>

          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
            >
              {cancelText}
            </button>
            <button
              type="submit"
              disabled={!tagName.trim()}
              className="px-4 py-2 text-sm font-medium text-white bg-orange-600 hover:bg-orange-700 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {confirmText}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  );
}
