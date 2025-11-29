'use client';

import { RecordingSpeaker, TranscriptSegment, VoiceprintExtractResult, BatchVoiceprintResponse } from '@/types';
import { Play, Pause, ArrowRightToLine, User, UserCheck, Fingerprint, Loader2 } from 'lucide-react';
import { useState } from 'react';
import ContextMenu from './ContextMenu';
import VoiceprintModal from './VoiceprintModal';
import { InlineColorPicker } from './ColorPicker';
import { updateSpeaker, mergeRecordingSpeakers, deleteRecordingSpeaker, extractVoiceprint, extractAllVoiceprints, promoteToGlobalSpeaker } from '@/lib/api';
import { useRouter } from 'next/navigation';
import { useNotificationStore } from '@/lib/notificationStore';

interface SpeakerPanelProps {
  speakers: RecordingSpeaker[];
  segments: TranscriptSegment[];
  onPlaySegment: (time: number, end?: number) => void;
  recordingId: number;
  speakerColors: Record<string, string>; // Now stores color keys, not full classes
  onColorChange: (speakerLabel: string, colorKey: string) => void;
  currentTime: number;
  isPlaying: boolean;
  onPause: () => void;
  onResume: () => void;
  onRefresh: () => void;
}

export default function SpeakerPanel({ speakers, segments, onPlaySegment, recordingId, speakerColors, onColorChange, currentTime, isPlaying, onPause, onResume, onRefresh }: SpeakerPanelProps) {
  const router = useRouter();
  const { addNotification } = useNotificationStore();
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; speaker: RecordingSpeaker } | null>(null);
  
  // Rename State
  const [renamingSpeaker, setRenamingSpeaker] = useState<RecordingSpeaker | null>(null);
  const [renameValue, setRenameValue] = useState("");
  
  // Merge State
  const [mergingSpeaker, setMergingSpeaker] = useState<RecordingSpeaker | null>(null);
  const [mergeTargetLabel, setMergeTargetLabel] = useState("");

  const [isSubmitting, setIsSubmitting] = useState(false);
  
  // Voiceprint State
  const [extractingVoiceprint, setExtractingVoiceprint] = useState<string | null>(null); // diarization_label
  const [voiceprintModalOpen, setVoiceprintModalOpen] = useState(false);
  const [voiceprintExtractResult, setVoiceprintExtractResult] = useState<VoiceprintExtractResult | null>(null);
  const [batchVoiceprintResults, setBatchVoiceprintResults] = useState<BatchVoiceprintResponse | null>(null);
  const [extractingAllVoiceprints, setExtractingAllVoiceprints] = useState(false);

  // Helper to get the display name for a speaker
  const getSpeakerName = (speaker: RecordingSpeaker): string => {
    return speaker.local_name || speaker.global_speaker?.name || speaker.name || speaker.diarization_label;
  };

  // Deduplicate speakers based on diarization_label
  const uniqueSpeakers = speakers.reduce((acc, current) => {
    const x = acc.find(item => item.diarization_label === current.diarization_label);
    if (!x) {
      return acc.concat([current]);
    } else {
      return acc;
    }
  }, [] as RecordingSpeaker[]).sort((a, b) => {
    return getSpeakerName(a).localeCompare(getSpeakerName(b));
  });

  const handlePlaySnippet = (label: string, isSpeakerActive: boolean) => {
    if (isSpeakerActive) {
        if (isPlaying) {
            onPause();
        } else {
            onResume();
        }
        return;
    }

    const speakerSegments = segments.filter(s => s.speaker === label);
    if (speakerSegments.length === 0) {
        addNotification({ type: 'warning', message: "No audio segments found for this speaker." });
        return;
    }
    const randomSegment = speakerSegments[Math.floor(Math.random() * speakerSegments.length)];
    onPlaySegment(randomSegment.start, randomSegment.end);
  };

  const handleNextSnippet = (label: string) => {
    const speakerSegments = segments.filter(s => s.speaker === label);
    if (speakerSegments.length === 0) {
        addNotification({ type: 'warning', message: "No audio segments found for this speaker." });
        return;
    }
    
    // Try to find a segment that is not currently playing
    let candidates = speakerSegments;
    if (speakerSegments.length > 1) {
        candidates = speakerSegments.filter(s => !(currentTime >= s.start && currentTime < s.end));
        if (candidates.length === 0) candidates = speakerSegments;
    }

    const randomSegment = candidates[Math.floor(Math.random() * candidates.length)];
    onPlaySegment(randomSegment.start, randomSegment.end);
  };

  const handleContextMenu = (e: React.MouseEvent, speaker: RecordingSpeaker) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, speaker });
  };

  const handleRenameStart = (speaker: RecordingSpeaker) => {
    setRenamingSpeaker(speaker);
    setRenameValue(getSpeakerName(speaker));
    setContextMenu(null);
  };

  const handleMergeStart = (speaker: RecordingSpeaker) => {
    setMergingSpeaker(speaker);
    setMergeTargetLabel("");
    setContextMenu(null);
  };

  const handleRenameSubmit = async () => {
    if (!renamingSpeaker || !renameValue.trim() || isSubmitting) return;
    
    setIsSubmitting(true);
    try {
        await updateSpeaker(recordingId, renamingSpeaker.diarization_label, renameValue.trim());
        setRenamingSpeaker(null);
        onRefresh();
    } catch (e) {
        console.error("Failed to rename speaker", e);
        addNotification({ type: 'error', message: "Failed to rename speaker." });
    } finally {
        setIsSubmitting(false);
    }
  };

  const handleMergeSubmit = async () => {
    if (!mergingSpeaker || !mergeTargetLabel || isSubmitting) return;

    setIsSubmitting(true);
    try {
        await mergeRecordingSpeakers(recordingId, mergeTargetLabel, mergingSpeaker.diarization_label);
        setMergingSpeaker(null);
        
        // Dispatch custom event to notify parent components of the merge
        window.dispatchEvent(new CustomEvent('recording-updated', { detail: { recordingId } }));
        
        onRefresh();
    } catch (e) {
        console.error("Failed to merge speakers", e);
        addNotification({ type: 'error', message: "Failed to merge speakers." });
    } finally {
        setIsSubmitting(false);
    }
  };

  const handleDelete = async (speaker: RecordingSpeaker) => {
    const speakerName = getSpeakerName(speaker);
    const isGlobal = !!speaker.global_speaker_id;
    const message = isGlobal
        ? `Remove ${speakerName} from this recording? Their segments will be marked as UNKNOWN. They will remain in your Speaker Library.`
        : `Delete ${speakerName} from this recording? Their segments will be marked as UNKNOWN.`;
    
    if (!confirm(message)) {
        return;
    }
    
    setIsSubmitting(true);
    try {
        await deleteRecordingSpeaker(recordingId, speaker.diarization_label);
        setContextMenu(null);
        onRefresh();
    } catch (e) {
        console.error("Failed to delete speaker", e);
        addNotification({ type: 'error', message: "Failed to delete speaker." });
    } finally {
        setIsSubmitting(false);
    }
  };

  const handleCreateVoiceprint = async (speaker: RecordingSpeaker) => {
    setContextMenu(null);
    setExtractingVoiceprint(speaker.diarization_label);
    
    try {
      const result = await extractVoiceprint(recordingId, speaker.diarization_label);
      setVoiceprintExtractResult(result);
      setBatchVoiceprintResults(null);
      setVoiceprintModalOpen(true);
    } catch (e: any) {
      console.error("Failed to extract voiceprint", e);
      addNotification({ type: 'error', message: e.response?.data?.detail || "Failed to extract voiceprint." });
    } finally {
      setExtractingVoiceprint(null);
    }
  };

  const handleCreateAllVoiceprints = async () => {
    setExtractingAllVoiceprints(true);
    
    try {
      const result = await extractAllVoiceprints(recordingId);
      if (result.speakers_processed === 0) {
        addNotification({ type: 'info', message: "All speakers already have voiceprints." });
        onRefresh(); // Refresh to ensure UI is in sync
        return;
      }
      setBatchVoiceprintResults(result);
      setVoiceprintExtractResult(null);
      setVoiceprintModalOpen(true);
    } catch (e: any) {
      console.error("Failed to extract voiceprints", e);
      addNotification({ type: 'error', message: e.response?.data?.detail || "Failed to extract voiceprints." });
    } finally {
      setExtractingAllVoiceprints(false);
    }
  };

  const handlePromoteToGlobal = async (speaker: RecordingSpeaker) => {
    setContextMenu(null);
    setIsSubmitting(true);
    
    try {
      await promoteToGlobalSpeaker(recordingId, speaker.diarization_label);
      onRefresh();
      addNotification({ type: 'success', message: "Speaker added to Global Library." });
    } catch (e: any) {
      console.error("Failed to promote speaker", e);
      addNotification({ type: 'error', message: e.response?.data?.detail || "Failed to promote speaker to global library." });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleVoiceprintModalComplete = () => {
    onRefresh();
  };

  // Check if any speakers are missing voiceprints
  const speakersWithoutVoiceprints = uniqueSpeakers.filter(s => !s.has_voiceprint);

  return (
    <aside className="flex-shrink-0 border-l border-gray-400 dark:border-gray-800 bg-gray-200 dark:bg-gray-900 h-full overflow-y-auto">
      {/* Header with batch voiceprint action */}
      {uniqueSpeakers.length > 0 && speakersWithoutVoiceprints.length > 0 && (
        <div className="p-2 border-b border-gray-300 dark:border-gray-700">
          <button
            onClick={handleCreateAllVoiceprints}
            disabled={extractingAllVoiceprints}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 text-xs font-medium text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-colors disabled:opacity-50"
            title="Create voice fingerprints for all speakers without one. This enables automatic speaker recognition in future recordings."
          >
            {extractingAllVoiceprints ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Fingerprint className="w-3.5 h-3.5" />
            )}
            <span>Add All ({speakersWithoutVoiceprints.length}) Voiceprints to Speaker Library</span>
          </button>
        </div>
      )}
      
      <div className="p-2 space-y-2 mt-2">
        {uniqueSpeakers.length === 0 ? (
            <div className="p-4 text-sm text-gray-500 dark:text-gray-400 text-center italic">
                No speakers detected.
            </div>
        ) : (
            uniqueSpeakers.map((speaker) => {
                const isRenaming = renamingSpeaker?.diarization_label === speaker.diarization_label;
                const isMerging = mergingSpeaker?.diarization_label === speaker.diarization_label;

                if (isMerging) {
                    const otherSpeakers = uniqueSpeakers.filter(s => s.diarization_label !== speaker.diarization_label);
                    return (
                        <div key={speaker.id} className="p-3 rounded-lg bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-800">
                            <p className="text-xs font-semibold text-orange-800 dark:text-orange-200 mb-2">
                                Merge {getSpeakerName(speaker)} into:
                            </p>
                            <select 
                                className="w-full text-sm p-1 mb-2 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                                value={mergeTargetLabel}
                                onChange={(e) => setMergeTargetLabel(e.target.value)}
                            >
                                <option value="">Select Speaker...</option>
                                {otherSpeakers.map(s => (
                                    <option key={s.diarization_label} value={s.diarization_label}>
                                        {getSpeakerName(s)}
                                    </option>
                                ))}
                            </select>
                            <div className="flex gap-2">
                                <button 
                                    onClick={handleMergeSubmit}
                                    disabled={!mergeTargetLabel}
                                    className="flex-1 px-2 py-1 bg-orange-500 text-white text-xs rounded hover:bg-orange-600 disabled:opacity-50"
                                >
                                    Confirm
                                </button>
                                <button 
                                    onClick={() => setMergingSpeaker(null)}
                                    className="px-2 py-1 bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 text-xs rounded hover:bg-gray-300 dark:hover:bg-gray-600"
                                >
                                    Cancel
                                </button>
                            </div>
                        </div>
                    );
                }

                const isSpeakerActive = segments.some(s => s.speaker === speaker.diarization_label && currentTime >= s.start && currentTime < s.end);

                return (
                <div 
                    key={speaker.id} 
                    className="relative group flex items-center justify-between p-3 rounded-lg bg-white dark:bg-gray-800/50 border border-gray-300 dark:border-gray-700 hover:border-blue-400 dark:hover:border-blue-700 transition-colors shadow-sm"
                    onContextMenu={(e) => handleContextMenu(e, speaker)}
                >
                    <div className="flex items-center gap-3 min-w-0 flex-1">
                    <div className="relative flex-shrink-0">
                        <div className="relative">
                            <div className="w-8 h-8 rounded-full border border-gray-300 dark:border-gray-600 flex items-center justify-center bg-gray-50 dark:bg-gray-800/50">
                                {speaker.has_voiceprint ? (
                                    <UserCheck className="w-4 h-4 opacity-70 text-blue-600 dark:text-blue-400" />
                                ) : (
                                    <User className="w-4 h-4 opacity-50 text-gray-500 dark:text-gray-400" />
                                )}
                            </div>
                            <div className="absolute -bottom-1 -right-1">
                                <InlineColorPicker
                                    selectedColor={speakerColors[speaker.diarization_label]}
                                    onColorSelect={(colorKey) => {
                                        onColorChange(speaker.diarization_label, colorKey);
                                    }}
                                />
                            </div>
                        </div>
                        {/* Extracting indicator */}
                        {extractingVoiceprint === speaker.diarization_label && (
                          <div className="absolute -top-0.5 -left-0.5 w-3.5 h-3.5 bg-blue-500 rounded-full flex items-center justify-center border-2 border-white dark:border-gray-800">
                            <Loader2 className="w-2 h-2 text-white animate-spin" />
                          </div>
                        )}
                    </div>
                    <div className="min-w-0 flex-1">
                        {isRenaming ? (
                            <input
                                autoFocus
                                type="text"
                                value={renameValue}
                                onChange={(e) => setRenameValue(e.target.value)}
                                onBlur={handleRenameSubmit}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter') handleRenameSubmit();
                                    if (e.key === 'Escape') setRenamingSpeaker(null);
                                }}
                                className="w-full text-sm font-medium bg-white dark:bg-gray-700 border border-blue-300 rounded px-1 focus:outline-none"
                            />
                        ) : (
                            <>
                                <p className="text-sm font-medium text-gray-900 dark:text-white truncate" title={getSpeakerName(speaker)}>
                                {getSpeakerName(speaker)}
                                </p>
                                {/* {speaker.global_speaker && (
                                    <p className="text-xs text-gray-500 truncate">{speaker.diarization_label}</p>
                                )} */}
                            </>
                        )}
                    </div>
                    </div>
                    <div className="flex items-center gap-1">
                        <button 
                        className={`p-1.5 rounded-full transition-colors ${
                            isSpeakerActive && isPlaying
                            ? 'text-blue-600 bg-blue-100 dark:text-blue-400 dark:bg-blue-900/30'
                            : 'text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20'
                        }`}
                        title={isSpeakerActive && isPlaying ? "Pause" : "Preview Voice"}
                        onClick={() => handlePlaySnippet(speaker.diarization_label, isSpeakerActive)}
                        >
                        {isSpeakerActive && isPlaying ? (
                            <Pause className="w-3 h-3 fill-current" />
                        ) : (
                            <Play className="w-3 h-3 fill-current" />
                        )}
                        </button>
                        {isSpeakerActive && isPlaying && (
                            <button 
                            className="p-1.5 text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-full transition-colors"
                            title="Next Snippet"
                            onClick={() => handleNextSnippet(speaker.diarization_label)}
                            >
                            <ArrowRightToLine className="w-3 h-3 fill-current" />
                            </button>
                        )}
                    </div>
                </div>
                );
            })
        )}
      </div>
      {contextMenu && (
        <ContextMenu
            x={contextMenu.x}
            y={contextMenu.y}
            onClose={() => setContextMenu(null)}
            items={[
                { 
                    label: 'Rename / Assign', 
                    onClick: () => handleRenameStart(contextMenu.speaker) 
                },
                {
                    label: 'Merge into...',
                    onClick: () => handleMergeStart(contextMenu.speaker)
                },
                // Voiceprint option - only show if speaker doesn't have one
                ...(!contextMenu.speaker.has_voiceprint ? [{
                    label: 'Create Voiceprint',
                    onClick: () => handleCreateVoiceprint(contextMenu.speaker)
                }] : []),
                // Add to Speaker Library option - only show if not already global
                ...(!contextMenu.speaker.global_speaker_id ? [{
                    label: 'Add Voiceprint to Speaker Library',
                    onClick: () => handlePromoteToGlobal(contextMenu.speaker)
                }] : []),
                {
                    label: 'Delete',
                    onClick: () => handleDelete(contextMenu.speaker),
                    className: 'text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20'
                }
            ]}
        />
      )}
      
      {/* Voiceprint Modal */}
      <VoiceprintModal
        isOpen={voiceprintModalOpen}
        onClose={() => {
          setVoiceprintModalOpen(false);
          setVoiceprintExtractResult(null);
          setBatchVoiceprintResults(null);
        }}
        onComplete={handleVoiceprintModalComplete}
        recordingId={recordingId}
        extractResult={voiceprintExtractResult ?? undefined}
        batchResults={batchVoiceprintResults?.results}
        allGlobalSpeakers={batchVoiceprintResults?.all_global_speakers || voiceprintExtractResult?.all_global_speakers}
      />
    </aside>
  );
}
