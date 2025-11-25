'use client';

import { TranscriptSegment } from '@/types';
import { useRef, useEffect, useState } from 'react';
import { Play, Search, X } from 'lucide-react';

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
  'bg-blue-50 dark:bg-blue-900/20 border-blue-100 dark:border-blue-800',
  'bg-green-50 dark:bg-green-900/20 border-green-100 dark:border-green-800',
  'bg-purple-50 dark:bg-purple-900/20 border-purple-100 dark:border-purple-800',
  'bg-yellow-50 dark:bg-yellow-900/20 border-yellow-100 dark:border-yellow-800',
  'bg-pink-50 dark:bg-pink-900/20 border-pink-100 dark:border-pink-800',
  'bg-indigo-50 dark:bg-indigo-900/20 border-indigo-100 dark:border-indigo-800',
  'bg-red-50 dark:bg-red-900/20 border-red-100 dark:border-red-800',
  'bg-teal-50 dark:bg-teal-900/20 border-teal-100 dark:border-teal-800',
];

const getSpeakerColor = (speaker: string) => {
  let hash = 0;
  for (let i = 0; i < speaker.length; i++) {
    hash = speaker.charCodeAt(i) + ((hash << 5) - hash);
  }
  const index = Math.abs(hash) % SPEAKER_COLORS.length;
  return SPEAKER_COLORS[index];
};

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
  const [showFindReplace, setShowFindReplace] = useState(false);
  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");

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
          setShowFindReplace(false);
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
      {/* Floating Find & Replace Button */}
      <div className="absolute top-0 right-0 z-10 p-2">
        <button 
            onClick={() => setShowFindReplace(!showFindReplace)}
            className="flex items-center justify-center w-8 h-8 bg-orange-500 text-white hover:bg-orange-600 rounded-full shadow-md transition-all hover:scale-105"
            title="Find & Replace"
        >
            <Search className="w-4 h-4" />
        </button>
      </div>

      {/* Find & Replace Bar */}
      {showFindReplace && (
          <div className="sticky top-0 z-20 flex items-center gap-2 mb-4 p-3 bg-white/95 dark:bg-gray-900/95 backdrop-blur-sm rounded-lg border border-gray-200 dark:border-gray-700 mx-2 shadow-lg">
              <input 
                  placeholder="Find..." 
                  value={findText} 
                  onChange={e => setFindText(e.target.value)}
                  className="flex-1 text-sm p-1.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 focus:ring-2 focus:ring-blue-500 outline-none"
              />
              <input 
                  placeholder="Replace with..." 
                  value={replaceText} 
                  onChange={e => setReplaceText(e.target.value)}
                  className="flex-1 text-sm p-1.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 focus:ring-2 focus:ring-blue-500 outline-none"
              />
              <button 
                  onClick={handleFindReplaceSubmit}
                  disabled={!findText || isSubmitting}
                  className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
              >
                  Replace All
              </button>
              <button 
                  onClick={() => setShowFindReplace(false)}
                  className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
              >
                  <X className="w-4 h-4" />
              </button>
          </div>
      )}

      <div className="space-y-6 pt-2">
        {displaySegments.map((segment, index) => {
          const isActive = currentTime >= segment.start && currentTime < segment.end;
          const speakerName = speakerMap[segment.speaker] || segment.speaker;
          const isEditingSpeaker = editingSpeaker === segment.speaker;
          const isEditingSegmentSpeaker = editingSegmentSpeakerIndex === index;
          const isEditingText = editingTextIndex === index;
          
          // Determine bubble color
          const bubbleColor = isActive 
            ? 'bg-orange-50 dark:bg-orange-900/20 border-orange-200 dark:border-orange-800' 
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
                      onClick={() => onPlaySegment(segment.start, segment.end)}
                      className={`p-1.5 rounded-full transition-colors shadow-sm ${
                          isActive 
                          ? 'bg-orange-500 text-white hover:bg-orange-600' 
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
