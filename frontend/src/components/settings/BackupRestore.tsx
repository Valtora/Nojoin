'use client';

import { useState } from 'react';
import { exportBackup, importBackup } from '@/lib/api';
import { Download, Upload, AlertTriangle, Loader2, CheckCircle } from 'lucide-react';

export default function BackupRestore() {
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [clearExisting, setClearExisting] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);

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
    } catch (error) {
      console.error('Export failed:', error);
      setMessage({ type: 'error', text: 'Failed to export backup.' });
    } finally {
      setExporting(false);
    }
  };

  const handleImport = async () => {
    if (!selectedFile) return;
    
    if (clearExisting && !confirm('Are you sure you want to clear all existing data? This action cannot be undone.')) {
      return;
    }

    try {
      setImporting(true);
      setMessage(null);
      await importBackup(selectedFile, clearExisting);
      setMessage({ type: 'success', text: 'Backup restored successfully. Please refresh the page.' });
      setSelectedFile(null);
    } catch (error) {
      console.error('Import failed:', error);
      setMessage({ type: 'error', text: 'Failed to restore backup.' });
    } finally {
      setImporting(false);
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
            </p>
            <button
              onClick={handleExport}
              disabled={exporting}
              className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50"
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
              <div className="flex items-center space-x-4">
                <input
                  type="file"
                  accept=".zip"
                  onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                  className="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100 dark:file:bg-gray-700 dark:file:text-gray-200"
                />
              </div>

              <div className="flex items-center space-x-2">
                <input
                  id="clear-existing"
                  type="checkbox"
                  checked={clearExisting}
                  onChange={(e) => setClearExisting(e.target.checked)}
                  className="h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded"
                />
                <label htmlFor="clear-existing" className="text-sm text-gray-700 dark:text-gray-300">
                  Clear existing data before restoring (Warning: This will delete all current data)
                </label>
              </div>

              <button
                onClick={handleImport}
                disabled={!selectedFile || importing}
                className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-green-600 hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500 disabled:opacity-50"
              >
                {importing ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Upload className="w-4 h-4 mr-2" />}
                {importing ? 'Restoring...' : 'Restore Backup'}
              </button>
            </div>
          </div>

          {message && (
            <div className={`p-4 rounded-md ${message.type === 'success' ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800'}`}>
              <div className="flex">
                {message.type === 'success' ? <CheckCircle className="h-5 w-5 mr-2" /> : <AlertTriangle className="h-5 w-5 mr-2" />}
                <p className="text-sm font-medium">{message.text}</p>
              </div>
            </div>
          )}

        </div>
      </div>
    </div>
  );
}
