'use client';

import { useState, useRef, useEffect, ReactNode } from 'react';
import { Search, ArrowRightLeft, Download, ChevronUp, ChevronDown, Undo2, Redo2, Sparkles, Loader2, Edit2, Check } from 'lucide-react';

interface NotesViewProps {
  recordingId: number;
  notes: string | null;
  onNotesChange: (notes: string) => void;
  onGenerateNotes: () => Promise<void>;
  onFindAndReplace: (find: string, replace: string) => void | Promise<void>;
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  isGenerating: boolean;
  onExport: () => void;
}

export default function NotesView({ 
  recordingId,
  notes,
  onNotesChange,
  onGenerateNotes,
  onFindAndReplace,
  onUndo,
  onRedo,
  canUndo,
  canRedo,
  isGenerating,
  onExport
}: NotesViewProps) {
  // Editing State
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(notes || '');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Find & Replace State
  const [showSearch, setShowSearch] = useState(false);
  const [showReplace, setShowReplace] = useState(false);
  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");

  // Search Matches State
  const [matches, setMatches] = useState<{startIndex: number, length: number}[]>([]);
  const [currentMatchIndex, setCurrentMatchIndex] = useState(-1);

  // Update editValue when notes change
  useEffect(() => {
    if (!isEditing) {
      setEditValue(notes || '');
    }
  }, [notes, isEditing]);

  // Calculate matches when findText or notes change
  useEffect(() => {
    if (!findText.trim() || !showSearch || !notes) {
        setMatches([]);
        setCurrentMatchIndex(-1);
        return;
    }

    const newMatches: {startIndex: number, length: number}[] = [];
    const lowerFind = findText.toLowerCase();
    const lowerNotes = notes.toLowerCase();
    
    let pos = 0;
    while (pos < lowerNotes.length) {
        const index = lowerNotes.indexOf(lowerFind, pos);
        if (index === -1) break;
        newMatches.push({
            startIndex: index,
            length: lowerFind.length
        });
        pos = index + 1;
    }

    setMatches(newMatches);
    if (newMatches.length > 0 && currentMatchIndex === -1) {
        setCurrentMatchIndex(0);
    } else if (newMatches.length === 0) {
        setCurrentMatchIndex(-1);
    }
  }, [findText, notes, showSearch]);

  const nextMatch = () => {
      if (matches.length === 0) return;
      setCurrentMatchIndex((prev) => (prev + 1) % matches.length);
  };

  const prevMatch = () => {
      if (matches.length === 0) return;
      setCurrentMatchIndex((prev) => (prev - 1 + matches.length) % matches.length);
  };

  const handleFindReplaceSubmit = async () => {
      if (!findText || isSubmitting) return;
      setIsSubmitting(true);
      try {
          await onFindAndReplace(findText, replaceText);
          setFindText("");
          setReplaceText("");
          setShowReplace(false);
          setShowSearch(false);
      } finally {
          setIsSubmitting(false);
      }
  };

  const handleReplaceCurrent = async () => {
      if (matches.length === 0 || currentMatchIndex === -1 || isSubmitting || !notes) return;
      
      const match = matches[currentMatchIndex];
      
      // Calculate new notes
      const prefix = notes.substring(0, match.startIndex);
      const suffix = notes.substring(match.startIndex + match.length);
      const newNotes = prefix + replaceText + suffix;
      
      setIsSubmitting(true);
      try {
          // Clear matches to avoid stale indices until recalculation
          setMatches([]);
          setCurrentMatchIndex(-1);
          onNotesChange(newNotes);
      } finally {
          setIsSubmitting(false);
      }
  };

  const handleEditSubmit = async () => {
    if (isSubmitting) return;
    setIsSubmitting(true);
    try {
      onNotesChange(editValue);
      setIsEditing(false);
    } finally {
      setIsSubmitting(false);
    }
  };

  const renderHighlightedNotes = (text: string) => {
    if (!findText || !showSearch || matches.length === 0) {
      return text;
    }

    let lastIndex = 0;
    const parts: ReactNode[] = [];

    matches.forEach((match, i) => {
      // Text before match
      if (match.startIndex > lastIndex) {
        parts.push(text.substring(lastIndex, match.startIndex));
      }

      // The match itself
      const isCurrent = i === currentMatchIndex;
      parts.push(
        <mark 
          key={`match-${match.startIndex}`}
          className={`${isCurrent ? 'bg-orange-400 text-white' : 'bg-yellow-200 dark:bg-yellow-900 text-gray-900 dark:text-gray-100'} rounded-sm px-0.5`}
        >
          {text.substring(match.startIndex, match.startIndex + match.length)}
        </mark>
      );

      lastIndex = match.startIndex + match.length;
    });

    // Remaining text
    if (lastIndex < text.length) {
      parts.push(text.substring(lastIndex));
    }

    return parts;
  };

  // Simple Markdown rendering
  const renderMarkdown = (text: string) => {
    if (!text) return null;
    
    const lines = text.split('\n');
    const elements: ReactNode[] = [];
    let inList = false;
    let listItems: string[] = [];
    let listType: 'ul' | 'ol' | 'checkbox' = 'ul';
    let key = 0;

    const flushList = () => {
      if (listItems.length > 0) {
        if (listType === 'checkbox') {
          elements.push(
            <ul key={key++} className="list-none space-y-1 ml-0 mb-4">
              {listItems.map((item, i) => {
                const checked = item.startsWith('[x]') || item.startsWith('[X]');
                const text = item.replace(/^\[[ xX]\]\s*/, '');
                return (
                  <li key={i} className="flex items-start gap-2">
                    <input 
                      type="checkbox" 
                      checked={checked} 
                      readOnly 
                      className="mt-1 accent-orange-500"
                    />
                    <span className={checked ? 'line-through text-gray-500' : ''}>{text}</span>
                  </li>
                );
              })}
            </ul>
          );
        } else if (listType === 'ol') {
          elements.push(
            <ol key={key++} className="list-decimal list-inside space-y-1 ml-4 mb-4">
              {listItems.map((item, i) => <li key={i}>{item}</li>)}
            </ol>
          );
        } else {
          elements.push(
            <ul key={key++} className="list-disc list-inside space-y-1 ml-4 mb-4">
              {listItems.map((item, i) => <li key={i}>{item}</li>)}
            </ul>
          );
        }
        listItems = [];
        inList = false;
      }
    };

    for (const line of lines) {
      const trimmedLine = line.trim();
      
      // Headers
      if (trimmedLine.startsWith('### ')) {
        flushList();
        elements.push(
          <h3 key={key++} className="text-lg font-bold text-gray-900 dark:text-white mt-6 mb-2">
            {trimmedLine.substring(4)}
          </h3>
        );
      } else if (trimmedLine.startsWith('## ')) {
        flushList();
        elements.push(
          <h2 key={key++} className="text-xl font-bold text-gray-900 dark:text-white mt-6 mb-3 border-b border-gray-200 dark:border-gray-700 pb-2">
            {trimmedLine.substring(3)}
          </h2>
        );
      } else if (trimmedLine.startsWith('# ')) {
        flushList();
        elements.push(
          <h1 key={key++} className="text-2xl font-bold text-gray-900 dark:text-white mt-4 mb-4">
            {trimmedLine.substring(2)}
          </h1>
        );
      }
      // Checkbox list items
      else if (trimmedLine.match(/^[-*]\s*\[[ xX]\]/)) {
        if (!inList || listType !== 'checkbox') {
          flushList();
          inList = true;
          listType = 'checkbox';
        }
        listItems.push(trimmedLine.replace(/^[-*]\s*/, ''));
      }
      // Unordered list items
      else if (trimmedLine.startsWith('- ') || trimmedLine.startsWith('* ')) {
        if (!inList || listType !== 'ul') {
          flushList();
          inList = true;
          listType = 'ul';
        }
        listItems.push(trimmedLine.substring(2));
      }
      // Ordered list items
      else if (trimmedLine.match(/^\d+\.\s/)) {
        if (!inList || listType !== 'ol') {
          flushList();
          inList = true;
          listType = 'ol';
        }
        listItems.push(trimmedLine.replace(/^\d+\.\s/, ''));
      }
      // Empty line
      else if (trimmedLine === '') {
        flushList();
        elements.push(<div key={key++} className="h-2" />);
      }
      // Bold text (simple handling)
      else if (trimmedLine.includes('**')) {
        flushList();
        const parts = trimmedLine.split(/\*\*(.*?)\*\*/g);
        elements.push(
          <p key={key++} className="text-gray-700 dark:text-gray-300 mb-2">
            {parts.map((part, i) => 
              i % 2 === 1 ? <strong key={i}>{part}</strong> : part
            )}
          </p>
        );
      }
      // Regular paragraph
      else {
        flushList();
        elements.push(
          <p key={key++} className="text-gray-700 dark:text-gray-300 mb-2">
            {trimmedLine}
          </p>
        );
      }
    }

    flushList();
    return elements;
  };

  return (
    <div className="flex flex-col h-full relative min-h-0">
      {/* Toolbar */}
      <div className="bg-gray-300 dark:bg-gray-900/95 border-b-2 border-gray-400 dark:border-gray-700 shadow-md z-10 flex flex-col">
        {/* Row 1: Header & Global Actions */}
        <div className="px-6 py-3 flex items-center justify-between gap-2">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Meeting Notes</h2>

            <div className="flex items-center gap-1">
                <button
                    onClick={onGenerateNotes}
                    disabled={isGenerating}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-orange-500 text-white text-sm rounded-md hover:bg-orange-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    title="Generate Notes with AI"
                >
                    {isGenerating ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                        <Sparkles className="w-4 h-4" />
                    )}
                    {isGenerating ? 'Generating...' : 'Generate'}
                </button>
                <div className="w-px h-4 bg-gray-300 dark:bg-gray-700 mx-1" />
                <button
                    onClick={onUndo}
                    disabled={!canUndo}
                    className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                    title="Undo"
                >
                    <Undo2 className="w-4 h-4" />
                </button>
                <button
                    onClick={onRedo}
                    disabled={!canRedo}
                    className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                    title="Redo"
                >
                    <Redo2 className="w-4 h-4" />
                </button>
                <div className="w-px h-4 bg-gray-300 dark:bg-gray-700 mx-1" />
                <button
                    onClick={onExport}
                    className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                    title="Export Notes"
                >
                    <Download className="w-4 h-4" />
                </button>
                <button 
                    onClick={() => {
                        const newState = !showSearch;
                        setShowSearch(newState);
                        if (!newState) setShowReplace(false);
                    }}
                    className={`p-2 rounded-md transition-colors ${showSearch && !showReplace ? 'bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400' : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-orange-500'}`}
                    title="Search"
                >
                    <Search className="w-4 h-4" />
                </button>
                <button 
                    onClick={() => {
                        if (showReplace) {
                            setShowReplace(false);
                            setShowSearch(false);
                        } else {
                            setShowReplace(true);
                            setShowSearch(true);
                        }
                    }}
                    className={`p-2 rounded-md transition-colors ${showReplace ? 'bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400' : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-orange-500'}`}
                    title="Find & Replace"
                >
                    <ArrowRightLeft className="w-4 h-4" />
                </button>
            </div>
        </div>

        {/* Row 2: Search & Replace Controls */}
        {(showSearch || showReplace) && (
            <div className="px-6 pb-3 flex items-center gap-2 animate-in fade-in slide-in-from-top-2 duration-200 border-t border-gray-400/30 dark:border-gray-700/50 pt-3">
                <div className="relative flex-1 flex items-center gap-1">
                    <div className="relative flex-1">
                        <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                        <input 
                            placeholder="Find..." 
                            value={findText} 
                            onChange={e => setFindText(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter') {
                                    if (e.shiftKey) prevMatch();
                                    else nextMatch();
                                }
                            }}
                            className="w-full pl-8 pr-2 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 focus:ring-2 focus:ring-orange-500 outline-none"
                            autoFocus
                        />
                    </div>
                    {matches.length > 0 && (
                        <div className="flex items-center gap-1 text-xs text-gray-500 whitespace-nowrap px-1">
                            <span>{currentMatchIndex + 1} of {matches.length}</span>
                            <button onClick={prevMatch} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded"><ChevronUp className="w-3 h-3" /></button>
                            <button onClick={nextMatch} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded"><ChevronDown className="w-3 h-3" /></button>
                        </div>
                    )}
                </div>
                {showReplace && (
                    <div className="relative flex-1">
                        <ArrowRightLeft className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                        <input 
                            placeholder="Replace..." 
                            value={replaceText} 
                            onChange={e => setReplaceText(e.target.value)}
                            className="w-full pl-8 pr-2 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 focus:ring-2 focus:ring-orange-500 outline-none"
                        />
                    </div>
                )}
                {showReplace && (
                    <>
                        <button 
                            onClick={nextMatch}
                            disabled={matches.length === 0}
                            className="px-3 py-1.5 bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 text-sm rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap shadow-sm border border-gray-200 dark:border-gray-700"
                        >
                            Find Next
                        </button>
                        <button 
                            onClick={handleReplaceCurrent}
                            disabled={matches.length === 0 || isSubmitting}
                            className="px-3 py-1.5 bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 text-sm rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap shadow-sm border border-gray-200 dark:border-gray-700"
                        >
                            Replace
                        </button>
                        <button 
                            onClick={handleFindReplaceSubmit}
                            disabled={!findText || isSubmitting}
                            className="px-3 py-1.5 bg-orange-600 text-white text-sm rounded-md hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap shadow-sm"
                        >
                            Replace All
                        </button>
                    </>
                )}
            </div>
        )}
      </div>

      {/* Notes Content */}
      <div className="flex-1 overflow-y-auto min-h-0 px-6 py-4">
        {notes ? (
          isEditing ? (
            <div className="relative">
              <textarea
                ref={textareaRef}
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                className="w-full min-h-[500px] p-4 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-lg resize-none focus:ring-2 focus:ring-orange-500 outline-none text-gray-900 dark:text-gray-100 font-mono text-sm"
                placeholder="Enter your meeting notes..."
              />
              <div className="flex justify-end gap-2 mt-3">
                <button
                  onClick={() => {
                    setIsEditing(false);
                    setEditValue(notes || '');
                  }}
                  className="px-4 py-2 text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
                >
                  Cancel
                </button>
                <button
                  onClick={handleEditSubmit}
                  disabled={isSubmitting}
                  className="flex items-center gap-2 px-4 py-2 bg-orange-500 text-white rounded-md hover:bg-orange-600 disabled:opacity-50"
                >
                  <Check className="w-4 h-4" />
                  Save
                </button>
              </div>
            </div>
          ) : (
            <div 
              className="prose prose-gray dark:prose-invert max-w-none cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded-lg p-4 transition-colors group relative"
              onClick={() => setIsEditing(true)}
              title="Click to edit"
            >
              <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-50 transition-opacity">
                <Edit2 className="w-4 h-4" />
              </div>
              {showSearch && findText ? (
                <div>
                  <div className="text-xs text-gray-400 dark:text-gray-500 mb-2 italic">
                    Showing plain text for search. Close search to view formatted notes.
                  </div>
                  <div className="whitespace-pre-wrap font-mono text-sm">{renderHighlightedNotes(notes)}</div>
                </div>
              ) : (
                renderMarkdown(notes)
              )}
            </div>
          )
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center text-gray-500 dark:text-gray-400">
            <Sparkles className="w-12 h-12 mb-4 opacity-20" />
            <p className="text-lg mb-2">No meeting notes yet</p>
            <p className="text-sm mb-4">Click "Generate" to create AI-powered meeting notes from the transcript.</p>
            <button
              onClick={onGenerateNotes}
              disabled={isGenerating}
              className="flex items-center gap-2 px-4 py-2 bg-orange-500 text-white rounded-md hover:bg-orange-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {isGenerating ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Sparkles className="w-4 h-4" />
              )}
              {isGenerating ? 'Generating...' : 'Generate Notes'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
