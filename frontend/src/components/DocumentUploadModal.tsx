'use client';

import { useState, useRef, useCallback } from 'react';
import {
    Upload,
    FileText,
    CheckCircle,
    Sparkles,
    AlertTriangle,
    Loader2,
    X,
} from 'lucide-react';
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

/** Per-file state. The queue is uploaded one file at a time, in order. */
interface QueuedUpload {
    /** Stable across re-renders and independent of the file's name. */
    id: string;
    file: File;
    deepParse: boolean;
    status: 'pending' | 'uploading' | 'success' | 'error';
    error?: string;
}

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

const isVisionOnlyFile = (file: File): boolean =>
    visionOnlyFormats.includes(getFileExtension(file.name));

export default function DocumentUploadModal({ isOpen, onClose, onSuccess, recordingId }: DocumentUploadModalProps) {
    const [queue, setQueue] = useState<QueuedUpload[]>([]);
    const [isUploading, setIsUploading] = useState(false);
    const [isDragging, setIsDragging] = useState(false);
    const { addNotification } = useNotificationStore();

    const fileInputRef = useRef<HTMLInputElement>(null);
    const dropZoneRef = useRef<HTMLDivElement>(null);
    // Ids are only ever compared against each other, so a counter is enough and
    // avoids depending on crypto.randomUUID being present.
    const nextIdRef = useRef(0);

    const resetState = useCallback(() => {
        setQueue([]);
        setIsUploading(false);
        setIsDragging(false);
        if (fileInputRef.current) {
            fileInputRef.current.value = '';
        }
    }, []);

    const handleClose = useCallback(() => {
        if (isUploading) return;
        resetState();
        onClose();
    }, [isUploading, resetState, onClose]);

    const validateFile = (file: File): string | null => {
        const extension = getFileExtension(file.name);
        if (!supportedFormats.includes(extension as (typeof SUPPORTED_DOCUMENT_FORMATS)[number])) {
            return `${file.name}: unsupported format "${extension}". Supported: ${supportedFormats.join(', ')}`;
        }
        if (file.size > maxSizeMB * 1024 * 1024) {
            return `${file.name}: too large (${formatFileSize(file.size)}). Maximum: ${maxSizeMB} MB`;
        }
        return null;
    };

    const handleFilesSelected = (files: FileList | File[]) => {
        const incoming = Array.from(files);
        if (incoming.length === 0) return;

        const rejected: string[] = [];
        const accepted: QueuedUpload[] = [];
        // Selecting the same file twice is a slip, not an instruction to upload
        // it twice, and the second copy would be indistinguishable in the
        // documents list. Name and size is as much identity as a File gives
        // without reading it.
        //
        // Sorted out here rather than inside the state updater, because an
        // updater must stay pure: React invokes it twice in development, which
        // would raise every rejection notification twice over.
        const seen = new Set(queue.map((item) => `${item.file.name}:${item.file.size}`));

        for (const file of incoming) {
            const error = validateFile(file);
            if (error) {
                rejected.push(error);
                continue;
            }
            const key = `${file.name}:${file.size}`;
            if (seen.has(key)) continue;
            seen.add(key);
            accepted.push({
                id: `upload-${nextIdRef.current++}`,
                file,
                // Visual analysis is on by default: text-only extraction misses
                // charts, diagrams and anything scanned, which is the common
                // case for decks.
                deepParse: true,
                status: 'pending',
            });
        }

        if (accepted.length > 0) {
            setQueue((current) => [...current, ...accepted]);
        }

        for (const message of rejected) {
            addNotification({ type: 'error', message });
        }

        // Clearing the input means the same file can be picked again after it
        // has been removed from the queue, which a browser otherwise suppresses
        // because the value has not changed.
        if (fileInputRef.current) {
            fileInputRef.current.value = '';
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

        if (isUploading) return;
        handleFilesSelected(e.dataTransfer.files);
    };

    const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files) {
            handleFilesSelected(e.target.files);
        }
    };

    const setDeepParse = (id: string, deepParse: boolean) => {
        setQueue((current) =>
            current.map((item) =>
                item.id === id && !isVisionOnlyFile(item.file)
                    ? { ...item, deepParse }
                    : item,
            ),
        );
    };

    const setDeepParseForAll = (deepParse: boolean) => {
        setQueue((current) =>
            current.map((item) =>
                // An image has no text layer, so turning visual analysis off
                // would leave nothing at all to index. It stays on whatever the
                // bulk control says.
                isVisionOnlyFile(item.file) ? item : { ...item, deepParse },
            ),
        );
    };

    const handleRemoveFile = (id: string) => {
        setQueue((current) => current.filter((item) => item.id !== id));
    };

    const handleUpload = async () => {
        // A retry after a partial failure re-sends only what failed; anything
        // already uploaded exists on the server and would otherwise duplicate.
        const pending = queue.filter((item) => item.status !== 'success');
        if (pending.length === 0 || isUploading) return;

        setIsUploading(true);
        setQueue((current) =>
            current.map((item) =>
                item.status === 'error' ? { ...item, status: 'pending', error: undefined } : item,
            ),
        );

        let failures = 0;

        // Sequential, not concurrent. The backend admits two document uploads
        // per user at a time and rejects the rest, so a fan-out of a ten-file
        // drop would fail most of it; one at a time also keeps the order of the
        // documents list matching the order they were queued in.
        for (const item of pending) {
            setQueue((current) =>
                current.map((entry) =>
                    entry.id === item.id ? { ...entry, status: 'uploading' } : entry,
                ),
            );

            try {
                await uploadDocument(recordingId, item.file, { deepParse: item.deepParse });
                setQueue((current) =>
                    current.map((entry) =>
                        entry.id === item.id ? { ...entry, status: 'success' } : entry,
                    ),
                );
                // Refresh per file rather than once at the end, so a long queue
                // fills the documents panel as it goes.
                onSuccess?.();
            } catch (error: unknown) {
                failures += 1;
                const message = getErrorMessage(error, 'Upload failed. Please try again.');
                setQueue((current) =>
                    current.map((entry) =>
                        entry.id === item.id ? { ...entry, status: 'error', error: message } : entry,
                    ),
                );
                addNotification({ type: 'error', message: `${item.file.name}: ${message}` });
            }
        }

        setIsUploading(false);

        // Everything landed, so the dialog has nothing left to say. A partial
        // failure keeps it open with the failed rows in place to retry.
        if (failures === 0) {
            setTimeout(() => {
                resetState();
                onClose();
            }, 1500);
        }
    };

    const pendingCount = queue.filter((item) => item.status !== 'success').length;
    const successCount = queue.filter((item) => item.status === 'success').length;
    const failedCount = queue.filter((item) => item.status === 'error').length;
    const allSucceeded = queue.length > 0 && successCount === queue.length;
    const totalBytes = queue.reduce((sum, item) => sum + item.file.size, 0);
    const nonVisionCount = queue.filter((item) => !isVisionOnlyFile(item.file)).length;
    const allDeepParse =
        nonVisionCount > 0 &&
        queue.every((item) => isVisionOnlyFile(item.file) || item.deepParse);

    const uploadLabel = isUploading
        ? 'Uploading...'
        : failedCount > 0
            ? `Retry ${failedCount} ${failedCount === 1 ? 'document' : 'documents'}`
            : pendingCount > 1
                ? `Upload ${pendingCount} documents`
                : 'Upload document';

    return (
        <Modal
            open={isOpen}
            onClose={handleClose}
            // An upload in flight cannot be cancelled, so the dialog stops
            // accepting a dismissal until it resolves.
            dismissible={!isUploading}
            size="md"
            title={queue.length > 1 ? 'Upload Documents' : 'Upload Document'}
            footer={
                <>
                    <Button
                        variant="ghost"
                        onClick={handleClose}
                        disabled={isUploading}
                    >
                        Cancel
                    </Button>
                    <Button
                        variant="primary"
                        onClick={handleUpload}
                        disabled={pendingCount === 0 || isUploading}
                        loading={isUploading}
                        iconLeft={<Upload aria-hidden="true" className="w-4 h-4" />}
                    >
                        {uploadLabel}
                    </Button>
                </>
            }
        >
            <div className="space-y-5">
                {/* Drop Zone */}
                <div
                    ref={dropZoneRef}
                    onClick={() => !isUploading && fileInputRef.current?.click()}
                    onDragEnter={handleDragEnter}
                    onDragLeave={handleDragLeave}
                    onDragOver={handleDragOver}
                    onDrop={handleDrop}
                    className={`
              relative cursor-pointer rounded-lg border-2 border-dashed p-6 text-center transition-colors
              ${isDragging
                            ? 'border-action bg-action-tint'
                            : queue.length > 0
                                ? 'border-status-success-border bg-status-success-bg'
                                : 'border-control-border hover:border-action-border'
                        }
              ${isUploading ? 'pointer-events-none opacity-75' : ''}
            `}
                >
                    <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        accept={supportedFormats.join(',')}
                        onChange={handleFileInputChange}
                        data-testid="document-file-input"
                        className="hidden"
                    />

                    <div className="space-y-2">
                        <Upload
                            aria-hidden="true"
                            className={`mx-auto h-10 w-10 ${queue.length > 0 ? 'text-status-success-fg' : 'text-contrast-icon-muted'}`}
                        />
                        <p className="text-contrast-helper">
                            <span className="font-medium text-action-text">Click to browse</span> or drag
                            and drop
                        </p>
                        <p className="text-xs text-contrast-helper">
                            {queue.length > 0
                                ? `${queue.length} ${queue.length === 1 ? 'file' : 'files'} ready, ${formatFileSize(totalBytes)} in total. Add more or upload.`
                                : `One file or many. PDF, PowerPoint, Word, Excel, CSV, text, Markdown or images, up to ${maxSizeMB}MB each.`}
                        </p>
                    </div>
                </div>

                {/* Bulk visual-analysis control. Only worth the row once there
                    is more than one file to flip, and only where at least one
                    of them can actually be turned off. */}
                {queue.length > 1 && nonVisionCount > 0 && (
                    <div className="flex flex-wrap items-center justify-between gap-2 px-1">
                        <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                            <Sparkles aria-hidden="true" className="h-4 w-4 text-action-text" />
                            Analyse visually with AI
                        </span>
                        <button
                            type="button"
                            onClick={() => setDeepParseForAll(!allDeepParse)}
                            disabled={isUploading}
                            className="text-sm font-medium text-action-text underline disabled:cursor-not-allowed disabled:opacity-60"
                        >
                            {allDeepParse ? 'Turn off for all' : 'Turn on for all'}
                        </button>
                    </div>
                )}

                {/* One row per file: what it is, whether it gets visual
                    analysis, and where it got to. */}
                {queue.length > 0 && (
                    <ul className="max-h-[20rem] space-y-2 overflow-y-auto pr-1">
                        {queue.map((item) => {
                            const visionOnly = isVisionOnlyFile(item.file);
                            const isLargeFile = item.file.size > DOCUMENT_SIZE_WARNING_BYTES;

                            return (
                                <li
                                    key={item.id}
                                    data-testid="document-upload-row"
                                    className="rounded-lg border border-surface-border p-3"
                                >
                                    <div className="flex items-start gap-3">
                                        <FileText
                                            aria-hidden="true"
                                            className="mt-0.5 h-5 w-5 flex-shrink-0 text-action-text"
                                        />
                                        <div className="min-w-0 flex-1">
                                            <p className="truncate text-sm font-medium text-foreground">
                                                {item.file.name}
                                            </p>
                                            <p className="text-xs text-contrast-helper">
                                                {formatFileSize(item.file.size)}
                                            </p>
                                        </div>

                                        {item.status === 'uploading' && (
                                            <Loader2
                                                aria-label="Uploading"
                                                className="h-4 w-4 flex-shrink-0 animate-spin text-action-text"
                                            />
                                        )}
                                        {item.status === 'success' && (
                                            <CheckCircle
                                                aria-label="Uploaded"
                                                className="h-4 w-4 flex-shrink-0 text-status-success-fg"
                                            />
                                        )}
                                        {item.status === 'error' && (
                                            <AlertTriangle
                                                aria-label="Failed"
                                                className="h-4 w-4 flex-shrink-0 text-status-danger-fg"
                                            />
                                        )}
                                        {!isUploading && item.status !== 'success' && (
                                            <button
                                                type="button"
                                                onClick={() => handleRemoveFile(item.id)}
                                                aria-label={`Remove ${item.file.name}`}
                                                className="flex-shrink-0 text-contrast-icon-muted transition-colors hover:text-danger-text"
                                            >
                                                <X aria-hidden="true" className="h-4 w-4" />
                                            </button>
                                        )}
                                    </div>

                                    {item.status !== 'success' && (
                                        <label
                                            className={`mt-3 flex items-start gap-2 ${visionOnly || isUploading ? 'cursor-not-allowed' : 'cursor-pointer'}`}
                                        >
                                            <input
                                                type="checkbox"
                                                checked={visionOnly ? true : item.deepParse}
                                                disabled={visionOnly || isUploading}
                                                onChange={(e) => setDeepParse(item.id, e.target.checked)}
                                                aria-label={`Analyse ${item.file.name} visually with AI`}
                                                className="mt-0.5 h-4 w-4 accent-action"
                                            />
                                            <span className="min-w-0 flex-1">
                                                <span className="flex items-center gap-1.5 text-sm text-foreground">
                                                    <Sparkles
                                                        aria-hidden="true"
                                                        className="h-3.5 w-3.5 text-action-text"
                                                    />
                                                    Analyse visually with AI
                                                </span>
                                                <span className="mt-1 block text-xs text-contrast-helper">
                                                    {visionOnly
                                                        ? 'Required for images: there is no text to extract without it.'
                                                        : 'Reads charts, diagrams, tables and scanned pages that text extraction alone would miss. Uses your configured AI provider.'}
                                                </span>
                                            </span>
                                        </label>
                                    )}

                                    {/* Large-file cost warning */}
                                    {isLargeFile && item.deepParse && item.status !== 'success' && (
                                        <div className="mt-3 flex items-start gap-2 rounded-lg bg-status-warning-bg p-3 text-status-warning-fg">
                                            <AlertTriangle
                                                aria-hidden="true"
                                                className="mt-0.5 h-4 w-4 flex-shrink-0"
                                            />
                                            <p className="text-xs">
                                                This file is {formatFileSize(item.file.size)}. With visual
                                                analysis on, every page is sent to your AI provider, so a
                                                document this size can take a while to parse and will use a
                                                noticeable amount of provider quota. Parsing runs in the
                                                background and you can keep working.
                                            </p>
                                        </div>
                                    )}

                                    {item.status === 'error' && item.error && (
                                        <p className="mt-2 text-xs text-danger-text">{item.error}</p>
                                    )}
                                </li>
                            );
                        })}
                    </ul>
                )}

                {/* Success Message */}
                {allSucceeded && (
                    <div className="flex items-center gap-2 rounded-lg bg-status-success-bg p-3 text-status-success-fg">
                        <CheckCircle aria-hidden="true" className="h-5 w-5 flex-shrink-0" />
                        <span>
                            {successCount === 1
                                ? 'Document uploaded successfully!'
                                : `${successCount} documents uploaded successfully!`}
                        </span>
                    </div>
                )}
            </div>
        </Modal>
    );
}
