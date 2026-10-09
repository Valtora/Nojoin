'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { Upload, FileAudio, CheckCircle, Calendar, FileText } from 'lucide-react';
import Button from '@/components/ui/Button';
import Input from '@/components/ui/Input';
import Modal from '@/components/ui/Modal';
import ModernDatePicker from '@/components/ui/ModernDatePicker';
import { importAudio, getSupportedAudioFormats } from '@/lib/api';
import SpeakerCapField from '@/components/SpeakerCapField';
import { useNotificationStore } from '@/lib/notificationStore';

interface ImportAudioModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

type UploadState = 'idle' | 'uploading' | 'success' | 'error';

const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const getFileExtension = (filename: string): string => {
  const lastDot = filename.lastIndexOf('.');
  return lastDot !== -1 ? filename.substring(lastDot).toLowerCase() : '';
};

export default function ImportAudioModal({ isOpen, onClose, onSuccess }: ImportAudioModalProps) {
  const [mounted, setMounted] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [meetingName, setMeetingName] = useState('');
  const [recordedAt, setRecordedAt] = useState<Date | null>(null);
  // null means auto-detect, which is the default and the unchanged path.
  const [maxSpeakers, setMaxSpeakers] = useState<number | null>(null);
  const [uploadState, setUploadState] = useState<UploadState>('idle');
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const { addNotification } = useNotificationStore();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropZoneRef = useRef<HTMLDivElement>(null);

  const supportedFormats = getSupportedAudioFormats();

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  const resetState = useCallback(() => {
    setSelectedFile(null);
    setMeetingName('');
    setRecordedAt(null);
    setMaxSpeakers(null);
    setUploadState('idle');
    setUploadProgress(0);
    setIsDragging(false);
  }, []);

  const handleClose = useCallback(() => {
    if (uploadState === 'uploading') return;
    resetState();
    onClose();
  }, [uploadState, resetState, onClose]);

  const validateFile = (file: File): string | null => {
    const extension = getFileExtension(file.name);
    if (!supportedFormats.includes(extension)) {
      return `Unsupported format "${extension}". Supported: ${supportedFormats.join(', ')}`;
    }
    return null;
  };

  const handleFileSelect = (file: File) => {
    const error = validateFile(file);
    if (error) {
      addNotification({ type: 'error', message: error });
      setUploadState('idle');
      return;
    }

    setSelectedFile(file);
    setUploadState('idle');

    // Auto-fill meeting name from filename (without extension)
    const nameWithoutExt = file.name.replace(/\.[^/.]+$/, '');
    if (!meetingName) {
      setMeetingName(nameWithoutExt);
    }
  };

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.currentTarget === dropZoneRef.current) {
      setIsDragging(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    const files = e.dataTransfer.files;
    if (files.length > 0) {
      handleFileSelect(files[0]);
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      handleFileSelect(files[0]);
    }
  };

  const handleImport = async () => {
    if (!selectedFile) return;

    setUploadState('uploading');
    setUploadProgress(0);

    try {
      await importAudio(selectedFile, {
        name: meetingName || undefined,
        recordedAt: recordedAt || undefined,
        maxSpeakers,
        onUploadProgress: setUploadProgress,
      });

      setUploadState('success');

      // Auto-close after success
      setTimeout(() => {
        onSuccess?.();
        handleClose();
      }, 1500);
    } catch (error: unknown) {
      setUploadState('idle');
      if (error && typeof error === 'object' && 'response' in error) {
        const axiosError = error as { response?: { data?: { detail?: string } } };
        addNotification({
          type: 'error',
          message: axiosError.response?.data?.detail || 'Upload failed. Please try again.',
        });
      } else {
        addNotification({
          type: 'error',
          message: 'Upload failed. Please check your connection and try again.',
        });
      }
    }
  };

  const handleRemoveFile = () => {
    setSelectedFile(null);
    setUploadState('idle');
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  if (!mounted) return null;

  return (
    <Modal
      open={isOpen}
      onClose={handleClose}
      // An upload in flight cannot be cancelled, so the dialog stops accepting
      // a dismissal until it resolves.
      dismissible={uploadState !== 'uploading'}
      size="md"
      title="Import Audio"
      className="max-h-[calc(100dvh-2rem)]"
      footer={
        <>
          <Button
            variant="ghost"
            onClick={handleClose}
            disabled={uploadState === 'uploading'}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleImport}
            disabled={!selectedFile || uploadState === 'uploading' || uploadState === 'success'}
            loading={uploadState === 'uploading'}
            iconLeft={<Upload aria-hidden="true" className="w-4 h-4" />}
          >
            {uploadState === 'uploading' ? 'Uploading...' : 'Import Audio'}
          </Button>
        </>
      }
    >
      <div className="space-y-6">
        {/* Drop Zone */}
        <div
          ref={dropZoneRef}
          onClick={() => uploadState !== 'uploading' && fileInputRef.current?.click()}
          onDragEnter={handleDragEnter}
          onDragLeave={handleDragLeave}
          onDragOver={handleDragOver}
          onDrop={handleDrop}
          className={`
              relative cursor-pointer rounded-lg border-2 border-dashed p-8 text-center transition-colors
              ${isDragging
              ? 'border-action bg-action-tint'
              : selectedFile
                ? 'border-status-success-border bg-status-success-bg'
                : 'border-control-border hover:border-action-border'
            }
              ${uploadState === 'uploading' ? 'pointer-events-none opacity-75' : ''}
            `}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept={supportedFormats.join(',')}
            onChange={handleFileInputChange}
            className="hidden"
          />

          {selectedFile ? (
            <div className="space-y-2">
              <FileAudio aria-hidden="true" className="mx-auto h-12 w-12 text-status-success-fg" />
              <p className="mx-auto max-w-xs truncate font-medium text-foreground">
                {selectedFile.name}
              </p>
              <p className="text-sm text-contrast-helper">
                {formatFileSize(selectedFile.size)}
              </p>
              {uploadState !== 'uploading' && (
                <button
                  onClick={(e) => { e.stopPropagation(); handleRemoveFile(); }}
                  className="text-sm text-danger-text underline hover:text-danger-text-hover"
                >
                  Remove
                </button>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              <Upload aria-hidden="true" className="mx-auto h-12 w-12 text-contrast-icon-muted" />
              <p className="text-contrast-helper">
                <span className="font-medium text-action-text">Click to browse</span> or drag and drop
              </p>
              <p className="text-xs text-contrast-helper">
                {supportedFormats.map(f => f.toUpperCase().replace('.', '')).join(', ')}
              </p>
            </div>
          )}
        </div>

        {/* Upload Progress */}
        {uploadState === 'uploading' && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="text-contrast-helper">Uploading...</span>
              <span className="font-medium text-foreground">{uploadProgress}%</span>
            </div>
            <div className="h-2 w-full rounded-full bg-surface-inset">
              <div
                className="h-2 rounded-full bg-action transition-all duration-300"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
          </div>
        )}

        {/* Success Message */}
        {uploadState === 'success' && (
          <div className="flex items-center gap-2 rounded-lg bg-status-success-bg p-3 text-status-success-fg">
            <CheckCircle aria-hidden="true" className="h-5 w-5 shrink-0" />
            <span>Audio imported successfully! Processing will begin shortly.</span>
          </div>
        )}

        {/* Optional Metadata */}
        {selectedFile && uploadState !== 'success' && (
          <div className="space-y-4 border-t border-surface-divider pt-2">
            <h3 className="text-sm font-medium text-contrast-muted">
              Optional Details
            </h3>

            {/* Meeting Name */}
            <Input
              type="text"
              label={
                <span className="flex items-center gap-2">
                  <FileText aria-hidden="true" className="w-4 h-4" />
                  Meeting Name
                </span>
              }
              value={meetingName}
              onChange={(e) => setMeetingName(e.target.value)}
              placeholder="Enter a custom name..."
              disabled={uploadState === 'uploading'}
            />

            {/* Recording Date */}
            <div>
              <label className="mb-1 flex items-center gap-2 text-sm text-contrast-helper">
                <Calendar aria-hidden="true" className="w-4 h-4" />
                Recording Date (optional)
              </label>
              <div className="w-full">
                <ModernDatePicker
                  selected={recordedAt}
                  onChange={(date) => setRecordedAt(date)}
                  showTimeSelect
                  dateFormat="MMMM d, yyyy h:mm aa"
                  placeholderText="Select date and time"
                  disabled={uploadState === 'uploading'}
                />
              </div>
              <p className="mt-1 text-xs text-contrast-helper">
                If not set, the current time will be used.
              </p>
            </div>

            {/* Speaker cap */}
            <SpeakerCapField
              value={maxSpeakers}
              onCommit={setMaxSpeakers}
              disabled={uploadState === 'uploading'}
              idPrefix="import-speaker-cap"
            />
          </div>
        )}
      </div>
    </Modal>
  );
}
