'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { CalendarPlus, Calendar, X } from 'lucide-react';
import { CalendarEventLink, RecordingId } from '@/types';
import {
  getRecordingCalendarEventCandidates,
  linkRecordingCalendarEvent,
} from '@/lib/api';

interface LinkedEventPanelProps {
  recordingId: RecordingId;
  linkedEvent?: CalendarEventLink | null;
  onLinkChanged?: () => void;
}

function formatEventTime(event: CalendarEventLink): string {
  if (!event.starts_at) return '';
  const start = new Date(event.starts_at);
  const datePart = start.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  });
  const timePart = start.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  });
  return `${datePart}, ${timePart}`;
}

export default function LinkedEventPanel({
  recordingId,
  linkedEvent,
  onLinkChanged,
}: LinkedEventPanelProps) {
  const [isPickerOpen, setIsPickerOpen] = useState(false);
  const [candidates, setCandidates] = useState<CalendarEventLink[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const openPicker = useCallback(async () => {
    setIsPickerOpen(true);
    setIsLoading(true);
    try {
      const events = await getRecordingCalendarEventCandidates(recordingId);
      setCandidates(events);
    } catch (error) {
      console.error('Failed to load calendar event candidates:', error);
      setCandidates([]);
    } finally {
      setIsLoading(false);
    }
  }, [recordingId]);

  useEffect(() => {
    if (!isPickerOpen) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsPickerOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isPickerOpen]);

  const handleSelect = async (calendarEventId: number | null) => {
    setIsSubmitting(true);
    try {
      await linkRecordingCalendarEvent(recordingId, calendarEventId);
      setIsPickerOpen(false);
      if (onLinkChanged) onLinkChanged();
    } catch (error) {
      console.error('Failed to update calendar event link:', error);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="relative" ref={containerRef}>
      {linkedEvent ? (
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-sm font-medium border bg-gray-100 dark:bg-gray-800 border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-200">
          <Calendar className="w-3.5 h-3.5 shrink-0" />
          <span className="truncate max-w-[16rem]">{linkedEvent.title}</span>
          {formatEventTime(linkedEvent) && (
            <span className="opacity-60">{formatEventTime(linkedEvent)}</span>
          )}
          <button
            onClick={openPicker}
            disabled={isSubmitting}
            className="ml-1 text-xs underline opacity-70 hover:opacity-100 focus:outline-none disabled:opacity-40"
          >
            Change
          </button>
          <button
            onClick={() => handleSelect(null)}
            disabled={isSubmitting}
            title="Unlink calendar event"
            className="inline-flex items-center justify-center w-4 h-4 rounded-full hover:bg-black/10 dark:hover:bg-white/10 focus:outline-none disabled:opacity-40"
          >
            <X className="w-3 h-3" />
          </button>
        </div>
      ) : (
        <button
          onClick={openPicker}
          disabled={isSubmitting}
          className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 border border-dashed border-gray-300 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500 transition-colors disabled:opacity-40"
        >
          <CalendarPlus className="w-4 h-4 mr-1.5" />
          Link calendar event
        </button>
      )}

      {isPickerOpen && (
        <div className="absolute z-20 mt-2 w-80 max-h-72 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg">
          {isLoading ? (
            <div className="px-3 py-3 text-sm text-gray-500 dark:text-gray-400">
              Loading calendar events...
            </div>
          ) : candidates.length === 0 ? (
            <div className="px-3 py-3 text-sm text-gray-500 dark:text-gray-400">
              No nearby calendar events found.
            </div>
          ) : (
            <ul className="py-1">
              {candidates.map((candidate) => (
                <li key={candidate.id}>
                  <button
                    onClick={() => handleSelect(candidate.id)}
                    disabled={isSubmitting}
                    className="w-full text-left px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-800 focus:outline-none disabled:opacity-40"
                  >
                    <span className="block text-sm font-medium text-gray-800 dark:text-gray-100 truncate">
                      {candidate.title}
                    </span>
                    {formatEventTime(candidate) && (
                      <span className="block text-xs text-gray-500 dark:text-gray-400">
                        {formatEventTime(candidate)}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
