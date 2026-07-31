"use client";

import {
  RecordingSpeaker,
  RecordingId,
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
} from "lucide-react";
import { useState } from "react";
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
  } = actions;

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
      className="shrink-0 border-l border-surface-border h-full"
    >
      {/* Header with batch voiceprint action */}

      <div className="p-2 space-y-2 mt-2">
        {speakerEntries.length === 0 ? (
          <div className="p-4 text-sm text-contrast-helper italic">
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
                  className="p-3 rounded-lg bg-action-tint border-action-border"
                >
                  <p className="text-xs font-semibold text-action-text">
                    Merge {entry.displayName} into:
                  </p>
                  <select
                    className="w-full text-sm p-1 mb-2 rounded border border-control-border"
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
                      className="flex-1 px-2 py-1 bg-action text-foreground text-xs rounded hover:bg-action disabled:opacity-50"
                    >
                      Confirm
                    </button>
                    <button
                      onClick={() => setMergingSpeaker(null)}
                      className="px-2 py-1 bg-surface-inset text-xs hover:bg-surface-card"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              );
            }

            const entryLabelSet = new Set(entry.labels);
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
                className="relative group p-3 rounded-lg bg-surface-card border-control-border hover:border-status-info-border shadow-card"
                onContextMenu={(e) => handleContextMenu(e, entry)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <div className="relative shrink-0">
                      <div className="relative">
                        <div className="w-8 h-8 rounded-full border border-control-border items-center justify-center bg-surface-inset">
                          {entry.hasVoiceprint ? (
                            <UserCheck className="w-4 h-4 opacity-70 text-status-info-fg" />
                          ) : (
                            <User className="w-4 h-4 opacity-50 text-contrast-helper" />
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
                        <div className="absolute -top-0.5 -left-0.5 w-3.5 h-3.5 bg-status-info-bg rounded-full flex items-center justify-center border-2 border-surface-border">
                          <Loader2 className="w-2 h-2 text-foreground animate-spin" />
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
                          className="w-full text-sm font-medium bg-surface-card border-status-info-border rounded px-1 focus:outline-none"
                        />
                      ) : (
                        <>
                          <p
                            className="text-sm font-medium text-foreground cursor-pointer hover:text-status-info-fg"
                            title="Double-click to rename"
                            onDoubleClick={() => handleRenameStart(entry)}
                          >
                            {entry.displayName}
                          </p>
                          {entry.members.length > 1 && (
                            <p className="text-xs text-contrast-helper">
                              {entry.members.length} linked labels
                            </p>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0 self-start">
                  <button
                    className={`p-1.5 rounded-full transition-colors ${
                      isSpeakerActive && isPlaying
                        ? "text-status-info-fg bg-status-info-bg"
                        : "text-contrast-icon-muted hover:text-status-info-fg"
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
                      className="p-1.5 text-contrast-icon-muted hover:text-status-info-fg rounded-full"
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
                "text-status-danger-fg",
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
