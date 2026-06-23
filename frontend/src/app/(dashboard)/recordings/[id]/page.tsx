"use client";

import { ArrowLeft, MessageSquare } from "lucide-react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import ChatPanel from "@/components/ChatPanel";
import SpeakerPanel from "@/components/SpeakerPanel";
import RecordingStatusDisplay from "@/components/RecordingStatusDisplay";
import ExportModal from "@/components/ExportModal";

import { useRecordingDetail } from "./_hooks/useRecordingDetail";
import { getAutoSpeakerReplacementName } from "./_hooks/recordingDetailUtils";
import RecordingMainContent from "./_components/RecordingMainContent";

export const dynamic = "force-dynamic";

interface PageProps {
  params: Promise<{ id: string }>;
}

export default function RecordingPage({ params }: PageProps) {
  const detail = useRecordingDetail({ params });

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
      <div className="h-full flex items-center justify-center text-gray-500">
        Loading...
      </div>
    );
  }

  if (!recording) {
    return null;
  }

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
      onUpdateSegmentSpeaker={handleUpdateSegmentSpeaker}
      onUpdateSegmentText={handleUpdateSegmentText}
      onFindAndReplace={handleGlobalFindAndReplace}
      onUndo={handleUndo}
      onRedo={handleRedo}
      onActiveEditUtteranceChange={setActiveTranscriptEditId}
      onExport={() => setShowExportModal(true)}
      isGeneratingNotes={
        isGeneratingNotes ||
        recording.transcript?.notes_status === "generating"
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
          <div className="h-full flex-1 min-w-0 overflow-y-auto bg-[radial-gradient(circle_at_top_left,_rgba(251,146,60,0.34),_transparent_32%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.26),_transparent_36%),linear-gradient(180deg,_#ffedd5_0%,_#fff7ed_45%,_#ffe4c4_100%)] dark:bg-[radial-gradient(circle_at_top_left,_rgba(251,146,60,0.22),_transparent_30%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.18),_transparent_34%),linear-gradient(180deg,_#0b1220_0%,_#0a0f1c_50%,_#0b1220_100%)]">
            <RecordingStatusDisplay
              recording={recording}
              onSaveProcessingNotes={handleProcessingNotesChange}
              onSaveMeetingEdgeFocus={handleMeetingEdgeFocusChange}
              meetingEdgeContextLevel={meetingEdgeContextLevel}
              onSaveMeetingEdgeContextLevel={handleMeetingEdgeContextLevelChange}
              showMeetingEdge={meetingEdgeEnabled}
              onBack={navigateToRecordings}
              showMobileBackButton={isMobile}
            />
          </div>
        ) : isMobile ? (
          <div className="flex h-full flex-1 min-w-0 flex-col bg-white dark:bg-gray-900">
            <div className="min-h-0 flex-1">
              {mainContent}
            </div>

            {!isMobileChatOpen && (
              <div className="pointer-events-none fixed bottom-[calc(env(safe-area-inset-bottom)+1rem)] right-4 z-40">
                <button
                  onClick={() => setIsMobileChatOpen(true)}
                  className="pointer-events-auto inline-flex h-14 w-14 items-center justify-center rounded-full bg-orange-600 text-white shadow-lg shadow-orange-950/20 transition-colors hover:bg-orange-700"
                  title="Open Meeting Chat"
                  aria-label="Open Meeting Chat"
                >
                  <MessageSquare className="h-6 w-6" />
                </button>
              </div>
            )}

            {/* Mobile Chat Full-Screen Modal */}
            {isMobileChatOpen && (
              <div className="fixed inset-0 z-50 flex h-dvh flex-col bg-white animate-in slide-in-from-bottom dark:bg-gray-900">
                <header className="flex shrink-0 items-center justify-between border-b-2 border-gray-200 bg-gray-50 px-4 pb-3 pt-[calc(env(safe-area-inset-top)+0.75rem)] dark:border-gray-800 dark:bg-gray-950">
                  <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                    <MessageSquare className="w-5 h-5 text-orange-500" />
                    Meeting Chat
                  </h2>
                <button
                  onClick={() => setIsMobileChatOpen(false)}
                  className="inline-flex items-center gap-2 rounded-lg px-2 py-2 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-200 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-white"
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
          <PanelGroup
            direction="horizontal"
            autoSaveId={`recording-layout-persistence-${isCompact ? "compact" : "comfortable"}`}
            className="h-full flex-1 min-w-0"
          >
            <Panel defaultSize={isCompact ? 78 : 75} minSize={30}>
              {mainContent}
            </Panel>

            <PanelResizeHandle
              className="bg-gray-200 dark:bg-gray-900 border-l border-gray-400 dark:border-gray-800 w-2 hover:bg-orange-500 dark:hover:bg-orange-500 transition-colors flex items-center justify-center group"
              onDragging={setIsPanelResizing}
            >
              <div className="h-8 w-1 bg-gray-400 dark:bg-gray-600 rounded-full group-hover:bg-white transition-colors" />
            </PanelResizeHandle>

            {/* Sidebar: Stacked Speaker and Chat panels */}
            <Panel defaultSize={isCompact ? 22 : 25} minSize={18}>
              <PanelGroup
                direction="vertical"
                onLayout={(sizes) => {
                  if (sizes.length === 2) {
                    setChatPanelHeight(sizes[1]);
                  }
                }}
              >
                <Panel defaultSize={100 - compactChatPanelHeight} minSize={20}>
                  <SpeakerPanel
                    speakers={recording.speakers || []}
                    speakerNameSuggestions={
                      recording.transcript?.speaker_name_suggestions || []
                    }
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
                    onSpeakerRenamed={async (oldName, newName) => {
                      if (recording?.transcript?.notes) {
                          await handleGlobalFindAndReplace(
                            oldName,
                            getAutoSpeakerReplacementName(newName),
                            { caseSensitive: true },
                          );
                      }
                    }}
                  />
                </Panel>

                <PanelResizeHandle
                  className="bg-gray-200 dark:bg-gray-900 border-t border-gray-400 dark:border-gray-800 h-2 hover:bg-orange-500 dark:hover:bg-orange-500 transition-colors flex items-center justify-center group"
                  onDragging={setIsPanelResizing}
                >
                  <div className="w-8 h-1 bg-gray-400 dark:bg-gray-600 rounded-full group-hover:bg-white transition-colors" />
                </PanelResizeHandle>

                <Panel defaultSize={compactChatPanelHeight} minSize={18}>
                  <ChatPanel onNotesUpdate={fetchRecording} />
                </Panel>
              </PanelGroup>
            </Panel>
          </PanelGroup>
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
