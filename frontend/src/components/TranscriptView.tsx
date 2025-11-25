'use client';

import { TranscriptSegment } from '@/types';
import { useRef, useEffect, useState } from 'react';
import { Play, Search, X, ArrowRightLeft, Users, Palette } from 'lucide-react';
import SpeakerManagementModal from './SpeakerManagementModal';

interface TranscriptViewProps {
  segments: TranscriptSegment[];
  currentTime: number;
  onPlaySegment: (start: number, end: number) => void;
  speakerMap: Record<string, string>;
  onRenameSpeaker: (label: string, newName: string) => void | Promise<void>;
  onUpdateSegmentSpeaker: (index: number, newSpeakerName: string) => void | Promise<void>;
  onUpdateSegmentText: (index: number, text: string) => void | Promise<void>;
  onFindAndReplace: (find: string, replace: string) => void | Promise<void>;
}

const formatTime = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
};

const SPEAKER_COLORS = [
  'bg-purple-50 dark:bg-purple-900/20 border-purple-100 dark:border-purple-800',
  'bg-yellow-50 dark:bg-yellow-900/20 border-yellow-100 dark:border-yellow-800',
  'bg-orange-50 dark:bg-orange-900/20 border-orange-100 dark:border-orange-800',
  'bg-blue-50 dark:bg-blue-900/20 border-blue-100 dark:border-blue-800',
  'bg-green-50 dark:bg-green-900/20 border-green-100 dark:border-green-800',
  'bg-red-50 dark:bg-red-900/20 border-red-100 dark:border-red-800',
  'bg-gray-50 dark:bg-gray-900/20 border-gray-100 dark:border-gray-800',
];

