'use client';

import { useState, useRef, useCallback } from 'react';
import { exportBackup, importBackup } from '@/lib/api';
import { Download, Upload, Loader2, CheckCircle, X, FileArchive, Trash2, AlertOctagon, AlertTriangle } from 'lucide-react';
import RestoreOptionsModal from '@/components/settings/RestoreOptionsModal';

const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

export default function BackupRestore() {
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isValidZip, setIsValidZip] = useState<boolean>(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [showRestoreOptions, setShowRestoreOptions] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropZoneRef = useRef<HTMLDivElement>(null);

  const handleExport = async () => {
    try {
      setExporting(true);
      setMessage(null);
      const blob = await exportBackup();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `nojoin_backup_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '_')}.zip`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      setMessage({ type: 'success', text: 'Backup exported successfully.' });
    } catch (error: any) {
      console.error('Export failed:', error);
      const errorMsg = error.response?.data?.detail || 'Failed to export backup';
      setMessage({ type: 'error', text: errorMsg });
    } finally {
      setExporting(false);
    }
  };

  const validateFile = (file: File): boolean => {
    const nameValid = file.name.toLowerCase().endsWith('.zip');
    const typeValid = !file.type || file.type === 'application/zip' || file.type === 'application/x-zip-compressed';

    if (nameValid && typeValid) {
      return true;
    }
    return false;
  };

  const handleFileSelect = (file: File) => {
    if (validateFile(file)) {
      setSelectedFile(file);
      setIsValidZip(true);
      setMessage(null);
    } else {
      setSelectedFile(null);
      setIsValidZip(false);
      setMessage({ type: 'error', text: 'Please select a valid .zip backup file.' });
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

  const handleRemoveFile = () => {
    setSelectedFile(null);
    setIsValidZip(false);
    setMessage(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleRestoreClick = () => {
    if (!selectedFile) return;
    setShowRestoreOptions(true); // Open the options modal
  };

  const performRestore = async (clear: boolean, overwrite: boolean) => {
    if (!selectedFile) return;

    try {
      setImporting(true);
      setUploadProgress(0);
      setMessage(null);
      setShowRestoreOptions(false); // Close the modal

      await importBackup(selectedFile, clear, overwrite, (progress) => {
        setUploadProgress(progress);
      });

      setMessage({ type: 'success', text: 'Backup restored successfully. Please refresh the page.' });
      setSelectedFile(null);
      setIsValidZip(false);

      // Refresh page after short delay to show success message
      setTimeout(() => {
        window.location.reload();
      }, 2000);

    } catch (error: any) {
      console.error('Import failed:', error);
      const errorMsg = error.response?.data?.detail || 'Failed to restore backup.';
      setMessage({ type: 'error', text: errorMsg });
    } finally {
      setImporting(false);
      setUploadProgress(0);
    }
  };

  return (
    <div className="space-y-6 pt-6 border-t border-gray-200 dark:border-gray-700">
      <div>
        <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Backup & Restore</h3>
        <div className="p-4 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 space-y-6">

          {/* Export Section */}
          <div className="pb-6 border-b border-gray-200 dark:border-gray-700">
            <h4 className="text-sm font-medium text-gray-900 dark:text-white mb-2">Export Backup</h4>
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
              Download a zip file containing your database, recordings, and settings.
              <br />
              <span className="text-xs text-orange-600 dark:text-orange-400 font-medium">
                Note: Sensitive API keys (e.g., OpenAI, Anthropic) are redacted for security and must be re-entered after restoration.
              </span>
            </p>
            <button
              onClick={handleExport}
              disabled={exporting}
              className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-orange-600 hover:bg-orange-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500 disabled:opacity-50 transition-colors"
            >
              {exporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Download className="w-4 h-4 mr-2" />}
              {exporting ? 'Exporting...' : 'Download Backup'}
            </button>
          </div>

          {/* Import Section */}
          <div>
            <h4 className="text-sm font-medium text-gray-900 dark:text-white mb-2">Import Backup</h4>
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
              Restore data from a previously exported backup file.
            </p>

            <div className="space-y-4">
              {/* Drag & Drop Zone */}
              <div
                ref={dropZoneRef}
                onClick={() => !importing && fileInputRef.current?.click()}
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
                  ${importing ? 'pointer-events-none opacity-75' : ''}
                `}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".zip"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) handleFileSelect(f);
                  }}
                  className="hidden"
                />

                {selectedFile ? (
                  <div className="space-y-2">
                    <FileArchive className="w-12 h-12 mx-auto text-green-500" />
                    <p className="font-medium text-gray-900 dark:text-white truncate max-w-xs mx-auto">
                      {selectedFile.name}
                    </p>
                    <p className="text-sm text-gray-500">
                      {formatFileSize(selectedFile.size)}
                    </p>
                    {!importing && (
                      <button
                        onClick={(e) => { e.stopPropagation(); handleRemoveFile(); }}
                        className="text-sm text-red-500 hover:text-red-600 underline flex items-center justify-center gap-1 mx-auto"
                      >
                        <Trash2 className="w-3 h-3" /> Remove
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
                      ZIP files only
                    </p>
                  </div>
                )}
              </div>

              {/* Upload Progress */}
              {importing && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-gray-600 dark:text-gray-400">
                      {uploadProgress < 100 ? 'Uploading...' : 'Processing on server (Do not close)...'}
                    </span>
                    <span className="text-gray-900 dark:text-white font-medium">{uploadProgress}%</span>
                  </div>
                  <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                    <div
                      className={`h-2 rounded-full transition-all duration-300 ${uploadProgress === 100 ? 'bg-green-500 animate-pulse' : 'bg-orange-500'}`}
                      style={{ width: `${uploadProgress}%` }}
                    />
                  </div>
                </div>
              )}

              <button
                onClick={handleRestoreClick}
                disabled={!isValidZip || importing}
                className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-orange-600 hover:bg-orange-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500 disabled:opacity-50 transition-colors"
              >
                {importing ? (
                  <>
                    <Loader2 className="animate-spin -ml-1 mr-2 h-4 w-4" />
                    Restoring...
                  </>
                ) : (
                  <>
                    <Upload className="-ml-1 mr-2 h-4 w-4" />
                    Restore Backup
                  </>
                )}
              </button>
            </div>
          </div>

          {message && (
            <div className={`p-4 rounded-md ${message.type === 'success' ? 'bg-green-50 text-green-800 dark:bg-green-900/20 dark:text-green-300' : 'bg-red-50 text-red-800 dark:bg-red-900/20 dark:text-red-300'}`}>
              <div className="flex">
                {message.type === 'success' ? <CheckCircle className="h-5 w-5 mr-2" /> : <AlertTriangle className="h-5 w-5 mr-2" />}
                <p className="text-sm font-medium">{message.text}</p>
              </div>
            </div>
          )}

        </div>
      </div>
      <RestoreOptionsModal
        isOpen={showRestoreOptions}
        onClose={() => setShowRestoreOptions(false)}
        onConfirm={performRestore}
        fileName={selectedFile?.name || 'backup.zip'}
      />
    </div>
  );
}