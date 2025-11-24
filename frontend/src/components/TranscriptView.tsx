'use client';

import { TranscriptSegment } from '@/types';
import { useRef, useEffect, useState } from 'react';

interface TranscriptViewProps {
  segments: TranscriptSegment[];
  currentTime: number;
  onSeek: (time: number) => void;
  speakerMap: Record<string, string>;
  onRenameSpeaker: (label: string, newName: string) => void;
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
  onRenameSpeaker
}: TranscriptViewProps) {
  const activeSegmentRef = useRef<HTMLDivElement>(null);
  const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

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
  };

  const handleSpeakerSubmit = (label: string) => {
    if (editValue.trim()) {
      onRenameSpeaker(label, editValue.trim());
    }
    setEditingSpeaker(null);
  };

  const handleKeyDown = (e: React.KeyboardEvent, label: string) => {
    if (e.key === 'Enter') {
      handleSpeakerSubmit(label);
    } else if (e.key === 'Escape') {
      setEditingSpeaker(null);
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
              ) : (
                <span 
                  className="text-sm font-bold text-blue-600 dark:text-blue-400 cursor-pointer hover:underline"
                  onClick={(e) => handleSpeakerClick(segment.speaker, speakerName, e)}
                  title="Click to rename speaker"
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
