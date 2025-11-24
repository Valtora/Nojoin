'use client';

import { TranscriptSegment } from '@/types';
import { useRef, useEffect, useState } from 'react';

interface TranscriptViewProps {
  segments: TranscriptSegment[];
  currentTime: number;
  onSeek: (time: number) => void;
  speakerMap: Record<string, string>;
  onRenameSpeaker: (label: string, newName: string) => void | Promise<void>;
  onUpdateSegmentSpeaker: (index: number, newSpeakerName: string) => void | Promise<void>;
}

const formatTime = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
};

export default function TranscriptView({ 
  segments, 
  currentTime, 
  onSeek,
  speakerMap,
  onRenameSpeaker,
  onUpdateSegmentSpeaker
}: TranscriptViewProps) {
  const activeSegmentRef = useRef<HTMLDivElement>(null);
  const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null);
  const [editingSegmentIndex, setEditingSegmentIndex] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (activeSegmentRef.current) {
      activeSegmentRef.current.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
    }
  }, [currentTime]);

  const handleSpeakerClick = (label: string, currentName: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingSpeaker(label);
    setEditValue(currentName);
    setEditingSegmentIndex(null);
  };

  const handleSpeakerDoubleClick = (index: number, currentName: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingSegmentIndex(index);
    setEditValue(currentName);
    setEditingSpeaker(null);
  };

  const handleSpeakerSubmit = async (label: string) => {
    if (editValue.trim() && !isSubmitting) {
      setIsSubmitting(true);
      try {
        await onRenameSpeaker(label, editValue.trim());
      } finally {
        setIsSubmitting(false);
        setEditingSpeaker(null);
      }
    } else if (!editValue.trim()) {
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
        setEditingSegmentIndex(null);
      }
    } else if (!editValue.trim()) {
        setEditingSegmentIndex(null);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent, label: string) => {
    if (e.key === 'Enter') {
      handleSpeakerSubmit(label);
    } else if (e.key === 'Escape') {
      setEditingSpeaker(null);
    }
  };

  const handleSegmentKeyDown = (e: React.KeyboardEvent, index: number) => {
    if (e.key === 'Enter') {
      handleSegmentSpeakerSubmit(index);
    } else if (e.key === 'Escape') {
      setEditingSegmentIndex(null);
    }
  };

  // Filter out UNKNOWN speakers if there are other identified speakers
  // This prevents "phantom" UNKNOWN segments (artifacts) from cluttering the view
  // while preserving the transcript if diarization failed completely (all UNKNOWN).
  const hasKnownSpeakers = segments.some(s => s.speaker !== 'UNKNOWN');
  const displaySegments = hasKnownSpeakers 
    ? segments.filter(s => s.speaker !== 'UNKNOWN')
    : segments;

  return (
    <div className="space-y-6">
      {displaySegments.map((segment, index) => {
        const isActive = currentTime >= segment.start && currentTime < segment.end;
        const speakerName = speakerMap[segment.speaker] || segment.speaker;
        const isEditing = editingSpeaker === segment.speaker;
        const isEditingSegment = editingSegmentIndex === index;

        return (
          <div
            key={index}
            ref={isActive ? activeSegmentRef : null}
            className={`flex flex-col ${isActive ? 'opacity-100' : 'opacity-80 hover:opacity-100'} transition-opacity`}
          >
            <div className="flex items-baseline space-x-2 mb-1 px-2">
              {isEditing ? (
                <input
                  autoFocus
                  type="text"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onBlur={() => handleSpeakerSubmit(segment.speaker)}
                  onKeyDown={(e) => handleKeyDown(e, segment.speaker)}
                  onClick={(e) => e.stopPropagation()}
                  className="text-sm font-bold text-blue-600 dark:text-blue-400 bg-white dark:bg-gray-700 border border-blue-300 rounded px-1 py-0.5 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              ) : isEditingSegment ? (
                <input
                  autoFocus
                  type="text"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onBlur={() => handleSegmentSpeakerSubmit(index)}
                  onKeyDown={(e) => handleSegmentKeyDown(e, index)}
                  onClick={(e) => e.stopPropagation()}
                  className="text-sm font-bold text-green-600 dark:text-green-400 bg-white dark:bg-gray-700 border border-green-300 rounded px-1 py-0.5 focus:outline-none focus:ring-2 focus:ring-green-500"
                />
              ) : (
                <span 
                  className="text-sm font-bold text-blue-600 dark:text-blue-400 cursor-pointer hover:underline"
                  onClick={(e) => handleSpeakerClick(segment.speaker, speakerName, e)}
                  onDoubleClick={(e) => handleSpeakerDoubleClick(index, speakerName, e)}
                  title="Click to rename speaker globally, Double-click to reassign this segment"
                >
                  {speakerName}
                </span>
              )}
              <span className="text-xs text-gray-400 font-mono">
                {formatTime(segment.start)} - {formatTime(segment.end)}
              </span>
            </div>
            
            <div
              className={`p-3 rounded-2xl rounded-tl-none w-full cursor-pointer transition-colors ${
                isActive
                  ? 'bg-blue-100 dark:bg-blue-900 text-gray-900 dark:text-white shadow-sm'
                  : 'bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200 hover:bg-gray-200 dark:hover:bg-gray-600'
              }`}
              onClick={() => onSeek(segment.start)}
            >
              <p className="leading-relaxed whitespace-pre-wrap">
                {segment.text}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
