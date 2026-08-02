"use client";

import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  MessageSquare,
  Users,
} from "lucide-react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import ChatPanel from "@/components/ChatPanel";
import SpeakerPanel from "@/components/SpeakerPanel";
import RecordingStatusDisplay from "@/components/RecordingStatusDisplay";
import ExportModal from "@/components/ExportModal";
import IconButton from "@/components/ui/IconButton";
import { useNavigationStore } from "@/lib/store";

import { useRecordingDetail } from "./_hooks/useRecordingDetail";
import { getAutoSpeakerReplacementName } from "./_hooks/recordingDetailUtils";
import RecordingMainContent from "./_components/RecordingMainContent";

export const dynamic = "force-dynamic";

interface PageProps {
  params: Promise<{ id: string }>;
}

export default function RecordingPage({ params }: PageProps) {
  const detail = useRecordingDetail({ params });
  const isSpeakerPanelCollapsed = useNavigationStore(
    (state) => state.isSpeakerPanelCollapsed,
  );
  const setSpeakerPanelCollapsed = useNavigationStore(
    (state) => state.setSpeakerPanelCollapsed,
  );
  const isChatPanelCollapsed = useNavigationStore(
    (state) => state.isChatPanelCollapsed,
  );
  const setChatPanelCollapsed = useNavigationStore(
    (state) => state.setChatPanelCollapsed,
  );

  const {
    recording,
    setRecording,
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
    handleProcessingNotesChange,
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
    <div className="h-full flex flex-col overflow-hidden">
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {isInFlightRecording ? (
          <div className="h-full flex-1 min-w-0 overflow-y-auto bg-surface-page">
            <RecordingStatusDisplay
              recording={recording}
              onSaveProcessingNotes={handleProcessingNotesChange}
              onSaveMeetingEdgeFocus={handleMeetingEdgeFocusChange}
              meetingEdgeContextLevel={meetingEdgeContextLevel}
              onSaveMeetingEdgeContextLevel={
                handleMeetingEdgeContextLevelChange
              }
              showMeetingEdge={meetingEdgeEnabled}
              onBack={navigateToRecordings}
              onDiscarded={navigateToRecordings}
              showMobileBackButton={isMobile}
            />
          </div>
        ) : isMobile ? (
          <div className="flex h-full flex-1 min-w-0 flex-col bg-surface-card">
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
                    {isSpeakerPanelCollapsed ? (
                      <div className="flex h-full flex-col">
                        <button
                          onClick={() => setSpeakerPanelCollapsed(false)}
                          className="flex h-9 w-full shrink-0 items-center gap-2 border-b border-surface-border bg-surface-card px-3 text-xs font-medium text-contrast-helper transition-colors hover:bg-surface-inset hover:text-foreground"
                          aria-expanded={false}
                          aria-controls="speaker-panel"
                        >
                          <Users className="h-3.5 w-3.5" />
                          Speakers
                          <ChevronDown className="ml-auto h-3.5 w-3.5" />
                        </button>
                        <div className="min-h-0 flex-1">
                          <ChatPanel
                            onNotesUpdate={fetchRecording}
                            onCollapse={() => setChatPanelCollapsed(true)}
                          />
                        </div>
                      </div>
                    ) : isChatPanelCollapsed ? (
                      <div className="flex h-full flex-col">
                        <div className="min-h-0 flex-1">
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
                            onCollapse={() => setSpeakerPanelCollapsed(true)}
                          />
                        </div>
                        <button
                          onClick={() => setChatPanelCollapsed(false)}
                          className="flex h-9 w-full shrink-0 items-center gap-2 border-t border-surface-border bg-surface-card px-3 text-xs font-medium text-contrast-helper transition-colors hover:bg-surface-inset hover:text-foreground"
                          aria-expanded={false}
                          aria-controls="meeting-chat"
                        >
                          <MessageSquare className="h-3.5 w-3.5" />
                          Chat
                          <ChevronUp className="ml-auto h-3.5 w-3.5" />
                        </button>
                      </div>
                    ) : (
                      <PanelGroup
                        direction="vertical"
                        onLayout={(sizes) => {
                          if (sizes.length === 2) {
                            setChatPanelHeight(sizes[1]);
                          }
                        }}
                      >
                        <Panel
                          id="side-speakers"
                          order={1}
                          defaultSize={100 - compactChatPanelHeight}
                          minSize={20}
                        >
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
                            onCollapse={() => setSpeakerPanelCollapsed(true)}
                          />
                        </Panel>

                        <PanelResizeHandle
                          className="bg-surface-inset border-t border-control-border h-2 hover:bg-action transition-colors flex items-center justify-center group"
                          onDragging={setIsPanelResizing}
                        >
                          <div className="w-8 h-1 bg-surface-card rounded-full group-hover:bg-surface-card transition-colors" />
                        </PanelResizeHandle>

                        <Panel
                          id="side-chat"
                          order={2}
                          defaultSize={compactChatPanelHeight}
                          minSize={18}
                        >
                          <ChatPanel
                            onNotesUpdate={fetchRecording}
                            onCollapse={() => setChatPanelCollapsed(true)}
                          />
                        </Panel>
                      </PanelGroup>
                    )}
                  </Panel>
                </>
              )}
            </PanelGroup>

            {isSpeakerPanelCollapsed && isChatPanelCollapsed && (
              <div className="flex h-full w-12 shrink-0 flex-col items-center gap-1 border-l border-surface-border bg-surface-inset py-2">
                <IconButton
                  size="sm"
                  icon={<Users />}
                  aria-label="Show speakers panel"
                  title="Show speakers panel"
                  onClick={() => setSpeakerPanelCollapsed(false)}
                />
                <IconButton
                  size="sm"
                  icon={<MessageSquare />}
                  aria-label="Show meeting chat panel"
                  title="Show meeting chat panel"
                  onClick={() => setChatPanelCollapsed(false)}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {/* Export Modal */}
      <ExportModal
        isOpen={showExportModal}
        onClose={() => setShowExportModal(false)}
        onExport={handleExport}
        hasNotes={!!recording?.transcript?.notes}
      />
    </div>
  );
}
