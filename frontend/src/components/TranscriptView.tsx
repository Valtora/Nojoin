'use client';

import { TranscriptSegment, RecordingSpeaker, GlobalSpeaker } from '@/types';
import { useRef, useEffect, useState } from 'react';
import { Play, Pause, Search, ArrowRightLeft, Download, ChevronUp, ChevronDown, Undo2, Redo2, Settings } from 'lucide-react';
import { getColorByKey } from '@/lib/constants';
import SpeakerAssignmentPopover from './SpeakerAssignmentPopover';
import Fuse from 'fuse.js';

interface TranscriptViewProps {
    recordingId: number;
    segments: TranscriptSegment[];
    currentTime: number;
    onPlaySegment: (start: number, end: number) => void;
    isPlaying: boolean;
    onPause: () => void;
    onResume: () => void;
    speakerMap: Record<string, string>;
    speakers: RecordingSpeaker[];
    globalSpeakers: GlobalSpeaker[];
    onRenameSpeaker: (label: string, newName: string) => void | Promise<void>;
    onUpdateSegmentSpeaker: (index: number, newSpeakerName: string) => void | Promise<void>;
    onUpdateSegmentText: (index: number, text: string) => void | Promise<void>;
    onFindAndReplace: (find: string, replace: string, options?: { caseSensitive?: boolean, useRegex?: boolean }) => void | Promise<void>;
    speakerColors: Record<string, string>;
    onUndo: () => void;
    onRedo: () => void;
    canUndo: boolean;
    canRedo: boolean;
    onExport: () => void;
}

