"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import { ArrowLeft, MessageSquare } from "lucide-react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import ChatPanel from "@/components/ChatPanel";
import RecordingSpeakers from "@/components/speakers/RecordingSpeakers";
import { SpeakerTurnHighlightProvider } from "@/components/speakers/SpeakerTurnHighlight";
import CapturesGallery from "@/components/notes/CapturesGallery";
import RecordingMyNotes from "@/components/notes/RecordingMyNotes";
import RecordingNoteComposer from "@/components/notes/RecordingNoteComposer";
import { NoteFilesProvider } from "@/components/notes/NoteFiles";
import MeetingEdgePanel from "@/components/MeetingEdgePanel";
import RecordingCaptureSlot from "@/components/RecordingCaptureSlot";
import ExportModal from "@/components/ExportModal";
import { RecordingStatus } from "@/types";
import { useRef } from "react";
import { useNavigationStore } from "@/lib/store";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import { HeadNoteOwnerProvider } from "@/lib/notes/HeadNoteOwner";

import { useRecordingDetail } from "./_hooks/useRecordingDetail";
import { getAutoSpeakerReplacementName } from "./_hooks/recordingDetailUtils";
import RecordingMainContent from "./_components/RecordingMainContent";
import RecordingSideColumn, { RecordingSideRail } from "./_components/RecordingSideColumn";

export const dynamic = "force-dynamic";

interface PageProps {
  params: Promise<{ id: string }>;
}

