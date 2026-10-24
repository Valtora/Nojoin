"use client";

import { ArrowLeft, MessageSquare } from "lucide-react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import ChatPanel from "@/components/ChatPanel";
import SpeakerPanel from "@/components/SpeakerPanel";
import CapturesGallery from "@/components/notes/CapturesGallery";
import MyNotesList from "@/components/notes/MyNotesList";
import { NoteFilesProvider } from "@/components/notes/NoteFiles";
import RecordingStatusDisplay from "@/components/RecordingStatusDisplay";
import ExportModal from "@/components/ExportModal";
import { RecordingStatus } from "@/types";
import { useNavigationStore } from "@/lib/store";

import { useRecordingDetail } from "./_hooks/useRecordingDetail";
import { getAutoSpeakerReplacementName } from "./_hooks/recordingDetailUtils";
import RecordingMainContent from "./_components/RecordingMainContent";
import RecordingSideColumn, { RecordingSideRail } from "./_components/RecordingSideColumn";

export const dynamic = "force-dynamic";

interface PageProps {
  params: Promise<{ id: string }>;
}

export default function RecordingPage({ params }: PageProps) {
  const detail = useRecordingDetail({ params });
  const isSpeakerPanelCollapsed = useNavigationStore(
    (state) => state.isSpeakerPanelCollapsed,
  );
  const isChatPanelCollapsed = useNavigationStore(
    (state) => state.isChatPanelCollapsed,
  );

  const {
    recording,
    setRecording,
    audioClock,
    timelineNotes,
    noteEdits,
    globalSpeakers,
    loading,
    meetingEdgeEnabled,
    meetingEdgeContextLevel,
    isInFlightRecording,
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

  const mainContent = (
    <RecordingMainContent
      recording={recording}
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
      setRecording={setRecording}
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
      onExport={() => setShowExportModal(true)}
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
    <NoteFilesProvider
      recordingId={recording.id}
      notes={timelineNotes}
      onPlay={isInFlightRecording ? undefined : (seconds) => handlePlaySegment(seconds)}
      onChanged={fetchRecording}
    >
    <div className="h-full flex flex-col overflow-hidden">
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {isInFlightRecording ? (
          <div className="h-full flex-1 min-w-0 overflow-y-auto bg-surface-page">
            <RecordingStatusDisplay
              recording={recording}
              onSaveMeetingEdgeFocus={handleMeetingEdgeFocusChange}
              meetingEdgeContextLevel={meetingEdgeContextLevel}
              onSaveMeetingEdgeContextLevel={
                handleMeetingEdgeContextLevelChange
              }
              showMeetingEdge={meetingEdgeEnabled}
              onBack={navigateToRecordings}
              onDiscarded={navigateToRecordings}
              showMobileBackButton={isMobile}
              audioClock={audioClock}
              timelineNotes={timelineNotes}
            />
          </div>
        ) : isMobile ? (
          // The chat button below floats over whichever tab is open, so the
          // tabs' scroll regions (pb-clear-floating-*) end with this much
          // room: the button's 3.5rem, its 1rem offset, a 0.5rem gap and the
          // safe area it also clears.
          <div className="flex h-full flex-1 min-w-0 flex-col bg-surface-card [--floating-action-clearance:calc(env(safe-area-inset-bottom)+5rem)]">
            <div className="min-h-0 flex-1">{mainContent}</div>

            {!isMobileChatOpen && (
              <div className="pointer-events-none fixed bottom-[calc(env(safe-area-inset-bottom)+1rem)] right-4 z-[var(--z-sticky)]">
                <button
                  onClick={() => setIsMobileChatOpen(true)}
                  className="pointer-events-auto inline-flex h-14 w-14 items-center justify-center rounded-full bg-action text-action-on shadow-float transition-colors hover:bg-action-hover"
                  title="Open Meeting Chat"
                  aria-label="Open Meeting Chat"
                >
                  <MessageSquare className="h-6 w-6" />
                </button>
              </div>
            )}

            {/* Mobile Chat Full-Screen Modal */}
            {isMobileChatOpen && (
              <div className="fixed inset-0 z-[var(--z-modal)] flex h-dvh flex-col bg-surface-card animate-in slide-in-from-bottom">
                <header className="flex shrink-0 items-center justify-between border-b-2 border-surface-border bg-surface-inset px-4 pb-3 pt-[calc(env(safe-area-inset-top)+0.75rem)]">
                  <h2 className="text-lg font-semibold text-foreground flex items-center gap-2">
                    <MessageSquare className="w-5 h-5 text-action-text" />
                    Meeting Chat
                  </h2>
                  <button
                    onClick={() => setIsMobileChatOpen(false)}
                    className="inline-flex items-center gap-2 rounded-lg px-2 py-2 text-sm font-medium text-contrast-helper transition-colors hover:bg-surface-inset hover:text-foreground"
                    title="Back to meeting"
                    aria-label="Back to meeting"
                  >
                    <ArrowLeft className="h-5 w-5" />
                    <span>Back</span>
                  </button>
                </header>
                <div className="flex-1 min-h-0 flex flex-col overflow-hidden pb-[env(safe-area-inset-bottom)]">
                  <ChatPanel onNotesUpdate={fetchRecording} />
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex h-full flex-1 min-w-0">
            <PanelGroup
              direction="horizontal"
              autoSaveId={`recording-layout-persistence-${isCompact ? "compact" : "comfortable"}`}
              className="h-full flex-1 min-w-0"
            >
              <Panel id="recording-main" order={1} defaultSize={isCompact ? 78 : 75} minSize={30}>
                {mainContent}
              </Panel>

              {!(isSpeakerPanelCollapsed && isChatPanelCollapsed) && (
                <>
                  <PanelResizeHandle
                    className="bg-surface-inset border-l border-control-border w-2 hover:bg-action transition-colors flex items-center justify-center group"
                    onDragging={setIsPanelResizing}
                  >
                    <div className="h-8 w-1 bg-surface-card rounded-full group-hover:bg-surface-card transition-colors" />
                  </PanelResizeHandle>

                  {/* Sidebar: Speaker and Chat panels, each independently collapsible */}
                  <Panel
                    id="recording-side"
                    order={2}
                    defaultSize={isCompact ? 22 : 25}
                    minSize={18}
                  >
                    <RecordingSideColumn
                      speakers={
                        <SpeakerPanel
                          speakers={recording.speakers || []}
                          segments={transcriptSegments}
                          onPlaySegment={handlePlaySegment}
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
                        />
                      }
                      myNotes={
                        <MyNotesList
                          recordingId={recording.id}
                          timelineNotes={timelineNotes}
                          segments={transcriptSegments}
                          noteEdits={noteEdits}
                          onPlay={handlePlaySegment}
                          onShowTranscript={() => setActivePanel("transcript")}
                          className="h-full bg-surface-inset"
                        />
                      }
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

            {isSpeakerPanelCollapsed && isChatPanelCollapsed && <RecordingSideRail />}
          </div>
        )}
      </div>

      <ExportModal
        isOpen={showExportModal}
        onClose={() => setShowExportModal(false)}
        onExport={handleExport}
        hasNotes={!!recording?.transcript?.notes}
        hasTimelineNotes={(timelineNotes?.length ?? 0) > 0}
        hasAnalytics={recording?.status === RecordingStatus.PROCESSED}
        hasAudio={recording?.has_audio !== false}
      />
    </div>
    </NoteFilesProvider>
  );
}