const formatTime = (seconds: number) => {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = Math.floor(seconds % 60);
    return `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
};

export default function TranscriptView({
    segments,
    currentTime,
    onPlaySegment,
    isPlaying,
    onPause,
    onResume,
    speakerMap,
    speakers,
    globalSpeakers,
    onRenameSpeaker,
    onUpdateSegmentSpeaker,
    onUpdateSegmentText,
    onFindAndReplace,
    speakerColors,
    onUndo,
    onRedo,
    canUndo,
    canRedo,
    onExport
}: TranscriptViewProps) {
    const activeSegmentRef = useRef<HTMLDivElement>(null);

    // Editing State
    const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null);
    const [editingSegmentSpeakerIndex, setEditingSegmentSpeakerIndex] = useState<number | null>(null);
    const [editingTextIndex, setEditingTextIndex] = useState<number | null>(null);

    // Popover State
    const [activePopover, setActivePopover] = useState<{ index: number; target: HTMLElement } | null>(null);

    const [editValue, setEditValue] = useState("");
    const [isSubmitting, setIsSubmitting] = useState(false);

    // Find & Replace State
    const [showSearch, setShowSearch] = useState(false);
    const [showReplace, setShowReplace] = useState(false);
    const [showSettings, setShowSettings] = useState(false);
    const [findText, setFindText] = useState("");
    const [replaceText, setReplaceText] = useState("");
    const [caseSensitive, setCaseSensitive] = useState(false);
    const [isFuzzy, setIsFuzzy] = useState(false);
    const [useRegex, setUseRegex] = useState(false);

    // Search Matches State
    const [matches, setMatches] = useState<{ segmentIndex: number, startIndex: number, length: number }[]>([]);
    const [currentMatchIndex, setCurrentMatchIndex] = useState(-1);
    const prevFindTextRef = useRef(findText);

    // Calculate matches when findText or segments change
    useEffect(() => {
        if (!findText.trim() || !showSearch) {
            setMatches([]);
            setCurrentMatchIndex(-1);
            return;
        }

        const newMatches: { segmentIndex: number, startIndex: number, length: number }[] = [];

        if (isFuzzy && !useRegex) {
            const fuse = new Fuse(segments, {
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
                                newMatches.push({
                                    segmentIndex: result.refIndex,
                                    startIndex: range[0],
                                    length: range[1] - range[0] + 1
                                });
                            });
                        }
                    });
                }
            });

            // Sort matches by segmentIndex then startIndex
            newMatches.sort((a, b) => {
                if (a.segmentIndex !== b.segmentIndex) return a.segmentIndex - b.segmentIndex;
                return a.startIndex - b.startIndex;
            });

        } else if (useRegex) {
            try {
                const flags = caseSensitive ? 'g' : 'gi';
                const regex = new RegExp(findText, flags);

                segments.forEach((segment, sIndex) => {
                    let match;
                    // Reset lastIndex for each segment if using global flag
                    regex.lastIndex = 0;

                    while ((match = regex.exec(segment.text)) !== null) {
                        newMatches.push({
                            segmentIndex: sIndex,
                            startIndex: match.index,
                            length: match[0].length
                        });
                        // Prevent infinite loop with zero-width matches
                        if (match.index === regex.lastIndex) {
                            regex.lastIndex++;
                        }
                    }
                });
            } catch (e) {
                // Invalid regex, ignore
            }
        } else {
            segments.forEach((segment, sIndex) => {
                const text = caseSensitive ? segment.text : segment.text.toLowerCase();
                const search = caseSensitive ? findText : findText.toLowerCase();

                let pos = 0;
                while (pos < text.length) {
                    const index = text.indexOf(search, pos);
                    if (index === -1) break;
                    newMatches.push({
                        segmentIndex: sIndex,
                        startIndex: index,
                        length: search.length
                    });
                    pos = index + 1;
                }
            });
        }

        setMatches(newMatches);

        // Smart index management
        setCurrentMatchIndex(prevIndex => {
            // If search term changed, reset to first match
            if (findText !== prevFindTextRef.current) {
                return newMatches.length > 0 ? 0 : -1;
            }

            // If segments updated (e.g. replace), try to maintain relative position
            if (newMatches.length === 0) return -1;
            if (prevIndex >= newMatches.length) return newMatches.length - 1;
            // If we just replaced the current match, the next one slides into this index (or close to it)
            return prevIndex;
        });

        prevFindTextRef.current = findText;
    }, [findText, segments, showSearch, caseSensitive, isFuzzy, useRegex]);

    // Scroll to current match
    useEffect(() => {
        if (currentMatchIndex >= 0 && matches[currentMatchIndex]) {
            const match = matches[currentMatchIndex];
            const element = document.getElementById(`segment-${match.segmentIndex}`);
            if (element) {
                element.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }
    }, [currentMatchIndex, matches]);

    const nextMatch = () => {
        if (matches.length === 0) return;
        setCurrentMatchIndex((prev) => (prev + 1) % matches.length);
    };

    const prevMatch = () => {
        if (matches.length === 0) return;
        setCurrentMatchIndex((prev) => (prev - 1 + matches.length) % matches.length);
    };

    const renderHighlightedText = (text: string, segmentIndex: number) => {
        if (!findText || !showSearch || matches.length === 0) return text;

        const segmentMatches = matches.filter(m => m.segmentIndex === segmentIndex);
        if (segmentMatches.length === 0) return text;

        let lastIndex = 0;
        const parts = [];

        segmentMatches.forEach((match) => {
            // Text before match
            if (match.startIndex > lastIndex) {
                parts.push(text.substring(lastIndex, match.startIndex));
            }

            // The match itself
            const isCurrent = matches[currentMatchIndex] === match;
            parts.push(
                <mark
                    key={`${segmentIndex}-${match.startIndex}`}
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

    const getSpeakerColor = (speakerLabel: string) => {
        // Get the color key from speakerColors, default to 'gray' if not found
        const colorKey = speakerColors[speakerLabel] || 'gray';
        const colorOption = getColorByKey(colorKey);
        // Return combined bg, border classes for the chat bubble
        return `${colorOption.bg} ${colorOption.border}`;
    };

    useEffect(() => {
        if (activeSegmentRef.current) {
            activeSegmentRef.current.scrollIntoView({
                behavior: 'smooth',
                block: 'center',
            });
        }
    }, [currentTime]);

    const handleSpeakerRenameSubmit = async () => {
        if (editingSpeaker && editValue.trim()) {
            setIsSubmitting(true);
            try {
                await onRenameSpeaker(editingSpeaker, editValue.trim());
            } finally {
                setIsSubmitting(false);
                setEditingSpeaker(null);
            }
        } else {
            setEditingSpeaker(null);
        }
    };

    const handleSegmentSpeakerSubmit = async (index: number) => {
        if (editValue.trim() && !isSubmitting) {
            setIsSubmitting(true);
            try {
                await onUpdateSegmentSpeaker(index, editValue.trim());
            } finally {
                setIsSubmitting(false);
                setEditingSegmentSpeakerIndex(null);
            }
        } else if (!editValue.trim()) {
            setEditingSegmentSpeakerIndex(null);
        }
    };

    const handleTextClick = (index: number, text: string, e: React.MouseEvent) => {
        e.stopPropagation();
        setEditingTextIndex(index);
        setEditValue(text);
        setEditingSpeaker(null);
        setEditingSegmentSpeakerIndex(null);
    };

    const handleTextSubmit = async (index: number) => {
        if (editValue !== segments[index].text && !isSubmitting) {
            setIsSubmitting(true);
            try {
                await onUpdateSegmentText(index, editValue);
            } finally {
                setIsSubmitting(false);
                setEditingTextIndex(null);
            }
        } else {
            setEditingTextIndex(null);
        }
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
        if (matches.length === 0 || currentMatchIndex === -1 || isSubmitting) return;

        const match = matches[currentMatchIndex];
        const segment = segments[match.segmentIndex];

        // Calculate new text
        const prefix = segment.text.substring(0, match.startIndex);
        const suffix = segment.text.substring(match.startIndex + match.length);
        const newText = prefix + replaceText + suffix;

        setIsSubmitting(true);
        try {
            await onUpdateSegmentText(match.segmentIndex, newText);
        } catch (e) {
            console.error("Failed to replace text", e);
        } finally {
            setIsSubmitting(false);
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent, type: 'segmentSpeaker' | 'text', indexOrLabel: number | string) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (type === 'segmentSpeaker') handleSegmentSpeakerSubmit(indexOrLabel as number);
            else if (type === 'text') handleTextSubmit(indexOrLabel as number);
        } else if (e.key === 'Escape') {
            setEditingSpeaker(null);
            setEditingSegmentSpeakerIndex(null);
            setEditingTextIndex(null);
        }
    };

    const hasKnownSpeakers = segments.some(s => s.speaker !== 'UNKNOWN');
    const displaySegments = hasKnownSpeakers
        ? segments.filter(s => s.speaker !== 'UNKNOWN')
        : segments;

    return (
        <div id="transcript-view" className="flex flex-col h-full relative min-h-0">
            {/* Toolbar */}
            <div className="bg-gray-300 dark:bg-gray-900/95 border-b-2 border-gray-400 dark:border-gray-700 shadow-md z-10 flex flex-col">
                {/* Row 1: Header & Global Actions */}
                <div className="px-6 py-3 flex items-center justify-between gap-2">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Transcript</h2>

                    <div className="flex items-center gap-1">
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
                            title="Export"
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
                        <div className="relative flex-1 flex items-center gap-1 min-w-0">
                            <div className="relative flex-1 min-w-0">
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
                                    className="w-full pl-8 pr-2 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 focus:ring-2 focus:ring-orange-500 outline-none min-w-0"
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
                            <div className="relative flex-1 min-w-0">
                                <ArrowRightLeft className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                                <input
                                    placeholder="Replace..."
                                    value={replaceText}
                                    onChange={e => setReplaceText(e.target.value)}
                                    className="w-full pl-8 pr-2 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 focus:ring-2 focus:ring-orange-500 outline-none min-w-0"
                                />
                            </div>
                        )}
                        {showReplace && (
                            <div className="flex items-center gap-2">
                                {/* Settings Toggle */}
                                <div className="relative">
                                    <button
                                        onClick={() => setShowSettings(!showSettings)}
                                        className={`p-1.5 rounded-md transition-colors ${showSettings ? 'bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400' : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-700'}`}
                                        title="Advanced Search Settings"
                                    >
                                        <Settings className="w-4 h-4" />
                                    </button>

                                    {/* Settings Dropdown */}
                                    {showSettings && (
                                        <>
                                            <div
                                                className="fixed inset-0 z-40"
                                                onClick={() => setShowSettings(false)}
                                            />
                                            <div className="absolute right-0 top-full mt-2 w-48 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 p-2 z-50 flex flex-col gap-1">
                                                <div className="text-xs font-semibold text-gray-400 px-2 py-1 mb-1 border-b border-gray-100 dark:border-gray-700">
                                                    Search Options
                                                </div>
                                                <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded cursor-pointer">
                                                    <input
                                                        type="checkbox"
                                                        checked={caseSensitive}
                                                        onChange={(e) => setCaseSensitive(e.target.checked)}
                                                        className="rounded border-gray-300 text-orange-600 focus:ring-orange-500 w-4 h-4"
                                                    />
                                                    <span className="text-sm text-gray-700 dark:text-gray-200">Case Sensitive</span>
                                                </label>
                                                <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded cursor-pointer">
                                                    <input
                                                        type="checkbox"
                                                        checked={isFuzzy}
                                                        onChange={(e) => {
                                                            setIsFuzzy(e.target.checked);
                                                            if (e.target.checked) setUseRegex(false);
                                                        }}
                                                        className="rounded border-gray-300 text-orange-600 focus:ring-orange-500 w-4 h-4"
                                                    />
                                                    <span className="text-sm text-gray-700 dark:text-gray-200">Fuzzy Match</span>
                                                </label>
                                                <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded cursor-pointer">
                                                    <input
                                                        type="checkbox"
                                                        checked={useRegex}
                                                        onChange={(e) => {
                                                            setUseRegex(e.target.checked);
                                                            if (e.target.checked) setIsFuzzy(false);
                                                        }}
                                                        className="rounded border-gray-300 text-orange-600 focus:ring-orange-500 w-4 h-4"
                                                    />
                                                    <span className="text-sm text-gray-700 dark:text-gray-200">Regex</span>
                                                </label>
                                            </div>
                                        </>
                                    )}
                                </div>

                                <div className="w-px h-4 bg-gray-300 dark:bg-gray-700 mx-1" />

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
                            </div>
                        )}
                    </div>
                )}
            </div>



            <div className="space-y-4 px-4 py-3 overflow-y-auto flex-1 min-h-0">
                {displaySegments.map((segment, index) => {
                    const isActive = currentTime >= segment.start && currentTime < segment.end;
                    const speakerName = speakerMap[segment.speaker] || segment.speaker;
                    const isEditingSpeaker = editingSpeaker === segment.speaker;
                    const isEditingSegmentSpeaker = editingSegmentSpeakerIndex === index;
                    const isEditingText = editingTextIndex === index;

                    // Determine bubble color
                    const bubbleColor = isActive
                        ? 'border-2 border-green-500 dark:border-green-400 bg-green-100 dark:bg-green-900/20'
                        : getSpeakerColor(segment.speaker);

                    return (
                        <div
                            key={index}
                            ref={isActive ? activeSegmentRef : null}
                            className={`flex gap-3 px-2 group ${isActive ? 'opacity-100' : 'opacity-90'} transition-opacity`}
                        >
                            {/* Timestamp & Play Control */}
                            <div className="flex flex-col items-end min-w-[60px] pt-1">
                                <span className="text-sm text-gray-400 font-mono mb-1">
                                    {formatTime(segment.start)}
                                </span>
                                <button
                                    onClick={() => {
                                        if (isActive) {
                                            if (isPlaying) onPause();
                                            else onResume();
                                        } else {
                                            onPlaySegment(segment.start, segment.end);
                                        }
                                    }}
                                    className={`p-1.5 rounded-full transition-colors shadow-sm ${isActive
                                        ? 'bg-green-500 text-white hover:bg-green-600'
                                        : 'bg-gray-100 text-gray-500 hover:bg-orange-600 hover:text-white dark:bg-gray-800 dark:text-gray-400'
                                        }`}
                                    title={isActive && isPlaying ? "Pause segment" : "Play segment"}
                                >
                                    {isActive && isPlaying ? (
                                        <Pause className="w-3 h-3 fill-current" />
                                    ) : (
                                        <Play className="w-3 h-3 fill-current" />
                                    )}
                                </button>
                            </div>


                            {/* Content */}
                            <div className="flex-1 min-w-0">
                                {/* Speaker Label */}
                                <div className="flex items-baseline space-x-2 mb-1">
                                    {isEditingSpeaker ? (
                                        <input
                                            autoFocus
                                            type="text"
                                            value={editValue}
                                            onChange={(e) => setEditValue(e.target.value)}
                                            onBlur={handleSpeakerRenameSubmit}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter') handleSpeakerRenameSubmit();
                                                if (e.key === 'Escape') setEditingSpeaker(null);
                                            }}
                                            onClick={(e) => e.stopPropagation()}
                                            className="text-sm font-bold text-blue-600 dark:text-blue-400 bg-white dark:bg-gray-700 border border-blue-300 rounded px-1 py-0.5 focus:outline-none focus:ring-2 focus:ring-blue-500"
                                        />
                                    ) : isEditingSegmentSpeaker ? (
                                        <input
                                            autoFocus
                                            type="text"
                                            value={editValue}
                                            onChange={(e) => setEditValue(e.target.value)}
                                            onBlur={() => handleSegmentSpeakerSubmit(index)}
                                            onKeyDown={(e) => handleKeyDown(e, 'segmentSpeaker', index)}
                                            onClick={(e) => e.stopPropagation()}
                                            className="text-sm font-bold text-green-600 dark:text-green-400 bg-white dark:bg-gray-700 border border-green-300 rounded px-1 py-0.5 focus:outline-none focus:ring-2 focus:ring-green-500"
                                        />
                                    ) : (
                                        <div className="relative">
                                            <button
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    if (activePopover?.index === index) {
                                                        setActivePopover(null);
                                                    } else {
                                                        setActivePopover({ index, target: e.currentTarget });
                                                    }
                                                }}
                                                onDoubleClick={(e) => {
                                                    e.stopPropagation();
                                                    setEditingSpeaker(segment.speaker);
                                                    setEditValue(speakerName);
                                                    setActivePopover(null);
                                                }}
                                                className="text-base font-bold text-gray-700 dark:text-gray-300 hover:text-orange-700 dark:hover:text-orange-400 transition-colors text-left"
                                                title="Click to change speaker, Double-click to rename"
                                            >
                                                {speakerName}
                                            </button>
                                            {activePopover?.index === index && (
                                                <SpeakerAssignmentPopover
                                                    currentSpeakerName={speakerName}
                                                    availableSpeakers={speakers}
                                                    globalSpeakers={globalSpeakers}
                                                    speakerColors={speakerColors}
                                                    targetElement={activePopover.target}
                                                    onSelect={(name) => {
                                                        onUpdateSegmentSpeaker(index, name);
                                                        setActivePopover(null);
                                                    }}
                                                    onClose={() => setActivePopover(null)}
                                                />
                                            )}
                                        </div>
                                    )}
                                </div>

                                {/* Transcript Text */}
                                <div
                                    id={`segment-${index}`}
                                    className={`p-3 rounded-2xl rounded-tl-none w-full transition-colors border ${bubbleColor} ${isEditingText ? 'ring-2 ring-blue-500' : ''
                                        }`}
                                >
                                    {isEditingText ? (
                                        <textarea
                                            autoFocus
                                            value={editValue}
                                            onChange={(e) => setEditValue(e.target.value)}
                                            onBlur={() => handleTextSubmit(index)}
                                            onKeyDown={(e) => handleKeyDown(e, 'text', index)}
                                            className="w-full bg-transparent resize-none outline-none text-gray-900 dark:text-white leading-relaxed"
                                            rows={Math.max(2, Math.ceil(editValue.length / 80))}
                                        />
                                    ) : (
                                        <p
                                            className="leading-relaxed whitespace-pre-wrap text-gray-800 dark:text-gray-200 cursor-text hover:text-gray-900 dark:hover:text-white"
                                            onClick={(e) => handleTextClick(index, segment.text, e)}
                                            title="Click to edit text"
                                        >
                                            {renderHighlightedText(segment.text, index)}
                                        </p>
                                    )}
                                </div>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
