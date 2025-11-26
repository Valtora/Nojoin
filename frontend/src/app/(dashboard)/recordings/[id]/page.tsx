'use client';

import { getRecording, updateSpeaker, updateTranscriptSegmentSpeaker, updateTranscriptSegmentText, findAndReplace, renameRecording, updateTranscriptSegments, getGlobalSpeakers } from '@/lib/api';
import AudioPlayer from '@/components/AudioPlayer';
import SpeakerPanel from '@/components/SpeakerPanel';
import TranscriptView from '@/components/TranscriptView';
import RecordingTagEditor from '@/components/RecordingTagEditor';
import Link from 'next/link';
import { ArrowLeft, Loader2, Edit2 } from 'lucide-react';
import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { Recording, RecordingStatus, TranscriptSegment, GlobalSpeaker } from '@/types';
import { useRouter } from 'next/navigation';
import { COLOR_PALETTE, getColorByKey } from '@/lib/constants';

export const dynamic = 'force-dynamic';

interface PageProps {
  params: Promise<{ id: string }>;
}

// History Stack Item
interface HistoryItem {
    segments: TranscriptSegment[];
    description: string;
}

export default function RecordingPage({ params }: PageProps) {
  const [recording, setRecording] = useState<Recording | null>(null);
  const [globalSpeakers, setGlobalSpeakers] = useState<GlobalSpeaker[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const audioRef = useRef<HTMLAudioElement>(null);
  
  // Undo/Redo State
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [future, setFuture] = useState<HistoryItem[]>([]);
  const [isUndoing, setIsUndoing] = useState(false);

  // Player State
  const [currentTime, setCurrentTime] = useState(0);
  const [stopTime, setStopTime] = useState<number | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);

  // Title Editing State
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleValue, setTitleValue] = useState("");

  // Speaker Colors State
  const [speakerColors, setSpeakerColors] = useState<Record<string, string>>({});

  useEffect(() => {
    const fetchRecording = async () => {
      try {
        const { id } = await params;
        const [recData, gsData] = await Promise.all([
            getRecording(parseInt(id)),
            getGlobalSpeakers()
        ]);
        setRecording(recData);
        setGlobalSpeakers(gsData);
        setTitleValue(recData.name);
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

  // Listen for recording updates (e.g. from Sidebar retry)
  useEffect(() => {
    const handleUpdate = (e: Event) => {
        const customEvent = e as CustomEvent;
        if (recording && customEvent.detail?.id === recording.id) {
            getRecording(recording.id).then(setRecording).catch(console.error);
        }
    };
    window.addEventListener('recording-updated', handleUpdate);
    return () => window.removeEventListener('recording-updated', handleUpdate);
  }, [recording]);

  // Initialize speaker colors
  useEffect(() => {
    if (!recording?.transcript?.segments) return;
    
    const newColors = { ...speakerColors };
    const segments = recording.transcript.segments;
    
    // Get all unique speaker labels (diarization labels)
    const speakerLabels = new Set<string>();
    segments.forEach(s => {
        speakerLabels.add(s.speaker);
    });

    speakerLabels.forEach(label => {
        if (!newColors[label]) {
            // Use a stable hash function to assign colors based on the label
            let hash = 0;
            for (let i = 0; i < label.length; i++) {
                hash = label.charCodeAt(i) + ((hash << 5) - hash);
            }
            const index = Math.abs(hash) % COLOR_PALETTE.length;
            newColors[label] = COLOR_PALETTE[index].key;
        }
    });
    setSpeakerColors(newColors);
  }, [recording]);

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

  const handlePause = () => {
    if (audioRef.current) {
        audioRef.current.pause();
    }
  };

  const handleResume = () => {
    if (audioRef.current) {
        audioRef.current.play();
    }
  };

  // History Management
  const pushToHistory = useCallback((description: string) => {
      if (!recording?.transcript?.segments) return;
      
      // Deep copy segments
      const segmentsSnapshot = JSON.parse(JSON.stringify(recording.transcript.segments));
      
      setHistory(prev => [...prev, { segments: segmentsSnapshot, description }]);
      setFuture([]); // Clear redo stack on new action
  }, [recording]);

  const handleUndo = async () => {
      if (history.length === 0 || !recording?.transcript?.segments || isUndoing) return;
      
      setIsUndoing(true);
      try {
          const previousState = history[history.length - 1];
          const currentSegments = JSON.parse(JSON.stringify(recording.transcript.segments));
          
          // Push current state to future
          setFuture(prev => [{ segments: currentSegments, description: "Undo" }, ...prev]);
          
          // Restore previous state
          await updateTranscriptSegments(recording.id, previousState.segments);
          
          // Update local state
          setHistory(prev => prev.slice(0, -1));
          const updated = await getRecording(recording.id);
          setRecording(updated);
      } catch (e) {
          console.error("Undo failed", e);
          alert("Undo failed.");
      } finally {
          setIsUndoing(false);
      }
  };

  const handleRedo = async () => {
      if (future.length === 0 || !recording?.transcript?.segments || isUndoing) return;
      
      setIsUndoing(true);
      try {
          const nextState = future[0];
          const currentSegments = JSON.parse(JSON.stringify(recording.transcript.segments));
          
          // Push current state to history
          setHistory(prev => [...prev, { segments: currentSegments, description: "Redo" }]);
          
          // Restore next state
          await updateTranscriptSegments(recording.id, nextState.segments);
          
          // Update local state
          setFuture(prev => prev.slice(1));
          const updated = await getRecording(recording.id);
          setRecording(updated);
      } catch (e) {
          console.error("Redo failed", e);
          alert("Redo failed.");
      } finally {
          setIsUndoing(false);
      }
  };

  // Transcript Handlers
  const handleRenameSpeaker = async (label: string, newName: string) => {
    if (!recording) return;
    // Note: Global speaker rename is not currently undoable via segment history
    // as it affects the global speaker table, not just segments.
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
    pushToHistory(`Change speaker segment ${index}`);
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
    pushToHistory(`Edit text segment ${index}`);
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
    pushToHistory(`Replace "${find}" with "${replace}"`);
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

  const handleColorChange = (speakerLabel: string, colorKey: string) => {
      setSpeakerColors(prev => ({
          ...prev,
          [speakerLabel]: colorKey
      }));
  };

  const speakerMap = useMemo(() => {
    const map: Record<string, string> = {};
    if (recording?.speakers) {
      recording.speakers.forEach((s) => {
        // Priority: local_name > global_speaker.name > deprecated name field
        if (s.local_name) {
          map[s.diarization_label] = s.local_name;
        } else if (s.global_speaker) {
          map[s.diarization_label] = s.global_speaker.name;
        } else if (s.name) {
          map[s.diarization_label] = s.name;
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
      <header className="p-6 border-b-2 border-gray-400 dark:border-gray-800 bg-gray-300 dark:bg-gray-900 sticky top-0 z-10 flex-shrink-0 space-y-4">
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
                
                <div className="flex flex-col items-start gap-2">
                    <RecordingTagEditor 
                        recordingId={recording.id}
                        tags={recording.tags || []} 
                        onTagsUpdated={() => {
                            // Refresh recording to get updated tags
                            getRecording(recording.id).then(setRecording).catch(console.error);
                        }}
                    />
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
                onPlay={() => setIsPlaying(true)}
                onPause={() => setIsPlaying(false)}
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
                <div className="flex-1 flex flex-col min-h-0">
                    <div className="flex-1 flex flex-col bg-white dark:bg-gray-800 overflow-hidden min-h-0 h-full">
                        {(recording.transcript?.segments && recording.transcript.segments.length > 0) ? (
                            <TranscriptView
                                recordingId={recording.id}
                                segments={recording.transcript.segments}
                                currentTime={currentTime}
                                onPlaySegment={handlePlaySegment}
                                isPlaying={isPlaying}
                                onPause={handlePause}
                                onResume={handleResume}
                                speakerMap={speakerMap}
                                speakers={recording.speakers || []}
                                globalSpeakers={globalSpeakers}
                                onRenameSpeaker={handleRenameSpeaker}
                                onUpdateSegmentSpeaker={handleUpdateSegmentSpeaker}
                                onUpdateSegmentText={handleUpdateSegmentText}
                                onFindAndReplace={handleFindAndReplace}
                                speakerColors={speakerColors}
                                onUndo={handleUndo}
                                onRedo={handleRedo}
                                canUndo={history.length > 0 && !isUndoing}
                                canRedo={future.length > 0 && !isUndoing}
                            />
                        ) : (
                            <p className="text-gray-500 dark:text-gray-400 italic p-6">
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
                    speakerColors={speakerColors}
                    onColorChange={handleColorChange}
                    currentTime={currentTime}
                    isPlaying={isPlaying}
                    onPause={handlePause}
                    onResume={handleResume}
                />
            </>
        )}
      </div>
    </div>
  );
}
