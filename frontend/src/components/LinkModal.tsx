'use client';

import { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X, Link as LinkIcon } from 'lucide-react';

interface LinkModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (url: string) => void;
  initialUrl?: string;
}

export default function LinkModal({ isOpen, onClose, onSubmit, initialUrl = '' }: LinkModalProps) {
  const [url, setUrl] = useState(initialUrl);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  useEffect(() => {
    if (isOpen) {
      setUrl(initialUrl);
    }
  }, [isOpen, initialUrl]);

  if (!isOpen || !mounted) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    let finalUrl = url.trim();
    if (finalUrl && !/^https?:\/\//i.test(finalUrl)) {
      finalUrl = 'https://' + finalUrl;
    }
    onSubmit(finalUrl);
    onClose();
  };

  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-md flex flex-col border border-gray-300 dark:border-gray-800">
        <div className="p-4 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center">
          <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <LinkIcon className="w-4 h-4" />
            {initialUrl ? 'Edit Link' : 'Insert Link'}
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
            <X className="w-5 h-5" />
          </button>
        </div>
        
        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              URL
            </label>
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com"
              autoFocus
              className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent outline-none"
            />
          </div>
          
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-4 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700"
            >
              Save
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  );
}
