import { useState } from "react";
import { X, Download, AlertTriangle, FileAudio } from "lucide-react";
import { createPortal } from "react-dom";
import { exportBackupAsync } from "@/lib/api";
import { useBackupStore } from "@/lib/backupStore";
import { useNotificationStore } from "@/lib/notificationStore";

interface BackupOptionsModalProps {
    isOpen: boolean;
    onClose: () => void;
}

export default function BackupOptionsModal({ isOpen, onClose }: BackupOptionsModalProps) {
    const [includeAudio, setIncludeAudio] = useState(true);
    const [isProcessing, setIsProcessing] = useState(false);
    const { setTaskId } = useBackupStore();
    const { addNotification } = useNotificationStore();

    if (!isOpen) return null;

    const handleExport = async () => {
        try {
            setIsProcessing(true);
            const { task_id } = await exportBackupAsync(includeAudio);

            // Set task ID to trigger global poller
            setTaskId(task_id);

            // Notify user
            addNotification({
                type: "success",
                message: "Backup started in background. Download will start automatically when ready.",
                persistent: false,
            });

            onClose();
        } catch (error) {
            console.error("Backup export failed:", error);
            addNotification({
                type: "error",
                message: "Failed to start backup process. Please try again.",
            });
        } finally {
            setIsProcessing(false);
        }
    };

    return createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 backdrop-blur-sm">
            <div
                className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-lg border border-gray-300 dark:border-gray-800 p-6 relative animate-in fade-in zoom-in-95 duration-200"
                role="dialog"
                aria-modal="true"
                onClick={(e) => e.stopPropagation()}
            >
                <button
                    onClick={onClose}
                    className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
                >
                    <X className="w-5 h-5" />
                </button>

                <div className="flex items-center gap-3 mb-6">
                    <div className="p-3 bg-blue-100 dark:bg-blue-900/30 rounded-lg">
                        <Download className="w-6 h-6 text-blue-600 dark:text-blue-400" />
                    </div>
                    <div>
                        <h2 className="text-xl font-semibold text-gray-900 dark:text-white">Create Backup</h2>
                        <p className="text-sm text-gray-500 dark:text-gray-400">
                            Export your data and recordings
                        </p>
                    </div>
                </div>

                <div className="space-y-6">
                    <div className="flex items-start gap-4 p-4 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-200 dark:border-gray-800">
                        <div className="mt-1">
                            <FileAudio className="w-5 h-5 text-gray-400" />
                        </div>
                        <div className="flex-1 space-y-2">
                            <div className="flex items-center justify-between">
                                <label htmlFor="include-audio" className="font-medium text-gray-900 dark:text-gray-200">
                                    Include Audio Files
                                </label>
                                <div className="relative inline-flex items-center cursor-pointer">
                                    <input
                                        type="checkbox"
                                        id="include-audio"
                                        className="sr-only peer"
                                        checked={includeAudio}
                                        onChange={(e) => setIncludeAudio(e.target.checked)}
                                    />
                                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                                </div>
                            </div>
                            <p className="text-sm text-gray-500 dark:text-gray-400">
                                Include original audio recordings in the backup archive.
                            </p>
                        </div>
                    </div>

                    {!includeAudio && (
                        <div className="flex items-start gap-3 p-4 bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-200 rounded-lg text-sm">
                            <AlertTriangle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                            <p>
                                Excluding audio files will create a much smaller backup, but restored meetings
                                will <strong>not be playable</strong>. Metadata, transcripts, and notes will still be preserved.
                            </p>
                        </div>
                    )}

                    <div className="flex items-center justify-end gap-3 mt-6 pt-6 border-t border-gray-100 dark:border-gray-800">
                        <button
                            onClick={onClose}
                            className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={handleExport}
                            disabled={isProcessing}
                            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg shadow-sm transition-colors flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            {isProcessing ? (
                                <>
                                    <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                    Starting...
                                </>
                            ) : (
                                <>
                                    <Download className="w-4 h-4" />
                                    Create Backup
                                </>
                            )}
                        </button>
                    </div>
                </div>
            </div>
        </div>,
        document.body
    );
}
