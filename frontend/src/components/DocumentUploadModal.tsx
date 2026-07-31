'use client';

import { useState, useRef, useCallback } from 'react';
import { Upload, FileText, CheckCircle, Sparkles, AlertTriangle } from 'lucide-react';
import Button from './ui/Button';
import Modal from './ui/Modal';
import { uploadDocument } from '@/lib/api';
import {
    DOCUMENT_SIZE_WARNING_BYTES,
    SUPPORTED_DOCUMENT_FORMATS,
    VISION_ONLY_DOCUMENT_FORMATS,
} from '@/lib/api/documents';
import { getErrorMessage } from '@/lib/errors';
import type { RecordingId } from '@/types';
import { useNotificationStore } from '@/lib/notificationStore';

interface DocumentUploadModalProps {
    isOpen: boolean;
    onClose: () => void;
    onSuccess?: () => void;
    recordingId: RecordingId;
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

const supportedFormats = [...SUPPORTED_DOCUMENT_FORMATS];
const visionOnlyFormats: readonly string[] = VISION_ONLY_DOCUMENT_FORMATS;
const maxSizeMB = 250;

export default function DocumentUploadModal({ isOpen, onClose, onSuccess, recordingId }: DocumentUploadModalProps) {
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    const [uploadState, setUploadState] = useState<UploadState>('idle');
    const [isDragging, setIsDragging] = useState(false);
    // Visual analysis is on by default: text-only extraction misses charts,
    // diagrams and anything scanned, which is the common case for decks.
    const [deepParse, setDeepParse] = useState(true);
    const { addNotification } = useNotificationStore();

    const fileInputRef = useRef<HTMLInputElement>(null);
    const dropZoneRef = useRef<HTMLDivElement>(null);

    const resetState = useCallback(() => {
        setSelectedFile(null);
        setUploadState('idle');
        setIsDragging(false);
        setDeepParse(true);
    }, []);

    const handleClose = useCallback(() => {
        if (uploadState === 'uploading') return;
        resetState();
        onClose();
    }, [uploadState, resetState, onClose]);

    const validateFile = (file: File): string | null => {
        const extension = getFileExtension(file.name);
        if (!supportedFormats.includes(extension as (typeof SUPPORTED_DOCUMENT_FORMATS)[number])) {
            return `Unsupported format "${extension}". Supported: ${supportedFormats.join(', ')}`;
        }
        if (file.size > maxSizeMB * 1024 * 1024) {
            return `File too large (${formatFileSize(file.size)}). Maximum: ${maxSizeMB} MB`;
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
        // An image has no text layer, so turning visual analysis off would
        // leave nothing at all to index.
        if (visionOnlyFormats.includes(getFileExtension(file.name))) {
            setDeepParse(true);
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

    const handleUpload = async () => {
        if (!selectedFile) return;

        setUploadState('uploading');

        try {
            await uploadDocument(recordingId, selectedFile, { deepParse });
            setUploadState('success');

            setTimeout(() => {
                onSuccess?.();
                handleClose();
            }, 1500);

        } catch (error: unknown) {
            setUploadState('idle');
            addNotification({
                type: 'error',
                message: getErrorMessage(error, 'Upload failed. Please try again.'),
            });
        }
    };

    const handleRemoveFile = () => {
        setSelectedFile(null);
        setUploadState('idle');
        if (fileInputRef.current) {
            fileInputRef.current.value = '';
        }
    };

    const isVisionOnly = selectedFile
        ? visionOnlyFormats.includes(getFileExtension(selectedFile.name))
        : false;
    const isLargeFile = !!selectedFile && selectedFile.size > DOCUMENT_SIZE_WARNING_BYTES;

    return (
        <Modal
            open={isOpen}
            onClose={handleClose}
            // An upload in flight cannot be cancelled, so the dialog stops
            // accepting a dismissal until it resolves.
            dismissible={uploadState !== 'uploading'}
            size="md"
            title="Upload Document"
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
                        onClick={handleUpload}
                        disabled={!selectedFile || uploadState === 'uploading' || uploadState === 'success'}
                        loading={uploadState === 'uploading'}
                        iconLeft={<Upload aria-hidden="true" className="w-4 h-4" />}
                    >
                        {uploadState === 'uploading' ? 'Uploading...' : 'Upload Document'}
                    </Button>
                </>
            }
        >
            <div className="space-y-5">
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
                            <FileText aria-hidden="true" className="mx-auto h-12 w-12 text-status-success-fg" />
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
                                PDF, PowerPoint, Word, Excel, CSV, text, Markdown or images, up to {maxSizeMB}MB
                            </p>
                        </div>
                    )}
                </div>

                {/* Visual analysis */}
                <label
                    className={`flex items-start gap-3 rounded-lg border p-3 transition-colors ${isVisionOnly
                        ? 'cursor-not-allowed border-surface-border bg-surface-inset'
                        : 'cursor-pointer border-surface-border hover:border-action-border'
                        }`}
                >
                    <input
                        type="checkbox"
                        checked={deepParse}
                        disabled={isVisionOnly || uploadState === 'uploading'}
                        onChange={(e) => setDeepParse(e.target.checked)}
                        className="mt-0.5 h-4 w-4 accent-action"
                    />
                    <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                            <Sparkles aria-hidden="true" className="h-4 w-4 text-action-text" />
                            Analyse visually with AI
                        </span>
                        <span className="mt-1 block text-xs text-contrast-helper">
                            {isVisionOnly
                                ? 'Required for images: there is no text to extract without it.'
                                : 'Reads charts, diagrams, tables and scanned pages that text extraction alone would miss. Uses your configured AI provider.'}
                        </span>
                    </span>
                </label>

                {/* Large-file cost warning */}
                {isLargeFile && deepParse && (
                    <div className="flex items-start gap-2 rounded-lg bg-status-warning-bg p-3 text-status-warning-fg">
                        <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 flex-shrink-0" />
                        <p className="text-xs">
                            This file is {formatFileSize(selectedFile.size)}. With visual analysis on, every
                            page is sent to your AI provider, so a document this size can take a while to
                            parse and will use a noticeable amount of provider quota. Parsing runs in the
                            background and you can keep working.
                        </p>
                    </div>
                )}

                {/* Success Message */}
                {uploadState === 'success' && (
                    <div className="flex items-center gap-2 rounded-lg bg-status-success-bg p-3 text-status-success-fg">
                        <CheckCircle aria-hidden="true" className="h-5 w-5 flex-shrink-0" />
                        <span>Document uploaded successfully!</span>
                    </div>
                )}
            </div>
        </Modal>
    );
}
