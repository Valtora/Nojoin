'use client';

import { useState, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { X, Upload, FileText, Loader2, CheckCircle, AlertCircle } from 'lucide-react';
import { uploadDocument } from '@/lib/api';

interface DocumentUploadModalProps {
    isOpen: boolean;
    onClose: () => void;
    onSuccess?: () => void;
    recordingId: number;
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

export default function DocumentUploadModal({ isOpen, onClose, onSuccess, recordingId }: DocumentUploadModalProps) {
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    const [uploadState, setUploadState] = useState<UploadState>('idle');
    const [errorMessage, setErrorMessage] = useState('');
    const [isDragging, setIsDragging] = useState(false);

    const fileInputRef = useRef<HTMLInputElement>(null);
    const dropZoneRef = useRef<HTMLDivElement>(null);

    const supportedFormats = ['.pdf', '.txt', '.md'];
    const maxSizeMB = 100;

    const resetState = useCallback(() => {
        setSelectedFile(null);
        setUploadState('idle');
        setErrorMessage('');
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
        if (file.size > maxSizeMB * 1024 * 1024) {
            return `File too large (${formatFileSize(file.size)}). Maximum: ${maxSizeMB} MB`;
        }
        return null;
    };

    const handleFileSelect = (file: File) => {
        const error = validateFile(file);
        if (error) {
            setErrorMessage(error);
            setUploadState('error');
            return;
        }

        setSelectedFile(file);
        setErrorMessage('');
        setUploadState('idle');
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
        setErrorMessage('');

        try {
            await uploadDocument(recordingId, selectedFile);
            setUploadState('success');

            setTimeout(() => {
                onSuccess?.();
                handleClose();
            }, 1500);
        } catch (error: any) {
            setUploadState('error');
            setErrorMessage(error.response?.data?.detail || 'Upload failed. Please try again.');
        }
    };

    const handleRemoveFile = () => {
        setSelectedFile(null);
        setUploadState('idle');
        setErrorMessage('');
        if (fileInputRef.current) {
            fileInputRef.current.value = '';
        }
    };

    if (!isOpen) return null;

    return createPortal(
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm">
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
                <div className="p-6 space-y-6">
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
                                ? 'border-blue-500 bg-blue-100 dark:bg-blue-900/20'
                                : selectedFile
                                    ? 'border-green-500 bg-green-50 dark:bg-green-900/20'
                                    : 'border-gray-300 dark:border-gray-700 hover:border-blue-400 dark:hover:border-blue-600'
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
                                    <span className="font-medium text-blue-500">Click to browse</span> or drag and drop
                                </p>
                                <p className="text-xs text-gray-500">
                                    PDF, TXT, MD up to 100MB
                                </p>
                            </div>
                        )}
                    </div>

                    {/* Success Message */}
                    {uploadState === 'success' && (
                        <div className="flex items-center gap-2 p-3 bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 rounded-lg">
                            <CheckCircle className="w-5 h-5 flex-shrink-0" />
                            <span>Document uploaded successfully!</span>
                        </div>
                    )}

                    {/* Error Message */}
                    {uploadState === 'error' && errorMessage && (
                        <div className="flex items-center gap-2 p-3 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 rounded-lg">
                            <AlertCircle className="w-5 h-5 flex-shrink-0" />
                            <span>{errorMessage}</span>
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
                        className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
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
