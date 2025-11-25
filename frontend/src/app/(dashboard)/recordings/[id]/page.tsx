'use client';

import { getRecording, addTagToRecording, removeTagFromRecording, updateSpeaker, updateTranscriptSegmentSpeaker, updateTranscriptSegmentText, findAndReplace, renameRecording } from '@/lib/api';
import AudioPlayer from '@/components/AudioPlayer';
import SpeakerPanel from '@/components/SpeakerPanel';
import TranscriptView from '@/components/TranscriptView';
import TagsInput from '@/components/TagsInput';
import Link from 'next/link';
import { ArrowLeft, Loader2, Edit2 } from 'lucide-react';
import { useState, useEffect, useRef, useMemo } from 'react';
import { Recording, RecordingStatus, TranscriptSegment } from '@/types';
import { useRouter } from 'next/navigation';

export const dynamic = 'force-dynamic';

interface PageProps {
  params: Promise<{ id: string }>;
}

export default function RecordingPage({ params }: PageProps) {
  const [recording, setRecording] = useState<Recording | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const audioRef = useRef<HTMLAudioElement>(null);
  
  // Player State
  const [currentTime, setCurrentTime] = useState(0);
  const [stopTime, setStopTime] = useState<number | null>(null);

  // Title Editing State
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleValue, setTitleValue] = useState("");

  useEffect(() => {
    const fetchRecording = async () => {
      try {
        const { id } = await params;
        const data = await getRecording(parseInt(id));
        setRecording(data);
        setTitleValue(data.name);
      } catch (e) {
        console.error("Failed to fetch recording:", e);
        setError("Failed to load recording.");
      } finally {
        setLoading(false);
      }
    };
    fetchRecording();
    
    // Poll for updates if processing
    const interval = setInterval(async () => {
        if (recording && (recording.status === RecordingStatus.PROCESSING || recording.status === RecordingStatus.UPLOADING)) {
             try {
                const { id } = await params;
                const data = await getRecording(parseInt(id));
                if (data.status !== recording.status) {
                    setRecording(data);
                    if (!isEditingTitle) setTitleValue(data.name);
                }
            } catch (e) {
                console.error("Polling failed", e);
            }
        }
    }, 5000);
    
    return () => clearInterval(interval);
  }, [params, recording?.status, isEditingTitle]);

  const handleAddTag = async (tagName: string) => {
    if (!recording) return;
    try {
      await addTagToRecording(recording.id, tagName);
      const updated = await getRecording(recording.id);
      setRecording(updated);
      router.refresh();
    } catch (e) {
      console.error("Failed to add tag:", e);
    }
  };

  const handleRemoveTag = async (tagName: string) => {
    if (!recording) return;
    try {
      await removeTagFromRecording(recording.id, tagName);
      const updated = await getRecording(recording.id);
      setRecording(updated);
      router.refresh();
    } catch (e) {
      console.error("Failed to remove tag:", e);
    }
  };

  // Player Handlers
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

  const handlePlaySegment = (start: number, end?: number) => {
    if (audioRef.current) {
        audioRef.current.currentTime = start;
        if (end) setStopTime(end);
        else setStopTime(null);
        audioRef.current.play();
    }
  };

  // Transcript Handlers
  const handleRenameSpeaker = async (label: string, newName: string) => {
    if (!recording) return;
    try {
      await updateSpeaker(recording.id, label, newName);
      router.refresh();
      const updated = await getRecording(recording.id);
      setRecording(updated);
    } catch (error) {
      console.error("Failed to rename speaker:", error);
      alert("Failed to rename speaker. Please try again.");
    }
  };

  const handleUpdateSegmentSpeaker = async (index: number, newSpeakerName: string) => {
    if (!recording) return;
    try {
      await updateTranscriptSegmentSpeaker(recording.id, index, newSpeakerName);
      router.refresh();
      const updated = await getRecording(recording.id);
      setRecording(updated);
    } catch (error) {
      console.error("Failed to update segment speaker:", error);
      alert("Failed to update segment speaker. Please try again.");
    }
  };

  const handleUpdateSegmentText = async (index: number, text: string) => {
    if (!recording) return;
    try {
      await updateTranscriptSegmentText(recording.id, index, text);
      router.refresh();
      const updated = await getRecording(recording.id);
      setRecording(updated);
    } catch (error) {
      console.error("Failed to update segment text:", error);
      alert("Failed to update segment text.");
    }
  };

  const handleFindAndReplace = async (find: string, replace: string) => {
    if (!recording) return;
    try {
      await findAndReplace(recording.id, find, replace);
      router.refresh();
      const updated = await getRecording(recording.id);
      setRecording(updated);
    } catch (error) {
      console.error("Failed to find and replace:", error);
      alert("Failed to find and replace.");
    }
  };

  const handleTitleSubmit = async () => {
    if (!recording || !titleValue.trim()) {
        setIsEditingTitle(false);
        setTitleValue(recording?.name || "");
        return;
    }
    
    if (titleValue.trim() === recording.name) {
        setIsEditingTitle(false);
        return;
    }

    try {
        await renameRecording(recording.id, titleValue.trim());
        const updated = await getRecording(recording.id);
        setRecording(updated);
        setIsEditingTitle(false);
        router.refresh();
    } catch (e) {
        console.error("Failed to rename recording:", e);
        alert("Failed to rename recording.");
    }
  };

  const speakerMap = useMemo(() => {
    const map: Record<string, string> = {};
    if (recording?.speakers) {
      recording.speakers.forEach((s) => {
        if (s.name) {
          map[s.diarization_label] = s.name;
        } else if (s.global_speaker) {
          map[s.diarization_label] = s.global_speaker.name;
        }
      });
    }
    return map;
  }, [recording?.speakers]);

  if (loading) {
    return <div className="h-full flex items-center justify-center text-gray-500">Loading...</div>;
  }

  if (error || !recording) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-red-600 mb-4">Error</h1>
          <p className="text-gray-600 dark:text-gray-400 mb-6">{error || "Recording not found"}</p>
          <Link href="/" className="text-orange-600 hover:underline">
            Back to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <header className="p-6 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 sticky top-0 z-10 flex-shrink-0 space-y-4">
        <div className="flex items-center justify-between">
            <div className="min-w-0 flex-1">
                {isEditingTitle ? (
                    <input
                        autoFocus
                        type="text"
                        value={titleValue}
                        onChange={(e) => setTitleValue(e.target.value)}
                        onBlur={handleTitleSubmit}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter') handleTitleSubmit();
                            if (e.key === 'Escape') {
                                setIsEditingTitle(false);
                                setTitleValue(recording.name);
                            }
                        }}
                        className="text-2xl font-bold text-gray-900 dark:text-white mb-2 w-full bg-transparent border-b-2 border-orange-500 focus:outline-none"
                    />
                ) : (
                    <h1 
                        className="text-2xl font-bold text-gray-900 dark:text-white truncate mb-2 cursor-pointer hover:text-orange-600 dark:hover:text-orange-400 flex items-center gap-2 group"
                        onClick={() => setIsEditingTitle(true)}
                        title="Click to rename"
                    >
                        {recording.name}
                        <Edit2 className="w-4 h-4 opacity-0 group-hover:opacity-50 transition-opacity" />
                    </h1>
                )}
                
                <div className="flex items-center gap-4">
                    <TagsInput 
                        tags={recording.tags || []} 
                        onAddTag={handleAddTag} 
                        onRemoveTag={handleRemoveTag} 
                    />
                    <div className="flex items-center text-sm text-gray-500 dark:text-gray-400 space-x-4">
                        <span>{new Date(recording.created_at).toLocaleString()}</span>
                        <span>•</span>
                        <span>{recording.duration_seconds ? `${Math.floor(recording.duration_seconds / 60)} ${Math.floor(recording.duration_seconds / 60) === 1 ? 'min' : 'mins'}` : 'Unknown'}</span>
                    </div>
                </div>
            </div>
        </div>

        {/* Audio Player in Header */}
        {(recording.status !== RecordingStatus.UPLOADING && recording.status !== RecordingStatus.PROCESSING) && (
            <AudioPlayer 
                recording={recording} 
                audioRef={audioRef} 
                currentTime={currentTime}
                onTimeUpdate={handleTimeUpdate}
            />
        )}
      </header>

      <div className="flex-1 flex min-h-0">
        {(recording.status === RecordingStatus.UPLOADING || recording.status === RecordingStatus.PROCESSING) ? (
             <div className="flex-1 flex flex-col items-center justify-center space-y-4">
                <Loader2 className="w-12 h-12 text-orange-500 animate-spin" />
                <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
                    {recording.status === RecordingStatus.UPLOADING ? "Meeting in Progress" : "Processing Recording..."}
                </h2>
                <p className="text-gray-500 dark:text-gray-400">
                    {recording.status === RecordingStatus.UPLOADING 
                        ? "Recording is active or finalizing upload..." 
                        : "This may take a few minutes depending on the recording length."}
                </p>
            </div>
        ) : (
            <>
                <div className="flex-1 overflow-y-auto p-6">
                    <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm p-6 border border-gray-200 dark:border-gray-700">
                        <h2 className="text-xl font-semibold mb-4 text-gray-900 dark:text-white">
                            Transcript
                        </h2>
                        {(recording.transcript?.segments && recording.transcript.segments.length > 0) ? (
                            <TranscriptView
                                segments={recording.transcript.segments}
                                currentTime={currentTime}
                                onPlaySegment={handlePlaySegment}
                                speakerMap={speakerMap}
                                onRenameSpeaker={handleRenameSpeaker}
                                onUpdateSegmentSpeaker={handleUpdateSegmentSpeaker}
                                onUpdateSegmentText={handleUpdateSegmentText}
                                onFindAndReplace={handleFindAndReplace}
                            />
                        ) : (
                            <p className="text-gray-500 dark:text-gray-400 italic">
                                No transcript available yet.
                            </p>
                        )}
                    </div>
                </div>
                <SpeakerPanel 
                    speakers={recording.speakers || []} 
                    segments={recording.transcript?.segments || []}
                    onPlaySegment={handlePlaySegment}
                    recordingId={recording.id}
                />
            </>
        )}
      </div>
    </div>
  );
}
