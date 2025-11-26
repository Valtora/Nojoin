'use client';

import { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X, Fingerprint, Link, Plus, HardDrive, AlertCircle, Check, Loader2 } from 'lucide-react';
import { VoiceprintExtractResult, VoiceprintMatchInfo, BatchVoiceprintResult } from '@/types';
import { applyVoiceprintAction, VoiceprintAction } from '@/lib/api';

interface VoiceprintModalProps {
  isOpen: boolean;
  onClose: () => void;
  onComplete: () => void;
  recordingId: number;
  
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
    } catch (e: any) {
      setError(e.response?.data?.detail || 'Failed to apply voiceprint action');
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
    } catch (e: any) {
      setError(e.response?.data?.detail || 'Failed to apply voiceprint actions');
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
        <div className="flex items-center gap-2 text-yellow-600 dark:text-yellow-400 text-sm">
          <AlertCircle className="w-4 h-4" />
          <span>No matching voice found in library</span>
        </div>
      );
    }
    
    return (
      <div className={`flex items-center gap-2 text-sm ${match.is_strong_match ? 'text-green-600 dark:text-green-400' : 'text-yellow-600 dark:text-yellow-400'}`}>
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
        <div className="p-3 bg-gray-50 dark:bg-gray-800 rounded-lg">
          {renderMatchInfo(extractResult.matched_speaker)}
        </div>
        
        {/* Action Options */}
        <div className="space-y-2">
          <p className="text-sm font-medium text-gray-700 dark:text-gray-300">What would you like to do?</p>
          
          {/* Link to matched speaker (if match exists) */}
          {extractResult.matched_speaker && (
            <label className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${selectedAction === 'link_existing' && selectedGlobalSpeakerId === extractResult.matched_speaker.id ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20' : 'border-gray-200 dark:border-gray-700 hover:border-blue-300'}`}>
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
                <p className="text-xs text-gray-500 mt-1">Use the matched voice profile. The voiceprint will be merged to improve recognition.</p>
              </div>
            </label>
          )}
          
          {/* Create new global speaker */}
          <label className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${selectedAction === 'create_new' ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20' : 'border-gray-200 dark:border-gray-700 hover:border-blue-300'}`}>
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
              <p className="text-xs text-gray-500 mt-1">Add this voice to your library with a new name.</p>
              {selectedAction === 'create_new' && (
                <input
                  type="text"
                  placeholder="Enter speaker name..."
                  value={newSpeakerName}
                  onChange={(e) => setNewSpeakerName(e.target.value)}
                  className="mt-2 w-full px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  autoFocus
                />
              )}
            </div>
          </label>
          
          {/* Force link to different speaker */}
          <label className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${selectedAction === 'force_link' ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20' : 'border-gray-200 dark:border-gray-700 hover:border-blue-300'}`}>
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
              <p className="text-xs text-gray-500 mt-1">Override the match and train the selected speaker&apos;s voice profile.</p>
              {selectedAction === 'force_link' && (
                <select
                  value={selectedGlobalSpeakerId ?? ''}
                  onChange={(e) => setSelectedGlobalSpeakerId(Number(e.target.value))}
                  className="mt-2 w-full px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
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
          <label className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${selectedAction === 'local_only' ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20' : 'border-gray-200 dark:border-gray-700 hover:border-blue-300'}`}>
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
              <p className="text-xs text-gray-500 mt-1">Save the voiceprint for this recording only. Won&apos;t be used for future recognition.</p>
            </div>
          </label>
        </div>
      </div>
    );
  };

  const renderBatchContent = () => {
    if (!batchResults || successfulResults.length === 0) {
      return (
        <div className="text-center py-8 text-gray-500">
          No voiceprints were extracted successfully.
        </div>
      );
    }
    
    const currentResult = successfulResults[currentBatchIndex];
    const currentAction = batchActions[currentResult.diarization_label];
    
    return (
      <div className="space-y-4">
        {/* Progress indicator */}
        <div className="flex items-center justify-between text-sm text-gray-500">
          <span>Speaker {currentBatchIndex + 1} of {successfulResults.length}</span>
          <span className="font-medium">{currentResult.speaker_name}</span>
        </div>
        
        {/* Progress bar */}
        <div className="w-full h-1 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
          <div 
            className="h-full bg-blue-500 transition-all duration-300"
            style={{ width: `${((currentBatchIndex + 1) / successfulResults.length) * 100}%` }}
          />
        </div>
        
        {/* Match Info */}
        <div className="p-3 bg-gray-50 dark:bg-gray-800 rounded-lg">
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
            className="px-3 py-2 text-sm rounded-lg border border-green-500 text-green-600 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-900/20 disabled:opacity-50 disabled:cursor-not-allowed"
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
            className="px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800"
          >
            Keep Local
          </button>
        </div>
        
        {/* Current selection indicator */}
        {currentAction && (
          <div className="text-sm text-blue-600 dark:text-blue-400 flex items-center gap-2">
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
            className="px-3 py-1.5 text-sm text-gray-600 dark:text-gray-400 disabled:opacity-50"
          >
            Previous
          </button>
          <button
            onClick={() => setCurrentBatchIndex(prev => Math.min(successfulResults.length - 1, prev + 1))}
            disabled={currentBatchIndex === successfulResults.length - 1}
            className="px-3 py-1.5 text-sm text-gray-600 dark:text-gray-400 disabled:opacity-50"
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

  if (!isOpen || !mounted) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-100 dark:bg-blue-900/30 rounded-lg">
              <Fingerprint className="w-5 h-5 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                {isBatchMode ? 'Configure Voiceprints' : 'Voiceprint Created'}
              </h2>
              <p className="text-sm text-gray-500">
                {isBatchMode 
                  ? `${successfulResults.length} voiceprint(s) extracted`
                  : 'Choose how to use this voice fingerprint'
                }
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        
        {/* Content */}
        <div className="px-6 py-4 max-h-[60vh] overflow-y-auto">
          {error && (
            <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-red-600 dark:text-red-400 text-sm flex items-center gap-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              {error}
            </div>
          )}
          
          {isBatchMode ? renderBatchContent() : renderSingleSpeakerContent()}
        </div>
        
        {/* Footer */}
        <div className="flex justify-end gap-3 px-6 py-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
          >
            Cancel
          </button>
          <button
            onClick={isBatchMode ? handleBatchSubmit : handleSingleSubmit}
            disabled={isSubmitting || isSubmitDisabled()}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {isSubmitting && <Loader2 className="w-4 h-4 animate-spin" />}
            {isBatchMode ? 'Apply All' : 'Apply'}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
