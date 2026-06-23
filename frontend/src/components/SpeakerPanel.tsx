"use client";

import {
  RecordingSpeaker,
  RecordingId,
  SpeakerNameSuggestion,
  TranscriptSegment,
  GlobalSpeaker,
} from "@/types";
import {
  Play,
  Pause,
  ArrowRightToLine,
  User,
  UserCheck,
  Loader2,
  Check,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";
import ContextMenu from "./ContextMenu";
import ConfirmationModal from "./ConfirmationModal";
import VoiceprintModal from "./VoiceprintModal";
import SplitPersonModal from "./people/SplitPersonModal";
import { InlineColorPicker } from "./ColorPicker";
import { useNotificationStore } from "@/lib/notificationStore";
import { getResolvedGlobalSpeakerId } from "@/lib/recordingSpeakerUtils";
import {
  useSpeakerPanelEntries,
  type SpeakerPanelEntry,
} from "./speakers/_hooks/useSpeakerPanelEntries";
import { useSpeakerSnippetPlayback } from "./speakers/_hooks/useSpeakerSnippetPlayback";
import { useSpeakerPanelActions } from "./speakers/_hooks/useSpeakerPanelActions";

interface SpeakerPanelProps {
  speakers: RecordingSpeaker[];
  speakerNameSuggestions?: SpeakerNameSuggestion[];
  segments: TranscriptSegment[];
  onPlaySegment: (time: number, end?: number) => void;
  recordingId: RecordingId;
  speakerColors: Record<string, string>; // Now stores color keys, not full classes
  onColorChange: (speakerLabel: string, colorKey: string) => void;
  currentTime: number;
  isPlaying: boolean;
  onPause: () => void;
  onResume: () => void;
  onRefresh: () => void;
  globalSpeakers: GlobalSpeaker[];
  onSpeakerRenamed?: (oldName: string, newName: string) => Promise<void> | void;
}

export default function SpeakerPanel({
  speakers,
  speakerNameSuggestions = [],
  segments,
  onPlaySegment,
  recordingId,
  speakerColors,
  onColorChange,
  currentTime,
  isPlaying,
  onPause,
  onResume,
  onRefresh,
  globalSpeakers,
  onSpeakerRenamed,
}: SpeakerPanelProps) {
  const { addNotification } = useNotificationStore();
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    speaker: SpeakerPanelEntry;
  } | null>(null);

  const closeContextMenu = () => setContextMenu(null);

  const { speakerEntries } = useSpeakerPanelEntries(
    speakers,
    segments,
    globalSpeakers,
  );

  const { playSnippet: handlePlaySnippet, nextSnippet: handleNextSnippet } =
    useSpeakerSnippetPlayback({
      segments,
      currentTime,
      isPlaying,
      onPlaySegment,
      onPause,
      onResume,
    });

  const actions = useSpeakerPanelActions({
    recordingId,
    onRefresh,
    onSpeakerRenamed,
    closeContextMenu,
  });

  const {
    renamingSpeaker,
    setRenamingSpeaker,
    renameValue,
    setRenameValue,
    startRename: handleRenameStart,
    submitRename: handleRenameSubmit,
    mergingSpeaker,
    setMergingSpeaker,
    mergeTargetLabel,
    setMergeTargetLabel,
    startMerge: handleMergeStart,
    submitMerge: handleMergeSubmit,
    deletingSpeaker,
    setDeletingSpeaker,
    requestDelete: handleDeleteClick,
    confirmDelete,
    splitModalOpen,
    setSplitModalOpen,
    speakerToSplit,
    setSpeakerToSplit,
    startSplit: handleSplitStart,
    extractingVoiceprint,
    voiceprintModalOpen,
    setVoiceprintModalOpen,
    voiceprintExtractResult,
    setVoiceprintExtractResult,
    batchVoiceprintResults,
    setBatchVoiceprintResults,
    createVoiceprint: handleCreateVoiceprint,
    promoteToGlobal: handlePromoteToGlobal,
    resolvingSuggestionId,
    acceptSuggestion: handleAcceptSuggestion,
    rejectSuggestion: handleRejectSuggestion,
  } = actions;

  const pendingSuggestions = useMemo(
    () =>
      speakerNameSuggestions.filter(
        (suggestion) => suggestion.status === "pending",
      ),
    [speakerNameSuggestions],
  );

  const handleContextMenu = (
    e: React.MouseEvent,
    speaker: SpeakerPanelEntry,
  ) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, speaker });
  };

  const handleVoiceprintModalComplete = () => {
    onRefresh();
  };

  return (
    <aside
      id="speaker-panel"
      className="shrink-0 border-l border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 h-full overflow-y-auto"
    >
      {/* Header with batch voiceprint action */}

      <div className="p-2 space-y-2 mt-2">
        {speakerEntries.length === 0 ? (
          <div className="p-4 text-sm text-gray-500 dark:text-gray-400 text-center italic">
            No speakers detected.
          </div>
        ) : (
          speakerEntries.map((entry) => {
            const { speaker } = entry;
            const isRenaming =
              renamingSpeaker?.key === entry.key;
            const isMerging =
              mergingSpeaker?.key === entry.key;

            if (isMerging) {
              const otherSpeakers = speakerEntries.filter(
                (candidate) => candidate.key !== entry.key,
              );
              return (
                <div
                  key={entry.key}
                  className="p-3 rounded-lg bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-800"
                >
                  <p className="text-xs font-semibold text-orange-800 dark:text-orange-200 mb-2">
                    Merge {entry.displayName} into:
                  </p>
                  <select
                    className="w-full text-sm p-1 mb-2 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                    value={mergeTargetLabel}
                    onChange={(e) => setMergeTargetLabel(e.target.value)}
                  >
                    <option value="">Select Speaker...</option>
                    {otherSpeakers.map((candidate) => (
                      <option
                        key={candidate.key}
                        value={candidate.speaker.diarization_label}
                      >
                        {candidate.displayName}
                      </option>
                    ))}
                  </select>
                  <div className="flex gap-2">
                    <button
                      onClick={handleMergeSubmit}
                      disabled={!mergeTargetLabel}
                      className="flex-1 px-2 py-1 bg-orange-600 text-white text-xs rounded hover:bg-orange-700 disabled:opacity-50"
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

            const entryLabelSet = new Set(entry.labels);
            const entrySuggestions = pendingSuggestions.filter((suggestion) =>
              entryLabelSet.has(suggestion.diarization_label),
            );
            const isSpeakerActive = segments.some(
              (segment) =>
                entryLabelSet.has(segment.speaker) &&
                currentTime >= segment.start &&
                currentTime < segment.end,
            );
            const selectedColor =
              entry.labels
                .map((label) => speakerColors[label])
                .find(Boolean) || speakerColors[speaker.diarization_label];

            return (
              <div
                key={entry.key}
                className="relative group p-3 rounded-lg bg-white dark:bg-gray-800/50 border border-gray-300 dark:border-gray-700 hover:border-blue-400 dark:hover:border-blue-700 transition-colors shadow-sm"
                onContextMenu={(e) => handleContextMenu(e, entry)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <div className="relative shrink-0">
                      <div className="relative">
                        <div className="w-8 h-8 rounded-full border border-gray-300 dark:border-gray-600 flex items-center justify-center bg-gray-50 dark:bg-gray-800/50">
                          {entry.hasVoiceprint ? (
                            <UserCheck className="w-4 h-4 opacity-70 text-blue-600 dark:text-blue-400" />
                          ) : (
                            <User className="w-4 h-4 opacity-50 text-gray-500 dark:text-gray-400" />
                          )}
                        </div>
                        <div className="absolute -bottom-1 -right-1">
                          <InlineColorPicker
                            selectedColor={selectedColor}
                            onColorSelect={(colorKey) => {
                              entry.labels.forEach((label) => {
                                onColorChange(label, colorKey);
                              });
                            }}
                          />
                        </div>
                      </div>
                      {/* Extracting indicator */}
                      {extractingVoiceprint === speaker.diarization_label && (
                        <div className="absolute -top-0.5 -left-0.5 w-3.5 h-3.5 bg-blue-500 rounded-full flex items-center justify-center border-2 border-white dark:border-gray-800">
                          <Loader2 className="w-2 h-2 text-white animate-spin" />
                        </div>
                      )}
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
                            if (e.key === "Enter") handleRenameSubmit();
                            if (e.key === "Escape") setRenamingSpeaker(null);
                          }}
                          className="w-full text-sm font-medium bg-white dark:bg-gray-700 border border-blue-300 rounded px-1 focus:outline-none"
                        />
                      ) : (
                        <>
                          <p
                            className="text-sm font-medium text-gray-900 dark:text-white truncate cursor-pointer hover:text-blue-600 dark:hover:text-blue-400"
                            title="Double-click to rename"
                            onDoubleClick={() => handleRenameStart(entry)}
                          >
                            {entry.displayName}
                          </p>
                          {entry.members.length > 1 && (
                            <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
                              {entry.members.length} linked labels
                            </p>
                          )}
                        </>
                      )}
                      {entrySuggestions.length > 0 && (
                        <div className="mt-2 space-y-2">
                          {entrySuggestions.map((suggestion) => {
                            const isResolving = resolvingSuggestionId === suggestion.id;

                            return (
                              <div
                                key={suggestion.id}
                                className="rounded-md border border-amber-200 bg-amber-50/80 px-2 py-2 text-xs text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100"
                              >
                                <div className="flex items-center justify-between gap-2">
                                  <div className="min-w-0">
                                    <p className="font-semibold">
                                      Suggestion: {suggestion.suggested_name}
                                      {entrySuggestions.length > 1 && (
                                        <span className="ml-1 text-[11px] font-normal opacity-80">
                                          ({suggestion.diarization_label})
                                        </span>
                                      )}
                                    </p>
                                  </div>
                                  <div className="flex items-center gap-1 shrink-0">
                                    <button
                                      type="button"
                                      className="inline-flex items-center gap-1 rounded bg-emerald-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
                                      onClick={() => handleAcceptSuggestion(suggestion)}
                                      disabled={isResolving}
                                    >
                                      <Check className="h-3 w-3" />
                                      Accept
                                    </button>
                                    <button
                                      type="button"
                                      className="inline-flex items-center gap-1 rounded bg-white px-2 py-1 text-[11px] font-medium text-amber-950 ring-1 ring-inset ring-amber-300 hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-transparent dark:text-amber-100 dark:ring-amber-700 dark:hover:bg-amber-900/40"
                                      onClick={() => handleRejectSuggestion(suggestion)}
                                      disabled={isResolving}
                                    >
                                      <X className="h-3 w-3" />
                                      Reject
                                    </button>
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0 self-start">
                  <button
                    className={`p-1.5 rounded-full transition-colors ${
                      isSpeakerActive && isPlaying
                        ? "text-blue-600 bg-blue-100 dark:text-blue-400 dark:bg-blue-900/30"
                        : "text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20"
                    }`}
                    title={
                      isSpeakerActive && isPlaying ? "Pause" : "Preview Voice"
                    }
                    onClick={() =>
                      handlePlaySnippet(entry.labels, isSpeakerActive)
                    }
                  >
                    {isSpeakerActive && isPlaying ? (
                      <Pause className="w-3 h-3 fill-current" />
                    ) : (
                      <Play className="w-3 h-3 fill-current" />
                    )}
                  </button>
                  {isSpeakerActive && isPlaying && (
                    <button
                      className="p-1.5 text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-full transition-colors"
                      title="Next Snippet"
                      onClick={() => handleNextSnippet(entry.labels)}
                    >
                      <ArrowRightToLine className="w-3 h-3 fill-current" />
                    </button>
                  )}
                  </div>
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
              label: "Rename / Assign",
              onClick: () => handleRenameStart(contextMenu.speaker),
            },
            {
              label: "Merge into...",
              onClick: () => handleMergeStart(contextMenu.speaker),
            },
            {
              label: "Split / Unmerge Speaker",
              onClick: () => handleSplitStart(contextMenu.speaker),
            },
            // Voiceprint option - only show if speaker doesn't have one
            ...(!contextMenu.speaker.hasVoiceprint
              ? [
                  {
                    label: "Create Voiceprint",
                    onClick: () => handleCreateVoiceprint(contextMenu.speaker),
                  },
                ]
              : []),
            // Add to Speaker Library option - only show if not already global (and no name match)
            ...(!getResolvedGlobalSpeakerId(contextMenu.speaker.speaker) &&
            !globalSpeakers.some(
              (gs) => gs.name === contextMenu.speaker.displayName,
            )
              ? [
                  {
                    label: "Add to People",
                    onClick: () => handlePromoteToGlobal(contextMenu.speaker),
                  },
                ]
              : []),
            {
              label: "Delete",
              onClick: () => handleDeleteClick(contextMenu.speaker),
              className:
                "text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20",
            },
          ]}
        />
      )}

      {/* Split Person Modal */}
      {speakerToSplit && (
        <SplitPersonModal
          isOpen={splitModalOpen}
          onClose={() => {
            setSplitModalOpen(false);
            setSpeakerToSplit(null);
          }}
          speaker={
            globalSpeakers.find(
              (gs) => gs.id === getResolvedGlobalSpeakerId(speakerToSplit.speaker),
            ) ||
            (getResolvedGlobalSpeakerId(speakerToSplit.speaker)
              ? ({
                  id: getResolvedGlobalSpeakerId(speakerToSplit.speaker),
                  name: speakerToSplit.displayName,
                } as unknown as GlobalSpeaker)
              : null)
          }
          localSpeaker={
            !getResolvedGlobalSpeakerId(speakerToSplit.speaker)
              ? {
                  recordingId: recordingId,
                  label: speakerToSplit.speaker.diarization_label,
                  name: speakerToSplit.displayName,
                }
              : null
          }
          initialSegments={
            !getResolvedGlobalSpeakerId(speakerToSplit.speaker)
              ? segments
                  .filter(
                    (segment) =>
                      segment.speaker === speakerToSplit.speaker.diarization_label,
                  )
                  .map((s) => ({
                    recording_id: recordingId,
                    recording_name: "", // Not needed for local play
                    recording_date: "",
                    start: s.start,
                    end: s.end,
                    text: s.text,
                  }))
              : undefined
          }
          onComplete={() => {
            setSplitModalOpen(false);
            setSpeakerToSplit(null);
            onRefresh();
            addNotification({
              type: "success",
              message: `Split complete.`,
            });
          }}
        />
      )}

      {/* Voiceprint Modal */}
      <VoiceprintModal
        isOpen={voiceprintModalOpen}
        onClose={() => {
          setVoiceprintModalOpen(false);
          setVoiceprintExtractResult(null);
          setBatchVoiceprintResults(null);
        }}
        onComplete={handleVoiceprintModalComplete}
        recordingId={recordingId}
        extractResult={voiceprintExtractResult ?? undefined}
        batchResults={batchVoiceprintResults?.results}
        allGlobalSpeakers={
          batchVoiceprintResults?.all_global_speakers ||
          voiceprintExtractResult?.all_global_speakers
        }
      />

      {/* Delete Confirmation Modal */}
      <ConfirmationModal
        isOpen={!!deletingSpeaker}
        onClose={() => setDeletingSpeaker(null)}
        onConfirm={confirmDelete}
        title="Delete Speaker"
        message={
          deletingSpeaker
            ? !!getResolvedGlobalSpeakerId(deletingSpeaker.speaker)
              ? `Remove ${deletingSpeaker.displayName} from this recording? Their segments will be marked as UNKNOWN. They will remain in your Speaker Library.`
              : `Delete ${deletingSpeaker.displayName} from this recording? Their segments will be marked as UNKNOWN.`
            : ""
        }
        confirmText="Delete"
        isDangerous={true}
      />
    </aside>
  );
}
