"use client";

import { useState, useEffect, useMemo } from "react";
import { createPortal } from "react-dom";
import { X, Search, Plus, Pencil, Trash2, BookOpen } from "lucide-react";
import { spellCheckService } from "@/lib/spellCheckService";

interface DictionaryModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function DictionaryModal({ isOpen, onClose }: DictionaryModalProps) {
  const [mounted, setMounted] = useState(false);
  const [words, setWords] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [newWord, setNewWord] = useState("");
  const [editingWord, setEditingWord] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [isAdding, setIsAdding] = useState(false);

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  useEffect(() => {
    if (isOpen) {
      setWords(spellCheckService.getPersonalDictionaryWords());
      setSearchQuery("");
      setNewWord("");
      setEditingWord(null);
      setIsAdding(false);
    }
  }, [isOpen]);

  const filteredWords = useMemo(() => {
    const sorted = [...words].sort((a, b) => a.localeCompare(b));
    if (!searchQuery.trim()) return sorted;
    const query = searchQuery.toLowerCase();
    return sorted.filter((w) => w.toLowerCase().includes(query));
  }, [words, searchQuery]);

  const handleAdd = async () => {
    const trimmed = newWord.trim();
    if (!trimmed) return;
    if (words.includes(trimmed)) {
      setNewWord("");
      return;
    }
    await spellCheckService.addToPersonalDictionary(trimmed);
    setWords(spellCheckService.getPersonalDictionaryWords());
    setNewWord("");
    setIsAdding(false);
  };

  const handleDelete = async (word: string) => {
    await spellCheckService.removeFromPersonalDictionary(word);
    setWords(spellCheckService.getPersonalDictionaryWords());
    if (editingWord === word) {
      setEditingWord(null);
    }
  };

  const handleEditStart = (word: string) => {
    setEditingWord(word);
    setEditValue(word);
  };

  const handleEditSave = async () => {
    if (!editingWord) return;
    const trimmed = editValue.trim();
    if (!trimmed || trimmed === editingWord) {
      setEditingWord(null);
      return;
    }
    // Remove old word, add new one
    await spellCheckService.removeFromPersonalDictionary(editingWord);
    await spellCheckService.addToPersonalDictionary(trimmed);
    setWords(spellCheckService.getPersonalDictionaryWords());
    setEditingWord(null);
  };

  const handleClear = async () => {
    if (!confirm("Are you sure you want to remove all words from your custom dictionary?")) return;
    await spellCheckService.clearPersonalDictionary();
    setWords([]);
  };

  if (!isOpen || !mounted) return null;

  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-lg flex flex-col border border-gray-300 dark:border-gray-800 max-h-[80vh]">
        {/* Header */}
        <div className="p-4 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center shrink-0">
          <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-orange-500" />
            Custom Dictionary
          </h2>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Search and Add */}
        <div className="p-4 border-b border-gray-200 dark:border-gray-800 space-y-3 shrink-0">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search words..."
              className="w-full pl-9 pr-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm focus:ring-2 focus:ring-orange-500 focus:border-transparent outline-none"
            />
          </div>

          {isAdding ? (
            <div className="flex gap-2">
              <input
                type="text"
                value={newWord}
                onChange={(e) => setNewWord(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleAdd();
                  if (e.key === "Escape") { setIsAdding(false); setNewWord(""); }
                }}
                placeholder="Enter new word..."
                autoFocus
                className="flex-1 px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm focus:ring-2 focus:ring-orange-500 focus:border-transparent outline-none"
              />
              <button
                onClick={handleAdd}
                disabled={!newWord.trim()}
                className="px-3 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium transition-colors"
              >
                Add
              </button>
              <button
                onClick={() => { setIsAdding(false); setNewWord(""); }}
                className="px-3 py-2 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg text-sm transition-colors"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={() => setIsAdding(true)}
              className="flex items-center gap-2 text-sm text-orange-600 dark:text-orange-400 hover:text-orange-700 dark:hover:text-orange-300 font-medium transition-colors"
            >
              <Plus className="w-4 h-4" />
              Add Word
            </button>
          )}
        </div>

        {/* Word List */}
        <div className="flex-1 overflow-y-auto min-h-0">
          {filteredWords.length === 0 ? (
            <div className="p-6 text-center text-sm text-gray-500 dark:text-gray-400">
              {words.length === 0
                ? "Your custom dictionary is empty. Words added via the spell check context menu will appear here."
                : "No words match your search."}
            </div>
          ) : (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {filteredWords.map((word) => (
                <li key={word} className="group">
                  {editingWord === word ? (
                    <div className="flex items-center gap-2 px-4 py-2">
                      <input
                        type="text"
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleEditSave();
                          if (e.key === "Escape") setEditingWord(null);
                        }}
                        autoFocus
                        className="flex-1 px-2 py-1 rounded border border-orange-400 dark:border-orange-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-white text-sm focus:ring-2 focus:ring-orange-500 focus:border-transparent outline-none"
                      />
                      <button
                        onClick={handleEditSave}
                        className="text-sm text-orange-600 hover:text-orange-700 font-medium"
                      >
                        Save
                      </button>
                      <button
                        onClick={() => setEditingWord(null)}
                        className="text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center justify-between px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors">
                      <span className="text-sm text-gray-900 dark:text-white">{word}</span>
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          onClick={() => handleEditStart(word)}
                          className="p-1.5 text-gray-400 hover:text-orange-500 rounded transition-colors"
                          title="Edit"
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => handleDelete(word)}
                          className="p-1.5 text-gray-400 hover:text-red-500 rounded transition-colors"
                          title="Delete"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-200 dark:border-gray-800 flex justify-between items-center shrink-0">
          <span className="text-xs text-gray-500">
            {words.length} {words.length === 1 ? "word" : "words"}
          </span>
          <div className="flex gap-2">
            <button
              onClick={handleClear}
              disabled={words.length === 0}
              className="px-3 py-1.5 text-sm text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              Clear All
            </button>
            <button
              onClick={onClose}
              className="px-4 py-1.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
