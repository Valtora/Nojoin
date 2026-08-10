"use client";

import type { RefObject } from "react";

import { useNavigationStore, type ActivePanel } from "@/lib/store";
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
import AnalyticsSection from "./AnalyticsSection";

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
  const setSpeakerPanelCollapsed = useNavigationStore(
    (state) => state.setSpeakerPanelCollapsed,
  );

  return (
    <div className="flex-1 flex flex-col min-h-0 h-full">
      {/* Header (Title, Tags, Audio Player). On mobile it also carries the
          back and actions controls inline, so nothing floats over content
          and no top padding is reserved for an overlay. */}
      <RecordingHeader
        onBack={onBack}
        setIsMobileHeaderActionsOpen={setIsMobileHeaderActionsOpen}
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
        onShowSpeakers={
          isMobile
            ? () => {
                setActivePanel("speakers");
                setIsMobileHeaderActionsOpen(false);
              }
            : undefined
        }
      />

      {/* Panel Tabs. Four on every viewport: Speakers used to hold a
          mobile-only fifth tab, but five labels do not fit legibly at 360px,
          so on mobile the speaker panel is reached from the header's actions
          menu instead. On desktop it lives in the side column as before. */}
      <div className="shrink-0 bg-surface-inset">
        <div className="grid grid-cols-4 border-b-2 border-surface-border">
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
          <button
            id="tab-analytics"
            onClick={() => setActivePanel("analytics")}
            className={tabClassName(activePanel === "analytics")}
          >
            <span className="truncate">Analytics</span>
          </button>
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

        <AnalyticsSection
          active={activePanel === "analytics"}
          recordingId={recording.id}
          speakerColors={speakerColors}
          onPlaySegment={(startMs) => onPlaySegment(startMs / 1000)}
          // Two routes to one panel: mobile switches to it, desktop reveals it
          // in the side column, which may have been collapsed away.
          onReviewSpeakers={
            isMobile
              ? () => setActivePanel("speakers")
              : () => setSpeakerPanelCollapsed(false)
          }
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
