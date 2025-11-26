'use client';

import { GlobalSpeaker, RecordingSpeaker } from '@/types';
import { Search, User, Plus } from 'lucide-react';
import { useState, useEffect, useRef } from 'react';
import { getColorByKey } from '@/lib/constants';

interface SpeakerAssignmentPopoverProps {
  currentSpeakerName: string;
  availableSpeakers: RecordingSpeaker[];
  globalSpeakers: GlobalSpeaker[];
  onSelect: (name: string) => void;
  onClose: () => void;
  speakerColors: Record<string, string>;
}

export default function SpeakerAssignmentPopover({
  currentSpeakerName,
  availableSpeakers,
  globalSpeakers,
  onSelect,
  onClose,
  speakerColors
}: SpeakerAssignmentPopoverProps) {
  const [search, setSearch] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    
    // Click outside to close
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        onClose();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onClose]);

  // Helper to get speaker name
  const getSpeakerName = (s: RecordingSpeaker): string => {
    return s.local_name || s.global_speaker?.name || s.name || s.diarization_label;
  };

  // Filter speakers
  const filteredAvailable = availableSpeakers.filter(s => {
    const name = getSpeakerName(s);
    return name.toLowerCase().includes(search.toLowerCase());
  });

  const filteredGlobal = globalSpeakers.filter(s => 
    s.name.toLowerCase().includes(search.toLowerCase()) &&
    !availableSpeakers.some(as => as.global_speaker_id === s.id) // Exclude if already in recording
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        if (search.trim()) {
            onSelect(search.trim());
        }
    } else if (e.key === 'Escape') {
        onClose();
    }
  };

  return (
    <div 
        ref={containerRef}
        className="absolute z-[60] mt-1 w-64 bg-white/95 dark:bg-gray-800/95 backdrop-blur-sm rounded-lg shadow-2xl border-2 border-gray-300 dark:border-gray-600 overflow-hidden flex flex-col animate-in fade-in zoom-in-95 duration-100 left-0 top-full"
        style={{ minWidth: '200px' }}
    >
      <div className="p-2 border-b border-gray-100 dark:border-gray-700">
        <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
                ref={inputRef}
                value={search}
                onChange={e => setSearch(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Search or add..."
                className="w-full pl-7 pr-2 py-1.5 text-sm bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-md focus:outline-none focus:ring-2 focus:ring-orange-500 text-gray-900 dark:text-gray-100"
            />
        </div>
      </div>
      
      <div className="max-h-60 overflow-y-auto py-1">
        {/* Current Recording Speakers */}
        {filteredAvailable.length > 0 && (
            <div className="px-2 py-1">
                <div className="text-xs font-semibold text-gray-400 mb-1 uppercase tracking-wider">In this recording</div>
                {filteredAvailable.map(s => {
                    const name = getSpeakerName(s);
                    const colorKey = speakerColors[name] || 'gray';
                    const colorOption = getColorByKey(colorKey);
                    
                    return (
                        <button
                            key={s.diarization_label}
                            onClick={() => onSelect(name)}
                            className="w-full text-left px-2 py-1.5 text-sm rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 text-gray-700 dark:text-gray-200"
                        >
                            <div className={`w-2 h-2 rounded-full ${colorOption.dot}`} />
                            <span className="truncate">{name}</span>
                        </button>
                    );
                })}
            </div>
        )}

        {/* Global Speakers */}
        {filteredGlobal.length > 0 && (
            <div className="px-2 py-1 border-t border-gray-100 dark:border-gray-700 mt-1 pt-2">
                <div className="text-xs font-semibold text-gray-400 mb-1 uppercase tracking-wider">Global Library</div>
                {filteredGlobal.map(s => (
                    <button
                        key={s.id}
                        onClick={() => onSelect(s.name)}
                        className="w-full text-left px-2 py-1.5 text-sm rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 text-gray-700 dark:text-gray-200"
                    >
                        <User className="w-3 h-3 text-gray-400" />
                        <span className="truncate">{s.name}</span>
                    </button>
                ))}
            </div>
        )}

        {/* Create New */}
        {search.trim() && !filteredAvailable.some(s => getSpeakerName(s) === search) && !filteredGlobal.some(s => s.name === search) && (
            <div className="px-2 py-1 border-t border-gray-100 dark:border-gray-700 mt-1 pt-2">
                <button
                    onClick={() => onSelect(search.trim())}
                    className="w-full text-left px-2 py-1.5 text-sm rounded-md hover:bg-orange-50 dark:hover:bg-orange-900/20 text-orange-600 dark:text-orange-400 flex items-center gap-2"
                >
                    <Plus className="w-3 h-3" />
                    <span className="truncate">Create &quot;{search}&quot;</span>
                </button>
            </div>
        )}
        
        {filteredAvailable.length === 0 && filteredGlobal.length === 0 && !search.trim() && (
             <div className="px-4 py-2 text-xs text-gray-400 text-center">
                Type to search or add
             </div>
        )}
      </div>
    </div>
  );
}
