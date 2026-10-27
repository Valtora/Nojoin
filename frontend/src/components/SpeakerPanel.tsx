"use client";

import { ButtonBase, InputBase, SelectBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
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
  Sparkles,
  Users,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ContextMenu from "./ContextMenu";
import ConfirmationModal from "./ConfirmationModal";
import VoiceprintModal from "./VoiceprintModal";
import SplitPersonModal from "./people/SplitPersonModal";
import { InlineColorPicker } from "./ColorPicker";
import { useNotificationStore } from "@/lib/notificationStore";
import { getResolvedGlobalSpeakerId, liveSpeakerDisplayName } from "@/lib/recordingSpeakerUtils";
import {
  useSpeakerPanelEntries,
  type SpeakerPanelEntry,
} from "./speakers/_hooks/useSpeakerPanelEntries";
import { useSpeakerSnippetPlayback } from "./speakers/_hooks/useSpeakerSnippetPlayback";
import {
  describeLineCount,
  useSpeakerPanelActions,
} from "./speakers/_hooks/useSpeakerPanelActions";
import { useMergedSpeakerClusters } from "./speakers/_hooks/useMergedSpeakerClusters";
import { DISABLED_LOOK, loadingButtonProps } from "@/components/ui/loadingButton";
import { TOUCH_TARGET_HEIGHT } from "@/components/ui/touchTarget";
import SpeakerAssignmentPopover from "./SpeakerAssignmentPopover";
import { useSpeakerTurnHighlight } from "./speakers/SpeakerTurnHighlight";
import LiveSpeakerRows, { type RenameLiveSpeaker } from "./speakers/LiveSpeakerRows";

interface SpeakerPanelProps {
  speakers: RecordingSpeaker[];
  segments: TranscriptSegment[];
  onPlaySegment: (time: number, end?: number) => void;
  recordingId: RecordingId;
  speakerColors: Record<string, string>; // palette keys, not class names
  onColorChange: (speakerLabel: string, colorKey: string) => void;
  currentTime: number;
  isPlaying: boolean;
  onPause: () => void;
  onResume: () => void;
  onRefresh: () => void;
  globalSpeakers: GlobalSpeaker[];
  onSpeakerRenamed?: (oldName: string, newName: string) => Promise<void> | void;
  disablePlayback?: boolean;
  disableEditing?: boolean;
  liveSpeakerNames?: Record<string, string>;
  onRenameLiveSpeaker?: RenameLiveSpeaker;
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
  disablePlayback = false,
  disableEditing = false,
  liveSpeakerNames = {},
  onRenameLiveSpeaker,
}: SpeakerPanelProps) {
  const { addNotification } = useNotificationStore();
  const highlight = useSpeakerTurnHighlight();
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pointerType = useRef("");
  useEffect(() => () => {
    if (highlightTimer.current !== null) clearTimeout(highlightTimer.current);
  }, []);
  const [picker, setPicker] = useState<{ entry: SpeakerPanelEntry; anchor: HTMLElement } | null>(null);
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
  const mergedClusters = useMergedSpeakerClusters(recordingId, speakers);

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
    undoMerge: handleUndoMerge,
    isReviewingName,
    reviewName: handleReviewName,
    confirmAllNames: handleConfirmAllNames,
    isSubmitting,
  } = actions;

  const pendingEntries = speakerEntries.filter((entry) => entry.pendingName);
  const actualLabels = new Set(speakers.map((speaker) => speaker.diarization_label));
  const liveLabels = [...new Set(segments.map((segment) => segment.speaker))]
    .filter((label) => !actualLabels.has(label) && liveSpeakerDisplayName(label) !== null);

  const handleContextMenu = (
    e: React.MouseEvent,
    speaker: SpeakerPanelEntry,
  ) => {
    e.preventDefault();
    if (disableEditing) return;
    setContextMenu({ x: e.clientX, y: e.clientY, speaker });
  };

  const handleVoiceprintModalComplete = () => {
    onRefresh();
  };

  return (
    <aside
      id="speaker-panel"
      data-ui-reading="pane"
      data-ui-fill="inset"
      className="@container flex h-full min-w-0 flex-col bg-surface-inset"
    >
      <div className="min-h-0 flex-1 overflow-y-auto p-2 pb-clear-floating-2 space-y-2">
        <LiveSpeakerRows labels={liveLabels} names={liveSpeakerNames} segments={segments} speakers={speakers}
          globalSpeakers={globalSpeakers} speakerColors={speakerColors} onRename={onRenameLiveSpeaker}
          readOnly={disableEditing} />
        {pendingEntries.length >= 2 && !disableEditing && (
          <div className="flex justify-end">
            <ButtonBase
              data-ui-fill="action"
              data-ui-hover="action-hover" chrome="action"
              type="button"
              className={`rounded px-2 py-1 text-xs font-medium bg-action text-action-on hover:bg-action-hover ${TOUCH_TARGET_HEIGHT} ${DISABLED_LOOK}`}
              {...loadingButtonProps(isReviewingName, undefined, () =>
                handleConfirmAllNames(pendingEntries),
              )}
            >
              Confirm all ({pendingEntries.length})
            </ButtonBase>
          </div>
        )}
        {speakerEntries.length === 0 && liveLabels.length === 0 ? (
          <div className="p-4 text-sm text-contrast-helper text-center italic">
            No speakers detected.
          </div>
        ) : (
          speakerEntries.map((entry) => {
            const { speaker } = entry;
            const isRenaming = !disableEditing &&
              renamingSpeaker?.key === entry.key;
            const isMerging = !disableEditing &&
              mergingSpeaker?.key === entry.key;

            if (isMerging) {
              const otherSpeakers = speakerEntries.filter(
                (candidate) => candidate.key !== entry.key,
              );
              return (
                <div
                  key={entry.key}
                  className="p-3 rounded-lg bg-action-tint border border-action-border"
                >
                  <p className="text-xs font-semibold text-action-text mb-2">
                    Merge {entry.displayName} into:
                  </p>
                  <SelectBase data-ui-fill="card" data-ui-border="full"
                    className="w-full text-sm p-1 mb-2 rounded border border-control-border bg-surface-card"
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
                  </SelectBase>
                  <div className="flex gap-2">
                    <ButtonBase
                      data-ui-fill="action"
                      data-ui-hover="action-hover" chrome="action"
                      onClick={handleMergeSubmit}
                      disabled={!mergeTargetLabel}
                      className="flex-1 px-2 py-1 bg-action text-action-on text-xs rounded hover:bg-action-hover disabled:opacity-50"
                    >
                      Confirm
                    </ButtonBase>
                    <ButtonBase
                      data-ui-fill="inset"
                      onClick={() => setMergingSpeaker(null)}
                      className="px-2 py-1 bg-surface-inset text-contrast-muted text-xs rounded hover:bg-surface-card"
                    >
                      Cancel
                    </ButtonBase>
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
            const memberIds = new Set(entry.members.map((member) => member.id));
            const absorbedClusters = mergedClusters.filter((cluster) =>
              memberIds.has(cluster.merged_into_id),
            );

            return (
              <SurfaceBase
                data-ui-fill="card"
                data-ui-border="full" kind="card"
                key={entry.key}
                className="relative group p-2.5 @min-[18rem]:p-3 rounded-lg bg-surface-card border border-control-border hover:border-status-info-border transition-colors shadow-card"
                onContextMenu={(e) => handleContextMenu(e, entry)}
              >
                <div className="flex items-start justify-between gap-2 @min-[18rem]:gap-3">
                  <div className="flex items-center gap-2 @min-[18rem]:gap-3 min-w-0 flex-1">
                    <div className="relative shrink-0">
                      <div className="relative">
                        <div className="w-8 h-8 rounded-full border border-control-border flex items-center justify-center bg-surface-inset">
                          {entry.hasVoiceprint ? (
                            <UserCheck className="w-4 h-4 opacity-70 text-status-info-fg" />
                          ) : (
                            <User className="w-4 h-4 opacity-50 text-contrast-helper" />
                          )}
                        </div>
                        <div className="absolute -bottom-1 -right-1">
                          {!disableEditing && <InlineColorPicker
                            selectedColor={selectedColor}
                            onColorSelect={(colorKey) => {
                              if (disableEditing) return;
                              entry.labels.forEach((label) => {
                                onColorChange(label, colorKey);
                              });
                            }}
                          />}
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
                        <InputBase
                          data-ui-fill="card"
                          data-ui-border="full"
                          autoFocus
                          type="text"
                          value={renameValue}
                          onChange={(e) => setRenameValue(e.target.value)}
                          onBlur={handleRenameSubmit}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleRenameSubmit();
                            if (e.key === "Escape") setRenamingSpeaker(null);
                          }}
                          className="w-full text-sm font-medium bg-surface-card border border-status-info-border rounded px-1 focus:outline-none"
                        />
                      ) : (
                        <>
                          <ButtonBase
                            type="button"
                            aria-pressed={entry.labels.every((label) => highlight.labels.includes(label))}
                            className={`flex min-w-0 max-w-full text-left text-sm font-medium text-foreground cursor-pointer hover:text-status-info-fg ${TOUCH_TARGET_HEIGHT}`}
                            title={
                              entry.pendingName
                                ? "Suggested name, not confirmed yet. Click to highlight turns. Double-click to rename"
                                : "Click to highlight turns. Double-click to rename"
                            }
                            onPointerDown={(event) => { pointerType.current = event.pointerType; }}
                            onClick={(event) => {
                              if (event.detail === 0 || pointerType.current === "touch") {
                                highlight.toggle(entry.labels);
                                return;
                              }
                              if (event.detail > 1) return;
                              // Wait for a possible mouse double-click before highlighting.
                              if (highlightTimer.current !== null) clearTimeout(highlightTimer.current);
                              highlightTimer.current = setTimeout(() => {
                                highlightTimer.current = null;
                                highlight.toggle(entry.labels);
                              }, 250);
                            }}
                            onDoubleClick={() => {
                              if (disableEditing) return;
                              if (highlightTimer.current !== null) clearTimeout(highlightTimer.current);
                              highlightTimer.current = null;
                              setPicker(null);
                              handleRenameStart(entry);
                            }}
                          >
                            <span className="truncate">{entry.displayName}</span>
                            {/* Outside the truncated name, so a long one
                                never hides that it is a suggestion. */}
                            {entry.pendingName && <span className="shrink-0">?</span>}
                          </ButtonBase>
                          {/* A rename relabels every line this speaker holds,
                              so it is disclosed once here rather than on each
                              utterance in the transcript. */}
                          {entry.namedByAssistant && (
                            <span
                              className="mt-0.5 inline-flex items-center gap-1 rounded-pill border border-surface-border bg-surface-inset px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-foreground"
                              title="An assistant connected through the MCP connector named this speaker"
                            >
                              <Sparkles className="h-2.5 w-2.5" aria-hidden="true" />
                              AI named
                            </span>
                          )}
                          {entry.members.length > 1 && (
                            <p className="text-xs text-contrast-helper truncate">
                              {entry.members.length} linked labels
                            </p>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0 self-start">
                  <ButtonBase
                    disabled={disablePlayback}
                    className={`p-1.5 rounded-full transition-colors ${
                      isSpeakerActive && isPlaying
                        ? "text-status-info-fg bg-status-info-bg"
                        : "text-contrast-icon-muted hover:text-status-info-fg hover:bg-status-info-bg"
                    }`}
                    title={
                      isSpeakerActive && isPlaying ? "Pause" : "Preview Voice"
                    }
                    onClick={() => {
                      if (!disablePlayback) handlePlaySnippet(entry.labels, isSpeakerActive);
                    }}
                  >
                    {isSpeakerActive && isPlaying ? (
                      <Pause className="w-3 h-3 fill-current" />
                    ) : (
                      <Play className="w-3 h-3 fill-current" />
                    )}
                  </ButtonBase>
                  {!disablePlayback && isSpeakerActive && isPlaying && (
                    <ButtonBase
                      className="p-1.5 text-contrast-icon-muted hover:text-status-info-fg hover:bg-status-info-bg rounded-full transition-colors"
                      title="Next Snippet"
                      onClick={() => handleNextSnippet(entry.labels)}
                    >
                      <ArrowRightToLine className="w-3 h-3 fill-current" />
                    </ButtonBase>
                  )}
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <ButtonBase type="button" disabled={disableEditing} aria-label={`Change identity for ${entry.displayName}`}
                    onClick={(event) => setPicker({ entry, anchor: event.currentTarget })}
                    className={`inline-flex items-center gap-1 rounded px-2 text-xs text-action-text hover:bg-action-tint ${TOUCH_TARGET_HEIGHT}`}>
                    <Users className="h-3.5 w-3.5" aria-hidden="true" /> Change identity
                  </ButtonBase>
                  {!getResolvedGlobalSpeakerId(speaker) && !globalSpeakers.some((person) => person.name === entry.displayName) && (
                    <ButtonBase type="button" disabled={isSubmitting || disableEditing} onClick={() => handlePromoteToGlobal(entry)}
                      className={`rounded px-2 text-xs text-action-text hover:bg-action-tint ${TOUCH_TARGET_HEIGHT} ${DISABLED_LOOK}`}>
                      Add to People
                    </ButtonBase>
                  )}
                </div>
                {/* A suggested name shows at once but links nobody to People
                    until it is confirmed here or on a transcript line. */}
                {entry.pendingName && !isRenaming && !disableEditing && (
                  <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1.5 rounded-md border border-status-warning-border bg-status-warning-bg px-2 py-1.5">
                    {entry.pendingName.reason && (
                      <p className="min-w-[8rem] flex-1 text-xs text-status-warning-fg">
                        {entry.pendingName.reason}
                      </p>
                    )}
                    {/* The reason wraps above the buttons in a narrow
                        column, and a long Not {name} truncates inside it. */}
                    <div className="ml-auto flex min-w-0 max-w-full items-center gap-1.5">
                      <ButtonBase
                        data-ui-fill="action"
                        data-ui-hover="action-hover" chrome="action"
                        type="button"
                        aria-label={`Confirm ${entry.displayName}`}
                        className={`shrink-0 rounded px-2 py-1 text-xs font-medium bg-action text-action-on hover:bg-action-hover ${TOUCH_TARGET_HEIGHT} ${DISABLED_LOOK}`}
                        {...loadingButtonProps(isReviewingName, undefined, () =>
                          handleReviewName(entry, "confirm"),
                        )}
                      >
                        Confirm
                      </ButtonBase>
                      <ButtonBase
                        data-ui-fill="card"
                        data-ui-hover="inset"
                        data-ui-border="full" chrome="action"
                        type="button"
                        className={`min-w-0 max-w-[10rem] truncate rounded border border-control-border bg-surface-card px-2 py-1 text-xs font-medium text-foreground hover:bg-surface-inset ${TOUCH_TARGET_HEIGHT} ${DISABLED_LOOK}`}
                        {...loadingButtonProps(isReviewingName, undefined, () =>
                          handleReviewName(entry, "dismiss"),
                        )}
                      >
                        Not {entry.displayName}
                      </ButtonBase>
                    </div>
                  </div>
                )}
                {/* Clusters processing merged into this speaker. Each can be
                    given back its own lines; a merge made by hand is not
                    listed, since its lines carry the person's lock. */}
                {absorbedClusters.length > 0 && !disableEditing && (
                  <ul className="mt-2 space-y-1 border-t border-control-border pt-2">
                    {absorbedClusters.map((cluster) => (
                      <li
                        key={cluster.id}
                        className="flex items-center justify-between gap-2 text-xs text-contrast-helper"
                      >
                        <span
                          className="min-w-0 truncate"
                          title={`Processing merged ${cluster.diarization_label} into this speaker`}
                        >
                          Merged in: {cluster.diarization_label},{" "}
                          {describeLineCount(cluster.restorable_utterance_count)}
                        </span>
                        <ButtonBase
                          type="button"
                          className="shrink-0 rounded px-2 py-0.5 text-xs font-medium text-action-text hover:bg-action-tint disabled:opacity-50 aria-disabled:opacity-50"
                          title="Give these lines back to a speaker of their own"
                          {...loadingButtonProps(isSubmitting, undefined, () => handleUndoMerge(cluster))}
                        >
                          Undo merge
                        </ButtonBase>
                      </li>
                    ))}
                  </ul>
                )}
              </SurfaceBase>
            );
          })
        )}
      </div>
      {picker && !disableEditing && <SpeakerAssignmentPopover
        availableSpeakers={speakers.filter((speaker) => !picker.entry.labels.includes(speaker.diarization_label))}
        globalSpeakers={globalSpeakers} currentSpeakerLabel={picker.entry.speaker.diarization_label}
        speakerColors={speakerColors} targetElement={picker.anchor} fixedScope="speaker_everywhere_in_recording"
        onSelect={async (assignment) => {
          if (await actions.assignIdentity(picker.entry, assignment.name)) {
            setPicker(null); picker.anchor.focus();
          }
        }}
        onClose={() => { setPicker(null); picker.anchor.focus(); }}
      />}
      {contextMenu && !disableEditing && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
          items={[
            {
              label: "Rename / Assign",
              onClick: () => handleRenameStart(contextMenu.speaker),
            },
            ...(!contextMenu.speaker.pendingName && contextMenu.speaker.dismissibleNameLabels.length > 0
              ? [{
                  label: `Not ${contextMenu.speaker.displayName}`,
                  onClick: () => handleReviewName(contextMenu.speaker, "dismiss"),
                }]
              : []),
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
                "text-status-danger-fg hover:bg-status-danger-bg",
            },
          ]}
        />
      )}

      {/* Split Person Modal */}
      {speakerToSplit && !disableEditing && (
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

      <VoiceprintModal
        isOpen={voiceprintModalOpen && !disableEditing}
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
        isOpen={!!deletingSpeaker && !disableEditing}
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
