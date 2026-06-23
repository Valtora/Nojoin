"use client";

import { ArrowLeft, MoreHorizontal } from "lucide-react";
import type { RefObject } from "react";

import type { ActivePanel } from "@/lib/store";
import {
  GlobalSpeaker,
  Recording,
  TranscriptSegment,
  TranscriptSpeakerAssignment,
} from "@/types";

import RecordingHeader from "./RecordingHeader";
import TranscriptSection from "./TranscriptSection";
import NotesSection from "./NotesSection";
import DocumentsSection from "./DocumentsSection";

interface RecordingMainContentProps {
  recording: Recording;
  isMobile: boolean;
  activePanel: ActivePanel;
  setActivePanel: (panel: ActivePanel) => void;
  // Header
  isEditingTitle: boolean;
  titleValue: string;
  isMobileHeaderActionsOpen: boolean;
  currentTime: number;
  isPlaying: boolean;
  audioRef: RefObject<HTMLAudioElement | null>;
  setRecording: (recording: Recording) => void;
  setTitleValue: (value: string) => void;
  setIsEditingTitle: (editing: boolean) => void;
  setIsMobileHeaderActionsOpen: (
    update: boolean | ((current: boolean) => boolean),
  ) => void;
  setIsPlaying: (playing: boolean) => void;
  onTitleSubmit: () => void;
  onTimeUpdate: () => void;
  onBack: () => void;
  // Transcript
  transcriptSegments: TranscriptSegment[];
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
  onActiveEditUtteranceChange: (id: string | null) => void;
  onExport: () => void;
  // Notes
  isGeneratingNotes: boolean;
  notesCanUndo: boolean;
  notesCanRedo: boolean;
  onNotesChange: (notes: string) => void;
  onGenerateNotes: () => Promise<void>;
  onNotesUndo: () => void;
  onNotesRedo: () => void;
}

const tabClassName = (active: boolean) =>
  `flex min-w-0 items-center justify-center border-b-2 px-3 py-2.5 text-[13px] font-medium transition-colors md:px-6 md:py-3 md:text-sm ${
    active
      ? "border-orange-500 text-orange-600 dark:text-orange-400 bg-white dark:bg-gray-800"
      : "border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800"
  }`;

