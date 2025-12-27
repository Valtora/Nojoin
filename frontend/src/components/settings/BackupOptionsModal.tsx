import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { FileArchive, X } from 'lucide-react';
import { Switch } from '@/components/ui/Switch';

interface BackupOptionsModalProps {
    isOpen: boolean;
    onClose: () => void;
    onConfirm: (includeAudio: boolean) => void;
    isLoading: boolean;
}

export const BackupOptionsModal: React.FC<BackupOptionsModalProps> = ({
    isOpen,
    onClose,
    onConfirm,
    isLoading
}) => {
    const [mounted, setMounted] = useState(false);
    const [includeAudio, setIncludeAudio] = useState(true);

    useEffect(() => {
        setMounted(true);
        return () => setMounted(false);
    }, []);

    if (!isOpen || !mounted) return null;

    return createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 backdrop-blur-sm">
            <div
                className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-lg border border-gray-300 dark:border-gray-800 p-6 relative animate-in fade-in zoom-in-95 duration-200"
                role="dialog"
                aria-modal="true"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex justify-between items-center mb-6">
                    <div className="flex items-center gap-3">
                        <div className="p-2 bg-orange-100 dark:bg-orange-900/20 rounded-lg">
                            <FileArchive className="w-6 h-6 text-orange-600 dark:text-orange-400" />
                        </div>
                        <div>
                            <h3 className="text-lg font-bold text-gray-900 dark:text-white">
                                Create Backup
                            </h3>
                            <p className="text-sm text-gray-500 dark:text-gray-400">
                                Configure settings before downloading.
                            </p>
                        </div>
                    </div>
                    <button
                        onClick={onClose}
                        disabled={isLoading}
                        className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
                    >
                        <X className="w-5 h-5" />
                    </button>
                </div>

                <div className="space-y-6">
                    <div className="rounded-lg border border-gray-200 dark:border-gray-800 p-4 bg-gray-50 dark:bg-gray-800/50">
                        <div className="flex items-center justify-between mb-2">
                            <label htmlFor="include-audio" className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                                Include Audio Files
                            </label>
                            <Switch
                                checked={includeAudio}
                                onCheckedChange={setIncludeAudio}
                                disabled={isLoading}
                                id="include-audio"
                            />
                        </div>
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                            {includeAudio
                                ? "Full backup with all recording audio files."
                                : "Metadata only backup (significantly smaller). Restored meetings will not have audio playback."}
                        </p>
                    </div>
                </div>

                <div className="flex justify-end pt-8 gap-3">
                    <button
                        onClick={onClose}
                        disabled={isLoading}
                        className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg text-sm font-medium"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={() => onConfirm(includeAudio)}
                        disabled={isLoading}
                        className="px-4 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed font-medium transition-colors flex items-center gap-2 text-sm shadow-sm"
                    >
                        {isLoading ? (
                            <>
                                <svg className="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                </svg>
                                Processing...
                            </>
                        ) : (
                            'Download Backup'
                        )}
                    </button>
                </div>
            </div>
        </div>,
        document.body
    );
};