export default function TranscriptView({ 
  segments, 
  currentTime, 
  onPlaySegment,
  speakerMap,
  onRenameSpeaker,
  onUpdateSegmentSpeaker,
  onUpdateSegmentText,
  onFindAndReplace
}: TranscriptViewProps) {
  const activeSegmentRef = useRef<HTMLDivElement>(null);
  
  // Editing State
  const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null);
  const [editingSegmentSpeakerIndex, setEditingSegmentSpeakerIndex] = useState<number | null>(null);
  const [editingTextIndex, setEditingTextIndex] = useState<number | null>(null);
  
  const [editValue, setEditValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Find & Replace State
  const [showSearch, setShowSearch] = useState(false);
  const [showReplace, setShowReplace] = useState(false);
  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");

  // Speaker Management State
  const [isSpeakerModalOpen, setIsSpeakerModalOpen] = useState(false);
  const [speakerColors, setSpeakerColors] = useState<Record<string, string>>({});

  // Initialize speaker colors
  useEffect(() => {
    const newColors = { ...speakerColors };
    let colorIndex = 0;
    
    // Get all unique speakers
    const speakers = new Set<string>();
    segments.forEach(s => {
        const name = speakerMap[s.speaker] || s.speaker;
        speakers.add(name);
    });

    speakers.forEach(speaker => {
        if (!newColors[speaker]) {
            // Deterministic assignment based on name hash if not set
            let hash = 0;
            for (let i = 0; i < speaker.length; i++) {
                hash = speaker.charCodeAt(i) + ((hash << 5) - hash);
            }
            const index = Math.abs(hash) % SPEAKER_COLORS.length;
            newColors[speaker] = SPEAKER_COLORS[index];
        }
    });
    setSpeakerColors(newColors);
  }, [segments, speakerMap]); // Re-run when segments/speakers change

  const getSpeakerColor = (speakerName: string) => {
      return speakerColors[speakerName] || SPEAKER_COLORS[0];
  };

  const handleColorChange = (speakerName: string, colorClass: string) => {
      setSpeakerColors(prev => ({
          ...prev,
          [speakerName]: colorClass
      }));
  };

  useEffect(() => {
    if (activeSegmentRef.current) {
      activeSegmentRef.current.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
    }
  }, [currentTime]);

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
          await onFindAndReplace(findText, replaceText);
          setFindText("");
          setReplaceText("");
          setShowReplace(false);
          setShowSearch(false);
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
    <div className="flex flex-col h-full relative">
      {/* Sticky Toolbar */}
      <div className="sticky top-0 z-20 bg-white/95 dark:bg-gray-900/95 backdrop-blur-sm border-b border-gray-200 dark:border-gray-700 px-6 py-4 flex items-center justify-between gap-2 shadow-sm">
        <div className="flex items-center gap-4 flex-1">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Transcript</h2>
            
            {/* Search Bar */}
            {(showSearch || showReplace) && (
                <div className="flex items-center gap-2 flex-1 max-w-md animate-in fade-in slide-in-from-top-2 duration-200">
                    <div className="relative flex-1">
                        <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                        <input 
                            placeholder="Find..." 
                            value={findText} 
                            onChange={e => setFindText(e.target.value)}
                            className="w-full pl-8 pr-2 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 focus:ring-2 focus:ring-orange-500 outline-none"
                            autoFocus
                        />
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
                        <button 
                            onClick={handleFindReplaceSubmit}
                            disabled={!findText || isSubmitting}
                            className="px-3 py-1.5 bg-orange-600 text-white text-sm rounded-md hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap shadow-sm"
                        >
                            Replace All
                        </button>
                    )}
                </div>
            )}
        </div>

        <div className="flex items-center gap-1">
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
                    const newState = !showReplace;
                    setShowReplace(newState);
                    if (newState) setShowSearch(true);
                }}
                className={`p-2 rounded-md transition-colors ${showReplace ? 'bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400' : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-orange-500'}`}
                title="Find & Replace"
            >
                <ArrowRightLeft className="w-4 h-4" />
            </button>
            <div className="w-px h-4 bg-gray-300 dark:bg-gray-700 mx-1" />
            <button 
                onClick={() => setIsSpeakerModalOpen(true)}
                className="p-2 rounded-md text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-orange-500 transition-colors"
                title="Manage Speakers"
            >
                <Users className="w-4 h-4" />
            </button>
        </div>
      </div>

      <SpeakerManagementModal
        isOpen={isSpeakerModalOpen}
        onClose={() => setIsSpeakerModalOpen(false)}
        speakers={Array.from(new Set(segments.map(s => s.speaker)))}
        speakerMap={speakerMap}
        colorMap={speakerColors}
        onRename={onRenameSpeaker}
        onColorChange={handleColorChange}
        availableColors={SPEAKER_COLORS}
      />

      <div className="space-y-6 p-6">
        {displaySegments.map((segment, index) => {
          const isActive = currentTime >= segment.start && currentTime < segment.end;
          const speakerName = speakerMap[segment.speaker] || segment.speaker;
          const isEditingSpeaker = editingSpeaker === segment.speaker;
          const isEditingSegmentSpeaker = editingSegmentSpeakerIndex === index;
          const isEditingText = editingTextIndex === index;
          
          // Determine bubble color
          const bubbleColor = isActive 
            ? 'border-2 border-green-500 dark:border-green-400 bg-green-50/10 dark:bg-green-900/10' 
            : getSpeakerColor(speakerName);

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
                      onClick={() => onPlaySegment(segment.start, segment.end)}
                      className={`p-1.5 rounded-full transition-colors shadow-sm ${
                          isActive 
                          ? 'bg-green-500 text-white hover:bg-green-600' 
                          : 'bg-gray-100 text-gray-500 hover:bg-orange-500 hover:text-white dark:bg-gray-800 dark:text-gray-400'
                      }`}
                      title="Play segment"
                  >
                      <Play className="w-3 h-3 fill-current" />
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
                        onClick={(e) => e.stopPropagation()}
                        className="text-sm font-bold text-blue-600 dark:text-blue-400 bg-white dark:bg-gray-700 border border-blue-300 rounded px-1 py-0.5 focus:outline-none focus:ring-2 focus:ring-blue-500"
                        disabled
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
                        <span 
                        className="text-base font-bold text-gray-700 dark:text-gray-300 cursor-default"
                        title="Speaker label"
                        >
                        {speakerName}
                        </span>
                    )}
                </div>
                
                {/* Transcript Text */}
                <div
                    className={`p-3 rounded-2xl rounded-tl-none w-full transition-colors border ${bubbleColor} ${
                        isEditingText ? 'ring-2 ring-blue-500' : ''
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
                            {segment.text}
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
