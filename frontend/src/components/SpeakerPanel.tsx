'use client';

import { RecordingSpeaker, TranscriptSegment } from '@/types';
import { Play, User, Merge } from 'lucide-react';
import { useState } from 'react';
import ContextMenu from './ContextMenu';
import { updateSpeaker, mergeRecordingSpeakers } from '@/lib/api';
import { useRouter } from 'next/navigation';

interface SpeakerPanelProps {
  speakers: RecordingSpeaker[];
  segments: TranscriptSegment[];
  onPlaySegment: (time: number) => void;
  recordingId: number;
}

export default function SpeakerPanel({ speakers, segments, onPlaySegment, recordingId }: SpeakerPanelProps) {
  const router = useRouter();
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; speaker: RecordingSpeaker } | null>(null);
  
  // Rename State
  const [renamingSpeaker, setRenamingSpeaker] = useState<RecordingSpeaker | null>(null);
  const [renameValue, setRenameValue] = useState("");
  
  // Merge State
  const [mergingSpeaker, setMergingSpeaker] = useState<RecordingSpeaker | null>(null);
  const [mergeTargetLabel, setMergeTargetLabel] = useState("");

  const [isSubmitting, setIsSubmitting] = useState(false);

  // Deduplicate speakers based on diarization_label
  const uniqueSpeakers = speakers.reduce((acc, current) => {
    const x = acc.find(item => item.diarization_label === current.diarization_label);
    if (!x) {
      return acc.concat([current]);
    } else {
      return acc;
    }
  }, [] as RecordingSpeaker[]).sort((a, b) => {
    const nameA = a.name || a.global_speaker?.name || a.diarization_label;
    const nameB = b.name || b.global_speaker?.name || b.diarization_label;
    return nameA.localeCompare(nameB);
  });

  const handlePlaySnippet = (label: string) => {
    const speakerSegments = segments.filter(s => s.speaker === label);
    if (speakerSegments.length === 0) {
        alert("No audio segments found for this speaker.");
        return;
    }
    const randomSegment = speakerSegments[Math.floor(Math.random() * speakerSegments.length)];
    onPlaySegment(randomSegment.start);
  };

  const handleContextMenu = (e: React.MouseEvent, speaker: RecordingSpeaker) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, speaker });
  };

  const handleRenameStart = (speaker: RecordingSpeaker) => {
    setRenamingSpeaker(speaker);
    setRenameValue(speaker.name || speaker.global_speaker?.name || speaker.diarization_label);
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
        router.refresh();
    } catch (e) {
        console.error("Failed to rename speaker", e);
        alert("Failed to rename speaker.");
    } finally {
        setIsSubmitting(false);
    }
  };

  const handleMergeSubmit = async () => {
    if (!mergingSpeaker || !mergeTargetLabel || isSubmitting) return;

    if (!confirm(`Are you sure you want to merge ${mergingSpeaker.name || mergingSpeaker.diarization_label} into the selected speaker? This cannot be undone.`)) {
        return;
    }

    setIsSubmitting(true);
    try {
        await mergeRecordingSpeakers(recordingId, mergeTargetLabel, mergingSpeaker.diarization_label);
        setMergingSpeaker(null);
        router.refresh();
    } catch (e) {
        console.error("Failed to merge speakers", e);
        alert("Failed to merge speakers.");
    } finally {
        setIsSubmitting(false);
    }
  };

  return (
    <aside className="w-64 flex-shrink-0 border-l border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 h-full overflow-y-auto">
      <div className="p-4 border-b border-gray-200 dark:border-gray-800">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-white uppercase tracking-wider">
          Speaker Management
        </h2>
      </div>
      <div className="p-2 space-y-2">
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
                                Merge {speaker.name || speaker.diarization_label} into:
                            </p>
                            <select 
                                className="w-full text-sm p-1 mb-2 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                                value={mergeTargetLabel}
                                onChange={(e) => setMergeTargetLabel(e.target.value)}
                            >
                                <option value="">Select Speaker...</option>
                                {otherSpeakers.map(s => (
                                    <option key={s.diarization_label} value={s.diarization_label}>
                                        {s.name || s.global_speaker?.name || s.diarization_label}
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

                return (
                <div 
                    key={speaker.id} 
                    className="relative group flex items-center justify-between p-3 rounded-lg bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-800 hover:border-blue-200 dark:hover:border-blue-800 transition-colors"
                    onContextMenu={(e) => handleContextMenu(e, speaker)}
                >
                    <div className="flex items-center gap-3 min-w-0 flex-1">
                    <div className="w-8 h-8 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center flex-shrink-0">
                        <User className="w-4 h-4 text-blue-600 dark:text-blue-400" />
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
                                <p className="text-sm font-medium text-gray-900 dark:text-white truncate" title={speaker.name || speaker.global_speaker?.name || speaker.diarization_label}>
                                {speaker.name || speaker.global_speaker?.name || speaker.diarization_label}
                                </p>
                                {/* {speaker.global_speaker && (
                                    <p className="text-xs text-gray-500 truncate">{speaker.diarization_label}</p>
                                )} */}
                            </>
                        )}
                    </div>
                    </div>
                    <div className="flex items-center">
                        <button 
                        className="p-1.5 text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-full transition-colors"
                        title="Preview Voice"
                        onClick={() => handlePlaySnippet(speaker.diarization_label)}
                        >
                        <Play className="w-3 h-3 fill-current" />
                        </button>
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
                }
            ]}
        />
      )}
    </aside>
  );
}
