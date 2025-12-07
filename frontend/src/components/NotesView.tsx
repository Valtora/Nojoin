'use client';

import { useState, useEffect, useRef } from 'react';
import { Search, ArrowRightLeft, Download, ChevronUp, ChevronDown, Undo2, Redo2, Sparkles, Loader2, Bold, Italic, Underline as UnderlineIcon, List, ListOrdered, Link as LinkIcon } from 'lucide-react';
import { Editor } from '@tiptap/react';
import RichTextEditor from './RichTextEditor';
import LinkModal from './LinkModal';
import Fuse from 'fuse.js';

interface NotesViewProps {
  recordingId: number;
  notes: string | null;
  onNotesChange: (notes: string) => void;
  onGenerateNotes: () => Promise<void>;
  onFindAndReplace: (find: string, replace: string, options?: { caseSensitive?: boolean, useRegex?: boolean }) => void | Promise<void>;
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  isGenerating: boolean;
  errorMessage?: string | null;
  onExport: () => void;
}

export default function NotesView({ 
  notes,
  onNotesChange,
  onGenerateNotes,
  onFindAndReplace,
  onUndo,
  onRedo,
  canUndo,
  canRedo,
  isGenerating,
  errorMessage,
  onExport
}: NotesViewProps) {
  const displayNotes = notes ? notes.replace(/^#+\s*Meeting Notes\s*/i, '').trim() : null;

  // Editing State
  const [localNotes, setLocalNotes] = useState(displayNotes || '');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [editor, setEditor] = useState<Editor | null>(null);
  const lastSavedNotes = useRef(displayNotes || '');
  const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Find & Replace State
  const [showSearch, setShowSearch] = useState(false);
  const [showReplace, setShowReplace] = useState(false);
  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [isFuzzy, setIsFuzzy] = useState(false);
  const [useRegex, setUseRegex] = useState(false);

  // Link Modal State
  const [isLinkModalOpen, setIsLinkModalOpen] = useState(false);
  const [linkModalUrl, setLinkModalUrl] = useState('');

  // Search Matches State
  const [matches, setMatches] = useState<{startIndex: number, length: number}[]>([]);
  const [currentMatchIndex, setCurrentMatchIndex] = useState(-1);

  // Update localNotes when notes prop changes externally
  useEffect(() => {
    const normalizedProp = displayNotes || '';
    // If the incoming prop is different from what we have locally
    if (normalizedProp !== localNotes) {
        // AND it is different from what we last saved (meaning it's not just an echo)
        if (normalizedProp !== lastSavedNotes.current) {
            setLocalNotes(normalizedProp);
            lastSavedNotes.current = normalizedProp;
        }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [displayNotes]);

  const handleEditorChange = (newContent: string) => {
    setLocalNotes(newContent);
    
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
    }

    saveTimeoutRef.current = setTimeout(() => {
      lastSavedNotes.current = newContent;
      onNotesChange(newContent);
    }, 1000); // 1 second debounce
  };

  // Calculate matches when findText or notes change
  useEffect(() => {
    if (!findText.trim() || !showSearch || !displayNotes) {
        setMatches([]);
        setCurrentMatchIndex(-1);
        return;
    }

    const newMatches: {startIndex: number, length: number}[] = [];
    
    if (isFuzzy && !useRegex) {
        // Split notes into lines to use Fuse.js (which expects a list)
        // We need to map back to original indices
        const lines = displayNotes.split('\n');
        let currentIndex = 0;
        const lineObjects = lines.map(line => {
            const obj = { text: line, startIndex: currentIndex };
            currentIndex += line.length + 1; // +1 for newline
            return obj;
        });
        
        const fuse = new Fuse(lineObjects, {
            keys: ['text'],
            includeMatches: true,
            threshold: 0.4,
            ignoreLocation: true,
            isCaseSensitive: caseSensitive
        });
        
        const results = fuse.search(findText);
        
        results.forEach(result => {
            if (result.matches) {
                result.matches.forEach(match => {
                    if (match.key === 'text' && match.indices) {
                        match.indices.forEach(range => {
                            // Calculate absolute index in the full notes string
                            const absoluteStart = result.item.startIndex + range[0];
                            newMatches.push({
                                startIndex: absoluteStart,
                                length: range[1] - range[0] + 1
                            });
                        });
                    }
                });
            }
        });
        
        // Sort matches by startIndex
        newMatches.sort((a, b) => a.startIndex - b.startIndex);
        
    } else if (useRegex) {
        try {
            const flags = caseSensitive ? 'g' : 'gi';
            const regex = new RegExp(findText, flags);
            let match;
            
            while ((match = regex.exec(displayNotes)) !== null) {
                newMatches.push({
                    startIndex: match.index,
                    length: match[0].length
                });
                if (match.index === regex.lastIndex) {
                    regex.lastIndex++;
                }
            }
        } catch (e) {
            // Invalid regex
        }
    } else {
        const search = caseSensitive ? findText : findText.toLowerCase();
        const notesContent = caseSensitive ? displayNotes : displayNotes.toLowerCase();
        
        let pos = 0;
        while (pos < notesContent.length) {
            const index = notesContent.indexOf(search, pos);
            if (index === -1) break;
            newMatches.push({
                startIndex: index,
                length: search.length
            });
            pos = index + 1;
        }
    }

    setMatches(newMatches);
    if (newMatches.length > 0 && currentMatchIndex === -1) {
        setCurrentMatchIndex(0);
    } else if (newMatches.length === 0) {
        setCurrentMatchIndex(-1);
    }
  }, [findText, displayNotes, showSearch, currentMatchIndex, caseSensitive, isFuzzy, useRegex]);

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
          await onFindAndReplace(findText, replaceText, { caseSensitive, useRegex });
          setFindText("");
          setReplaceText("");
          setShowReplace(false);
          setShowSearch(false);
      } finally {
          setIsSubmitting(false);
      }
  };

  const handleReplaceCurrent = async () => {
      if (matches.length === 0 || currentMatchIndex === -1 || isSubmitting || !localNotes) return;
      
      const match = matches[currentMatchIndex];
      
      // Calculate new notes
      const prefix = localNotes.substring(0, match.startIndex);
      const suffix = localNotes.substring(match.startIndex + match.length);
      const newNotes = prefix + replaceText + suffix;
      
      setIsSubmitting(true);
      try {
          // Clear matches to avoid stale indices until recalculation
          setMatches([]);
          setCurrentMatchIndex(-1);
          handleEditorChange(newNotes);
      } finally {
          setIsSubmitting(false);
      }
  };

  return (
    <div id="meeting-notes" className="flex flex-col h-full relative min-h-0">
      {/* Toolbar */}
      <div className="bg-gray-300 dark:bg-gray-900/95 border-b-2 border-gray-400 dark:border-gray-700 shadow-md z-10 flex flex-col">
        {/* Row 1: Header & Global Actions */}
        <div className="px-6 py-3 flex items-center justify-between gap-2">
            {/* Formatting Toolbar */}
            <div className="flex items-center gap-1">
              {editor && (
                <>
                  <button
                    onClick={() => editor.chain().focus().toggleBold().run()}
                    disabled={!editor.can().chain().focus().toggleBold().run()}
                    className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors ${editor.isActive('bold') ? 'bg-gray-200 dark:bg-gray-700 text-orange-600 dark:text-orange-400' : 'text-gray-600 dark:text-gray-400'}`}
                    title="Bold"
                  >
                    <Bold className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => editor.chain().focus().toggleItalic().run()}
                    disabled={!editor.can().chain().focus().toggleItalic().run()}
                    className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors ${editor.isActive('italic') ? 'bg-gray-200 dark:bg-gray-700 text-orange-600 dark:text-orange-400' : 'text-gray-600 dark:text-gray-400'}`}
                    title="Italic"
                  >
                    <Italic className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => editor.chain().focus().toggleUnderline().run()}
                    disabled={!editor.can().chain().focus().toggleUnderline().run()}
                    className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors ${editor.isActive('underline') ? 'bg-gray-200 dark:bg-gray-700 text-orange-600 dark:text-orange-400' : 'text-gray-600 dark:text-gray-400'}`}
                    title="Underline"
                  >
                    <UnderlineIcon className="w-4 h-4" />
                  </button>
                  <div className="w-px h-4 bg-gray-300 dark:bg-gray-700 mx-1" />
                  <button
                    onClick={() => editor.chain().focus().toggleBulletList().run()}
                    className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors ${editor.isActive('bulletList') ? 'bg-gray-200 dark:bg-gray-700 text-orange-600 dark:text-orange-400' : 'text-gray-600 dark:text-gray-400'}`}
                    title="Bullet List"
                  >
                    <List className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => editor.chain().focus().toggleOrderedList().run()}
                    className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors ${editor.isActive('orderedList') ? 'bg-gray-200 dark:bg-gray-700 text-orange-600 dark:text-orange-400' : 'text-gray-600 dark:text-gray-400'}`}
                    title="Numbered List"
                  >
                    <ListOrdered className="w-4 h-4" />
                  </button>
                  <div className="w-px h-4 bg-gray-300 dark:bg-gray-700 mx-1" />
                  <button
                    onClick={() => {
                      const previousUrl = editor.getAttributes('link').href;
                      setLinkModalUrl(previousUrl || '');
                      setIsLinkModalOpen(true);
                    }}
                    className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors ${editor.isActive('link') ? 'bg-gray-200 dark:bg-gray-700 text-orange-600 dark:text-orange-400' : 'text-gray-600 dark:text-gray-400'}`}
                    title="Link"
                  >
                    <LinkIcon className="w-4 h-4" />
                  </button>
                </>
              )}
            </div>

            <div className="flex items-center gap-1">
                <button
                    onClick={onGenerateNotes}
                    disabled={isGenerating}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-orange-600 text-white text-sm rounded-md hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    title="Generate Notes with AI"
                >
                    {isGenerating ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                        <Sparkles className="w-4 h-4" />
                    )}
                    {isGenerating ? 'Generating...' : (notes ? 'Re-Generate Notes' : 'Generate Notes')}
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
                        <div className="flex items-center gap-1.5 px-2">
                            <input 
                                type="checkbox" 
                                id="caseSensitiveNotes" 
                                checked={caseSensitive} 
                                onChange={(e) => setCaseSensitive(e.target.checked)}
                                className="rounded border-gray-300 text-orange-600 focus:ring-orange-500"
                            />
                            <label htmlFor="caseSensitiveNotes" className="text-xs text-gray-600 dark:text-gray-400 whitespace-nowrap cursor-pointer select-none">
                                Case Sensitive
                            </label>
                        </div>
                        <div className="flex items-center gap-1.5 px-2">
                            <input 
                                type="checkbox" 
                                id="fuzzySearchNotes" 
                                checked={isFuzzy} 
                                onChange={(e) => {
                                    setIsFuzzy(e.target.checked);
                                    if (e.target.checked) setUseRegex(false);
                                }}
                                className="rounded border-gray-300 text-orange-600 focus:ring-orange-500"
                            />
                            <label htmlFor="fuzzySearchNotes" className="text-xs text-gray-600 dark:text-gray-400 whitespace-nowrap cursor-pointer select-none">
                                Fuzzy
                            </label>
                        </div>
                        <div className="flex items-center gap-1.5 px-2">
                            <input 
                                type="checkbox" 
                                id="regexSearchNotes" 
                                checked={useRegex} 
                                onChange={(e) => {
                                    setUseRegex(e.target.checked);
                                    if (e.target.checked) setIsFuzzy(false);
                                }}
                                className="rounded border-gray-300 text-orange-600 focus:ring-orange-500"
                            />
                            <label htmlFor="regexSearchNotes" className="text-xs text-gray-600 dark:text-gray-400 whitespace-nowrap cursor-pointer select-none">
                                Regex
                            </label>
                        </div>
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
      <div className="flex-1 overflow-y-auto min-h-0">
        {localNotes || isGenerating ? (
            <div className="relative h-full">
              <RichTextEditor
                content={localNotes}
                onChange={handleEditorChange}
                onEditorReady={setEditor}
              />
            </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center text-gray-500 dark:text-gray-400">
            <Sparkles className="w-12 h-12 mb-4 opacity-20" />
            <p className="text-lg mb-2">No meeting notes yet</p>
            <p className="text-sm mb-4">Click &quot;Generate Notes&quot; to create AI-powered meeting notes from the transcript.</p>
            {errorMessage && (
                <div className="mb-4 p-3 bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 rounded-md text-sm max-w-md">
                    <p className="font-semibold">Generation Failed</p>
                    <p>{errorMessage}</p>
                </div>
            )}
            <button
              onClick={onGenerateNotes}
              disabled={isGenerating}
              className="flex items-center gap-2 px-4 py-2 bg-orange-600 text-white rounded-md hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
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

      {/* Link Modal */}
      <LinkModal
        isOpen={isLinkModalOpen}
        onClose={() => setIsLinkModalOpen(false)}
        initialUrl={linkModalUrl}
        onSubmit={(url) => {
          if (!editor) return;
          if (url === '') {
            editor.chain().focus().extendMarkRange('link').unsetLink().run();
            return;
          }
          editor.chain().focus().extendMarkRange('link').setLink({ href: url }).run();
        }}
      />
    </div>
  );
}
