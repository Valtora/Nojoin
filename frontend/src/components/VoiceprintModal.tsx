'use client';

import { useState, useEffect } from 'react';
import { Fingerprint, Link, Plus, HardDrive, AlertCircle, Check } from 'lucide-react';
import Button from './ui/Button';
import Modal from './ui/Modal';
import { RecordingId, VoiceprintExtractResult, VoiceprintMatchInfo, BatchVoiceprintResult } from '@/types';
import { applyVoiceprintAction, VoiceprintAction } from '@/lib/api';
import { getErrorMessage } from '@/lib/errors';

interface VoiceprintModalProps {
  isOpen: boolean;
  onClose: () => void;
  onComplete: () => void;
  recordingId: RecordingId;

  // Single speaker mode
  extractResult?: VoiceprintExtractResult;

  // Batch mode
  batchResults?: BatchVoiceprintResult[];
  allGlobalSpeakers?: Array<{ id: number; name: string; has_voiceprint: boolean }>;
}

interface SpeakerAction {
  action: VoiceprintAction;
  globalSpeakerId?: number;
  newSpeakerName?: string;
}

export default function VoiceprintModal({
  isOpen,
  onClose,
  onComplete,
  recordingId,
  extractResult,
  batchResults,
  allGlobalSpeakers: propGlobalSpeakers,
}: VoiceprintModalProps) {
  const [mounted, setMounted] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Single speaker mode state
  const [selectedAction, setSelectedAction] = useState<VoiceprintAction | null>(null);
  const [selectedGlobalSpeakerId, setSelectedGlobalSpeakerId] = useState<number | null>(null);
  const [newSpeakerName, setNewSpeakerName] = useState('');

  // Batch mode state
  const [batchActions, setBatchActions] = useState<Record<string, SpeakerAction>>({});
  const [currentBatchIndex, setCurrentBatchIndex] = useState(0);

  const isBatchMode = !!batchResults && batchResults.length > 0;
  const successfulResults = batchResults?.filter(r => r.success) || [];
  const globalSpeakers = propGlobalSpeakers || extractResult?.all_global_speakers || [];

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  useEffect(() => {
    if (isOpen) {
      setError(null);
      setSelectedAction(null);
      setSelectedGlobalSpeakerId(null);
      setNewSpeakerName('');
      setBatchActions({});
      setCurrentBatchIndex(0);

      // Pre-select action based on match
      if (extractResult?.matched_speaker?.is_strong_match) {
        setSelectedAction('link_existing');
        setSelectedGlobalSpeakerId(extractResult.matched_speaker.id);
      }
    }
  }, [isOpen, extractResult]);

  const handleSingleSubmit = async () => {
    if (!extractResult || !selectedAction) return;

    setIsSubmitting(true);
    setError(null);

    try {
      await applyVoiceprintAction(
        recordingId,
        extractResult.diarization_label,
        selectedAction,
        {
          globalSpeakerId: selectedGlobalSpeakerId ?? undefined,
          newSpeakerName: selectedAction === 'create_new' ? newSpeakerName : undefined,
        }
      );
      onComplete();
      onClose();

        } catch (e: unknown) {
      setError(getErrorMessage(e, 'Failed to apply voiceprint action'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleBatchSubmit = async () => {
    if (!batchResults) return;

    setIsSubmitting(true);
    setError(null);

    try {
      // Process each speaker action
      for (const result of successfulResults) {
        const action = batchActions[result.diarization_label];
        if (action) {
          await applyVoiceprintAction(
            recordingId,
            result.diarization_label,
            action.action,
            {
              globalSpeakerId: action.globalSpeakerId,
              newSpeakerName: action.newSpeakerName,
            }
          );
        }
      }
      onComplete();
      onClose();

        } catch (e: unknown) {
      setError(getErrorMessage(e, 'Failed to apply voiceprint actions'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const updateBatchAction = (label: string, action: SpeakerAction) => {
    setBatchActions(prev => ({ ...prev, [label]: action }));
  };

  const renderMatchInfo = (match: VoiceprintMatchInfo | null | undefined) => {
    if (!match) {
      return (
        <div className="flex items-center gap-2 text-status-warning-fg text-sm">
          <AlertCircle className="w-4 h-4" />
          <span>No matching voice found in library</span>
        </div>
      );
    }

    return (
      <div className={`flex items-center gap-2 text-sm ${match.is_strong_match ? 'text-status-success-fg' : 'text-status-warning-fg'}`}>
        {match.is_strong_match ? <Check className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
        <span>
          {match.is_strong_match ? 'Strong match' : 'Possible match'}: <strong>{match.name}</strong> ({Math.round(match.similarity_score * 100)}% confidence)
        </span>
      </div>
    );
  };

  const renderSingleSpeakerContent = () => {
    if (!extractResult) return null;

    return (
      <div className="space-y-4">
        {/* Match Info */}
        <div className="p-3 bg-surface-inset rounded-lg">
          {renderMatchInfo(extractResult.matched_speaker)}
        </div>

        {/* Action Options */}
        <div className="space-y-2">
          <p className="text-sm font-medium text-contrast-muted">What would you like to do?</p>

          {/* Link to matched speaker (if match exists) */}
          {extractResult.matched_speaker && (
            <label className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${selectedAction === 'link_existing' && selectedGlobalSpeakerId === extractResult.matched_speaker.id ? 'border-status-info-border bg-status-info-bg' : 'border-surface-border hover:border-status-info-border'}`}>
              <input
                type="radio"
                name="action"
                checked={selectedAction === 'link_existing' && selectedGlobalSpeakerId === extractResult.matched_speaker.id}
                onChange={() => {
                  setSelectedAction('link_existing');
                  setSelectedGlobalSpeakerId(extractResult.matched_speaker!.id);
                }}
                className="mt-1"
              />
              <div>
                <div className="flex items-center gap-2">
                  <Link className="w-4 h-4" />
                  <span className="font-medium">Link to {extractResult.matched_speaker.name}</span>
                </div>
                <p className="text-xs text-contrast-helper mt-1">Use the matched voice profile. The voiceprint will be merged to improve recognition.</p>
              </div>
            </label>
          )}

          {/* Create new global speaker */}
          <label className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${selectedAction === 'create_new' ? 'border-status-info-border bg-status-info-bg' : 'border-surface-border hover:border-status-info-border'}`}>
            <input
              type="radio"
              name="action"
              checked={selectedAction === 'create_new'}
              onChange={() => setSelectedAction('create_new')}
              className="mt-1"
            />
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <Plus className="w-4 h-4" />
                <span className="font-medium">Create new speaker</span>
              </div>
              <p className="text-xs text-contrast-helper mt-1">Add this voice to your library with a new name.</p>
              {selectedAction === 'create_new' && (
                <input
                  type="text"
                  placeholder="Enter speaker name..."
                  value={newSpeakerName}
                  onChange={(e) => setNewSpeakerName(e.target.value)}
                  className="mt-2 w-full px-3 py-1.5 text-sm border border-control-border rounded bg-control-bg focus:outline-none focus:ring-2 focus:ring-status-info-border"
                  autoFocus
                />
              )}
            </div>
          </label>

          {/* Force link to different speaker */}
          <label className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${selectedAction === 'force_link' ? 'border-status-info-border bg-status-info-bg' : 'border-surface-border hover:border-status-info-border'}`}>
            <input
              type="radio"
              name="action"
              checked={selectedAction === 'force_link'}
              onChange={() => setSelectedAction('force_link')}
              className="mt-1"
            />
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <Link className="w-4 h-4" />
                <span className="font-medium">Link to different speaker</span>
              </div>
              <p className="text-xs text-contrast-helper mt-1">Override the match and train the selected speaker&apos;s voice profile.</p>
              {selectedAction === 'force_link' && (
                <select
                  value={selectedGlobalSpeakerId ?? ''}
                  onChange={(e) => setSelectedGlobalSpeakerId(Number(e.target.value))}
                  className="mt-2 w-full px-3 py-1.5 text-sm border border-control-border rounded bg-control-bg focus:outline-none focus:ring-2 focus:ring-status-info-border"
                >
                  <option value="">Select a speaker...</option>
                  {globalSpeakers.map((gs) => (
                    <option key={gs.id} value={gs.id}>
                      {gs.name} {gs.has_voiceprint ? '(has voiceprint)' : ''}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </label>

          {/* Keep local only */}
          <label className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${selectedAction === 'local_only' ? 'border-status-info-border bg-status-info-bg' : 'border-surface-border hover:border-status-info-border'}`}>
            <input
              type="radio"
              name="action"
              checked={selectedAction === 'local_only'}
              onChange={() => setSelectedAction('local_only')}
              className="mt-1"
            />
            <div>
              <div className="flex items-center gap-2">
                <HardDrive className="w-4 h-4" />
                <span className="font-medium">Keep local only</span>
              </div>
              <p className="text-xs text-contrast-helper mt-1">Save the voiceprint for this recording only. Won&apos;t be used for future recognition.</p>
            </div>
          </label>
        </div>
      </div>
    );
  };

  const renderBatchContent = () => {
    if (!batchResults || successfulResults.length === 0) {
      return (
        <div className="text-center py-8 text-contrast-helper">
          No voiceprints were extracted successfully.
        </div>
      );
    }

    const currentResult = successfulResults[currentBatchIndex];
    const currentAction = batchActions[currentResult.diarization_label];

    return (
      <div className="space-y-4">
        {/* Progress indicator */}
        <div className="flex items-center justify-between text-sm text-contrast-helper">
          <span>Speaker {currentBatchIndex + 1} of {successfulResults.length}</span>
          <span className="font-medium">{currentResult.speaker_name}</span>
        </div>

        {/* Progress bar */}
        <div className="w-full h-1 bg-surface-inset rounded-full overflow-hidden">
          <div
            className="h-full bg-status-info-bg transition-all duration-300"
            style={{ width: `${((currentBatchIndex + 1) / successfulResults.length) * 100}%` }}
          />
        </div>

        {/* Match Info */}
        <div className="p-3 bg-surface-inset rounded-lg">
          {renderMatchInfo(currentResult.matched_speaker)}
        </div>

        {/* Quick Actions */}
        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={() => {
              if (currentResult.matched_speaker) {
                updateBatchAction(currentResult.diarization_label, {
                  action: 'link_existing',
                  globalSpeakerId: currentResult.matched_speaker.id,
                });
              }
              if (currentBatchIndex < successfulResults.length - 1) {
                setCurrentBatchIndex(prev => prev + 1);
              }
            }}
            disabled={!currentResult.matched_speaker}
            className="px-3 py-2 text-sm rounded-lg border border-status-success-border text-status-success-fg hover:bg-status-success-bg disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {currentResult.matched_speaker ? `Link to ${currentResult.matched_speaker.name}` : 'No match'}
          </button>
          <button
            onClick={() => {
              updateBatchAction(currentResult.diarization_label, { action: 'local_only' });
              if (currentBatchIndex < successfulResults.length - 1) {
                setCurrentBatchIndex(prev => prev + 1);
              }
            }}
            className="px-3 py-2 text-sm rounded-lg border border-control-border hover:bg-surface-inset"
          >
            Keep Local
          </button>
        </div>

        {/* Current selection indicator */}
        {currentAction && (
          <div className="text-sm text-status-info-fg flex items-center gap-2">
            <Check className="w-4 h-4" />
            <span>
              Selected: {currentAction.action === 'link_existing' ? `Link to speaker` :
                        currentAction.action === 'create_new' ? `Create "${currentAction.newSpeakerName}"` :
                        currentAction.action === 'force_link' ? 'Force link' : 'Keep local'}
            </span>
          </div>
        )}

        {/* Navigation */}
        <div className="flex justify-between pt-2">
          <button
            onClick={() => setCurrentBatchIndex(prev => Math.max(0, prev - 1))}
            disabled={currentBatchIndex === 0}
            className="px-3 py-1.5 text-sm text-contrast-helper disabled:opacity-50"
          >
            Previous
          </button>
          <button
            onClick={() => setCurrentBatchIndex(prev => Math.min(successfulResults.length - 1, prev + 1))}
            disabled={currentBatchIndex === successfulResults.length - 1}
            className="px-3 py-1.5 text-sm text-contrast-helper disabled:opacity-50"
          >
            Next
          </button>
        </div>
      </div>
    );
  };

  const isSubmitDisabled = () => {
    if (isBatchMode) {
      return Object.keys(batchActions).length === 0;
    }
    if (!selectedAction) return true;
    if (selectedAction === 'create_new' && !newSpeakerName.trim()) return true;
    if (selectedAction === 'force_link' && !selectedGlobalSpeakerId) return true;
    return false;
  };

  if (!mounted) return null;

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="md"
      className="max-h-[calc(100dvh-2rem)]"
      title={
        <span className="flex items-center gap-3">
          <span className="rounded-lg bg-status-info-bg p-2">
            <Fingerprint aria-hidden="true" className="h-5 w-5 text-status-info-fg" />
          </span>
          <span>
            <span className="block">
              {isBatchMode ? 'Configure Voiceprints' : 'Voiceprint Created'}
            </span>
            <span className="block text-sm font-normal text-contrast-helper">
              {isBatchMode
                ? `${successfulResults.length} voiceprint(s) extracted`
                : 'Choose how to use this voice fingerprint'
              }
            </span>
          </span>
        </span>
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={isBatchMode ? handleBatchSubmit : handleSingleSubmit}
            disabled={isSubmitting || isSubmitDisabled()}
            loading={isSubmitting}
          >
            {isBatchMode ? 'Apply All' : 'Apply'}
          </Button>
        </>
      }
    >
      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-status-danger-border bg-status-danger-bg p-3 text-sm text-status-danger-fg">
          <AlertCircle aria-hidden="true" className="h-4 w-4 flex-shrink-0" />
          {error}
        </div>
      )}

      {isBatchMode ? renderBatchContent() : renderSingleSpeakerContent()}
    </Modal>
  );
}
