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
  compact?: boolean;
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
  compact = false,
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

  const basePillClass = compact
    ? "inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs font-medium"
    : "inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm font-medium";
  const dropdownWidthClass = compact ? "w-[min(18rem,calc(100vw-2rem))]" : "w-80";

  return (
    <div className="relative" ref={containerRef}>
      {linkedEvent ? (
        <div className={`${basePillClass} border-gray-300 bg-gray-100 text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200`}>
          <Calendar className="w-3.5 h-3.5 shrink-0" />
          <span className={`truncate ${compact ? "max-w-[10rem]" : "max-w-[16rem]"}`}>{linkedEvent.title}</span>
          {formatEventTime(linkedEvent) && (
            <span className={`opacity-60 ${compact ? "hidden" : ""}`}>{formatEventTime(linkedEvent)}</span>
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
          className={`${basePillClass} border-dashed border-gray-300 text-gray-500 transition-colors hover:border-gray-400 hover:text-gray-700 disabled:opacity-40 dark:border-gray-600 dark:text-gray-400 dark:hover:border-gray-500 dark:hover:text-gray-200`}
        >
          <CalendarPlus className={`${compact ? "mr-1 h-3.5 w-3.5" : "mr-1.5 h-4 w-4"}`} />
          Link calendar event
        </button>
      )}

      {isPickerOpen && (
        <div className={`absolute z-20 mt-2 max-h-72 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg dark:border-gray-700 dark:bg-gray-900 ${dropdownWidthClass}`}>
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
