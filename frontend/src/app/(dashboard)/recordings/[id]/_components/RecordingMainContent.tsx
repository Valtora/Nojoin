"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import { ChevronsLeft, ChevronsRight } from "lucide-react";
import {
  useEffect,
  useRef,
  type KeyboardEvent,
  type RefObject,
} from "react";

import { useNavigationStore, type ActivePanel } from "@/lib/store";
import {
  GlobalSpeaker,
  Recording,
  TimelineNote,
  TranscriptSegment,
  TranscriptSpeakerAssignment,
} from "@/types";

import SpeakerPanel from "@/components/SpeakerPanel";
import CapturesGallery from "@/components/notes/CapturesGallery";
import MyNotesList from "@/components/notes/MyNotesList";
import type { NoteEdits } from "@/lib/notes/useNoteEdits";

import RecordingHeader from "./RecordingHeader";
import LanguageNoticeBanner from "./LanguageNoticeBanner";
import TranscriptSection from "./TranscriptSection";
import NotesSection from "./NotesSection";
import DocumentsSection from "./DocumentsSection";
import AnalyticsSection from "./AnalyticsSection";
import { useScrollEdges } from "../_hooks/useScrollEdges";

interface RecordingMainContentProps {
  recording: Recording;
  /** The recording's notes as last listed; null while they load. */
  timelineNotes: readonly TimelineNote[] | null;
  /** The page's note edits, shared by the transcript and My notes. */
  noteEdits: NoteEdits;
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

// On mobile the row scrolls sideways, so each tab keeps its full label.
const tabClassName = (active: boolean, isMobile: boolean) =>
  `flex ${isMobile ? "flex-1 shrink-0" : "min-w-0"} items-center justify-center border-b-2 px-3 py-2.5 text-[13px] font-medium transition-colors md:px-6 md:py-3 md:text-sm ${
    active
      ? "border-action text-action-text bg-surface-card"
      : "border-transparent text-contrast-helper hover:text-foreground hover:bg-surface-inset"
  }`;

// On desktop Speakers and My notes live in the side column, so they have no tab.
const TABS: { panel: ActivePanel; label: string }[] = [
  { panel: "transcript", label: "Transcript" },
  { panel: "notes", label: "Notes" },
  { panel: "documents", label: "Documents" },
  { panel: "analytics", label: "Analytics" },
];
const MOBILE_TABS: { panel: ActivePanel; label: string }[] = [
  ...TABS.slice(0, 2),
  { panel: "my-notes", label: "My notes" },
  { panel: "captures", label: "Captures" },
  ...TABS.slice(2),
  { panel: "speakers", label: "Speakers" },
];

// Marks a side of the mobile tab row with tabs scrolled out of view. Decorative:
// the tabs themselves stay reachable, and a tap passes through to the one below.
// The row is inset over card, so the fade stacks both to match it.
function MoreTabsHint({ side }: { side: "left" | "right" }) {
  const Icon = side === "left" ? ChevronsLeft : ChevronsRight;
  return (
    <div
      aria-hidden="true"
      data-testid={`more-tabs-${side}`}
      className={`pointer-events-none absolute top-0 bottom-0.5 z-10 from-surface-card from-50% to-transparent ${side === "left" ? "left-0 bg-linear-to-r" : "right-0 bg-linear-to-l"}`}
    >
      <div
        className={`flex h-full w-8 items-center from-surface-inset from-50% to-transparent px-1 text-contrast-helper ${side === "left" ? "justify-start bg-linear-to-r" : "justify-end bg-linear-to-l"}`}
      >
        <Icon className="h-4 w-4" />
      </div>
    </div>
  );
}

export default function RecordingMainContent({
  recording,
  timelineNotes,
  noteEdits,
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
  const setSideTab = useNavigationStore((state) => state.setSideTab);
  const tablistRef = useRef<HTMLDivElement>(null);
  const tabsOutOfView = useScrollEdges(tablistRef, isMobile);
  const tabs = isMobile ? MOBILE_TABS : TABS;
  // The tab that Tab and the arrow keys start from: the current panel's, or the
  // first when it has no tab here (desktop holds "speakers" for one render
  // before useRecordingDetail falls back to the transcript).
  const current = tabs.some(({ panel }) => panel === activePanel)
    ? activePanel
    : tabs[0].panel;

  // Arrow keys move between tabs and open them, wrapping at the ends; Home and
  // End jump to the first and last (the WAI-ARIA tabs pattern).
  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = tabs.findIndex(({ panel }) => panel === current);
    const targets: Record<string, number> = {
      ArrowLeft: index - 1,
      ArrowRight: index + 1,
      Home: 0,
      End: tabs.length - 1,
    };
    if (!(event.key in targets)) return;
    event.preventDefault();
    const { panel } = tabs[(targets[event.key] + tabs.length) % tabs.length];
    setActivePanel(panel);
    tablistRef.current?.querySelector<HTMLElement>(`#tab-${panel}`)?.focus();
  };

  // A jump from My notes opens the transcript, and focus goes with it once
  // it shows: the list's row is hidden from then on.
  const showTranscript = () => {
    setActivePanel("transcript");
    requestAnimationFrame(() =>
      document.getElementById("panel-transcript")?.focus({ preventScroll: true }),
    );
  };

  // A tab chosen other than by a tap on the row (the page reopening on the
  // panel last used, Analytics' link to the speakers) may sit past its edge.
  useEffect(() => {
    if (!isMobile) return;
    tablistRef.current
      ?.querySelector(`#tab-${activePanel}`)
      ?.scrollIntoView({ inline: "nearest", block: "nearest" });
  }, [isMobile, activePanel]);

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
      />

      {/* Above the tabs, so it is in view from every panel: the language
          affects the notes as well as the transcript. */}
      <LanguageNoticeBanner
        recording={recording}
        onAnswered={() =>
          tablistRef.current?.querySelector<HTMLElement>(`#tab-${current}`)?.focus()
        }
      />

      {/* Panel Tabs. On mobile, My notes and Speakers have tabs too and the
          row scrolls sideways; on desktop both live in the side column. */}
      <div className="relative shrink-0 bg-surface-inset">
        {tabsOutOfView.left && <MoreTabsHint side="left" />}
        {tabsOutOfView.right && <MoreTabsHint side="right" />}
        <div
          ref={tablistRef}
          role="tablist"
          aria-label="Meeting panels"
          className={`${isMobile ? "flex overflow-x-auto" : "grid grid-cols-4"} border-b-2 border-surface-border`}
        >
          {tabs.map(({ panel, label }) => (
            <ButtonBase
              key={panel}
              type="button"
              role="tab"
              id={`tab-${panel}`}
              aria-selected={activePanel === panel}
              aria-controls={`panel-${panel}`}
              tabIndex={panel === current ? 0 : -1}
              onClick={() => setActivePanel(panel)}
              onKeyDown={onTabKeyDown}
              className={tabClassName(activePanel === panel, isMobile)}
            >
              <span className="truncate">{label}</span>
            </ButtonBase>
          ))}
        </div>
      </div>

      {/* Panel Content */}
      <SurfaceBase kind="pane" className="flex-1 flex flex-col bg-surface-card overflow-hidden min-h-0 h-full relative">
        <TranscriptSection
          active={activePanel === "transcript"}
          recording={recording}
          timelineNotes={timelineNotes}
          noteEdits={noteEdits}
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
          onRefresh={onRefresh}
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
              : () => {
                  setSideTab("speakers");
                  setSpeakerPanelCollapsed(false);
                }
          }
        />

        {isMobile && (
          <div
            role="tabpanel"
            id="panel-my-notes"
            aria-labelledby="tab-my-notes"
            className={`absolute inset-0 flex flex-col ${activePanel === "my-notes" ? "z-10 visible" : "z-0 invisible"}`}
          >
            <MyNotesList
              recordingId={recording.id}
              timelineNotes={timelineNotes}
              segments={transcriptSegments}
              noteEdits={noteEdits}
              onPlay={onPlaySegment}
              onShowTranscript={showTranscript}
              className="flex-1"
            />
          </div>
        )}

        {isMobile && (
          <div
            role="tabpanel"
            id="panel-captures"
            aria-labelledby="tab-captures"
            className={`absolute inset-0 flex flex-col ${activePanel === "captures" ? "z-10 visible" : "z-0 invisible"}`}
          >
            <CapturesGallery
              recordingId={recording.id}
              timelineNotes={timelineNotes}
              segments={transcriptSegments}
              noteEdits={noteEdits}
              onShowTranscript={showTranscript}
              className="flex-1"
            />
          </div>
        )}

        {isMobile && (
          <div
            role="tabpanel"
            id="panel-speakers"
            aria-labelledby="tab-speakers"
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
      </SurfaceBase>
    </div>
  );
}
