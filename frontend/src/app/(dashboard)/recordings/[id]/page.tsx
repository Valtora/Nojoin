'use client';

import { getRecording, updateSpeaker, updateTranscriptSegmentSpeaker, updateTranscriptSegmentText, findAndReplace, renameRecording, updateTranscriptSegments, getGlobalSpeakers, updateSpeakerColor, generateNotes, updateNotes, findAndReplaceNotes, exportContent, ExportContentType } from '@/lib/api';
import ChatPanel from '@/components/ChatPanel';
import AudioPlayer from '@/components/AudioPlayer';
import SpeakerPanel from '@/components/SpeakerPanel';
import TranscriptView from '@/components/TranscriptView';
import NotesView from '@/components/NotesView';
import ExportModal from '@/components/ExportModal';
import RecordingTagEditor from '@/components/RecordingTagEditor';
import Link from 'next/link';
import { Loader2, Edit2 } from 'lucide-react';
import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { Recording, RecordingStatus, ClientStatus, TranscriptSegment, GlobalSpeaker } from '@/types';
import { useRouter } from 'next/navigation';
import { COLOR_PALETTE } from '@/lib/constants';
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

type ActivePanel = 'transcript' | 'notes';

const getStatusMessage = (recording: Recording) => {
    if (recording.status === RecordingStatus.UPLOADING) {
        if (recording.client_status === ClientStatus.RECORDING) return "Meeting is in progress...";
        if (recording.client_status === ClientStatus.PAUSED) return "Meeting is paused...";
        if (recording.client_status === ClientStatus.UPLOADING) return "Meeting is being uploaded...";
        return "Recording is active or finalizing upload...";
    }
    if (recording.status === RecordingStatus.QUEUED) {
        return "Meeting is in queue to be processed...";
    }
    if (recording.status === RecordingStatus.PROCESSING) {
        return recording.processing_step || "Processing...";
    }
    return "";
};

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

  // Panel State (Transcript or Notes)
  const [activePanel, setActivePanel] = useState<ActivePanel>('transcript');
  const [isGeneratingNotes, setIsGeneratingNotes] = useState(false);
  
  // Export Modal State
  const [showExportModal, setShowExportModal] = useState(false);

  // Notes History (separate from transcript history, can include null values)
  const [notesHistory, setNotesHistory] = useState<(string | null)[]>([]);
  const [notesFuture, setNotesFuture] = useState<(string | null)[]>([]);

  const fetchRecording = useCallback(async () => {
    try {
      const { id } = await params;
      const [recData, gsData] = await Promise.all([
          getRecording(parseInt(id)),
          getGlobalSpeakers()
      ]);
      setRecording(recData);
      setGlobalSpeakers(gsData);
      // Only set title if not editing, or on first load
      if (!isEditingTitle) {
          setTitleValue(recData.name);
      }
    } catch (e) {
      console.error("Failed to fetch recording:", e);
      setError("Failed to load recording.");
    } finally {
      setLoading(false);
    }
  }, [params, isEditingTitle]);

  useEffect(() => {
    fetchRecording();
  }, [fetchRecording]);

  useEffect(() => {
    if (!recording) return;

    // Poll for updates if processing or generating notes
    const interval = setInterval(async () => {
        if (
            recording.status === RecordingStatus.PROCESSING || 
            recording.status === RecordingStatus.UPLOADING || 
            recording.status === RecordingStatus.QUEUED ||
            recording.transcript?.notes_status === 'generating'
        ) {
             try {
                const { id } = await params;
                const data = await getRecording(parseInt(id));
                
                if (
                    data.status !== recording.status || 
                    data.client_status !== recording.client_status ||
                    data.processing_step !== recording.processing_step ||
                    data.transcript?.notes_status !== recording.transcript?.notes_status ||
                    data.transcript?.notes !== recording.transcript?.notes ||
                    JSON.stringify(data.speakers) !== JSON.stringify(recording.speakers)
                ) {
                    setRecording(data);
                    if (!isEditingTitle) setTitleValue(data.name);
                }
            } catch (e) {
                console.error("Polling failed", e);
            }
        }
    }, 3000);
    
    return () => clearInterval(interval);
  }, [params, recording, isEditingTitle]);

  // Listen for recording updates (e.g. from Sidebar retry or rename)
  useEffect(() => {
    const handleUpdate = (e: Event) => {
        const customEvent = e as CustomEvent;
        if (recording && customEvent.detail?.id === recording.id) {
            if (customEvent.detail.name) {
                // Optimistic update
                setRecording(prev => prev ? { ...prev, name: customEvent.detail.name } : null);
                if (!isEditingTitle) setTitleValue(customEvent.detail.name);
            } else {
                // Force refresh for other updates (like status change or speaker inference)
                getRecording(recording.id).then(setRecording).catch(console.error);
            }
        }
    };
    window.addEventListener('recording-updated', handleUpdate);
    return () => window.removeEventListener('recording-updated', handleUpdate);
  }, [recording, isEditingTitle]);

  // Initialize speaker colors
  useEffect(() => {
    if (!recording?.transcript?.segments) return;
    
    const newColors = { ...speakerColors };
    const segments = recording.transcript.segments;
    
    // Create a map of name -> diarization_label to handle legacy transcripts
    const nameToLabel: Record<string, string> = {};
    if (recording.speakers) {
        recording.speakers.forEach(s => {
            if (s.name) nameToLabel[s.name] = s.diarization_label;
            if (s.local_name) nameToLabel[s.local_name] = s.diarization_label;
            if (s.global_speaker?.name) nameToLabel[s.global_speaker.name] = s.diarization_label;
            // Also map label to itself
            nameToLabel[s.diarization_label] = s.diarization_label;
        });
    }

    // Get all unique speaker labels (diarization labels)
    const speakerLabels = new Set<string>();
    segments.forEach(s => {
        speakerLabels.add(s.speaker);
    });

    speakerLabels.forEach(label => {
        // Try to resolve to diarization_label
        const diarizationLabel = nameToLabel[label] || label;

        // Check if color is already set in recording speakers
        const speaker = recording.speakers?.find(s => s.diarization_label === diarizationLabel);
        if (speaker) {
            if (speaker.global_speaker?.color) {
                newColors[label] = speaker.global_speaker.color;
                // Also set for diarizationLabel if different
                if (label !== diarizationLabel) {
                    newColors[diarizationLabel] = speaker.global_speaker.color;
                }
                return;
            }
            if (speaker.color) {
                newColors[label] = speaker.color;
                if (label !== diarizationLabel) {
                    newColors[diarizationLabel] = speaker.color;
                }
                return;
            }
        }

        if (!newColors[label]) {
            // Use a stable hash function to assign colors based on the label
            let hash = 0;
            for (let i = 0; i < label.length; i++) {
                hash = label.charCodeAt(i) + ((hash << 5) - hash);
            }
            const index = Math.abs(hash) % COLOR_PALETTE.length;
            newColors[label] = COLOR_PALETTE[index].key;
            
            // Also set for diarizationLabel if different and not set
            if (label !== diarizationLabel && !newColors[diarizationLabel]) {
                newColors[diarizationLabel] = COLOR_PALETTE[index].key;
            }
        }
    });
    setSpeakerColors(newColors);
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  const handlePlaySegment = async (start: number, end?: number) => {
    if (audioRef.current) {
        try {
            // Pause first to interrupt any pending play requests
            audioRef.current.pause();
            
            audioRef.current.currentTime = start;
            if (end) setStopTime(end);
            else setStopTime(null);
            
            await audioRef.current.play();
        } catch (err: any) {
            // Ignore AbortError which happens when play() is interrupted by another play() or pause()
            if (err.name !== 'AbortError') {
                console.error("Playback failed:", err);
            }
        }
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

  // Helper to push both transcript and notes history for unified operations
  const pushBothHistories = useCallback((description: string) => {
      if (!recording?.transcript?.segments) return;
      
      // Push transcript history
      const segmentsSnapshot = JSON.parse(JSON.stringify(recording.transcript.segments));
      setHistory(prev => [...prev, { segments: segmentsSnapshot, description }]);
      setFuture([]);
      
      // Always push notes history, even if notes is null/empty
      setNotesHistory(prev => [...prev, recording.transcript?.notes ?? null]);
      setNotesFuture([]);
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

  const handleFindAndReplace = async (find: string, replace: string, options?: { caseSensitive?: boolean, useRegex?: boolean }) => {
    if (!recording) return;
    // Push both transcript and notes to history since this affects both
    pushBothHistories(`Replace "${find}" with "${replace}"`);
    try {
      await findAndReplace(recording.id, find, replace, options);
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

  const handleColorChange = async (speakerLabel: string, colorKey: string) => {
      // Resolve label if it's a name
      let targetLabel = speakerLabel;
      if (recording?.speakers) {
          const speaker = recording.speakers.find(s => 
              s.diarization_label === speakerLabel || 
              s.name === speakerLabel || 
              s.local_name === speakerLabel || 
              s.global_speaker?.name === speakerLabel
          );
          if (speaker) {
              targetLabel = speaker.diarization_label;
          }
      }

      setSpeakerColors(prev => ({
          ...prev,
          [speakerLabel]: colorKey,
          [targetLabel]: colorKey // Ensure both are updated
      }));
      
      if (recording) {
          try {
              await updateSpeakerColor(recording.id, targetLabel, colorKey);
              // Refresh recording to get updated speaker data
              const updated = await getRecording(recording.id);
              setRecording(updated);
          } catch (e) {
              console.error("Failed to update speaker color", e);
          }
      }
  };

  // Notes Handlers
  const handleGenerateNotes = async () => {
      if (!recording) return;
      setIsGeneratingNotes(true);
      try {
          await generateNotes(recording.id);
          const updated = await getRecording(recording.id);
          setRecording(updated);
          setActivePanel('notes'); // Switch to notes panel after generation
      } catch (e: any) {
          console.error("Failed to generate notes:", e);
          alert(e.response?.data?.detail || "Failed to generate notes. Please check your LLM settings.");
      } finally {
          setIsGeneratingNotes(false);
      }
  };

  const handleNotesChange = async (notes: string) => {
      if (!recording) return;
      
      // Always push current notes to history (even if null) before making changes
      setNotesHistory(prev => [...prev, recording.transcript?.notes ?? null]);
      setNotesFuture([]); // Clear redo stack
      
      try {
          await updateNotes(recording.id, notes);
          const updated = await getRecording(recording.id);
          setRecording(updated);
      } catch (e) {
          console.error("Failed to update notes:", e);
          alert("Failed to update notes.");
      }
  };

  const handleNotesFindAndReplace = async (find: string, replace: string, options?: { caseSensitive?: boolean, useRegex?: boolean }) => {
      if (!recording) return;
      
      // Push both transcript and notes to history since this affects both
      pushBothHistories(`Replace "${find}" with "${replace}" (from Notes)`);
      
      try {
          await findAndReplaceNotes(recording.id, find, replace, options);
          const updated = await getRecording(recording.id);
          setRecording(updated);
      } catch (e) {
          console.error("Failed to find and replace in notes:", e);
          alert("Failed to find and replace.");
      }
  };

  const handleNotesUndo = async () => {
      if (notesHistory.length === 0 || !recording) return;
      
      const previousNotes = notesHistory[notesHistory.length - 1];
      const currentNotes = recording.transcript?.notes ?? null;
      
      setNotesFuture(prev => [currentNotes, ...prev]);
      setNotesHistory(prev => prev.slice(0, -1));
      
      try {
          await updateNotes(recording.id, previousNotes || '');
          const updated = await getRecording(recording.id);
          setRecording(updated);
      } catch (e) {
          console.error("Notes undo failed:", e);
      }
  };

  const handleNotesRedo = async () => {
      if (notesFuture.length === 0 || !recording) return;
      
      const nextNotes = notesFuture[0];
      const currentNotes = recording.transcript?.notes ?? null;
      
      setNotesHistory(prev => [...prev, currentNotes]);
      setNotesFuture(prev => prev.slice(1));
      
      try {
          await updateNotes(recording.id, nextNotes || '');
          const updated = await getRecording(recording.id);
          setRecording(updated);
      } catch (e) {
          console.error("Notes redo failed:", e);
      }
  };

  const handleExport = (contentType: ExportContentType) => {
      if (!recording) return;
      exportContent(recording.id, contentType);
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
        {(recording.status !== RecordingStatus.UPLOADING && recording.status !== RecordingStatus.PROCESSING && recording.status !== RecordingStatus.QUEUED) && (
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
        {(recording.status === RecordingStatus.UPLOADING || recording.status === RecordingStatus.PROCESSING || recording.status === RecordingStatus.QUEUED) ? (
             <div className="flex-1 flex flex-col items-center justify-center space-y-4">
                <Loader2 className="w-12 h-12 text-orange-500 animate-spin" />
                <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
                    {recording.status === RecordingStatus.UPLOADING ? "Meeting in Progress" : 
                     recording.status === RecordingStatus.QUEUED ? "Queued for Processing" : "Processing Recording..."}
                </h2>
                <p className="text-gray-500 dark:text-gray-400">
                    {getStatusMessage(recording)}
                </p>
            </div>
        ) : (
                <PanelGroup direction="horizontal" autoSaveId="recording-layout-persistence" className="h-full flex-1 min-w-0">
                <Panel defaultSize={75} minSize={30}>
                    <div className="flex-1 flex flex-col min-h-0 h-full">
                        {/* Panel Tabs */}
                        <div className="bg-gray-200 dark:bg-gray-900 border-b-2 border-gray-400 dark:border-gray-700 flex-shrink-0">
                            <div className="flex">
                                <button
                                    onClick={() => setActivePanel('transcript')}
                                    className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors border-b-2 -mb-0.5 ${
                                        activePanel === 'transcript'
                                            ? 'border-orange-500 text-orange-600 dark:text-orange-400 bg-white dark:bg-gray-800'
                                            : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800'
                                    }`}
                                >
                                    Transcript
                                </button>
                                <button
                                    onClick={() => setActivePanel('notes')}
                                    className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors border-b-2 -mb-0.5 ${
                                        activePanel === 'notes'
                                            ? 'border-orange-500 text-orange-600 dark:text-orange-400 bg-white dark:bg-gray-800'
                                            : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800'
                                    }`}
                                >
                                    Notes
                                </button>
                            </div>
                        </div>

                        {/* Panel Content */}
                        <div className="flex-1 flex flex-col bg-white dark:bg-gray-800 overflow-hidden min-h-0 h-full">
                            {activePanel === 'transcript' ? (
                                (recording.transcript?.segments && recording.transcript.segments.length > 0) ? (
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
                                        onExport={() => setShowExportModal(true)}
                                    />
                                ) : (
                                    <div className="flex flex-col items-center justify-center h-full p-6 text-center space-y-4">
                                        {recording.transcript?.text ? (
                                            <>
                                                <div className="p-4 rounded-lg bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 max-w-md">
                                                    <p className="text-lg font-medium text-gray-700 dark:text-gray-300">
                                                        {recording.transcript.text.replace(/[\[\]]/g, '')}
                                                    </p>
                                                </div>
                                                <p className="text-sm text-gray-500 dark:text-gray-400">
                                                    The audio file was processed, but no speech segments were generated.
                                                </p>
                                            </>
                                        ) : (
                                            <p className="text-gray-500 dark:text-gray-400 italic">
                                                No transcript available yet.
                                            </p>
                                        )}
                                    </div>
                                )
                            ) : (
                                <NotesView
                                    recordingId={recording.id}
                                    notes={recording.transcript?.notes || null}
                                    onNotesChange={handleNotesChange}
                                    onGenerateNotes={handleGenerateNotes}
                                    onFindAndReplace={handleNotesFindAndReplace}
                                    onUndo={handleNotesUndo}
                                    onRedo={handleNotesRedo}
                                    canUndo={notesHistory.length > 0}
                                    canRedo={notesFuture.length > 0}
                                    isGenerating={isGeneratingNotes || recording.transcript?.notes_status === 'generating'}
                                    onExport={() => setShowExportModal(true)}
                                />
                            )}
                        </div>
                    </div>
                </Panel>
                
                <PanelResizeHandle className="bg-gray-200 dark:bg-gray-900 border-l border-gray-400 dark:border-gray-800 w-2 hover:bg-orange-500 dark:hover:bg-orange-500 transition-colors flex items-center justify-center group">
                    <div className="h-8 w-1 bg-gray-400 dark:bg-gray-600 rounded-full group-hover:bg-white transition-colors" />
                </PanelResizeHandle>
                
                <Panel defaultSize={25} minSize={15}>
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
                        onRefresh={fetchRecording}
                    />
                </Panel>
                
                <PanelResizeHandle className="bg-gray-200 dark:bg-gray-900 border-l border-gray-400 dark:border-gray-800 w-2 hover:bg-orange-500 dark:hover:bg-orange-500 transition-colors flex items-center justify-center group">
                    <div className="h-8 w-1 bg-gray-400 dark:bg-gray-600 rounded-full group-hover:bg-white transition-colors" />
                </PanelResizeHandle>
                <Panel defaultSize={20} minSize={15}>
                    <ChatPanel />
                </Panel>
            </PanelGroup>
        )}
      </div>

      {/* Export Modal */}
      <ExportModal
          isOpen={showExportModal}
          onClose={() => setShowExportModal(false)}
          onExport={handleExport}
          hasNotes={!!recording.transcript?.notes}
      />
    </div>
  );
}