export default function RecordingMainContent({
  recording,
  isMobile,
  activePanel,
  setActivePanel,
  isEditingTitle,
  titleValue,
  isMobileHeaderActionsOpen,
  currentTime,
  isPlaying,
  audioRef,
  setRecording,
  setTitleValue,
  setIsEditingTitle,
  setIsMobileHeaderActionsOpen,
  setIsPlaying,
  onTitleSubmit,
  onTimeUpdate,
  onBack,
  transcriptSegments,
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
  onActiveEditUtteranceChange,
  onExport,
  isGeneratingNotes,
  notesCanUndo,
  notesCanRedo,
  onNotesChange,
  onGenerateNotes,
  onNotesUndo,
  onNotesRedo,
}: RecordingMainContentProps) {
  return (
    <div className="flex-1 flex flex-col min-h-0 h-full">
      {isMobile ? (
        <div className="pointer-events-none fixed inset-x-0 top-0 z-40 flex items-start justify-between px-4 pt-[calc(env(safe-area-inset-top)+0.75rem)] lg:hidden">
          <button
            onClick={onBack}
            className="pointer-events-auto inline-flex h-12 shrink-0 items-center gap-2 rounded-2xl border border-gray-200 bg-white/90 px-4 text-sm font-medium text-gray-700 shadow-lg shadow-black/10 backdrop-blur-sm transition-colors hover:bg-white dark:border-gray-700 dark:bg-gray-800/90 dark:text-gray-300 dark:hover:bg-gray-800 dark:shadow-black/30"
            title="Back to Recordings"
            aria-label="Back to Recordings"
          >
            <ArrowLeft className="h-4 w-4" />
            <span>Back</span>
          </button>

          <button
            onClick={() => setIsMobileHeaderActionsOpen((current) => !current)}
            className={`pointer-events-auto inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border shadow-lg shadow-black/10 backdrop-blur-sm transition-colors dark:shadow-black/30 ${isMobileHeaderActionsOpen ? "border-orange-200 bg-orange-50 text-orange-700 dark:border-orange-500/30 dark:bg-orange-500/10 dark:text-orange-300" : "border-gray-200 bg-white/90 text-gray-700 hover:bg-white dark:border-gray-700 dark:bg-gray-800/90 dark:text-gray-300 dark:hover:bg-gray-800"}`}
            title={isMobileHeaderActionsOpen ? "Hide meeting actions" : "Show meeting actions"}
            aria-label={isMobileHeaderActionsOpen ? "Hide meeting actions" : "Show meeting actions"}
          >
            <MoreHorizontal className="h-5 w-5" />
          </button>
        </div>
      ) : null}

      {/* Header (Title, Tags, Audio Player) */}
      <RecordingHeader
        recording={recording}
        isMobile={isMobile}
        isEditingTitle={isEditingTitle}
        titleValue={titleValue}
        isMobileHeaderActionsOpen={isMobileHeaderActionsOpen}
        currentTime={currentTime}
        audioRef={audioRef}
        setRecording={setRecording}
        setTitleValue={setTitleValue}
        setIsEditingTitle={setIsEditingTitle}
        onTitleSubmit={onTitleSubmit}
        onTimeUpdate={onTimeUpdate}
        onPlay={() => setIsPlaying(true)}
        onPause={() => setIsPlaying(false)}
      />

      {/* Panel Tabs */}
      <div className="shrink-0 bg-gray-50 dark:bg-gray-900">
        <div className="grid grid-cols-3 border-b-2 border-gray-200 dark:border-gray-700">
          <button
            id="tab-transcript"
            onClick={() => setActivePanel("transcript")}
            className={tabClassName(activePanel === "transcript")}
          >
            <span className="truncate">Transcript</span>
          </button>
          <button
            id="tab-notes"
            onClick={() => setActivePanel("notes")}
            className={tabClassName(activePanel === "notes")}
          >
            <span className="truncate">Notes</span>
          </button>
          <button
            id="tab-documents"
            onClick={() => setActivePanel("documents")}
            className={tabClassName(activePanel === "documents")}
          >
            <span className="truncate">Documents</span>
          </button>
        </div>
      </div>

      {/* Panel Content */}
      <div className="flex-1 flex flex-col bg-white dark:bg-gray-800 overflow-hidden min-h-0 h-full relative">
        <TranscriptSection
          active={activePanel === "transcript"}
          recording={recording}
          transcriptSegments={transcriptSegments}
          currentTime={currentTime}
          isPlaying={isPlaying}
          speakerMap={speakerMap}
          speakerColors={speakerColors}
          globalSpeakers={globalSpeakers}
          canUndo={canUndo}
          canRedo={canRedo}
          deferredTranscriptUtteranceIds={deferredTranscriptUtteranceIds}
          onPlaySegment={onPlaySegment}
          onPause={onPause}
          onResume={onResume}
          onRenameSpeaker={onRenameSpeaker}
          onUpdateSegmentSpeaker={onUpdateSegmentSpeaker}
          onUpdateSegmentText={onUpdateSegmentText}
          onFindAndReplace={onFindAndReplace}
          onUndo={onUndo}
          onRedo={onRedo}
          onExport={onExport}
          onActiveEditUtteranceChange={onActiveEditUtteranceChange}
        />

        <NotesSection
          active={activePanel === "notes"}
          recording={recording}
          isGenerating={isGeneratingNotes}
          canUndo={notesCanUndo}
          canRedo={notesCanRedo}
          onNotesChange={onNotesChange}
          onGenerateNotes={onGenerateNotes}
          onFindAndReplace={onFindAndReplace}
          onUndo={onNotesUndo}
          onRedo={onNotesRedo}
          onExport={onExport}
        />

        <DocumentsSection
          active={activePanel === "documents"}
          recordingId={recording.id}
        />
      </div>
    </div>
  );
}
