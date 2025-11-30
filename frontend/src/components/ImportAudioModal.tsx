'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { X, Upload, FileAudio, Loader2, CheckCircle, AlertCircle, Calendar, FileText } from 'lucide-react';
import ModernDatePicker from '@/components/ui/ModernDatePicker';
import { importAudio, getSupportedAudioFormats, getMaxUploadSizeMB } from '@/lib/api';

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
  const [uploadState, setUploadState] = useState<UploadState>('idle');
  const [uploadProgress, setUploadProgress] = useState(0);
  const [errorMessage, setErrorMessage] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropZoneRef = useRef<HTMLDivElement>(null);

  const supportedFormats = getSupportedAudioFormats();
  const maxSizeMB = getMaxUploadSizeMB();

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  const resetState = useCallback(() => {
    setSelectedFile(null);
    setMeetingName('');
    setRecordedAt(null);
    setUploadState('idle');
    setUploadProgress(0);
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
    setErrorMessage('');
    
    try {
      await importAudio(selectedFile, {
        name: meetingName || undefined,
        recordedAt: recordedAt || undefined,
        onUploadProgress: setUploadProgress,
      });
      
      setUploadState('success');
      
      // Auto-close after success
      setTimeout(() => {
        onSuccess?.();
        handleClose();
      }, 1500);
    } catch (error: unknown) {
      setUploadState('error');
      if (error && typeof error === 'object' && 'response' in error) {
        const axiosError = error as { response?: { data?: { detail?: string } } };
        setErrorMessage(axiosError.response?.data?.detail || 'Upload failed. Please try again.');
      } else {
        setErrorMessage('Upload failed. Please check your connection and try again.');
      }
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

  if (!isOpen || !mounted) return null;

  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-lg flex flex-col border border-gray-300 dark:border-gray-800">
        {/* Header */}
        <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center">
          <h2 className="text-xl font-bold text-gray-900 dark:text-white">Import Audio</h2>
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
                ? 'border-orange-500 bg-orange-50 dark:bg-orange-900/20' 
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
                <FileAudio className="w-12 h-12 mx-auto text-green-500" />
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
                  <span className="font-medium text-orange-500">Click to browse</span> or drag and drop
                </p>
                <p className="text-xs text-gray-500">
                  {supportedFormats.map(f => f.toUpperCase().replace('.', '')).join(', ')} up to {maxSizeMB}MB
                </p>
              </div>
            )}
          </div>

          {/* Upload Progress */}
          {uploadState === 'uploading' && (
            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-gray-600 dark:text-gray-400">Uploading...</span>
                <span className="text-gray-900 dark:text-white font-medium">{uploadProgress}%</span>
              </div>
              <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                <div 
                  className="bg-orange-500 h-2 rounded-full transition-all duration-300"
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
            </div>
          )}

          {/* Success Message */}
          {uploadState === 'success' && (
            <div className="flex items-center gap-2 p-3 bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 rounded-lg">
              <CheckCircle className="w-5 h-5 flex-shrink-0" />
              <span>Audio imported successfully! Processing will begin shortly.</span>
            </div>
          )}

          {/* Error Message */}
          {uploadState === 'error' && errorMessage && (
            <div className="flex items-center gap-2 p-3 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 rounded-lg">
              <AlertCircle className="w-5 h-5 flex-shrink-0" />
              <span>{errorMessage}</span>
            </div>
          )}

          {/* Optional Metadata */}
          {selectedFile && uploadState !== 'success' && (
            <div className="space-y-4 pt-2 border-t border-gray-200 dark:border-gray-800">
              <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">
                Optional Details
              </h3>
              
              {/* Meeting Name */}
              <div>
                <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400 mb-1">
                  <FileText className="w-4 h-4" />
                  Meeting Name
                </label>
                <input
                  type="text"
                  value={meetingName}
                  onChange={(e) => setMeetingName(e.target.value)}
                  placeholder="Enter a custom name..."
                  disabled={uploadState === 'uploading'}
                  className="w-full p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white disabled:opacity-50"
                />
              </div>
              
              {/* Recording Date */}
              <div>
                <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400 mb-1">
                  <Calendar className="w-4 h-4" />
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
                <p className="text-xs text-gray-500 mt-1">
                  If not set, the current time will be used.
                </p>
              </div>
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
            onClick={handleImport}
            disabled={!selectedFile || uploadState === 'uploading' || uploadState === 'success'}
            className="px-4 py-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {uploadState === 'uploading' ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Uploading...
              </>
            ) : (
              <>
                <Upload className="w-4 h-4" />
                Import Audio
              </>
            )}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
