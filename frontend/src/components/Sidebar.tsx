'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Recording, RecordingStatus } from '@/types';
import { Calendar, Clock, CheckCircle, Loader2, AlertCircle, HelpCircle, UploadCloud, MoreVertical, Trash2, Edit2, RefreshCw } from 'lucide-react';
import MeetingControls from './MeetingControls';
import { useState, useEffect } from 'react';
import { getRecordings, deleteRecording, renameRecording, retryProcessing } from '@/lib/api';
import ContextMenu from './ContextMenu';
import ConfirmationModal from './ConfirmationModal';

interface SidebarProps {
  recordings: Recording[];
}

const formatDurationString = (seconds?: number) => {
  if (!seconds) return '0s';
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  
  if (hours > 0) {
      return `${hours}hr ${minutes}mins`;
  }
  return `${minutes}mins`;
};

const formatDate = (dateString: string) => {
  return new Date(dateString).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
};

const formatTime = (date: Date) => {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
};

const StatusIcon = ({ status }: { status: RecordingStatus }) => {
  switch (status) {
    case RecordingStatus.PROCESSED:
      return <CheckCircle className="w-3 h-3 text-green-500" />;
    case RecordingStatus.PROCESSING:
      return <Loader2 className="w-3 h-3 text-blue-500 animate-spin" />;
    case RecordingStatus.UPLOADING:
      return <UploadCloud className="w-3 h-3 text-orange-500 animate-pulse" />;
    case RecordingStatus.ERROR:
      return <AlertCircle className="w-3 h-3 text-red-500" />;
    default:
      return <HelpCircle className="w-3 h-3 text-gray-500" />;
  }
};

export default function Sidebar({ recordings: initialRecordings }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [recordings, setRecordings] = useState<Recording[]>(initialRecordings);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; recordingId: number } | null>(null);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");
  
  // Confirmation Modal State
  const [confirmModal, setConfirmModal] = useState<{
    isOpen: boolean;
    title: string;
    message: string;
    onConfirm: () => void;
    isDangerous?: boolean;
  }>({
    isOpen: false,
    title: "",
    message: "",
    onConfirm: () => {},
  });

  const fetchRecordings = async () => {
    try {
      const data = await getRecordings();
      setRecordings(data);
    } catch (error) {
      console.error("Failed to fetch recordings:", error);
    }
  };

  useEffect(() => {
    fetchRecordings();
    // Poll for updates every 5 seconds
    const interval = setInterval(fetchRecordings, 5000);
    return () => clearInterval(interval);
  }, []);

  // Also update if initialRecordings changes (e.g. from server refresh)
  useEffect(() => {
    setRecordings(initialRecordings);
  }, [initialRecordings]);

  const handleContextMenu = (e: React.MouseEvent, recordingId: number) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, recordingId });
  };

  const handleDelete = async (id: number) => {
    setConfirmModal({
        isOpen: true,
        title: "Delete Recording",
        message: "Are you sure you want to delete this recording? This action cannot be undone.",
        isDangerous: true,
        onConfirm: async () => {
            try {
                await deleteRecording(id);
                fetchRecordings();
                if (pathname === `/recordings/${id}`) {
                    router.push('/');
                }
            } catch (e) {
                console.error("Failed to delete", e);
            }
        }
    });
  };

  const handleRenameStart = (id: number, currentName: string) => {
    setRenamingId(id);
    setRenameValue(currentName);
    setContextMenu(null);
  };

  const handleRenameSubmit = async (id: number) => {
    if (!renameValue.trim()) return;
    try {
      await renameRecording(id, renameValue);
      setRenamingId(null);
      fetchRecordings();
    } catch (e) {
      console.error("Failed to rename", e);
    }
  };

  const handleRetry = async (id: number) => {
    try {
      await retryProcessing(id);
      fetchRecordings();
    } catch (e) {
      console.error("Failed to retry", e);
    }
  };

  return (
    <aside className="w-80 flex-shrink-0 border-r border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 overflow-y-auto h-screen sticky top-0">
      <MeetingControls onMeetingEnd={fetchRecordings} />
      <div className="p-4 border-b border-gray-200 dark:border-gray-800">
        <input 
          type="text" 
          placeholder="Search meetings..." 
          className="w-full bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
        />
      </div>
      <div className="p-2 space-y-2">
        {recordings.map((recording) => {
          const isActive = pathname === `/recordings/${recording.id}`;
          const startDate = new Date(recording.created_at);
          const endDate = new Date(startDate.getTime() + (recording.duration_seconds || 0) * 1000);
          const isRenaming = renamingId === recording.id;
          
          return (
            <div key={recording.id} className="relative group">
            {isRenaming ? (
                <div className="p-3 rounded-lg border bg-white dark:bg-gray-900 border-orange-500">
                    <input
                        autoFocus
                        type="text"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onBlur={() => handleRenameSubmit(recording.id)}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter') handleRenameSubmit(recording.id);
                            if (e.key === 'Escape') setRenamingId(null);
                        }}
                        className="w-full text-sm font-semibold bg-transparent focus:outline-none"
                    />
                </div>
            ) : (
            <Link 
              href={`/recordings/${recording.id}`}
              onContextMenu={(e) => handleContextMenu(e, recording.id)}
              className={`block p-3 rounded-lg border transition-all ${
                isActive 
                  ? 'bg-orange-50 dark:bg-orange-900/20 border-orange-500 dark:border-orange-500 shadow-sm' 
                  : 'bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-800 hover:border-orange-300 dark:hover:border-orange-700'
              }`}
            >
              <div className="flex justify-between items-start mb-1">
                  <h3 className={`text-sm font-semibold truncate pr-6 ${isActive ? 'text-orange-700 dark:text-orange-400' : 'text-gray-900 dark:text-gray-100'}`}>
                    {recording.name}
                  </h3>
                  <StatusIcon status={recording.status} />
              </div>
              
              <div className="flex items-center text-xs text-gray-500 dark:text-gray-400 gap-2">
                <span>{formatDate(recording.created_at)}</span>
                <span className="text-gray-300 dark:text-gray-700">|</span>
                <span>{formatTime(startDate)} - {formatTime(endDate)}</span>
                <span className="text-gray-300 dark:text-gray-700">|</span>
                <span>{formatDurationString(recording.duration_seconds)}</span>
              </div>
            </Link>
            )}
            </div>
          );
        })}
      </div>

      {contextMenu && (
        <ContextMenu
            x={contextMenu.x}
            y={contextMenu.y}
            onClose={() => setContextMenu(null)}
            items={[
                { 
                    label: 'Rename', 
                    onClick: () => {
                        const rec = recordings.find(r => r.id === contextMenu.recordingId);
                        if (rec) handleRenameStart(rec.id, rec.name);
                    } 
                },
                ...(recordings.find(r => r.id === contextMenu.recordingId)?.status === RecordingStatus.ERROR ? [{
                    label: 'Retry Processing',
                    onClick: () => handleRetry(contextMenu.recordingId)
                }] : []),
                { 
                    label: 'Delete', 
                    className: 'text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20',
                    onClick: () => handleDelete(contextMenu.recordingId) 
                },
            ]}
        />
      )}
      
      <ConfirmationModal
        isOpen={confirmModal.isOpen}
        onClose={() => setConfirmModal({ ...confirmModal, isOpen: false })}
        onConfirm={confirmModal.onConfirm}
        title={confirmModal.title}
        message={confirmModal.message}
        isDangerous={confirmModal.isDangerous}
      />
    </aside>
  );
}
