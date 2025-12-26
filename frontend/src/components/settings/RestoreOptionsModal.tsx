'use client';

import { X, AlertTriangle, FileArchive } from 'lucide-react';
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Switch } from '@/components/ui/Switch';

interface RestoreOptionsModalProps {
    isOpen: boolean;
    onClose: () => void;
    onConfirm: (clearExisting: boolean, overwriteExisting: boolean) => void;
    fileName: string;
}

export default function RestoreOptionsModal({
    isOpen,
    onClose,
    onConfirm,
    fileName,
}: RestoreOptionsModalProps) {
    const [mounted, setMounted] = useState(false);
    const [clearExisting, setClearExisting] = useState(false);
    const [overwriteExisting, setOverwriteExisting] = useState(false);

    useEffect(() => {
        setMounted(true);
        return () => setMounted(false);
    }, []);

    // Reset state when opened
    useEffect(() => {
        if (isOpen) {
            setClearExisting(false);
            setOverwriteExisting(false);
        }
    }, [isOpen]);

    if (!isOpen || !mounted) return null;

    return createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 backdrop-blur-sm">
            <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-lg border border-gray-300 dark:border-gray-800 p-6 relative animate-in fade-in zoom-in-95 duration-200">
                <div className="flex justify-between items-center mb-6">
                    <div className="flex items-center gap-3">
                        <div className="p-2 bg-orange-100 dark:bg-orange-900/20 rounded-lg">
                            <FileArchive className="w-6 h-6 text-orange-600 dark:text-orange-400" />
                        </div>
                        <div>
                            <h3 className="text-lg font-bold text-gray-900 dark:text-white">Restore Backup</h3>
                            <p className="text-sm text-gray-500 dark:text-gray-400">{fileName}</p>
                        </div>
                    </div>
                    <button onClick={onClose} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
                        <X className="w-5 h-5" />
                    </button>
                </div>

                <div className="space-y-6">
                    {/* Option 1: Clear Existing Data */}
                    <div className="rounded-lg border border-gray-200 dark:border-gray-800 p-4 bg-gray-50 dark:bg-gray-800/50">
                        <div className="flex items-center justify-between mb-2">
                            <label className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                                Clear All Existing Data
                            </label>
                            <Switch checked={clearExisting} onCheckedChange={setClearExisting} />
                        </div>
                        <div className="flex gap-2 text-yellow-600 dark:text-yellow-500 text-xs items-start">
                            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                            <p>
                                WARNING: This will delete all current recordings and settings.
                                <br />
                                <span className="font-bold text-yellow-700 dark:text-yellow-400">User accounts are preserved to prevent lockout.</span>
                            </p>
                        </div>
                    </div>

                    {/* Option 2: Conflict Resolution (Only if NOT clearing data) */}
                    {!clearExisting && (
                        <div className="rounded-lg border border-gray-200 dark:border-gray-800 p-4">
                            <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3">
                                Conflict Resolution
                            </h4>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
                                How should we handle meetings in the backup that already exist on this system?
                            </p>

                            <div className="space-y-3">
                                {/* Skip Option */}
                                <label className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${!overwriteExisting
                                        ? 'border-orange-500 bg-orange-50 dark:bg-orange-900/10'
                                        : 'border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800'
                                    }`}>
                                    <input
                                        type="radio"
                                        name="conflict_resolution"
                                        className="mt-1"
                                        checked={!overwriteExisting}
                                        onChange={() => setOverwriteExisting(false)}
                                    />
                                    <div>
                                        <span className="block text-sm font-medium text-gray-900 dark:text-gray-100">
                                            Skip (Safe Merge)
                                        </span>
                                        <span className="block text-xs text-gray-500 dark:text-gray-400 mt-1">
                                            If a meeting already exists, keep the current version. Only add new meetings.
                                        </span>
                                    </div>
                                </label>

                                {/* Overwrite Option */}
                                <label className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${overwriteExisting
                                        ? 'border-orange-500 bg-orange-50 dark:bg-orange-900/10'
                                        : 'border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800'
                                    }`}>
                                    <input
                                        type="radio"
                                        name="conflict_resolution"
                                        className="mt-1"
                                        checked={overwriteExisting}
                                        onChange={() => setOverwriteExisting(true)}
                                    />
                                    <div>
                                        <span className="block text-sm font-medium text-gray-900 dark:text-gray-100">
                                            Overwrite
                                        </span>
                                        <span className="block text-xs text-gray-500 dark:text-gray-400 mt-1">
                                            If a meeting already exists, <strong>delete</strong> the current version and replace it with the backup.
                                        </span>
                                    </div>
                                </label>
                            </div>
                        </div>
                    )}
                </div>

                <div className="flex justify-end gap-3 mt-8">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg text-sm font-medium"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={() => {
                            onConfirm(clearExisting, overwriteExisting);
                            onClose();
                        }}
                        className="px-4 py-2 text-white rounded-lg text-sm font-medium bg-orange-600 hover:bg-orange-700 shadow-sm"
                    >
                        Start Restore
                    </button>
                </div>
            </div>
        </div>,
        document.body
    );
}
