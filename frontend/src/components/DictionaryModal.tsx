"use client";

import { useState, useEffect, useMemo } from "react";
import { Search, Plus, Pencil, Trash2, BookOpen } from "lucide-react";
import { spellCheckService } from "@/lib/spellCheckService";

import Button from "./ui/Button";
import IconButton from "./ui/IconButton";
import Input from "./ui/Input";
import Modal from "./ui/Modal";

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

  if (!mounted) return null;

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="lg"
      className="max-h-[calc(100dvh-2rem)]"
      title={
        <span className="flex items-center gap-2">
          <BookOpen aria-hidden="true" className="w-5 h-5 text-action-text" />
          Custom Dictionary
        </span>
      }
      footer={
        <div className="flex w-full items-center justify-between">
          <span className="text-xs text-contrast-helper">
            {words.length} {words.length === 1 ? "word" : "words"}
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="danger"
              onClick={handleClear}
              disabled={words.length === 0}
            >
              Clear All
            </Button>
            <Button size="sm" variant="ghost" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>
      }
    >
      {/* Search and Add */}
      <div className="space-y-3 pb-3">
        <Input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search words..."
          aria-label="Search words"
          iconLeft={<Search aria-hidden="true" />}
        />

        {isAdding ? (
          <div className="flex gap-2">
            <Input
              type="text"
              value={newWord}
              onChange={(e) => setNewWord(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleAdd();
                if (e.key === "Escape") { setIsAdding(false); setNewWord(""); }
              }}
              placeholder="Enter new word..."
              aria-label="New word"
              autoFocus
            />
            <Button variant="primary" onClick={handleAdd} disabled={!newWord.trim()}>
              Add
            </Button>
            <Button
              variant="ghost"
              onClick={() => { setIsAdding(false); setNewWord(""); }}
            >
              Cancel
            </Button>
          </div>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setIsAdding(true)}
            className="text-action-text"
            iconLeft={<Plus aria-hidden="true" className="w-4 h-4" />}
          >
            Add Word
          </Button>
        )}
      </div>

      {/* Word List */}
      <div className="-mx-5 border-t border-surface-divider">
        {filteredWords.length === 0 ? (
          <div className="p-6 text-center text-sm text-contrast-helper">
            {words.length === 0
              ? "Your custom dictionary is empty. Words added via the spell check context menu will appear here."
              : "No words match your search."}
          </div>
        ) : (
          <ul className="divide-y divide-surface-divider">
            {filteredWords.map((word) => (
              <li key={word} className="group">
                {editingWord === word ? (
                  <div className="flex items-center gap-2 px-4 py-2">
                    <Input
                      type="text"
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleEditSave();
                        if (e.key === "Escape") setEditingWord(null);
                      }}
                      aria-label={`Rename ${word}`}
                      autoFocus
                    />
                    <Button size="sm" variant="ghost" onClick={handleEditSave} className="text-action-text">
                      Save
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditingWord(null)}>
                      Cancel
                    </Button>
                  </div>
                ) : (
                  <div className="flex items-center justify-between px-4 py-2.5 transition-colors hover:bg-surface-inset">
                    <span className="text-sm text-foreground">{word}</span>
                    {/* Shown outright on touch, hover-revealed on desktop. */}
                    <div className="flex items-center gap-1 transition-opacity lg:opacity-0 lg:group-hover:opacity-100">
                      <IconButton
                        size="sm"
                        onClick={() => handleEditStart(word)}
                        aria-label={`Edit ${word}`}
                        title="Edit"
                        icon={<Pencil aria-hidden="true" />}
                      />
                      <IconButton
                        size="sm"
                        variant="danger"
                        onClick={() => handleDelete(word)}
                        aria-label={`Delete ${word}`}
                        title="Delete"
                        icon={<Trash2 aria-hidden="true" />}
                      />
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
