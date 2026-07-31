'use client';

import { useState, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { X, Upload, FileText, Loader2, CheckCircle, Sparkles, AlertTriangle } from 'lucide-react';
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

    if (!isOpen) return null;

    const isVisionOnly = selectedFile
        ? visionOnlyFormats.includes(getFileExtension(selectedFile.name))
        : false;
    const isLargeFile = !!selectedFile && selectedFile.size > DOCUMENT_SIZE_WARNING_BYTES;

    return createPortal(
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-scrim">
            <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-lg flex flex-col border border-gray-300 dark:border-gray-800">
                {/* Header */}
                <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center">
                    <h2 className="text-xl font-bold text-gray-900 dark:text-white">Upload Document</h2>
                    <button
                        onClick={handleClose}
                        disabled={uploadState === 'uploading'}
                        className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 disabled:opacity-50"
                    >
                        <X className="w-6 h-6" />
                    </button>
                </div>

                {/* Content */}
                <div className="p-6 space-y-5">
                    {/* Drop Zone */}
                    <div
                        ref={dropZoneRef}
                        onClick={() => uploadState !== 'uploading' && fileInputRef.current?.click()}
                        onDragEnter={handleDragEnter}
                        onDragLeave={handleDragLeave}
                        onDragOver={handleDragOver}
                        onDrop={handleDrop}
                        className={`
              relative border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors
              ${isDragging
                                ? 'border-orange-500 bg-orange-100 dark:bg-orange-900/20'
                                : selectedFile
                                    ? 'border-green-500 bg-green-50 dark:bg-green-900/20'
                                    : 'border-gray-300 dark:border-gray-700 hover:border-orange-400 dark:hover:border-orange-600'
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
                                <FileText className="w-12 h-12 mx-auto text-green-500" />
                                <p className="font-medium text-gray-900 dark:text-white truncate max-w-xs mx-auto">
                                    {selectedFile.name}
                                </p>
                                <p className="text-sm text-gray-500">
                                    {formatFileSize(selectedFile.size)}
                                </p>
                                {uploadState !== 'uploading' && (
                                    <button
                                        onClick={(e) => { e.stopPropagation(); handleRemoveFile(); }}
                                        className="text-sm text-red-500 hover:text-red-600 underline"
                                    >
                                        Remove
                                    </button>
                                )}
                            </div>
                        ) : (
                            <div className="space-y-2">
                                <Upload className="w-12 h-12 mx-auto text-gray-400" />
                                <p className="text-gray-600 dark:text-gray-400">
                                    <span className="font-medium text-orange-600 dark:text-orange-400">Click to browse</span> or drag and drop
                                </p>
                                <p className="text-xs text-gray-500">
                                    PDF, PowerPoint, Word, Excel, CSV, text, Markdown or images, up to {maxSizeMB}MB
                                </p>
                            </div>
                        )}
                    </div>

                    {/* Visual analysis */}
                    <label
                        className={`flex items-start gap-3 rounded-lg border p-3 transition-colors ${isVisionOnly
                            ? 'cursor-not-allowed border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-gray-800/40'
                            : 'cursor-pointer border-gray-200 hover:border-orange-300 dark:border-gray-800 dark:hover:border-orange-500/30'
                            }`}
                    >
                        <input
                            type="checkbox"
                            checked={deepParse}
                            disabled={isVisionOnly || uploadState === 'uploading'}
                            onChange={(e) => setDeepParse(e.target.checked)}
                            className="mt-0.5 h-4 w-4 accent-orange-600"
                        />
                        <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-1.5 text-sm font-medium text-gray-900 dark:text-white">
                                <Sparkles className="h-4 w-4 text-orange-500" />
                                Analyse visually with AI
                            </span>
                            <span className="mt-1 block text-xs text-gray-500 dark:text-gray-400">
                                {isVisionOnly
                                    ? 'Required for images: there is no text to extract without it.'
                                    : 'Reads charts, diagrams, tables and scanned pages that text extraction alone would miss. Uses your configured AI provider.'}
                            </span>
                        </span>
                    </label>

                    {/* Large-file cost warning */}
                    {isLargeFile && deepParse && (
                        <div className="flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-amber-800 dark:bg-amber-900/20 dark:text-amber-300">
                            <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" />
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
                        <div className="flex items-center gap-2 p-3 bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 rounded-lg">
                            <CheckCircle className="w-5 h-5 flex-shrink-0" />
                            <span>Document uploaded successfully!</span>
                        </div>
                    )}

                </div>

                {/* Footer */}
                <div className="p-6 border-t border-gray-200 dark:border-gray-800 flex justify-end gap-3">
                    <button
                        onClick={handleClose}
                        disabled={uploadState === 'uploading'}
                        className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg disabled:opacity-50"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleUpload}
                        disabled={!selectedFile || uploadState === 'uploading' || uploadState === 'success'}
                        className="px-4 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                    >
                        {uploadState === 'uploading' ? (
                            <>
                                <Loader2 className="w-4 h-4 animate-spin" />
                                Uploading...
                            </>
                        ) : (
                            <>
                                <Upload className="w-4 h-4" />
                                Upload Document
                            </>
                        )}
                    </button>
                </div>
            </div>
        </div>,
        document.body
    );
}