export default function RecordingPage({ params }: PageProps) {
  const liveNoteBoxRef = useRef<HTMLTextAreaElement>(null);
  const capture = useCapture();
  const detail = useRecordingDetail({ params, capture });
  const isSpeakerPanelCollapsed = useNavigationStore(
    (state) => state.isSpeakerPanelCollapsed,
  );
  const isChatPanelCollapsed = useNavigationStore(
    (state) => state.isChatPanelCollapsed,
  );

  const {
    recording,
    audioClock,
    timelineNotes,
    noteEdits,
    globalSpeakers,
    loading,
    meetingEdgeContextLevel,
    isInFlightRecording,
    liveTranscript,
    usesInFlightTranscript,
    transcriptIsAuthoritative,
    hasRetainedPublication,
    canPlayback: detailCanPlayback,
    canExport: detailCanExport,
    transcriptSegments,
    deferredTranscriptUtteranceIds,
    speakerMap,
    speakerColors,
    history,
    future,
    isUndoing,
    isGeneratingNotes,
    notesHistory,
    notesFuture,
    audioRef,
    currentTime,
    isPlaying,
    setIsPlaying,
    isEditingTitle,
    setIsEditingTitle,
    titleValue,
    setTitleValue,
    activePanel,
    setActivePanel,
    setChatPanelHeight,
    compactChatPanelHeight,
    isCompact,
    showExportModal,
    setShowExportModal,
    isMobile,
    isMobileChatOpen,
    setIsMobileChatOpen,
    isMobileHeaderActionsOpen,
    setIsMobileHeaderActionsOpen,
    setIsPanelResizing,
    navigateToRecordings,
    fetchRecording,
    refreshRecordingView,
    refreshPeople,
    handleTimeUpdate,
    handlePlaySegment,
    handlePause,
    handleResume,
    handleUndo,
    handleRedo,
    handleRenameSpeaker,
    handleUpdateSegmentSpeaker,
    handleUpdateSegmentText,
    handleGlobalFindAndReplace,
    handleTitleSubmit,
    handleColorChange,
    handleGenerateNotes,
    handleNotesChange,
    handleNotesUndo,
    handleNotesRedo,
    handleMeetingEdgeFocusChange,
    handleMeetingEdgeContextLevelChange,
    handleExport,
    setActiveTranscriptEditId,
  } = detail;

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-contrast-helper">
        Loading...
      </div>
    );
  }

  if (!recording) {
    return null;
  }

  const isLocalCaptureActive = capture.recordingId === recording.id &&
    (capture.status === "recording" || capture.status === "paused" || capture.status === "finalizing");
  const showCaptureSlot = isInFlightRecording || isLocalCaptureActive;
  const sideColumnCollapsed = isSpeakerPanelCollapsed && (showCaptureSlot || isChatPanelCollapsed);
  const canPlayback = detailCanPlayback && !isLocalCaptureActive;
  const canExport = detailCanExport && !isLocalCaptureActive;

  // Renaming a speaker propagates into the generated notes (shared by the
  // desktop side panel and the mobile Speakers tab).
  const handleSpeakerRenamed = async (oldName: string, newName: string) => {
    if (recording?.transcript?.notes) {
      await handleGlobalFindAndReplace(
        oldName,
        getAutoSpeakerReplacementName(newName),
        { caseSensitive: true },
      );
    }
  };

  const speakersContent = <RecordingSpeakers
    recording={recording}
    liveTranscript={usesInFlightTranscript ? liveTranscript : undefined}
    speakers={usesInFlightTranscript ? liveTranscript.speakers : recording.speakers || []}
    segments={transcriptSegments}
    onPlaySegment={handlePlaySegment}
    disablePlayback={!canPlayback}
    recordingId={recording.id}
    speakerColors={speakerColors}
    onColorChange={handleColorChange}
    currentTime={currentTime}
    isPlaying={isPlaying}
    onPause={handlePause}
    onResume={handleResume}
    onRefresh={refreshRecordingView}
    globalSpeakers={globalSpeakers}
    onSpeakerRenamed={handleSpeakerRenamed}
    onPeopleChanged={refreshPeople}
    readOnly={!isLiveCaptureInProgress(recording) && (showCaptureSlot ||
      (hasRetainedPublication && (!transcriptIsAuthoritative || deferredTranscriptUtteranceIds.length > 0)))}
  />;
  const showTranscript = () => {
    setActivePanel("transcript");
    requestAnimationFrame(() => document.getElementById("panel-transcript")?.focus({ preventScroll: true }));
  };
  const myNotesContent = <RecordingMyNotes
    recording={recording}
    timelineNotes={timelineNotes}
    segments={transcriptSegments}
    noteEdits={noteEdits}
    onPlay={handlePlaySegment}
    disablePlayback={!canPlayback}
    onShowTranscript={showTranscript}
    className="bg-surface-inset"
  />;
  const meetingEdgeContent = <MeetingEdgePanel
    readOnly={!isLiveCaptureInProgress(recording)}
    payload={recording.transcript?.meeting_edge_payload}
    focusText={recording.transcript?.meeting_edge_focus}
    status={recording.transcript?.meeting_edge_status}
    onSaveFocus={handleMeetingEdgeFocusChange}
    contextLevel={meetingEdgeContextLevel}
    onSaveContextLevel={handleMeetingEdgeContextLevelChange}
  />;

  const mainContent = (
    <RecordingMainContent
      liveTranscript={usesInFlightTranscript ? liveTranscript : undefined}
      liveNoteBoxRef={liveNoteBoxRef}
      key={recording.id}
      recording={recording}
      inFlight={showCaptureSlot}
      canPlayback={canPlayback}
      canExport={canExport}
      transcriptIsAuthoritative={transcriptIsAuthoritative}
      hasRetainedPublication={hasRetainedPublication}
      speakersContent={speakersContent}
      myNotesContent={myNotesContent}
      meetingEdgeContent={meetingEdgeContent}
      playerSlot={showCaptureSlot ? <RecordingCaptureSlot recording={recording} onDiscarded={navigateToRecordings} /> : undefined}
      inFlightContent={<RecordingNoteComposer recording={recording} segments={transcriptSegments}
        audioClock={audioClock} boxRef={liveNoteBoxRef} />}
      timelineNotes={timelineNotes}
      noteEdits={noteEdits}
      isMobile={isMobile}
      activePanel={activePanel}
      setActivePanel={setActivePanel}
      isEditingTitle={isEditingTitle}
      titleValue={titleValue}
      isMobileHeaderActionsOpen={isMobileHeaderActionsOpen}
      currentTime={currentTime}
      isPlaying={isPlaying}
      audioRef={audioRef}
      setTitleValue={setTitleValue}
      setIsEditingTitle={setIsEditingTitle}
      setIsMobileHeaderActionsOpen={setIsMobileHeaderActionsOpen}
      setIsPlaying={setIsPlaying}
      onTitleSubmit={handleTitleSubmit}
      onTimeUpdate={handleTimeUpdate}
      onBack={() => navigateToRecordings()}
      transcriptSegments={transcriptSegments}
      speakerMap={speakerMap}
      speakerColors={speakerColors}
      globalSpeakers={globalSpeakers}
      canUndo={history.length > 0 && !isUndoing}
      canRedo={future.length > 0 && !isUndoing}
      deferredTranscriptUtteranceIds={deferredTranscriptUtteranceIds}
      onPlaySegment={handlePlaySegment}
      onPause={handlePause}
      onResume={handleResume}
      onRenameSpeaker={handleRenameSpeaker}
      onColorChange={handleColorChange}
      onRefresh={refreshRecordingView}
      onSpeakerRenamed={handleSpeakerRenamed}
      onUpdateSegmentSpeaker={handleUpdateSegmentSpeaker}
      onUpdateSegmentText={handleUpdateSegmentText}
      onFindAndReplace={handleGlobalFindAndReplace}
      onUndo={handleUndo}
      onRedo={handleRedo}
      onActiveEditUtteranceChange={setActiveTranscriptEditId}
      onExport={() => { if (canExport) setShowExportModal(true); }}
      isGeneratingNotes={
        isGeneratingNotes || recording.transcript?.notes_status === "generating"
      }
      notesCanUndo={notesHistory.length > 0}
      notesCanRedo={notesFuture.length > 0}
      onNotesChange={handleNotesChange}
      onGenerateNotes={handleGenerateNotes}
      onNotesUndo={handleNotesUndo}
      onNotesRedo={handleNotesRedo}
    />
  );

  return (
    // Notes' files open one viewer, from a note's line or the Captures tab.
    <SpeakerTurnHighlightProvider key={recording.id}>
    <HeadNoteOwnerProvider key={recording.id} recordingId={recording.id} timelineNotes={timelineNotes}>
    <NoteFilesProvider
      recordingId={recording.id}
      notes={timelineNotes}
      noteEdits={noteEdits}
      onPlay={canPlayback ? (seconds) => handlePlaySegment(seconds) : undefined}
      onChanged={fetchRecording}
    >
    <div className="h-full flex flex-col overflow-hidden">
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {isMobile ? (
          // The chat button below floats over whichever tab is open, so the
          // tabs' scroll regions (pb-clear-floating-*) end with this much
          // room: the button's 3.5rem, its 1rem offset, a 0.5rem gap and the
          // safe area it also clears.
          <SurfaceBase data-ui-fill="card" kind="pane" className="flex h-full flex-1 min-w-0 flex-col bg-surface-card [--floating-action-clearance:calc(env(safe-area-inset-bottom)+5rem)]">
            <div className="min-h-0 flex-1">{mainContent}</div>

            {!showCaptureSlot && !isMobileChatOpen && (
              <div className="pointer-events-none fixed bottom-[calc(env(safe-area-inset-bottom)+1rem)] right-4 z-[var(--z-sticky)]">
                <ButtonBase
                  data-ui-fill="action"
                  data-ui-hover="action-hover" chrome="action"
                  onClick={() => setIsMobileChatOpen(true)}
                  className="pointer-events-auto inline-flex h-14 w-14 items-center justify-center rounded-full bg-action text-action-on shadow-float transition-colors hover:bg-action-hover"
                  title="Open Meeting Chat"
                  aria-label="Open Meeting Chat"
                >
                  <MessageSquare className="h-6 w-6" />
                </ButtonBase>
              </div>
            )}

            {/* Mobile Chat Full-Screen Modal */}
            {!showCaptureSlot && isMobileChatOpen && (
              <SurfaceBase data-ui-fill="card" kind="pane" className="fixed inset-0 z-[var(--z-modal)] flex h-dvh flex-col bg-surface-card animate-in slide-in-from-bottom">
                <header className="flex shrink-0 items-center justify-between border-b-2 border-surface-border bg-surface-inset px-4 pb-3 pt-[calc(env(safe-area-inset-top)+0.75rem)]">
                  <h2 className="text-lg font-semibold text-foreground flex items-center gap-2">
                    <MessageSquare className="w-5 h-5 text-action-text" />
                    Meeting Chat
                  </h2>
                  <ButtonBase
                    onClick={() => setIsMobileChatOpen(false)}
                    className="inline-flex items-center gap-2 rounded-lg px-2 py-2 text-sm font-medium text-contrast-helper transition-colors hover:bg-surface-inset hover:text-foreground"
                    title="Back to meeting"
                    aria-label="Back to meeting"
                  >
                    <ArrowLeft className="h-5 w-5" />
                    <span>Back</span>
                  </ButtonBase>
                </header>
                <div className="flex-1 min-h-0 flex flex-col overflow-hidden pb-[env(safe-area-inset-bottom)]">
                  <ChatPanel onNotesUpdate={fetchRecording} />
                </div>
              </SurfaceBase>
            )}
          </SurfaceBase>
        ) : (
          <div className="flex h-full flex-1 min-w-0">
            <PanelGroup
              data-ui-resize-group="horizontal"
              direction="horizontal"
              autoSaveId={`recording-layout-persistence-${isCompact ? "compact" : "comfortable"}`}
              className="h-full flex-1 min-w-0"
            >
              <Panel id="recording-main" order={1} defaultSize={isCompact ? 78 : 75} minSize={30}>
                {mainContent}
              </Panel>

              {!sideColumnCollapsed && (
                <>
                  <PanelResizeHandle
                    data-ui-resize-handle="horizontal"
                    className="bg-surface-inset border-l border-control-border w-2 hover:bg-action transition-colors flex items-center justify-center group"
                    onDragging={setIsPanelResizing}
                  >
                    <div data-ui-resize-grip="" className="h-8 w-1 bg-surface-card rounded-full group-hover:bg-surface-card transition-colors" />
                  </PanelResizeHandle>

                  {/* Sidebar: Speaker and Chat panels, each independently collapsible */}
                  <Panel
                    id="recording-side"
                    order={2}
                    defaultSize={isCompact ? 22 : 25}
                    minSize={18}
                  >
                    <RecordingSideColumn
                      allowChat={!showCaptureSlot}
                      speakers={
                        speakersContent
                      }
                      myNotes={
                        myNotesContent
                      }
                      meetingEdge={meetingEdgeContent}
                      captures={
                        <CapturesGallery
                          recordingId={recording.id}
                          timelineNotes={timelineNotes}
                          segments={transcriptSegments}
                          noteEdits={noteEdits}
                          onShowTranscript={() => setActivePanel("transcript")}
                          className="h-full bg-surface-inset"
                        />
                      }
                      chatPanelHeight={compactChatPanelHeight}
                      onChatPanelHeightChange={setChatPanelHeight}
                      onNotesUpdate={fetchRecording}
                      onResizeDragging={setIsPanelResizing}
                    />
                  </Panel>
                </>
              )}
            </PanelGroup>

            {sideColumnCollapsed && <RecordingSideRail allowChat={!showCaptureSlot} />}
          </div>
        )}
      </div>

      {canExport && <ExportModal
        isOpen={showExportModal}
        onClose={() => setShowExportModal(false)}
        onExport={handleExport}
        hasNotes={!!recording?.transcript?.notes}
        hasTimelineNotes={(timelineNotes?.length ?? 0) > 0}
        hasAnalytics={recording?.status === RecordingStatus.PROCESSED}
        hasAudio={recording?.has_audio !== false}
      />}
    </div>
    </NoteFilesProvider>
    </HeadNoteOwnerProvider>
    </SpeakerTurnHighlightProvider>
  );
}
