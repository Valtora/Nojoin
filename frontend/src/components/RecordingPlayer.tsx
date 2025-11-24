'use client';

import { Recording, TranscriptSegment } from '@/types';
import { getRecordingStreamUrl, updateSpeaker, updateTranscriptSegmentSpeaker } from '@/lib/api';
import { useState, useRef, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import TranscriptView from './TranscriptView';

interface RecordingPlayerProps {
  recording: Recording;
  audioRef: React.RefObject<HTMLAudioElement | null>;
}

export default function RecordingPlayer({ recording, audioRef }: RecordingPlayerProps) {
  const [currentTime, setCurrentTime] = useState(0);
  const [stopTime, setStopTime] = useState<number | null>(null);
  // const audioRef = useRef<HTMLAudioElement>(null); // Removed local ref
  const router = useRouter();

  const handleTimeUpdate = () => {
    if (audioRef.current) {
      const current = audioRef.current.currentTime;
      setCurrentTime(current);
      
      if (stopTime !== null && current >= stopTime) {
        audioRef.current.pause();
        setStopTime(null);
      }
    }
  };

  const handlePlaySegment = (start: number, end: number) => {
    if (audioRef.current) {
      audioRef.current.currentTime = start;
      setStopTime(end);
      audioRef.current.play();
    }
  };

  const handleRenameSpeaker = async (label: string, newName: string) => {
    try {
      await updateSpeaker(recording.id, label, newName);
      router.refresh();
    } catch (error) {
      console.error("Failed to rename speaker:", error);
      alert("Failed to rename speaker. Please try again.");
    }
  };

  const handleUpdateSegmentSpeaker = async (index: number, newSpeakerName: string) => {
    try {
      await updateTranscriptSegmentSpeaker(recording.id, index, newSpeakerName);
      router.refresh();
    } catch (error) {
      console.error("Failed to update segment speaker:", error);
      alert("Failed to update segment speaker. Please try again.");
    }
  };

  const segments: TranscriptSegment[] = recording.transcript?.segments || [];

  const speakerMap = useMemo(() => {
    const map: Record<string, string> = {};
    if (recording.speakers) {
      recording.speakers.forEach((s) => {
        if (s.global_speaker) {
          map[s.diarization_label] = s.global_speaker.name;
        }
      });
    }
    return map;
  }, [recording.speakers]);

  return (
    <div className="space-y-6">
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm p-4 border border-gray-200 dark:border-gray-700 sticky top-0 z-10">
        <audio
          ref={audioRef}
          controls
          className="w-full"
          src={getRecordingStreamUrl(recording.id)}
          onTimeUpdate={handleTimeUpdate}
        >
          Your browser does not support the audio element.
        </audio>
      </div>

      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm p-6 border border-gray-200 dark:border-gray-700">
          <h2 className="text-xl font-semibold mb-4 text-gray-900 dark:text-white">
          Transcript
        </h2>
        {segments.length > 0 ? (
          <TranscriptView
            segments={segments}
            currentTime={currentTime}
            onPlaySegment={handlePlaySegment}
            speakerMap={speakerMap}
            onRenameSpeaker={handleRenameSpeaker}
            onUpdateSegmentSpeaker={handleUpdateSegmentSpeaker}
          />
        ) : (
          <p className="text-gray-500 dark:text-gray-400 italic">
            No transcript available yet.
          </p>
        )}
      </div>
    </div>
  );
}
