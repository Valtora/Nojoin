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

import SpeakerPanel from "@/components/SpeakerPanel";

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
  onColorChange: (speakerLabel: string, colorKey: string) => void;
  onRefresh: () => void;
  onSpeakerRenamed?: (oldName: string, newName: string) => Promise<void> | void;
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
  onGenerateNotes: (notesTemplateId?: number | null) => Promise<void>;
  onNotesUndo: () => void;
  onNotesRedo: () => void;
}

const tabClassName = (active: boolean) =>
  `flex min-w-0 items-center justify-center border-b-2 px-3 py-2.5 text-[13px] font-medium transition-colors md:px-6 md:py-3 md:text-sm ${
    active
      ? "border-action text-action-text bg-surface-card"
      : "border-transparent text-contrast-helper hover:text-foreground hover:bg-surface-inset"
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
  onColorChange,
  onRefresh,
  onSpeakerRenamed,
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
        <div className="pointer-events-none fixed inset-x-0 top-0 z-[var(--z-sticky)] flex items-start justify-between px-4 pt-[calc(env(safe-area-inset-top)+0.75rem)] lg:hidden">
          <button
            onClick={onBack}
            className="pointer-events-auto inline-flex h-12 shrink-0 items-center gap-2 rounded-2xl border border-surface-border bg-surface-card px-4 text-sm font-medium text-contrast-muted shadow-float transition-colors hover:bg-surface-card"
            title="Back to Recordings"
            aria-label="Back to Recordings"
          >
            <ArrowLeft className="h-4 w-4" />
            <span>Back</span>
          </button>

          <button
            onClick={() => setIsMobileHeaderActionsOpen((current) => !current)}
            className={`pointer-events-auto inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border shadow-float transition-colors dark: ${isMobileHeaderActionsOpen ? "border-action-border bg-action-tint text-action-text" : "border-surface-border bg-surface-card text-contrast-muted hover:bg-surface-card"}`}
            title={
              isMobileHeaderActionsOpen
                ? "Hide meeting actions"
                : "Show meeting actions"
            }
            aria-label={
              isMobileHeaderActionsOpen
                ? "Hide meeting actions"
                : "Show meeting actions"
            }
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

      {/* Panel Tabs. The Speakers tab is mobile-only; on desktop the speaker
          panel lives in the side column. */}
      <div className="shrink-0 bg-surface-inset">
        <div
          className={`grid ${isMobile ? "grid-cols-4" : "grid-cols-3"} border-b-2 border-surface-border`}
        >
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
          {isMobile && (
            <button
              id="tab-speakers"
              onClick={() => setActivePanel("speakers")}
              className={tabClassName(activePanel === "speakers")}
            >
              <span className="truncate">Speakers</span>
            </button>
          )}
        </div>
      </div>

      {/* Panel Content */}
      <div className="flex-1 flex flex-col bg-surface-card overflow-hidden min-h-0 h-full relative">
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

        {isMobile && (
          <div
            className={`absolute inset-0 flex flex-col ${activePanel === "speakers" ? "z-10 visible" : "z-0 invisible"}`}
          >
            <SpeakerPanel
              speakers={recording.speakers || []}
              segments={transcriptSegments}
              onPlaySegment={onPlaySegment}
              recordingId={recording.id}
              speakerColors={speakerColors}
              onColorChange={onColorChange}
              currentTime={currentTime}
              isPlaying={isPlaying}
              onPause={onPause}
              onResume={onResume}
              onRefresh={onRefresh}
              globalSpeakers={globalSpeakers}
              onSpeakerRenamed={onSpeakerRenamed}
            />
          </div>
        )}
      </div>
    </div>
  );
}
