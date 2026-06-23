"use client";

import TranscriptView from "@/components/TranscriptView";
import type {
  GlobalSpeaker,
  Recording,
  TranscriptSegment,
  TranscriptSpeakerAssignment,
} from "@/types";

interface TranscriptSectionProps {
  active: boolean;
  recording: Recording;
  transcriptSegments: TranscriptSegment[];
  currentTime: number;
  isPlaying: boolean;
  speakerMap: Record<string, string>;
  speakerColors: Record<string, string>;
  globalSpeakers: GlobalSpeaker[];
  canUndo: boolean;
  canRedo: boolean;
  deferredTranscriptUtteranceIds: string[];
  onPlaySegment: (start: number, end?: number) => void | Promise<void>;
  onPause: () => void;
  onResume: () => void;
  onRenameSpeaker: (label: string, newName: string) => void | Promise<void>;
  onUpdateSegmentSpeaker: (
    segment: TranscriptSegment,
    assignment: TranscriptSpeakerAssignment,
  ) => void | Promise<void>;
  onUpdateSegmentText: (
    segment: TranscriptSegment,
    text: string,
  ) => void | Promise<void>;
  onFindAndReplace: (
    find: string,
    replace: string,
    options?: { caseSensitive?: boolean; useRegex?: boolean },
  ) => void | Promise<void>;
  onUndo: () => void;
  onRedo: () => void;
  onExport: () => void;
  onActiveEditUtteranceChange: (id: string | null) => void;
}

export default function TranscriptSection({
  active,
  recording,
  transcriptSegments,
  currentTime,
  isPlaying,
  speakerMap,
  speakerColors,
  globalSpeakers,
  canUndo,
  canRedo,
  deferredTranscriptUtteranceIds,
  onPlaySegment,
  onPause,
  onResume,
  onRenameSpeaker,
  onUpdateSegmentSpeaker,
  onUpdateSegmentText,
  onFindAndReplace,
  onUndo,
  onRedo,
  onExport,
  onActiveEditUtteranceChange,
}: TranscriptSectionProps) {
  return (
    <div
      className={`absolute inset-0 flex flex-col ${active ? "z-10 visible" : "z-0 invisible"}`}
    >
      {transcriptSegments.length > 0 ? (
        <TranscriptView
          recordingId={recording.id}
          segments={transcriptSegments}
          currentTime={currentTime}
          onPlaySegment={onPlaySegment}
          isPlaying={isPlaying}
          onPause={onPause}
          onResume={onResume}
          speakerMap={speakerMap}
          speakers={recording.speakers || []}
          globalSpeakers={globalSpeakers}
          onRenameSpeaker={onRenameSpeaker}
          onUpdateSegmentSpeaker={onUpdateSegmentSpeaker}
          onUpdateSegmentText={onUpdateSegmentText}
          onFindAndReplace={onFindAndReplace}
          speakerColors={speakerColors}
          onUndo={onUndo}
          onRedo={onRedo}
          canUndo={canUndo}
          canRedo={canRedo}
          onExport={onExport}
          onActiveEditUtteranceChange={onActiveEditUtteranceChange}
          pendingRemoteUtteranceIds={deferredTranscriptUtteranceIds}
        />
      ) : (
        <div className="flex flex-col items-center justify-center h-full p-6 text-center space-y-4">
          {recording.transcript?.text ? (
            <>
              <div className="p-4 rounded-lg bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 max-w-md">
                <p className="text-lg font-medium text-gray-700 dark:text-gray-300">
                  {recording.transcript.text.replace(/[\[\]]/g, "")}
                </p>
              </div>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                The audio file was processed, but no speech segments were
                generated.
              </p>
            </>
          ) : (
            <p className="text-gray-500 dark:text-gray-400 italic">
              No transcript available yet.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
